# pulumi-aws-static-site

**Nothing here stays deployed, and there is no live URL to go and look at.** This is a reference
implementation of a static-site stack on AWS — a private S3 bucket served through CloudFront —
in Pulumi and Python, and its operating model is that every environment is applied, verified and
destroyed inside the same hour. There is no long-running demo environment, no drift detection and
nothing on call. What is on offer instead is a lifecycle you can run yourself, in your own AWS
account, from a fresh clone: `stage` and `prod` both stand up from zero, serve real traffic, and
come down again leaving nothing behind. A CI job that runs that whole lifecycle against `stage`
on demand is part of the design and arrives with the Phase 4 workflows; until then the proof is a
hand-run walk, and it is only ever as recent as the last one.

That constraint is what the rest of this repository is arranged around. Reproducibility from zero
and complete, verifiable teardown are the goals; uptime and cost optimisation of running
infrastructure are not. Anything that blocks or survives `pulumi destroy` is treated as a defect
here.

This repository builds infrastructure only. It never uploads an application. A second repository
— `react-cloudfront-app` — builds and deploys the site, through an identity this repository
creates for it and destroys with the environment; `docs/DEPLOY_CONTRACT.md` (written in Phase 5) is
the whole of that interface, and it is byte-for-byte the contract `terraform-aws-static-site` publishes: the app
repository cannot tell which of the two stood its environment up.

It is a port. `terraform-aws-static-site`, beside this one, is the reference: the same operating
model, the same resource set, the same bootstrap split, the same gates, and every non-obvious
choice here cites the Terraform file it comes from. `IMPLEMENTATION_PLAN.md` is the port's plan
and `docs/LOCKED_DECISIONS.md` the five decisions it does not reopen.

---

## What it builds

One `StaticSite` component, instantiated once per environment from one Pulumi project with one
stack per environment. This is the target shape, the Terraform module's resource set mapped one
for one; **as of Phase 0 the component declares nothing**, and Phase 2 fills it.

```
  viewer ──HTTPS──▶ CloudFront distribution ──OAC / SigV4──▶ S3 bucket (private)
                     │                                       no website config,
                     │                                       no public read, no ACLs
                     ├─ 2 cache + 2 response headers policies, one pair per behaviour.
                     │  BOTH carry CSP + HSTS + nosniff; only Cache-Control differs:
                     │    /assets/*         public, max-age=31536000, immutable
                     │    everything else   no-cache
                     │
                     ├─ viewer-request function: a URI naming no file → /index.html
                     │
                     └─ standard logging (v2) ──▶ CloudWatch Logs, always in us-east-1

  beside them, created and destroyed with the environment:

    SSM parameters      /static-site/<env>/{bucket_name, cloudfront_distribution_id, site_url}
    IAM role            the app repository's deploy identity, trusted on its OIDC subject
                        and carrying a permissions boundary the bootstrap owns
```

The control plane, which is where most of the design actually lives:

```
  bootstrap/            applied by hand, on a file:// backend, with an elevated identity
    ├── S3 state bucket (versioned, SSE, protect=True)
    ├── GitHub Actions OIDC provider
    ├── <prefix>-ci-plan            trusted on  repo:<owner>@<id>/<repo>@<id>:pull_request
    ├── <prefix>-ci-apply-<env>     one per environment, trusted on
    │                                   repo:<owner>@<id>/<repo>@<id>:environment:<env>
    └── app-deploy permissions boundary (a managed policy, outlives every environment)
                    │
                    ▼
  envs/  stacks stage, prod   applied through those roles, from a workflow,
                              with no long-lived AWS access key anywhere
```

That lower half is **workload identity federation**: the CI roles trust no AWS principal at all,
only `sts:AssumeRoleWithWebIdentity` from that provider, conditioned on `aud` and on exactly the
subjects above. `bootstrap/` itself is a human at a terminal with an exported session, and
deliberately not that. The plan role is read-only on the state bucket — a Pulumi preview takes no
lock and persists nothing — and each apply role is granted its own stack's checkpoint, lock and
history prefixes and denied every other stack's (`docs/LOCKED_DECISIONS.md`, LOCKED-5).

