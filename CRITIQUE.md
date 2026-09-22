Critique of `IMPLEMENTATION_PLAN.md` (read-only; nothing written). Where a claim could be run, it was run: Pulumi CLI 3.263.0 installed into the scratchpad, uv 0.12.12 (local brew), throwaway workspaces with `pulumi==3.263.0`, `pulumi-random==4.21.1`, `pulumi-policy==1.21.0`, `mypy==2.3.1`, `ruff==0.16.8`; `pkg/backend/diy/{state,backend}.go` at tag `v3.263.0` read from GitHub.

## Pass 1 — hostile review

### Important

| Id | Section | Finding | Failure scenario |
|---|---|---|---|
| G1 | §2 row 1 (:131), §5 (:481, :491-499), Phase 0 (:257), Phase 2 (:374), §6 (:517 `uv sync --frozen`) | The plan says `uv run --frozen` "refuses a drifted lock". **It does not.** `--frozen` = "use `uv.lock` as-is, don't check it" (the exact `UV_FROZEN` semantics the same sentence rejects); `--locked` is the one that refuses drift. Worse: `UV_LOCKED=1` + `--frozen` prints `warning: Ignoring UV_LOCKED because --frozen was provided` — so the Makefile's exported guard is dead for every tool invocation. Measured 2026-09-21: drifted `pyproject.toml` → `--frozen` ran, `--locked` and `UV_LOCKED=1` errored. | A PR edits `pyproject.toml` (say bumps mypy) without `uv lock`. `python`/`pytest` jobs run the *old* mypy silently; only the Pulumi-invoked sync (which has no `--frozen`) goes red — the plan's own "red job, not a quiet one" argument, violated on the tool half. |
| G2 | §6.2 (:539), §6.4 step 3 (:553), §6.3 by omission | `pulumi -C envs preview --policy-pack policy` — `-C` chdirs first, so `policy` resolves to `envs/policy`. Measured 2026-09-21: `error: failed to load Pulumi policy project located at "policy": open policy/PulumiPolicy.yaml: no such file or directory`. Phase 2's script (`../../policy`) and §5 (`$(CURDIR)/policy`) get it right; §6 does not. | First `plan (stage)` and first `e2e` run are red at policy load. |
| G3 | §3.2 row 7 (:219), §5, §6, LOCKED-5 "backups disabled by env var so no grant is needed" | `backend.go:1466-1481` (v3.263.0): `backupStack` runs **once, after** a successful update, and its error is returned: `saving backup: <err>`. The apply role denies nothing on `.pulumi/backups/` but grants nothing either → `PutObject` AccessDenied. So a workflow that omits `PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS` turns a *successful* `up`/`destroy` (resources changed, checkpoint and history written) into a red run. The plan relies on the variable being present everywhere and nothing enforces it; meanwhile lifecycle rule (c) "expire-backups, defence in depth" prunes a prefix nothing can write. | `apply prod` completes, job fails with `saving backup: AccessDenied`; the operator reads a failed apply that in fact applied. In e2e, the failure lands after `armed=true` and triggers destroy + `orphaned-resources` for a clean environment. |
| G4 | §6.3 vs §5, §6.2, §6.4 | `apply.yml`'s saving preview and constrained `up` carry no `--policy-pack`; `plan.yml`, `e2e.yml` and every Makefile verb do. Unstated asymmetry: the one path that changes `prod` is the one path the posture/interface pack never sees. | A change that passes `preview-offline` on the `test` fixtures but violates a per-stack value (`policy/config/prod.json`) is applied to prod without a policy verdict. |
| G5 | §1 `pyproject.toml` (:19), §5 `typecheck`, §6 `uv sync --frozen` | Root-level `mypy --strict envs` needs `pulumi_aws`/`pulumi_policy` in the shared `.venv`, but a root `uv sync` installs the root closure only and **prunes** members' packages (measured 2026-09-21: after root sync, member deps gone; `uv run` is inexact and installs nothing extra). Virtual members (`package = false`) cannot be root dependencies. Nothing in the plan says how the root venv gets the members' closures. | `python` job: `uv sync` then `uv run mypy --strict envs` → `Cannot find implementation or library stub for module named "pulumi_aws"` on every PR. |

### Minor