State is a self-managed `s3://` backend with the passphrase secrets provider: no Pulumi Cloud, no
access token, no KMS key, no DynamoDB (LOCKED-1). What the passphrase protects is three values
the provider marks secret by schema — the SSM parameters' `value` — none of which is sensitive.

## Repository map

| Path | What it is |
|---|---|
| `packages/static-site/` | The component package (uv workspace member `static-site`, import name `static_site`). Today: `args.py` (`StaticSiteArgs`, the full field list with docstrings), `validation.py`, `stack_config.py` (the ten `static-site:*` keys read from a stack), and a `component.py` whose `StaticSite` declares nothing. Phase 2 adds the naming rules, the policy-document builders and one file per Terraform source file the component ports. Its `README.md` carries the interface, with the arguments and outputs table generated between `BEGIN_INTERFACE`/`END_INTERFACE` markers. |
| `bootstrap/` | Pulumi project `static-site-bootstrap`. Today an empty program (`Pulumi.yaml`, `pyproject.toml`, a stub `__main__.py`). Phase 1 makes it the state bucket, the OIDC provider, one plan role, one apply role per environment and the app-deploy permissions boundary, applied by hand on a gitignored `file://` backend because it creates the bucket remote state lives in, with `Pulumi.bootstrap.yaml` as its committed configuration. |
| `envs/` | Pulumi project `static-site`, one stack per environment. Today an empty program. Phase 1 adds `Pulumi.stage.yaml` and `Pulumi.prod.yaml`, the committed inputs — the same ten keys, differing in nothing but the file name — and Phase 3 makes `__main__.py` instantiate the component once with `environment = pulumi.get_stack()`. One project rather than two directories: a stack is a config file, so "two values differ" becomes "one file differs" and the environment constant that had to equal a directory name becomes something that cannot disagree with itself. |
| `policy/` | The CrossGuard policy pack. Today an empty `PolicyPack` (`PulumiPolicy.yaml`, `__main__.py`, `stack_config.py`) that `preview-offline` already loads. Phase 2 adds `interface/`, asserting what the Terraform module's `plan.tftest.hcl` asserted, and `posture/`, the AWS-misconfiguration rules trivy used to supply over HCL — no third-party scanner reads Pulumi Python (LOCKED-4), so the rules this repository acts on are written here — and a `README.md` listing what is enforced and what is not. |
| `tests/unit/` | pytest over pure functions with no Pulumi runtime. Today: validation, the stack-config readers, the interface generator. Phase 1 adds the bootstrap config and every policy-document builder; Phase 2 adds naming. |
| `tests/offline/` | The credential-free preview: a throwaway project with fake-key providers, a `file://` backend and the fixture configuration, previewed through the real `pulumi-aws` plugin with the policy pack attached, then asserted by `test_preview.py` over the preview JSON. Runs today over the empty component (one test: the preview has `steps` and no custom-resource step); Phase 2 writes the real assertions. Replaces `terraform test` and the module example (LOCKED-3). |
| `scripts/` | `preview_offline.sh` (the preview above, warm-up then a black-holed network) and `gen_interface.py` (the component README's generated table). Phase 1 adds `simulate_roles.sh`, the `iam simulate-principal-policy` table. |
| `docs/` | `LOCKED_DECISIONS.md` and the plan critique today; `BOOTSTRAP.md` arrives in Phase 1, `TEARDOWN.md`, `DEPLOY_CONTRACT.md` and `OIDC_CI_PRIMER.md` in Phase 5. |
| `Makefile` | Every check and every environment verb. `make help` lists them. CI invokes these targets rather than reimplementing them, so a local run and a green check are the same command. |
| `.github/workflows/` | `validate.yml` (ten AWS-free jobs, seven of them required checks). `plan.yml`, `apply.yml` and `e2e.yml` arrive in Phase 4; there is no provider-lock-refresh workflow and there will not be one — `uv.lock` pins the provider and Dependabot proposes the bump. |
| `pyproject.toml`, `uv.lock` | The uv workspace root and its one cross-platform lock. `[tool.uv] required-version` is the uv pin; `uv.lock` is where ruff, mypy, pytest, `pulumi`, `pulumi-aws`, `pulumi-random` and `pulumi-policy` are pinned. |
| `.pulumi.version`, `.python-version` | The Pulumi CLI and interpreter pins; see "Pinned by hand" below. |
| `backend.env.example` | `PULUMI_STATE_BUCKET=` / `AWS_REGION=` for local use; copied to the gitignored `backend.env`, which every AWS `make` verb reads. |

---

## Quickstart, from an empty AWS account

Written for someone who has cloned this and has nothing else. The AWS steps — bootstrap,
pointing an environment at the state bucket, apply, verify, destroy and prove it — are filled in
as Phases 1, 3 and 4 land and the runbooks in `docs/` are written from the real events. What is
here now is the part that needs no account: the toolchain, and a green `make check` on it.

### Before you start

| Tool | Version | Why this floor |
|---|---|---|
| Pulumi CLI | exactly what `.pulumi.version` pins (3.263.0 today) | `make check-pulumi` refuses any other version and prints the install line: `curl -fsSL https://get.pulumi.com \| sh -s -- --version "$(cat .pulumi.version)"`. CI installs the same file's version through `pulumi/actions` with `pulumi-version-file`. |
| uv | exactly what `[tool.uv] required-version` in `pyproject.toml` pins (0.12.15 today) | uv enforces the pin itself on every invocation, so a wrong version fails inside the Pulumi CLI's own `uv sync` too; `make check-uv` is the named failure. Homebrew cannot pin uv (no versioned formula, `uv self update` refused on a brew build), so install with `curl -LsSf https://astral.sh/uv/0.12.15/install.sh \| sh` and make sure `~/.local/bin` precedes `/opt/homebrew/bin` on `PATH` — the installer warns "shadowed by other commands in your PATH" otherwise. |
| Python | what `.python-version` pins (3.13.15 today) | uv installs it; nothing else needs to. |
| AWS CLI | v2 (2.13+ if your profile uses MFA) | Not needed for anything on this page. From Phase 1 on, `aws configure export-credentials` is the only way to hand an MFA-backed session to the provider — the SDK never prompts and cannot read the CLI's cached session, and `AWS_PROFILE` alone fails at provider configuration. |
| `gh` | recent, authenticated as a repository admin | Not needed for anything on this page. From Phase 1 on, the ruleset, the two GitHub Environments and the repository variables are API calls, not clicks. |
| `make` | any | Assumed below. |
| `trivy`, `zizmor` | exactly what the `Makefile` pins (0.74.0 and 1.30.1) | Only for `make scan audit`; each target checks the binary's version, refuses to run on a different one, and the error names the pin and how to install it. ruff, mypy and pytest need no guard of their own: `uv run --locked` is the guard. |

### 0. The toolchain, and a green check on an empty project

```bash
git clone <this repository> && cd pulumi-aws-static-site
curl -LsSf https://astral.sh/uv/0.12.15/install.sh | sh                          # the uv pin; see the table
curl -fsSL https://get.pulumi.com | sh -s -- --version "$(cat .pulumi.version)"   # the CLI pin; or brew — check-pulumi decides
uv sync --locked --all-packages          # the only sync anywhere: --locked refuses a drifted lock,
                                         # --all-packages keeps every workspace member's closure in .venv
make check-pulumi check-uv check-zizmor check-trivy
make fmt-check lint typecheck test docs-check audit
make preview-offline                     # the real pulumi-aws plugin, a file:// backend, no AWS_* variables,
                                         # and the network black-holed after the caches are warm
pre-commit install && pre-commit run --all-files
```

`uv sync --locked --all-packages` is spelled that way for two measured reasons. A plain root
`uv sync` installs the root's closure only and prunes every member's, after which
`mypy --strict envs` cannot import `pulumi_aws`. And `--locked` is the flag that refuses a lock
which no longer matches `pyproject.toml`; `--frozen` is not — it uses the stale lock silently and
prints `warning: Ignoring UV_LOCKED because --frozen was provided`, which also disables the guard
the Makefile exports for the Pulumi CLI's own sync. Neither `--frozen` nor `UV_FROZEN` appears
anywhere in this repository, and `make audit-policy` checks that it stays that way.

### 1. Bootstrap — once, by hand

Filled in Phase 1 (`docs/BOOTSTRAP.md`).

### 2. Point an environment at your state bucket

Filled in Phase 1 (`make init-stacks`). `backend.env.example` already exists — copy it to `backend.env`
and fill in the bucket the bootstrap prints; the AWS `make` verbs refuse to run until it does.

### 3. Apply

Filled in Phase 3 (`make up-stage`).

### 4. Verify

Filled in Phase 3.

### 5. Destroy, and prove it

Filled in Phase 3 (`make destroy-stage`, `make verify-teardown`; `docs/TEARDOWN.md`).

### How long one cycle takes

Filled in Phase 3 and Phase 4 from measured, dated runs, each marked *single run* where it is
one. Nothing is written here until it has been timed.

### What one cycle costs

Filled in Phase 5, derived from the resource set and public AWS pricing rather than from a bill.

---

## Working on it

```bash
make help          # every target, with what it does
make fmt-check lint typecheck test docs-check audit preview-offline   # = make check
```

That line covers five of `validate.yml`'s ten jobs — `python` (`fmt-check lint typecheck`),
`pytest` (`test`), `docs-check`, `zizmor` (`audit`) and `preview-offline` — invoked identically,
so for those a local run and a green check really are the same command. Two more jobs have
targets of their own that `make check` deliberately never runs: `sca` is `make scan`, which needs
the vulnerability database, and `audit-online` is `make audit-online`, which needs a GitHub token
— each is a network call that can change its answer while this repository does not change, and
`check` is kept to what a laptop with no network and no token can prove. Every Python invocation
is `uv run --locked --all-packages`, and every Makefile recipe exports `UV_LOCKED=1` for the sync
the Pulumi CLI runs on its own, `PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS=1` and
`PULUMI_SKIP_UPDATE_CHECK=true`. No recipe runs `pulumi login`: each one that touches a backend
sets `PULUMI_BACKEND_URL` inline, so a stale login or a stray export cannot redirect it.

**Two of `validate.yml`'s required checks have no Makefile target, and cannot honestly be given
one.** `actionlint` runs a pinned Docker image rather than a binary on `$PATH`, and `pr-title`
greps a title that exists only in the pull request event payload, which no local command can
read. So editing a workflow, running the line above and pushing can still produce a red required
check: that is the one place the "same command" property does not reach.

No job in `validate.yml` needs an AWS account, a role or a state bucket, which is what lets seven
of its ten be required status checks: `python`, `pytest`, `preview-offline`, `docs-check`,
`actionlint`, `zizmor` and `pr-title`. The eighth context in the ruleset, `plan-gate`, belongs
to `plan.yml` and is added when that workflow lands in Phase 4 — adding it earlier would leave
every merge waiting on a context nothing reports. Three jobs are not required. `sca` and
`audit-online` each read a database that moves without a commit, so either can go red on a Tuesday
without this repository having changed — they run, they show red beside a merge button that still
works, and the reviewer decides. `cross-root-consistency` compares the three strings the
bootstrap and the environment stack files each declare separately, and exits 0 with a notice
while no environment stack file exists.

`make docs` regenerates the arguments and outputs table inside `packages/static-site/README.md`,
between its `BEGIN_INTERFACE`/`END_INTERFACE` markers. **This file is not generated and carries
no markers.**

`make preview-offline` is the check that replaces `terraform test`: `scripts/preview_offline.sh`
creates a throwaway stack named `test` on a `file://` backend under `tests/offline/.state/`, sets
the fixture configuration, warms the plugin and package caches, then points `HTTP_PROXY` and
`HTTPS_PROXY` at `127.0.0.1:9` and sets `UV_OFFLINE=1` for everything after — so the no-network
property is the script's, not the runner's, and the same command proves the same thing on a
laptop and in CI. Per variant it runs one plain preview with `--policy-pack` (a policy that is
skipped because it read an unknown value prints `can't run policy`, which the script treats as a
failure) and one `--json` preview for `pytest tests/offline`.

## Pinned by hand

Dependabot moves the `uses:` SHAs in `.github/workflows/` and the packages in `uv.lock`
(`.github/dependabot.yml`, weekly, one grouped pull request per ecosystem, seven days' cooldown).
Three pins are outside its reach and move only by a reviewed pull request, on no schedule:

| Pin | Where | Read by |
|---|---|---|
| Pulumi CLI 3.263.0 | `.pulumi.version` | `make check-pulumi`; `pulumi/actions` `pulumi-version-file` in every workflow that runs the CLI |
| Python 3.13.15 | `.python-version` | uv, on every invocation |
| uv 0.12.15 | `[tool.uv] required-version` in `pyproject.toml` | uv itself, on every invocation; `make check-uv`; `astral-sh/setup-uv` through `make -s print-uv-version` |

The Terraform repository ran a monthly `provider-lock-refresh.yml` because a Dependabot-authored
`.terraform.lock.hcl` narrows to one platform. `uv.lock` is cross-platform by construction, so
the provider pin (`pulumi-aws==7.46.0` in `packages/static-site/pyproject.toml`) is a normal
Dependabot bump — minors and patches; a major arrives as a hand-written pull request carrying the
migration guide, which the Dependabot `ignore` rule enforces. A provider bump still goes red at
`plan-gate` once `plan.yml` exists, because a Dependabot pull request gets no repository secrets
and the passphrase is one; it is re-pushed from a branch, as a fork's would be.

**Provider plugin downloads are not checksum-verified here, and the pin is the version alone.**
Read from the CLI source at v3.263.0 on 2026-09-21 (`sdk/go/common/workspace/plugins.go` and
`project.go`, `proto/pulumi/plugin.proto`, `sdk/python/cmd/pulumi-language-python/main.go`,
`sdk/go/common/resource/plugin/plugin.go`): the plugin installer wraps its download in a SHA-256
check only when the plugin's spec carries a `checksums` map keyed `<os>-<arch>`, and otherwise
skips validation. The protocol has a field for that map (`PackageDependency.checksums`), but the
Python language host never fills it — it reports name, version, kind and server from each
package's `pulumi-plugin.json`, whose struct has no checksum key, and the file `pulumi_aws`
7.46.0 ships is `{"resource": true, "name": "aws", "version": "7.46.0"}`. The one place a map
can be written by hand is a `checksums:` key on a `packages:` entry in `Pulumi.yaml`. So what
fixes the `pulumi-resource-aws` binary a preview runs is the version `uv.lock` pins, fetched over
TLS from `github.com/pulumi/pulumi-aws/releases`. That is weaker than the `h1:` hashes in the
Terraform repository's `.terraform.lock.hcl`, and it is recorded here rather than fixed: writing
per-platform checksums into the four `Pulumi.yaml` files would be a second copy of the provider
pin that moves by hand every time Dependabot moves the first.

## Tradeoffs

Filled in Phase 5. The decisions themselves are already fixed and argued in
`docs/LOCKED_DECISIONS.md` — the passphrase provider over KMS, one AWS account isolated by naming,
the offline preview over a mocked unit tier, a hand-written policy pack over a third-party scanner
that cannot read Pulumi Python, and a read-only plan role — and `IMPLEMENTATION_PLAN.md` §8 lists
what the port deliberately does not do.

---

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