| Id | Section | Finding |
|---|---|---|
| G6 | Phase 1 `bootstrap-init` (:292), Phase 2 script, §5 | `file://` backend directory must pre-exist: `error: unable to open state directory ... no such file or directory` (measured). `bootstrap-init` and `preview_offline.sh` need `mkdir -p`. |
| G7 | Phase 2 exit criteria (:395) | "`HOME` unset" — the CLI panics without `$HOME` unless `PULUMI_HOME` is set (measured: `fatal: could not get workspace path ... $HOME set in environment`); uv also needs `UV_CACHE_DIR`. The criterion as written cannot pass. |
| G8 | Phase 5 cloner note (:471) | Only `envs/Pulumi.*.yaml` salts are named; `bootstrap/Pulumi.bootstrap.yaml` also carries an `encryptionsalt` and F10 measured `incorrect passphrase` at `stack init` on a foreign salt. |
| G9 | Phase 1 files vs Phase 3 files (:403) | `init-stacks` (Phase 1) *creates* `envs/Pulumi.stage.yaml`/`prod.yaml` with the salt; Phase 3 lists them as created there. Phase 1's "git status clean apart from the files it lists" is false as written. |
| G10 | §1 `.gitignore` (:111) | Script writes `preview.<variant>.txt/json`; ignore list has `preview.json` only. |
| G11 | §5 `check-uv` (:487), Phase 0 (:259) | Pin is 0.12.15, the dev machine has brew 0.12.12 (plan's own note). Phase 0's `make check-uv` fails on the machine that runs Phase 0 unless the install method (astral installer at the pinned version) is stated. |
| G12 | Phase 0 commands (:254-258) | `uv add --package static-site-policy` before `policy/` exists as a member; `bootstrap/`, `envs/`, `tests/offline/` members never `uv init`-ed; `pytest pyyaml` unpinned while §2 says `pyproject.toml` declares exact versions. |
| G13 | §1 (:72), Phase 3 (:405) | "same nine values" — it is nine config values plus `forceDestroy` (mandatory, no default) = ten keys per file. |
| G14 | §1 `policy/config/{stage,prod}.json`, §2 `cross-root-consistency` | A third copy of `namePrefix`/`project`/owner ids, not covered by the consistency job. Fails closed (the pack rejects the preview), but the job's purpose is to say *why* before the preview does. |

### Verified, no finding
`destroy --preview-only` exists and (50 ms polling) took no observable lock; `preview --refresh --save-plan` → `up --refresh --plan` drives an update on 3.263.0 (§6.3 combination); `awssdk=` query parameter is ignored by 3.263.0 (`v1`/`v2`/`v3` all behave identically — `awssdk=v2` is harmless doc parity); `py.typed` ships in `pulumi`, `pulumi_random`, `pulumi_policy` (V2-1 remains open only for `pulumi_aws` nested arg classes); the CLI's own `uv sync` does **not** prune root dev tools (mypy survived a preview from a member dir), so `typechecker: mypy` is safe; `UV_LOCKED=1` does make that sync fail on drift, and without it `uv.lock` is rewritten — both as the plan says; the first S3 call the backend makes is `GetObject .pulumi/meta.yaml`, matching the plan role's grant; oidc.tf:3116-3134 sums the three inline policies, matching §1's `< 9000` test.

## Pass 2 — self-critique

| Id | Verdict | Fix critique | Corrected recommendation |
|---|---|---|---|
| G1 | **real** (run, not recalled) | root-cause | Replace every `--frozen` with `--locked` (Makefile, workflows, Phase 0/2 commands); keep `UV_LOCKED=1` exported for the CLI path (it cannot take a flag). One semantics, two spellings, stated once in §2's "Pinned by" column. Not bundled with anything else. |
| G2 | **real** (run) | root-cause | `--policy-pack "$GITHUB_WORKSPACE/policy" --policy-pack-config "$GITHUB_WORKSPACE/policy/config/<env>.json"` in §6.2, §6.4 (and §6.3 if G4 is accepted) — the Makefile's `$(CURDIR)` shape. |
| G3 | **real** (source read at the tag; the S3 denial itself not run) | my first instinct — grant `PutObject` on `.pulumi/backups/<project>/<env>/*` — is a patch that reopens LOCKED-5 and quietly re-enables an object class the design removed. Root cause is "an env var is the only guard": make it mechanical. | Add `audit-policy` rule 6: every workflow step that runs `pulumi up\|destroy` sits under a job/workflow `env:` that sets `PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS: "1"` (anchored grep, same shape as rules 4–5). Record in §7 (new R-4) the exact failure mode: red *after* a successful update, `saving backup: AccessDenied`. Either keep lifecycle rule (c) as a stated no-op or drop it — the plan currently calls it defence in depth for objects nothing can write. |
| G4 | **real** as an inconsistency; not a bug | a decision, not a fix | Recommend: add `--policy-pack` to both the saving preview and `up --plan` in §6.3 (cost: one more `uv sync`; `up --plan` accepts the flag). If rejected, state in the `apply.yml` header why prod applies are unpoliced. |
| G5 | **real** (run) | root-cause; the first fix I considered (root depends on all members) is impossible for `package = false` members | Every `uv sync` in CI and the Makefile becomes `uv sync --locked --all-packages` (measured working with virtual members); `uv run --locked` afterwards is inexact and keeps them. State the closure design in §1 next to `pyproject.toml`. Separable from G1 but lands in the same lines. |
| G6 | real (run) | trivial | `mkdir -p` before the first `stack init` in `bootstrap-init` and in `preview_offline.sh`. |
| G7 | real (run) | wording | Rewrite the criterion: "`HOME` unset with `PULUMI_HOME` and `UV_CACHE_DIR` pointed at pre-warmed caches, no `AWS_*` variables, outbound blocked" — or drop the `HOME` clause and keep the property that matters (no credentials, no network after cache). |
| G8–G10, G13 | real | one-line edits | As stated. |
| G11 | real; slightly overstated (it is a Phase 0 speed bump, not a design hole) | wording | Phase 0: "install uv 0.12.15 with `curl -LsSf https://astral.sh/uv/0.12.15/install.sh \| sh`; brew is behind" — or pin to what brew ships and let Dependabot-style bumps move it. Pick one; the exact-match guard is right either way. |
| G12 | real | bookkeeping | Phase 0: `uv init --bare` each of `bootstrap`, `envs`, `policy`, `tests/offline` (with `package = false`) before any `uv add --package`; pin `pytest`/`pyyaml` like ruff/mypy. |
| G14 | real; low value | patch vs. design: the real fix (pack reads the stack's config) is not available to CrossGuard; the third copy stays | Extend `cross-root-consistency` to `policy/config/<env>.json` keys. Optional. |

Nothing in Pass 1 was wrong or overstated to the point of dropping; no LOCKED decision needs reopening (G3's rejected alternative would have).

## Open decisions (yours, not the plan's)

1. **Account: separate or shared with the Terraform repo?** The plan defers it to Phase 1 (`oidcProvider: create|adopt`, prefix `isai2105-pulumi`). Recommended: separate account — it deletes the adopt path, V1-3, V4-5 and the "one `<env>` standing at a time" rule in one go.
2. **Policy pack on `apply.yml`** (G4). Recommended: yes, on preview and `up --plan`.
3. **Backups guard** (G3): mechanical `audit-policy` grep (recommended, keeps LOCKED-5) vs. granting the backups prefix.
4. **uv pin** (G11): 0.12.15 via the astral installer (recommended, matches the locked-decisions header) vs. brew's 0.12.12.

## Corrections to apply to the plan

1. `--frozen` → `--locked` everywhere (§2 :131, §5 :491-499, Phase 0 :257, Phase 2 :374, §6 :517); fix §2's sentence that says `--frozen` refuses drift; note `UV_LOCKED` is ignored under `--frozen`.
2. §6.2 :539, §6.4 :553 (and §6.3): absolute `--policy-pack "$GITHUB_WORKSPACE/policy"` and `--policy-pack-config`.
3. §5 `audit-policy`: rule 6 asserting `PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS: "1"` on every workflow running `pulumi up|destroy`; §7 R-4 naming the `saving backup:` post-success failure; §3.2 row 7 rule (c) marked as a no-op-by-design or removed.
4. §6.3: `--policy-pack`/`--policy-pack-config` on the saving preview and `up --plan` (or a header sentence saying why not).
5. §1 `pyproject.toml` line and §5/§6: `uv sync --locked --all-packages` is the only sync; state that root `uv sync` alone prunes member packages.
6. `bootstrap-init` and `preview_offline.sh`: `mkdir -p` the `file://` directory.
7. Phase 2 exit criteria :395: `PULUMI_HOME`/`UV_CACHE_DIR` when `HOME` is unset.
8. Phase 5 cloner note: also delete `encryptionsalt` from `bootstrap/Pulumi.bootstrap.yaml`.
9. Phase 1 "files created": add `envs/Pulumi.stage.yaml`, `envs/Pulumi.prod.yaml` (salt only, committed); Phase 3 "adds the ten config keys".
10. §1 `.gitignore`: `preview.*.json`, `preview.*.txt`.
11. Phase 0: uv install line at the pinned version; `uv init --bare` for the four project members before `uv add --package`; pin `pytest`/`pyyaml`.
12. §1 :72 and Phase 3 :405: "nine values" → "nine values plus `forceDestroy`".
13. §2 `cross-root-consistency`: include `policy/config/<env>.json`.
14. §7: record today's verifications — `awssdk=` ignored on 3.263.0; `destroy --preview-only` and `up --refresh --plan` exercised; `py.typed` present for `pulumi`/`pulumi_random`/`pulumi_policy`; the CLI's uv sync does not prune root dev tools.

## Verdict

**Not ready as written; ready after corrections 1–5.** Those five are all cheap and mechanical, but each would make a Phase 0/4 gate red on the first run or (G1, G3) leave a guard the plan claims to have not actually guarding. Items 6–14 can be applied in the same edit or discovered during the phases without risk. No locked decision needs to change.
