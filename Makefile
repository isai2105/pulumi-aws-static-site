# Makefile — the single entry point for every check and every environment verb
# in this repository.
#
# Every check is invoked through these targets, locally and in CI alike: the
# workflows under .github/ call these exact targets rather than open-coding the
# commands, so the two invocation paths cannot drift apart. This is the port of
# terraform-aws-static-site's Makefile, verb for verb, and it keeps that file's
# rule that only targets that work today ship here — a target that does not
# work yet is indistinguishable from one that is broken.
#
# The file is in two halves. Everything down to `check` is a check and reaches
# no AWS account; everything after the line marked "The environment targets"
# talks to AWS. All but one of the checks are also credential-free and safe to
# require on a pull request — `audit-online` is the exception, and it argues
# for itself where it sits.
#
# Three things every recipe below relies on, stated once:
#
#   - No recipe runs `pulumi login`, and no recipe exports PULUMI_BACKEND_URL.
#     Each one that touches a backend sets the variable inline, on the command
#     itself, for the backend it means. An exported PULUMI_BACKEND_URL wins over
#     `pulumi login` (measured 2026-09-16 on 3.263.0: with file://A exported,
#     `pulumi login file://B` succeeds and every later command still uses A),
#     so a stray export in the operator's shell could otherwise redirect
#     `init-stacks` into bootstrap/.pulumi-state/ and never touch the bucket.
#     Inline, the recipe's own value is the one the CLI sees.
#   - Every Python invocation is `uv run --locked --all-packages …` and the
#     only install is `uv sync --locked --all-packages`. `--locked` refuses a
#     uv.lock that does not match any pyproject.toml in the workspace; the
#     other flag, `--fro`+`zen`, uses the stale lock silently and prints
#     `warning: Ignoring UV_LOCKED` — so it appears nowhere in this repository,
#     and `audit-policy` rule 8 below keeps it out. `--all-packages` keeps every
#     workspace member's closure in the shared .venv: a plain `uv sync` at the
#     root prunes them (measured 2026-09-21, uv 0.12.12) and `mypy --strict envs`
#     then cannot import pulumi_aws.
#   - Three variables are exported for every recipe (below the pins). The
#     Pulumi CLI runs its own `uv sync --inexact` before every preview and up,
#     takes no lock flag, and re-locks on drift unless UV_LOCKED=1 is in its
#     environment; the DIY backend writes a checkpoint backup after every
#     up/refresh/destroy unless told not to, and the CI apply role grants
#     nothing under .pulumi/backups/ by design (LOCKED-5).

# Bash rather than /bin/sh, with `-e -u -o pipefail`, so a command that fails
# inside a loop or a pipeline fails the target instead of being swallowed.
#
# The flags are stated twice, and the repetition is deliberate. `.SHELLFLAGS`
# is their documented home and it arrived in GNU Make 3.82; macOS ships 3.81 —
# the last GPLv2 release and the newest Apple will distribute — which parses
# the variable and then ignores it. A `SHELL` carrying its own arguments is
# honoured by both, because make builds the shell's argv by splitting this
# variable on whitespace. Drop this line and the checks stop failing on macOS;
# drop the other and they depend on an undocumented reading of `SHELL`. The
# Terraform repository measured both halves (its Makefile:17-46) on 3.81 and
# 4.3; this file was re-checked on the same 3.81.
SHELL := /usr/bin/env bash -euo pipefail
.SHELLFLAGS := -euo pipefail -c

# No built-in rules: nothing here is compiled, and a stray `%.o`-style rule
# firing on a typo'd target name is exactly the silent behaviour this file is
# written against.
MAKEFLAGS += --no-builtin-rules

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# The pins
# ---------------------------------------------------------------------------

# The Pulumi CLI version has exactly one source of truth: .pulumi.version,
# which pulumi/actions reads through `pulumi-version-file` and `check-pulumi`
# below compares against. Reading it here means the Makefile and the file
# cannot disagree about which binary the repository declares.
PULUMI_VERSION := $(shell cat .pulumi.version)
ifeq ($(PULUMI_VERSION),)
$(error .pulumi.version is empty or missing; it is the Pulumi CLI pin and every target here depends on it)
endif

# uv is pinned in pyproject.toml, `[tool.uv] required-version = "==X.Y.Z"`,
# because that is the one place uv itself enforces on every invocation —
# including the `uv sync` the Pulumi CLI runs on its own — and astral-sh/setup-uv
# reads the same key. The sed is anchored to the whole line, so the value has
# to sit alone on its line in that file; a rewrite that moves it makes this
# variable empty, which the check directly below turns into a named failure
# rather than a guard comparing against nothing.
UV_VERSION := $(shell sed -n 's/^required-version = "==\(.*\)"$$/\1/p' pyproject.toml)
ifeq ($(UV_VERSION),)
$(error could not read [tool.uv] required-version = "==X.Y.Z" from pyproject.toml; the value must sit alone on its line)
endif

# Trivy has no version file of its own, so this is the pin and validate.yml
# reads it back through `print-trivy-version`. Its one job here is SCA over
# uv.lock — no IaC scanner reads Pulumi Python (LOCKED-4) — and 0.74.0 is the
# release that was measured parsing uv.lock as type `uv` and suppressing the
# dev group unless told otherwise (see `scan`).
TRIVY_VERSION := 0.74.0

# And zizmor, on the same terms: no version file of its own, so this is the
# pin and validate.yml reads it back through `print-zizmor-version`. What it
# audits is .github/ itself, and its rules are compiled into the binary, so
# this number fixes what counts as a hardening failure and not only who
# reports it.
ZIZMOR_VERSION := 1.30.1

# The runner image, a pin rather than a convention only because `audit-policy`
# below checks every `runs-on:` against it (rule 4 of the policy at the top of
# validate.yml). `ubuntu-latest` has already moved once, from 22.04 to 24.04,
# on a schedule GitHub picks rather than this repository.
RUNNER_IMAGE := ubuntu-24.04

# ---------------------------------------------------------------------------
# Exported for every recipe
# ---------------------------------------------------------------------------

# The DIY backend runs `backupStack` once after every non-dry-run up, refresh
# and destroy, and returns its error as `saving backup: <err>` AFTER the
# checkpoint and history are written (pkg/backend/diy/backend.go:1465-1482 at
# v3.263.0). Under the CI apply role, which grants nothing on .pulumi/backups/
# by design, a successful apply would therefore exit 1. The value must be
# literally `1` or `true`: set-but-empty, `0` and `yes` do not disable backups
# (sdk/go/common/util/env/env.go:338-347, measured 2026-09-21). The workflows
# set the same variable at workflow level, and `audit-policy` rule 7 asserts
# that they do.
export PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS := 1

# The CLI otherwise makes a network call on every invocation to ask whether a
# newer release exists — noise on a laptop and a black-holed request inside
# the offline preview.
export PULUMI_SKIP_UPDATE_CHECK := true

# The guard for the `uv sync --inexact` the Pulumi CLI runs on its own before
# every preview and up (sdk/python/toolchain/uv.go:368-370). It takes no flag;
# without this it rewrites uv.lock on drift, with it the sync fails with
# `error: The lockfile at uv.lock needs to be updated, but UV_LOCKED=1 was
# provided` and leaves the lock untouched. One semantics, two spellings:
# `--locked` on every uv command this file writes, this variable for the one
# uv command it does not.
export UV_LOCKED := 1

.PHONY: help \
	check-pulumi check-uv check-trivy check-zizmor check-aws-cli \
	print-uv-version print-trivy-version print-zizmor-version print-state-bucket \
	fmt fmt-check lint typecheck test docs docs-check scan \
	preview-offline policy \
	audit audit-policy audit-online \
	check \
	bootstrap-init bootstrap-preview bootstrap-up bootstrap-destroy \
	init-stacks \
	preview-stage up-stage destroy-stage cancel-stage \
	preview-prod up-prod destroy-prod cancel-prod \
	verify-teardown

help: ## Show the available targets.
	@echo "Usage: make <target>"
	@echo
	@grep -E '^[a-zA-Z][a-zA-Z_-]*:.*## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN { FS = ":.*## " } { printf "  %-20s %s\n", $$1, $$2 }'
	@echo
	@echo "pulumi pinned by .pulumi.version:                   $(PULUMI_VERSION)"
	@echo "uv pinned by pyproject.toml [tool.uv] required-version: $(UV_VERSION)"
	@echo "trivy pinned by this Makefile:                      $(TRIVY_VERSION)"
	@echo "zizmor pinned by this Makefile:                     $(ZIZMOR_VERSION)"
	@echo "runner image pinned by this Makefile:               $(RUNNER_IMAGE)"
	@echo "ruff, mypy, pytest, pulumi-aws, pulumi-policy:      pinned by uv.lock"

# ---------------------------------------------------------------------------
# Toolchain guards
# ---------------------------------------------------------------------------

# Internal guard, deliberately absent from `help`: not a check in its own
# right, but a prerequisite of every target that shells out to pulumi. Running
# a preview against a CLI the repository does not declare produces a result
# that does not describe the repository, so refuse rather than proceed.
# `pulumi version` prints `vX.Y.Z` alone on a line; the `v` is stripped so the
# comparison is against the bare number .pulumi.version holds.
check-pulumi:
	@if ! command -v pulumi >/dev/null 2>&1; then \
		echo "make: pulumi was not found on PATH." >&2; \
		echo "      this repository pins pulumi $(PULUMI_VERSION) in .pulumi.version." >&2; \
		echo "      install it with:  curl -fsSL https://get.pulumi.com | sh -s -- --version $(PULUMI_VERSION)" >&2; \
		echo "      then put ~/.pulumi/bin on PATH:  export PATH=\"\$$PATH:\$$HOME/.pulumi/bin\"" >&2; \
		exit 1; \
	fi; \
	out="$$(pulumi version)"; \
	actual="$${out%%$$'\n'*}"; \
	actual="$${actual#v}"; \
	if [ "$$actual" != "$(PULUMI_VERSION)" ]; then \
		echo "make: pulumi version mismatch." >&2; \
		echo "      expected $(PULUMI_VERSION)  (pinned in .pulumi.version)" >&2; \
		echo "      found    $$actual  ($$(command -v pulumi))" >&2; \
		echo "      fix with:  curl -fsSL https://get.pulumi.com | sh -s -- --version $(PULUMI_VERSION)" >&2; \
		echo "      (a brew-installed pulumi earlier on PATH shadows ~/.pulumi/bin; brew cannot pin it)" >&2; \
		exit 1; \
	fi

# The same guard for uv, and the one guard here that is redundant on purpose.
# uv enforces `[tool.uv] required-version` itself on every command, so a wrong
# version already fails — inside the Pulumi CLI's own `uv sync`, with uv's
# message and no hint of where the pin lives. This target is the failure that
# names the fix. `uv self version --short` prints the bare number; the
# `uv --version` line carries a build hash and date that are not part of the
# pin, so the fallback strips everything after the first space.
#
# brew cannot install a pinned uv (no versioned formula, and `uv self update`
# is refused on a brew build), so the astral installer is the reproducible
# path. It puts the binary in ~/.local/bin, which must precede /opt/homebrew/bin
# on PATH — the installer itself warns "shadowed by other commands in your
# PATH" otherwise — or the brew one is what `uv` resolves to.
check-uv:
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "make: uv was not found on PATH." >&2; \
		echo "      this repository pins uv $(UV_VERSION) in pyproject.toml ([tool.uv] required-version)." >&2; \
		echo "      install it with:  curl -LsSf https://astral.sh/uv/$(UV_VERSION)/install.sh | sh" >&2; \
		echo "      then make sure ~/.local/bin precedes /opt/homebrew/bin on PATH." >&2; \
		exit 1; \
	fi; \
	if ! out="$$(uv self version --short 2>/dev/null)"; then \
		out="$$(uv --version)"; \
		out="$${out#uv }"; \
	fi; \
	actual="$${out%% *}"; \
	if [ "$$actual" != "$(UV_VERSION)" ]; then \
		echo "make: uv version mismatch." >&2; \
		echo "      expected $(UV_VERSION)  (pinned in pyproject.toml, [tool.uv] required-version)" >&2; \
		echo "      found    $$actual  ($$(command -v uv))" >&2; \
		echo "      fix with:  curl -LsSf https://astral.sh/uv/$(UV_VERSION)/install.sh | sh" >&2; \
		echo "      A brew-installed uv earlier on PATH shadows the pinned one: brew has no" >&2; \
		echo "      versioned formula and refuses \`uv self update\`, so either put ~/.local/bin" >&2; \
		echo "      before /opt/homebrew/bin on PATH or \`brew uninstall uv\`." >&2; \
		exit 1; \
	fi

# And the same guard for trivy, where an unpinned binary silently changes what
# a vulnerability scan considers a finding.
check-trivy:
	@if ! command -v trivy >/dev/null 2>&1; then \
		echo "make: trivy was not found on PATH." >&2; \
		echo "      this repository pins trivy $(TRIVY_VERSION)." >&2; \
		echo "      install it with:  brew install trivy" >&2; \
		echo "                   or:  from https://github.com/aquasecurity/trivy/releases/tag/v$(TRIVY_VERSION)" >&2; \
		exit 1; \
	fi; \
	out="$$(trivy --version)"; \
	actual="$${out%%$$'\n'*}"; \
	actual="$${actual#Version: }"; \
	if [ "$$actual" != "$(TRIVY_VERSION)" ]; then \
		echo "make: trivy version mismatch." >&2; \
		echo "      expected $(TRIVY_VERSION)  (pinned in the Makefile)" >&2; \
		echo "      found    $$actual  ($$(command -v trivy))" >&2; \
		echo "      trivy has no version manager, so pick the matching build:" >&2; \
		echo "        brew upgrade trivy   (when the formula is at $(TRIVY_VERSION))" >&2; \
		echo "        https://github.com/aquasecurity/trivy/releases/tag/v$(TRIVY_VERSION)" >&2; \
		exit 1; \
	fi

# And for zizmor: the audit rules are compiled into the binary, so an older
# zizmor passes a workflow a newer one rejects — and what it would have
# rejected is the hardening `audit` exists to hold in place.
check-zizmor:
	@if ! command -v zizmor >/dev/null 2>&1; then \
		echo "make: zizmor was not found on PATH." >&2; \
		echo "      this repository pins zizmor $(ZIZMOR_VERSION)." >&2; \
		echo "      install it with:  brew install zizmor" >&2; \
		echo "                   or:  from https://github.com/zizmorcore/zizmor/releases/tag/v$(ZIZMOR_VERSION)" >&2; \
		exit 1; \
	fi; \
	out="$$(zizmor --version)"; \
	actual="$${out#zizmor }"; \
	if [ "$$actual" != "$(ZIZMOR_VERSION)" ]; then \
		echo "make: zizmor version mismatch." >&2; \
		echo "      expected $(ZIZMOR_VERSION)  (pinned in the Makefile)" >&2; \
		echo "      found    $$actual  ($$(command -v zizmor))" >&2; \
		echo "      zizmor has no version manager, so pick the matching build:" >&2; \
		echo "        brew upgrade zizmor   (when the formula is at $(ZIZMOR_VERSION))" >&2; \
		echo "        https://github.com/zizmorcore/zizmor/releases/tag/v$(ZIZMOR_VERSION)" >&2; \
		exit 1; \
	fi

# And the AWS CLI, which `verify-teardown` is built on. It is the only guard
# here that does not pin an exact version, and that deviation argues for
# itself: the CLI carries a query to an API versioned by AWS rather than by
# the binary, so pinning it would fix the transport and not the answer, while
# making the sweep refuse to run on every workstation whose awscli formula has
# moved. The major version is asserted because that is the one boundary at
# which the answer does change — v1 and v2 differ in output defaults, in
# pagination and in how `--query` is applied to a paginated result, and every
# query in `verify-teardown` is written against v2.
check-aws-cli:
	@if ! command -v aws >/dev/null 2>&1; then \
		echo "make: the AWS CLI was not found on PATH." >&2; \
		echo "      verify-teardown asks AWS what is standing in the account, so" >&2; \
		echo "      there is nothing it can do without one. Version 2 is required." >&2; \
		echo "      install it with:  brew install awscli" >&2; \
		echo "                   or:  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" >&2; \
		exit 1; \
	fi; \
	out="$$(aws --version 2>&1)"; \
	actual="$${out#aws-cli/}"; \
	actual="$${actual%% *}"; \
	case "$$actual" in \
	2.*) ;; \
	*) \
		echo "make: the AWS CLI on PATH is not version 2." >&2; \
		echo "      expected 2.x  (the queries in verify-teardown are written against v2)" >&2; \
		echo "      found    $$actual  ($$(command -v aws))" >&2; \
		echo "      upgrade with:  brew install awscli" >&2; \
		exit 1; \
		;; \
	esac

# The one place validate.yml can ask which uv this repository pins, so the
# setup-uv step and the guard cannot disagree.
print-uv-version:
	@echo "$(UV_VERSION)"

# The same for trivy: validate.yml installs whatever this prints, so the
# installed binary and the version check-trivy demands are the same number.
print-trivy-version:
	@echo "$(TRIVY_VERSION)"

# And for zizmor.
print-zizmor-version:
	@echo "$(ZIZMOR_VERSION)"

# The state bucket carries a per-account random suffix, so its name is not in
# the tree: it comes from PULUMI_STATE_BUCKET when that is exported — the
# repository variable the workflows compose the backend URL from — and
# otherwise from the gitignored backend.env. Finding neither is an error that
# names both, never an empty line a caller could paste into an S3 command.
print-state-bucket:
	@bucket="$${PULUMI_STATE_BUCKET:-}"; \
	if [ -z "$$bucket" ] && [ -f backend.env ]; then \
		bucket="$$(. ./backend.env; printf '%s' "$${PULUMI_STATE_BUCKET:-}")"; \
	fi; \
	if [ -z "$$bucket" ]; then \
		echo "make: no state bucket name is available." >&2; \
		echo "      Neither PULUMI_STATE_BUCKET is exported nor does backend.env declare it." >&2; \
		echo "      The bucket carries a per-account random suffix, so it is not in the tree:" >&2; \
		echo "" >&2; \
		echo "        cp backend.env.example backend.env" >&2; \
		echo "        PULUMI_BACKEND_URL=file://$(CURDIR)/bootstrap/.pulumi-state pulumi -C bootstrap stack output --stack bootstrap" >&2; \
		echo "" >&2; \
		echo "      and fill PULUMI_STATE_BUCKET and AWS_REGION from that output." >&2; \
		exit 1; \
	fi; \
	echo "$$bucket"

# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

# Formatting is purely textual, so it runs from the repository root over
# every workspace member at once. ruff's version is uv.lock's, and
# `--locked` is what makes that a pin rather than a suggestion.
fmt: check-uv ## Rewrite Python files into canonical format, in place (ruff format).
	uv run --locked --all-packages ruff format .

fmt-check: check-uv ## Fail if any Python file is not canonically formatted.
	uv run --locked --all-packages ruff format --check --diff .

# The lint replaces tflint: pyflakes/pycodestyle for the code, pep8-naming
# (`N`) for the snake_case rule .tflint.hcl used to hold, import order and
# the modern-syntax rules for the pinned interpreter. Rule selection lives in
# pyproject.toml, where the reviewer of a rule change can see it.
lint: check-uv ## Lint every Python file with ruff.
	uv run --locked --all-packages ruff check .

# Five invocations rather than one, and the split is measured, not stylistic.
# Four of the workspace members are Pulumi programs, each with a `__main__.py`
# at its root; mypy given all four in one run stops with
# `Duplicate module named "__main__"` (measured 2026-09-16). So each program
# is checked from its own directory, and the fifth run covers everything
# that is not a program: the component package, the unit tests and scripts/.
# `--strict` is in pyproject.toml too; it is repeated on the command line so
# a reader of a CI log sees what was asked for without opening the config.
#
# This is half of what `terraform validate` used to do (the types); the other
# half — the provider's Check on every resource — is `preview-offline`.
typecheck: check-uv ## Type-check every Pulumi program and the component package with mypy --strict.
	@for dir in bootstrap envs policy tests/offline; do \
		echo "==> mypy --strict $$dir"; \
		uv run --locked --all-packages mypy --strict "$$dir"; \
	done
	@echo "==> mypy --strict packages tests/unit scripts"
	@uv run --locked --all-packages mypy --strict packages tests/unit scripts

# The unit tier: pure functions with no Pulumi runtime — validation, naming,
# the policy-document builders, the bootstrap's config rules. The offline
# preview's pytest is not here; it runs inside `preview-offline`, which is the
# only place the preview JSON it reads exists.
test: check-uv ## Run the unit tests (pytest tests/unit).
	uv run --locked --all-packages pytest tests/unit

# The component's interface table in packages/static-site/README.md is
# generated from StaticSiteArgs and the component's outputs by
# scripts/gen_interface.py, between the BEGIN_INTERFACE/END_INTERFACE markers
# — the terraform-docs block, ported. `docs` rewrites it; `docs-check` fails
# instead of rewriting, which is the form CI runs: a job that regenerated and
# pushed would be a gate that reports green on work it did itself.
#
# `python scripts/…` rather than handing uv the script path: a script carrying
# inline `# /// script` metadata would make `uv run <path>` build it a
# separate environment from a separate lock, silently outside the one
# `--locked` guards. Naming the interpreter keeps it in the workspace's.
docs: check-uv ## Regenerate the interface table in packages/static-site/README.md.
	uv run --locked --all-packages python scripts/gen_interface.py

docs-check: check-uv ## Fail if the generated interface table is out of date.
	@if ! uv run --locked --all-packages python scripts/gen_interface.py --check; then \
		echo "" >&2; \
		echo "make: packages/static-site/README.md no longer matches StaticSiteArgs." >&2; \
		echo "      The block between the BEGIN_INTERFACE and END_INTERFACE markers is" >&2; \
		echo "      generated, so it is regenerated rather than edited:" >&2; \
		echo "" >&2; \
		echo "        make docs" >&2; \
		echo "" >&2; \
		echo "      To change what it says, edit the field's docstring in args.py and" >&2; \
		echo "      regenerate." >&2; \
		exit 1; \
	fi

# Dependency vulnerabilities over uv.lock — the one job trivy can still do
# here, no IaC scanner reading Pulumi Python (LOCKED-4; the misconfiguration
# rules it used to bring live in policy/posture/ and run inside
# `preview-offline`).
#
# `--include-dev-deps` is not optional: trivy 0.74.0 parses uv.lock as type
# `uv` and drops the `dev` group unless told otherwise (measured 2026-09-16),
# and that group — ruff, mypy, pytest, pyyaml — runs on every CI runner from
# the same lock. `--severity` is spelled out in full so an upstream change to
# the default cannot quietly raise the bar for what counts as a failure.
#
# This is the one check in this file that makes a network call and can change
# its answer without a commit: it needs the vulnerability database, which
# moves on its own schedule. That is the argument that keeps `audit-online`
# out of the required set (below), and it lands in the same place here — the
# `sca` job runs this on every pull request and is red beside a working merge
# button, never `continue-on-error` and never required.
scan: check-trivy ## Scan uv.lock for vulnerable dependencies with trivy (advisory; needs the vulnerability DB).
	trivy fs --scanners vuln --include-dev-deps --exit-code 1 \
		--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL uv.lock

# The credential-free preview (LOCKED-3): the replacement for `terraform test`
# and, together with the policy pack it loads, for `terraform validate` and
# the trivy misconfiguration rules. The whole of it is scripts/preview_offline.sh
# — a file:// backend set through PULUMI_BACKEND_URL on a directory recreated
# empty every run, a throwaway stack `test` carrying the tftest fixture values,
# a cache warm-up (the only two lines that may reach the network), then every
# outbound HTTP request black-holed through a proxy on 127.0.0.1:9 and
# UV_OFFLINE=1 for everything after. Per variant: one plain preview with the
# policy pack whose output is grepped for `can't run policy` (a skipped policy
# is a failure), then one --json preview for pytest tests/offline. The script
# argues each line where it sits.
preview-offline: check-pulumi check-uv ## Preview the component offline with the policy pack and run pytest tests/offline.
	scripts/preview_offline.sh

# The fast inner loop while writing a policy: the same script, the `default`
# variant only, stopping after the plain policy preview — no JSON run and no
# pytest. A subset of `preview-offline`, never a gate on its own.
policy: check-pulumi check-uv ## Run only the policy pack over the default offline variant (fast inner loop).
	PREVIEW_OFFLINE_VARIANTS=default PREVIEW_OFFLINE_POLICY_ONLY=1 scripts/preview_offline.sh

# The workflows themselves, audited the way the code is checked: static
# analysis against rules compiled into a pinned binary, with everything it
# finds failing the build.
#
# It does not overlap `actionlint`, which runs in validate.yml and in
# .pre-commit-config.yaml and deliberately not from here, there being no
# invocation for a target to own. actionlint asks whether a workflow is
# *valid*; zizmor asks whether it is *safe* — an unpinned `uses:`, a
# `permissions:` block wider than the job needs, a checkout leaving a
# credential on disk. Neither has anything to say about the other's findings.
#
# `.` rather than a list of files: zizmor collects workflows, composite
# actions, the Dependabot configuration and .pre-commit-config.yaml from
# wherever they are, honouring .gitignore, so this keeps covering files added
# after it was written.
#
# `--offline` equalises the local run and the CI run, downward and on purpose:
# zizmor selects its mode from whether a GitHub token is in the environment,
# and a CI runner is always the shell that has none. What it surrenders —
# `impostor-commit`, `ref-confusion`, `known-vulnerable-actions` and, the one
# that costs something, `ref-version-mismatch` (the `# vX.Y.Z` comment beside
# each SHA pin, which Dependabot now rewrites weekly) — is what `audit-online`
# below runs, on every pull request, as a non-required job.
#
# `--strict-collection` changes the contract rather than the findings: without
# it, an input zizmor cannot validate against its schema is dropped with a
# WARN line and the run carries on, which is exit 0 on a file nobody read. A
# mistyped `package-ecosystem` in dependabot.yml is the measured case (the
# Terraform repository's Makefile:798-819): a Dependabot configuration that
# does nothing, reported green identically to one that works.
#
# `audit-policy` runs first, as a prerequisite rather than a second command
# here, because a red target should name what broke.
audit: check-zizmor audit-policy ## Audit the GitHub Actions workflows: zizmor, and the rules zizmor has no rule for.
	zizmor --offline --strict-collection .

# The rules of the policy at the top of validate.yml that no linter here has a
# rule for. Measured rather than assumed: at zizmor 1.30.1 and actionlint 1.7.12
# a workflow carrying a floating `runs-on`, a job with no `timeout-minutes`, a
# missing backups variable, a stale-lock flag or an un-policed `pulumi up`
# produces zero findings from either tool.
#
# Greps rather than a YAML parse, and the objection to answer is that these
# files are full of prose *about* the keys they check. One character separates
# the two: a comment begins with `#`, so anchoring to the start of the line —
# `^    runs-on:` for a job key, `^  KEY:` for a workflow-level env key,
# `^[^#]*` for "before any comment marker" — tells them apart completely.
#
# What makes a grep acceptable is not that it is right today, it is that it
# refuses when its assumption stops holding. Every rule below compares an
# anchored count against an unanchored one and fails the file if they differ,
# rather than reporting a pass built on an indentation the file no longer
# uses. A structural check that quietly stops applying is the failure this
# target exists to prevent, arriving from inside it.
#
#   rule 4  every `runs-on:` is `$(RUNNER_IMAGE)`; the key sits at four
#           spaces, where a job's own keys are.
#   rule 5  as many `timeout-minutes:` at four spaces as `runs-on:` — counted
#           rather than attributed, so the message names the file and how many
#           it is short, not the job.
#   rule 7  every workflow with a non-comment occurrence of the word `pulumi`
#           — `uses: pulumi/actions@`, a `pulumi` command in a `run:` block,
#           `pulumi-version-file:`, anything — mentions
#           DISABLE_CHECKPOINT_BACKUPS exactly once on a non-comment line, and
#           that once is the workflow-level `env:` key
#           PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS with the literal
#           value "1" at two spaces. The word rather than a spelling of the
#           invocation is the trigger on purpose: a `defaults.run.working-
#           directory: envs` job with a bare `pulumi up` line ran the CLI
#           without ever writing `pulumi -C `, and over-triggering costs only
#           an env line a workflow that mentions the CLI should carry anyway.
#           The unanchored count is on the bare suffix so that the CLI's
#           alternative name PULUMI_DISABLE_CHECKPOINT_BACKUPS
#           (`env.Alternative("DISABLE_CHECKPOINT_BACKUPS")`,
#           sdk/go/common/env/env.go:203-205 at v3.263.0) counts as the second
#           mention it is; a job- or step-level override does the same. What
#           it prevents: a successful `pulumi up|refresh|destroy` under the
#           apply role that writes the checkpoint and history and then exits
#           1 with `error: saving backup: … AccessDenied` (§7 R-4).
#   rule 8  no `--fro`+`zen` and no `UV_FRO`+`ZEN` on a non-comment line of the
#           Makefile, the workflows, scripts/*.sh or .pre-commit-config.yaml —
#           either one uses a stale uv.lock silently and prints
#           `warning: Ignoring UV_LOCKED`, disabling the exported guard. The
#           pattern is spelled with brackets so the rule's own recipe line,
#           which is not a comment and sits in a file the rule reads, does not
#           match itself; the message spells it with a quote break for the same
#           reason.
#   rule 9  every non-comment line under .github/workflows/ that runs a
#           preview or an up — the word `pulumi` followed later on the line by
#           the bare word `preview` or `up`, whatever flags sit between, or a
#           pulumi/actions `command: preview|up` input — carries both
#           `-C envs` and `--policy-pack "$GITHUB_WORKSPACE/policy"` on that
#           same line. Two spellings are therefore mandatory: `-C envs` (never
#           `--cwd`, never a `cd`, never a working directory) so the stack the
#           line touches is readable off the line, and the absolute pack path
#           (`-C` chdirs before the flag is resolved). The `command:` form can
#           never satisfy it, which is the point: pulumi/actions is install-only
#           in this repository (§6) and its `work-dir`/`policyPacks` inputs
#           would move both facts off the line the grep reads. One `up` path
#           without the pack is the asymmetry this closes. `destroy` takes no
#           such flag on 3.263.0 and is not a subject. Backslash continuations
#           are joined before the grep (portable sed: `:a; /\\$/N;
#           s/\\\n[[:space:]]*/ /; ta`), so `pulumi -C envs \` + `up …` on
#           the next line is one subject, numbered by its first physical line;
#           the group uses `[^#]*` rather than `.*` so a trailing `# clean up`
#           comment on a `stack output` line is not a verb. Two limits, stated:
#           a `run:` written as a YAML double-quoted string escapes the pack's
#           quotes (`\"$GITHUB_WORKSPACE/policy\"`) and fails the fixed-string
#           match — write `run:` as a plain or block scalar; and a verb held in
#           a shell variable (`verb=up; pulumi -C envs "$verb"`) is not seen,
#           which is deliberate evasion rather than an accident and is left to
#           review, because variables as arguments (`--stack "$ENV"`) must stay
#           legal.
#   cwd     no `working-directory:` and no `work-dir:` key and no `--cwd` on
#           any non-comment line under .github/workflows/. Not a numbered rule
#           of the policy but the guard that keeps rule 9 honest: with a
#           working directory set, `pulumi up` from a step reads as a bare
#           `up` with no `-C envs` to grep for, and rule 9's whole claim is
#           that the stack and the pack are readable off the line. Every
#           `run:` block in this repository runs from the checkout root and
#           says where it means with `-C`.
#
# The Makefile is a subject of rule 8 only, so the recipe lines below that
# spell the rule 7, 9 and cwd patterns cannot trip those rules; validate.yml's
# policy header describes them in `#` comments, which every rule excludes.
# The rules that are not here: 1, 3 and 6 are zizmor's (`unpinned-uses`,
# `excessive-permissions`, `artipacked`); rule 2 (`using: node24` at each
# pinned SHA) has no tool and says so where it is written.
#
# An empty .github/workflows/ is a refusal, not a pass: this target discovers
# its own work, and an empty result would be a pass that asserted nothing.
audit-policy: ## Check the hardening rules no linter here covers (rules 4, 5, 7, 8, 9 of validate.yml's policy, plus the no-working-directory guard).
	@files="$$(find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | sort || true)"; \
	if [ -z "$$files" ]; then \
		echo "make: no workflow files found under .github/workflows/." >&2; \
		echo "      This target discovers its own work, so an empty result is a pass" >&2; \
		echo "      that asserted nothing rather than a repository with nothing to say." >&2; \
		exit 1; \
	fi; \
	status=0; \
	while IFS= read -r file; do \
		loose="$$(grep -cE '^[[:space:]]*runs-on:' "$$file" || true)"; \
		strict="$$(grep -cE '^    runs-on:' "$$file" || true)"; \
		if [ "$$loose" != "$$strict" ]; then \
			echo "make: $$file indents a runs-on key somewhere other than four spaces." >&2; \
			echo "      Four is where a job's own keys sit, and it is the assumption rules 4" >&2; \
			echo "      and 5 are built on. Refusing rather than reporting a pass this target" >&2; \
			echo "      cannot stand behind." >&2; \
			status=1; \
			continue; \
		fi; \
		bad="$$(awk -v want="runs-on: $(RUNNER_IMAGE)" '/^    runs-on:/ { value = $$0; sub(/^ +/, "", value); if (value != want) printf "        %s:%d: %s\n", FILENAME, FNR, value }' "$$file")"; \
		if [ -n "$$bad" ]; then \
			echo "make: $$file names a runner this repository does not pin." >&2; \
			printf '%s\n' "$$bad" >&2; \
			echo "      Rule 4 at the top of validate.yml: the runner image is pinned like the" >&2; \
			echo "      rest of the toolchain, at $(RUNNER_IMAGE), because a floating label" >&2; \
			echo "      moves underneath a repository that pins its CLI to a patch. A job that" >&2; \
			echo "      genuinely needs another image changes RUNNER_IMAGE in this Makefile." >&2; \
			status=1; \
		fi; \
		timeouts="$$(grep -cE '^    timeout-minutes:' "$$file" || true)"; \
		if [ "$$strict" != "$$timeouts" ]; then \
			echo "make: $$file declares $$strict runner(s) and $$timeouts job timeout(s)." >&2; \
			echo "      Rule 5 at the top of validate.yml: every job carries a timeout-minutes," >&2; \
			echo "      because GitHub's default is six hours and the failure shape here is a" >&2; \
			echo "      hang rather than a crash. The counts are compared rather than the jobs" >&2; \
			echo "      matched up, so this names the file and not the job." >&2; \
			status=1; \
		fi; \
		runs_cli="$$(grep -cE '^[^#]*(^|[^[:alnum:]_])pulumi([^[:alnum:]_]|$$)' "$$file" || true)"; \
		if [ "$$runs_cli" -gt 0 ]; then \
			mentions="$$(grep -cE '^[^#]*DISABLE_CHECKPOINT_BACKUPS' "$$file" || true)"; \
			anchored="$$(grep -cE '^  PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS: "1"$$' "$$file" || true)"; \
			if [ "$$mentions" != 1 ] || [ "$$anchored" != 1 ]; then \
				echo "make: $$file mentions pulumi on $$runs_cli non-comment line(s) and does not set the backups variable exactly once at workflow level." >&2; \
				echo "      non-comment lines mentioning DISABLE_CHECKPOINT_BACKUPS (either spelling): $$mentions  (want 1)" >&2; \
				echo "      workflow-level  \`  PULUMI_DIY_BACKEND_DISABLE_CHECKPOINT_BACKUPS: \"1\"\`: $$anchored  (want 1)" >&2; \
				echo "      Rule 7 at the top of validate.yml: the DIY backend writes a checkpoint" >&2; \
				echo "      backup after every up, refresh and destroy, and the apply role grants" >&2; \
				echo "      nothing under .pulumi/backups/ by design — without this variable a" >&2; \
				echo "      successful apply writes its checkpoint and history and then exits 1" >&2; \
				echo "      with 'error: saving backup: … AccessDenied'. Set it once, at the" >&2; \
				echo "      workflow's own env: (two-space indent), value \"1\", nowhere else —" >&2; \
				echo "      not per job, not per step, and not under the alternative name" >&2; \
				echo "      PULUMI_DISABLE_CHECKPOINT_BACKUPS, which counts as a second mention." >&2; \
				status=1; \
			fi; \
		fi; \
		joined="$$(sed -e :a -e '/\\$$/N; s/\\\n[[:space:]]*/ /; ta' "$$file")"; \
		verbs="$$(printf '%s\n' "$$joined" | grep -nE '^[^#]*((^|[^[:alnum:]_])pulumi([[:space:]]+|[^[:alnum:]_][^#]*[[:space:]])(preview|up)([[:space:]]|$$)|(^|[[:space:]])command:[[:space:]]*["'"'"']?(preview|up)["'"'"']?([[:space:]]|$$))' || true)"; \
		unpoliced="$$(printf '%s\n' "$$verbs" | grep -vE -- '-C envs([[:space:]]|$$)' | grep . || true)"; \
		unpoliced="$$(printf '%s\n' "$$unpoliced"; printf '%s\n' "$$verbs" | grep -vF -- '--policy-pack "$$GITHUB_WORKSPACE/policy"' | grep . || true)"; \
		unpoliced="$$(printf '%s\n' "$$unpoliced" | grep . | sort -t: -k1,1n -u || true)"; \
		if [ -n "$$unpoliced" ]; then \
			echo "make: $$file runs a pulumi preview or up that does not say -C envs and the policy pack on the same line:" >&2; \
			printf '%s\n' "$$unpoliced" | sed 's/^/        /' >&2; \
			echo "      Rule 9 at the top of validate.yml: every workflow line that runs a preview" >&2; \
			echo "      or an up — the word pulumi followed by the bare word preview or up, or a" >&2; \
			echo "      pulumi/actions command: input — carries both \`-C envs\` and" >&2; \
			echo "      --policy-pack \"\$$GITHUB_WORKSPACE/policy\" on that line. The spelling is" >&2; \
			echo "      the rule: not --cwd, not a cd, not a working directory, not the action's" >&2; \
			echo "      work-dir/policyPacks inputs (pulumi/actions is install-only here), so the" >&2; \
			echo "      stack and the pack are readable off the line. The path is absolute" >&2; \
			echo "      because -C chdirs before the flag is resolved; the pack reads the stack's" >&2; \
			echo "      own static-site:* config. destroy takes no such flag and is not checked." >&2; \
			echo "      A backslash-continued command is read as one line (numbered by its first" >&2; \
			echo "      physical line). Write run: as a plain or block scalar: a YAML double-quoted" >&2; \
			echo "      string escapes the pack's quotes and fails the match. A line that only" >&2; \
			echo "      mentions pulumi beside the word up or preview (an echo, say) trips this too:" >&2; \
			echo "      reword it." >&2; \
			status=1; \
		fi; \
		cwd="$$(printf '%s\n' "$$joined" | grep -nE '^[^#]*((^|[[:space:]])(working-directory|work-dir):|--cwd([[:space:]=]|$$))' || true)"; \
		if [ -n "$$cwd" ]; then \
			echo "make: $$file changes the working directory, which rule 9 cannot see through:" >&2; \
			printf '%s\n' "$$cwd" | sed 's/^/        /' >&2; \
			echo "      No working-directory: or work-dir: key and no --cwd under .github/workflows/." >&2; \
			echo "      With one set, a bare \`pulumi up\` in a step runs against envs/ with no" >&2; \
			echo "      -C envs for rule 9 to read, and the stack the line touches is no longer" >&2; \
			echo "      readable off the line. Every run: block runs from the checkout root and" >&2; \
			echo "      says where it means with -C." >&2; \
			status=1; \
		fi; \
	done <<< "$$files"; \
	shopt -s nullglob; \
	subjects=(); \
	for f in Makefile .github/workflows/*.yml .github/workflows/*.yaml scripts/*.sh .pre-commit-config.yaml; do \
		[ -f "$$f" ] && subjects+=("$$f"); \
	done; \
	stale="$$(grep -nE '^[^#]*(--fro[z]en|UV_FRO[Z]EN)' "$${subjects[@]}" || true)"; \
	if [ -n "$$stale" ]; then \
		echo "make: a stale-lock spelling is in use:" >&2; \
		printf '%s\n' "$$stale" | sed 's/^/        /' >&2; \
		printf '%s\n' '      Rule 8 at the top of validate.yml: neither --fro''zen nor UV_FRO''ZEN' >&2; \
		echo "      may appear on a non-comment line of the Makefile, the workflows," >&2; \
		echo "      scripts/*.sh or .pre-commit-config.yaml. Either one uses a stale uv.lock" >&2; \
		echo "      silently and prints 'warning: Ignoring UV_LOCKED', disabling the guard" >&2; \
		echo "      this Makefile exports. Use --locked on uv commands and UV_LOCKED=1 for" >&2; \
		echo "      the sync the Pulumi CLI runs on its own." >&2; \
		status=1; \
	fi; \
	exit $$status

# The audits `audit` cannot run, and deliberately never a gate: the same tool
# with a network. `known-vulnerable-actions` reports on an advisory database
# that moves without a commit, so a required check running it could take
# `main` red on a Tuesday with nothing here having changed. The `audit-online`
# job in validate.yml runs this on every pull request as a non-required job,
# which covers the moment `ref-version-mismatch` exists for — a `uses:` pin
# moving, which Dependabot now does weekly.
#
# The token is asserted, because a missing one cannot be constructed away:
# finding none, zizmor falls back to offline, says so once at WARN level, and
# reports "No findings" having run none of the audits this target exists for.
# The mode is constructed, because it can be: `ZIZMOR_OFFLINE` and
# `ZIZMOR_NO_ONLINE_AUDITS` each force offline from the environment, so both
# are unset for the child rather than tested for.
audit-online: check-zizmor ## Run zizmor's online audits too. Needs a GitHub token; never a gate.
	@if [ -z "$${GH_TOKEN:-}$${GITHUB_TOKEN:-}$${ZIZMOR_GITHUB_TOKEN:-}" ]; then \
		echo "make: audit-online needs a GitHub token and the environment has none." >&2; \
		echo "      Without one zizmor falls back to offline mode and reports a clean" >&2; \
		echo "      run having skipped every audit this target exists for." >&2; \
		echo "" >&2; \
		echo '        export GH_TOKEN="$$(gh auth token)"    # or a fine-grained PAT' >&2; \
		echo "" >&2; \
		echo "      make audit is the offline subset and needs no token." >&2; \
		exit 1; \
	fi
	env -u ZIZMOR_OFFLINE -u ZIZMOR_NO_ONLINE_AUDITS zizmor .

# The README's one-liner: every credential-free check, in the order the
# cheapest failures come first.
check: fmt-check lint typecheck test docs-check audit preview-offline ## Run every credential-free check.

# ---------------------------------------------------------------------------
# The environment targets
# ---------------------------------------------------------------------------

# Everything above this line is a check and reaches no AWS account; everything
# below it talks to AWS. They are separated rather than interleaved so that the
# boundary is visible in the file rather than remembered.
#
# Two guards are shared by every verb below and spliced into recipes as
# `$(require_passphrase)` and `$(require_backend_env)`. Each is one logical
# recipe line, so each runs in its own shell and its `exit 1` stops make.
#
# The passphrase: every stack in this repository uses the passphrase secrets
# provider (LOCKED-1), and the CLI verifies the passphrase against the
# committed `encryptionsalt` when it builds the secrets manager — on every
# operation, preview included, before any value is touched. Without the
# variable `stack init` prompts and `preview` fails with `passphrase must be
# set with PULUMI_CONFIG_PASSPHRASE`; this guard names it first.
define require_passphrase
@if [ -z "$${PULUMI_CONFIG_PASSPHRASE:-}" ]; then \
	echo "make: PULUMI_CONFIG_PASSPHRASE is not set, so $@ cannot open the stack." >&2; \
	echo "      Every stack here uses the passphrase secrets provider, and the CLI checks" >&2; \
	echo "      the passphrase against the committed encryptionsalt on every operation." >&2; \
	echo "      Export the one you also stored as the repository secret:" >&2; \
	echo "" >&2; \
	echo "        export PULUMI_CONFIG_PASSPHRASE='…'" >&2; \
	echo "" >&2; \
	exit 1; \
fi
endef

# backend.env: the state bucket carries a per-account random suffix and the
# region is per-account too, so neither is in the tree. The file is gitignored
# and created from the example; the bootstrap prints both values. Nothing here
# reads the bucket from any other place, so a stale export in the shell cannot
# stand in for it.
define require_backend_env
@if [ ! -f backend.env ]; then \
	echo "make: backend.env is missing, so $@ does not know which state bucket to open." >&2; \
	echo "      The bucket name carries a per-account random suffix, so the file is" >&2; \
	echo "      gitignored and created from the example:" >&2; \
	echo "" >&2; \
	echo "        cp backend.env.example backend.env" >&2; \
	echo "" >&2; \
	echo "      Then fill PULUMI_STATE_BUCKET and AWS_REGION. The bootstrap prints them:" >&2; \
	echo "" >&2; \
	echo "        PULUMI_BACKEND_URL=file://$(CURDIR)/bootstrap/.pulumi-state pulumi -C bootstrap stack output backendUrl --stack bootstrap" >&2; \
	echo "" >&2; \
	exit 1; \
fi
endef

# The bootstrap: one stack, `bootstrap`, on a file:// backend under
# bootstrap/.pulumi-state/ — the analogue of bootstrap/terraform.tfstate, kept
# out of Git and backed up outside the working copy (docs/BOOTSTRAP.md §4). It
# is the layer that outlives every environment cycle: the state bucket, the
# OIDC provider, the plan and apply roles, the boundary.
#
# The file:// backend never creates its directory (measured 2026-09-21 on
# 3.263.0: `stack init`, `stack select --create` and `pulumi login` all fail
# with `unable to open state directory … no such file or directory`). Only
# `bootstrap-init` creates it, inline and not as a shared prerequisite: for
# the other three verbs a missing directory must stay that error — the one
# that means "restore the backup" — rather than become `no stack named
# 'bootstrap'` against a directory this Makefile silently made empty.
#
# `stack init <name>` and `--stack` are mutually exclusive on the CLI, so the
# first verb names the stack positionally and the other three with the flag.
# `up` and `destroy` prompt: the interactive confirmation is the only thing
# standing between a mistyped target and the account's bootstrap layer, and
# nothing in CI calls these. `destroy` is refused by the CLI while the state
# bucket carries `protect=True`, which is the guard TEARDOWN §8 walks through
# lifting on purpose.
BOOTSTRAP_BACKEND := file://$(CURDIR)/bootstrap/.pulumi-state

bootstrap-init: check-pulumi ## Create the bootstrap stack on its file:// backend (writes encryptionsalt into Pulumi.bootstrap.yaml).
	$(require_passphrase)
	mkdir -p "$(CURDIR)/bootstrap/.pulumi-state"
	PULUMI_BACKEND_URL="$(BOOTSTRAP_BACKEND)" pulumi -C bootstrap stack init bootstrap --secrets-provider passphrase

bootstrap-preview: check-pulumi ## Preview the bootstrap stack against AWS.
	$(require_passphrase)
	PULUMI_BACKEND_URL="$(BOOTSTRAP_BACKEND)" pulumi -C bootstrap preview --stack bootstrap

bootstrap-up: check-pulumi ## Apply the bootstrap stack. Prompts before changing anything.
	$(require_passphrase)
	PULUMI_BACKEND_URL="$(BOOTSTRAP_BACKEND)" pulumi -C bootstrap up --stack bootstrap

bootstrap-destroy: check-pulumi ## Destroy the bootstrap stack. Prompts; refused while the state bucket is protected.
	$(require_passphrase)
	PULUMI_BACKEND_URL="$(BOOTSTRAP_BACKEND)" pulumi -C bootstrap destroy --stack bootstrap

# The per-environment stacks, once, under the bootstrap's elevated identity.
# Pulumi needs a stack to exist before anything can preview it, and `stack
# init` is what writes the (empty) checkpoint the CI roles are then granted
# access to by name — so CI cannot do this step, and the first backend
# construction under the s3:// URL, which writes .pulumi/meta.yaml, happens
# here rather than under a plan role that is denied the write (LOCKED-1,
# amended 2026-09-16).
#
# The backend URL comes from the bootstrap stack's `backendUrl` output, read
# under its own file:// URL inline, and the environment list from the
# bootstrap's `environments` config — the same values the roles were built
# from, so the stacks initialised here are exactly the ones the policies name.
# `config get` prints a list as compact JSON (`["stage","prod"]`, measured
# 2026-09-21); the names are constrained by the bootstrap's validation to
# lower-case letters, digits and hyphens, which is what makes the `tr` safe.
#
# Idempotent by an explicit guard: the CLI errors `stack
# 'organization/static-site/<env>' already exists` on a second init (measured
# 2026-09-21), so each name is looked up in `stack ls --json` first and skipped
# when present. The ten-key envs/Pulumi.<env>.yaml must already exist (Phase
# 1); `stack init` keeps its `config:` block and appends the salt after it.
init-stacks: check-pulumi ## Initialise the stage and prod stacks in the state bucket (once, after bootstrap-up).
	$(require_passphrase)
	@url="$$(PULUMI_BACKEND_URL="$(BOOTSTRAP_BACKEND)" pulumi -C bootstrap stack output backendUrl --stack bootstrap)"; \
	if [ -z "$$url" ]; then \
		echo "make: the bootstrap stack has no backendUrl output." >&2; \
		echo "      init-stacks reads the s3:// URL from there; run make bootstrap-up first." >&2; \
		exit 1; \
	fi; \
	raw="$$(PULUMI_BACKEND_URL="$(BOOTSTRAP_BACKEND)" pulumi -C bootstrap config get environments --stack bootstrap)"; \
	environments="$$(printf '%s' "$$raw" | tr -d '[]" ' | tr ',' '\n')"; \
	if [ -z "$$environments" ]; then \
		echo "make: the bootstrap's environments config is empty; nothing to initialise." >&2; \
		echo "      bootstrap/Pulumi.bootstrap.yaml must list the environments (e.g. [\"stage\", \"prod\"])." >&2; \
		exit 1; \
	fi; \
	echo "==> backend: $$url"; \
	existing="$$(PULUMI_BACKEND_URL="$$url" pulumi -C envs stack ls --json)"; \
	while IFS= read -r env; do \
		[ -n "$$env" ] || continue; \
		if [ ! -f "envs/Pulumi.$$env.yaml" ]; then \
			echo "make: envs/Pulumi.$$env.yaml is missing." >&2; \
			echo "      stack init appends the encryptionsalt to that file, so it must already" >&2; \
			echo "      hold the ten static-site:* config keys (IMPLEMENTATION_PLAN.md, Phase 1)." >&2; \
			exit 1; \
		fi; \
		if printf '%s\n' "$$existing" | grep -qE "^ *\"name\": \"$$env\",?$$"; then \
			echo "==> stack $$env already exists in the bucket; skipping"; \
			continue; \
		fi; \
		echo "==> pulumi -C envs stack init $$env"; \
		PULUMI_BACKEND_URL="$$url" pulumi -C envs stack init "$$env" --secrets-provider passphrase; \
	done <<< "$$environments"

# One canned recipe, called once per environment per verb, instead of eight
# near-identical bodies: a prod target written by copying a stage target is
# exactly where a `--stack` gets left pointing at stage, and with one body the
# environment is an argument and cannot be half-edited. The targets are still
# spelled out one per environment rather than written as a `%` pattern rule: a
# pattern would silently accept `make up-nonsense`.
#
# The backend URL is composed from backend.env on the command line, never
# exported (the argument at the top of this file). `--policy-pack` is on
# `preview` and `up` only: `destroy` has no such flag on 3.263.0 (`error:
# unknown flag: --policy-pack`, measured 2026-09-21). The path is absolute
# because `-C` chdirs before the flag is resolved — a bare `policy` would be
# `envs/policy`. There is no `--policy-pack-config`: the pack reads
# `Pulumi.<env>.yaml`'s own static-site:* keys.
#
# None of the verbs passes `--yes`: the interactive confirmation on up and
# destroy is the only thing standing between a mistyped target and a real
# environment, and CI does not use these targets — apply.yml applies a
# reviewed preview artefact behind a GitHub Environment, which is a different
# and stronger control than a prompt.
#
# $(1) is the pulumi verb, $(2) the environment, $(3) any extra flags.
define pulumi_env
	$(require_backend_env)
	$(require_passphrase)
	@. ./backend.env; \
	if [ -z "$${PULUMI_STATE_BUCKET:-}" ] || [ -z "$${AWS_REGION:-}" ]; then \
		echo "make: backend.env must set both PULUMI_STATE_BUCKET and AWS_REGION." >&2; \
		echo "      The bootstrap prints them:" >&2; \
		echo "        PULUMI_BACKEND_URL=file://$(CURDIR)/bootstrap/.pulumi-state pulumi -C bootstrap stack output --stack bootstrap" >&2; \
		exit 1; \
	fi; \
	printf '==> pulumi -C envs %s --stack %s %s  [backend s3://%s, region %s]\n' '$(1)' '$(2)' '$(3)' "$$PULUMI_STATE_BUCKET" "$$AWS_REGION"; \
	PULUMI_BACKEND_URL="s3://$${PULUMI_STATE_BUCKET}?region=$${AWS_REGION}&awssdk=v2" \
		pulumi -C envs $(1) --stack $(2) $(3)
endef

preview-stage: check-pulumi ## Preview the stage stack against AWS (with --refresh and the policy pack).
	$(call pulumi_env,preview,stage,--refresh --policy-pack "$(CURDIR)/policy")

up-stage: check-pulumi ## Apply the stage stack with the policy pack. Prompts before changing anything.
	$(call pulumi_env,up,stage,--policy-pack "$(CURDIR)/policy")

destroy-stage: check-pulumi ## Destroy the stage stack. Prompts before removing anything.
	$(call pulumi_env,destroy,stage,)

# The `force-unlock` analogue (TEARDOWN §5.3): removes a stranded lock object
# under .pulumi/locks/ left by an interrupted up or destroy. Prompts.
cancel-stage: check-pulumi ## Clear a stranded lock on the stage stack (pulumi cancel). Prompts.
	$(call pulumi_env,cancel,stage,)

preview-prod: check-pulumi ## Preview the prod stack against AWS (with --refresh and the policy pack).
	$(call pulumi_env,preview,prod,--refresh --policy-pack "$(CURDIR)/policy")

up-prod: check-pulumi ## Apply the prod stack with the policy pack. Prompts before changing anything.
	$(call pulumi_env,up,prod,--policy-pack "$(CURDIR)/policy")

destroy-prod: check-pulumi ## Destroy the prod stack. Prompts before removing anything.
	$(call pulumi_env,destroy,prod,)

cancel-prod: check-pulumi ## Clear a stranded lock on the prod stack (pulumi cancel). Prompts.
	$(call pulumi_env,cancel,prod,)

# ---------------------------------------------------------------------------
# The post-destroy sweep
# ---------------------------------------------------------------------------

# docs/TEARDOWN.md section 6 as a target, because a checklist a human pastes
# is a checklist that drifts from the code it describes. The Terraform
# repository's Makefile:1066-1385 argues every row and every shape below at
# length; what follows is the port, with what changed said where it changed.
#
# What it is for. A destroy exiting 0 is a claim about a checkpoint, and the
# resources most worth worrying about are the ones that leave state without
# leaving AWS. Three of the types the component creates — the cache policies,
# the response headers policies and the origin access control — expose no
# tags at all, so the tag-based assertion e2e.yml makes is structurally blind
# to a leak in any of them. What finds the rest is a sweep by name, which is
# this.
#
# ONE ACCOUNT-WIDE TARGET, NOT `verify-teardown-stage` AND `-prod`. The origin
# access control and the CloudFront Function are named
# `<namePrefix>-site-<env>-<8 hex>` with the site bucket's random suffix, and
# after a destroy the checkpoint that knew the suffix has no resources, so the
# only expression that can find either is a prefix match on
# `<namePrefix>-site-`, which every environment shares. The account is also
# the more useful question: the two CloudFront policy quotas are 20 per
# account, and this component burns 2 of each per environment.
#
# WHERE `namePrefix` COMES FROM, which is the one input that can make this
# target lie: a wrong prefix or `project` makes a query match nothing and
# report clean over a live leak. So it is deliberately not an environment
# variable and not a `?=` override. It is read out of envs/Pulumi.<env>.yaml —
# the committed config each environment is applied with — by a sed anchored
# on the exact two-space key the files write; `project` and `awsRegion` come
# from the same files, and the environments are the file names, which is what
# `pulumi.get_stack()` makes the Env tag equal to (there is no directory name
# for a copied root to disagree with). The three values are per-account rather
# than per-environment, so the files must agree; this refuses if they do not,
# rather than sweeping half an account under one of two prefixes.
#
# Queries filter on the *new* `Project` value and the *new* prefix, so the
# Terraform repository's `isai2105-site-*` resources, standing or not, never
# read as this repository's leak (LOCKED-2).
#
# WHAT IT CHECKS, the eleven rows of section 6: 1 distributions matched on the
# comment or origin domain; 2–3 custom cache and response headers policies
# (`--type custom`, with the account total against the quota printed as
# information); 4 origin access controls and 5 CloudFront Functions by prefix;
# 6 access log groups under /aws/vendedlogs/cloudfront/<prefix>-site-* in
# us-east-1; 7 delivery sources, destinations and deliveries in us-east-1; 8
# ACM certificates left PENDING_VALIDATION, reported and never counted; 9 site
# buckets, through the tag inventory rather than a bucket listing (the CI role
# holds no s3:ListAllMyBuckets, and the component's tags contract makes an
# untagged bucket unable to reach apply); 10 the tag inventory for Project and
# Env in BOTH the environment's region and us-east-1; 11 — the row that
# changed — objects under .pulumi/locks/ in the state bucket: a stranded DIY
# lock left by an interrupted up or destroy, cleared by `make cancel-<env>`.
# The bucket comes from PULUMI_STATE_BUCKET (env) or backend.env; neither
# present is reported as a check that could not run, never as one that passed.
#
# One resource is on none of those rows on purpose: `<prefix>-app-deploy-boundary`
# outlives every environment by design and comes down with the bootstrap.
#
# THREE OUTCOMES, and a caller tells them apart by the last line of stdout,
# not by the exit code (make collapses 1 and 2 into its own `Error N`):
#
#   verify-teardown-result: clean        every check ran, none found anything
#   verify-teardown-result: leak         a check found a resource matching this naming
#   verify-teardown-result: incomplete   a check could not run; nothing was disproved
#
# TRUNCATION: `ListCachePolicies`, `ListResponseHeadersPolicies` and
# `ListFunctions` have no paginator in the CLI, so those three queries ask for
# the marker in the same call by prepending `!TRUNCATED!` when `NextMarker` is
# set, and the recipe treats that as could-not-look rather than as clean.
# Every query is `--output text` and judged by whether it printed anything, so
# a finding names the resource; the literal `None` the CLI prints for a null
# JMESPath result is stripped; each call's stderr goes to a file printed only
# when the call failed. The `sts:GetCallerIdentity` preflight alone keeps its
# stderr on the terminal, so an MFA prompt is visible.
#
# CI-READY WITH NO NEW GRANT: every call below is one the apply role already
# holds; a check added here that needs another action is a change to
# bootstrap/oidc.py and should arrive as one.
verify-teardown: check-aws-cli ## Sweep the account for anything this component named that survived a destroy.
	@finish() { printf 'verify-teardown-result: %s\n' "$$1"; exit "$$2"; }; \
	config_list="$$(find envs -mindepth 1 -maxdepth 1 -type f -name 'Pulumi.*.yaml' | sort || true)"; \
	if [ -z "$$config_list" ]; then \
		echo "make: verify-teardown found no envs/Pulumi.<env>.yaml to read." >&2; \
		echo "      Every query it makes is scoped by static-site:namePrefix, and a query" >&2; \
		echo "      built from an empty prefix matches nothing — which this target would" >&2; \
		echo "      then report as a clean account. Refusing instead." >&2; \
		finish incomplete 2; \
	fi; \
	configs=(); \
	while IFS= read -r file; do configs+=("$$file"); done <<< "$$config_list"; \
	lines() { printf '%s\n' "$$1" | grep -c . || true; }; \
	VALUE=""; \
	read_key() { \
		local name="$$1" values count; \
		values="$$(sed -n "s/^  static-site:$${name}: *\"\{0,1\}\([^\"]*\)\"\{0,1\} *$$/\1/p" "$${configs[@]}" | sort -u || true)"; \
		count="$$(lines "$$values")"; \
		if [ "$$count" -eq 0 ]; then \
			echo "make: no environment declares static-site:$${name}, so verify-teardown has" >&2; \
			echo "      nothing to match on and every query below would match everything or" >&2; \
			echo "      nothing. Looked for the two-space-indented key in:" >&2; \
			printf '        %s\n' "$${configs[@]}" >&2; \
			finish incomplete 2; \
		fi; \
		if [ "$$count" -gt 1 ]; then \
			echo "make: the environments disagree about static-site:$${name}:" >&2; \
			printf '%s\n' "$$values" | sed 's/^/        /' >&2; \
			echo "      This value is per-account rather than per-environment. Refusing rather" >&2; \
			echo "      than picking one and sweeping for part of the account." >&2; \
			finish incomplete 2; \
		fi; \
		VALUE="$$values"; \
	}; \
	read_key namePrefix; name_prefix="$$VALUE"; \
	read_key project; project="$$VALUE"; \
	read_key awsRegion; env_region="$$VALUE"; \
	site="$${name_prefix}-site-"; \
	environments=(); \
	while IFS= read -r file; do \
		value="$$(basename "$$file")"; value="$${value#Pulumi.}"; value="$${value%.yaml}"; \
		environments+=("$$value"); \
	done <<< "$$config_list"; \
	state_bucket="$${PULUMI_STATE_BUCKET:-}"; \
	state_region="$${AWS_REGION:-}"; \
	if [ -f backend.env ]; then \
		[ -n "$$state_bucket" ] || state_bucket="$$(. ./backend.env; printf '%s' "$${PULUMI_STATE_BUCKET:-}")"; \
		[ -n "$$state_region" ] || state_region="$$(. ./backend.env; printf '%s' "$${AWS_REGION:-}")"; \
	fi; \
	state_region="$${state_region:-$$env_region}"; \
	dirty=0; \
	incomplete=0; \
	if ! errfile="$$(mktemp)"; then \
		echo "make: verify-teardown could not create a temporary file for stderr." >&2; \
		finish incomplete 2; \
	fi; \
	trap 'rm -f "$$errfile"' EXIT; \
	QUERY_RESULT=""; \
	QUERY_TRUNCATED=0; \
	aws_query() { \
		local label="$$1" raw; \
		shift; \
		QUERY_TRUNCATED=0; \
		if ! raw="$$("$$@" 2>"$$errfile")"; then \
			{ printf '??    %s\n' "$$label"; \
			  printf '      the AWS call failed, so this check proved nothing:\n'; \
			  sed 's/^/        /' "$$errfile"; } >&2; \
			incomplete=1; \
			return 1; \
		fi; \
		QUERY_RESULT="$$(printf '%s' "$$raw" | tr -s '[:space:]' '\n' | sed -e '/^None$$/d' -e '/^$$/d')"; \
		if printf '%s\n' "$$QUERY_RESULT" | grep -qxF '!TRUNCATED!'; then \
			QUERY_TRUNCATED=1; \
			incomplete=1; \
			QUERY_RESULT="$$(printf '%s\n' "$$QUERY_RESULT" | grep -vxF '!TRUNCATED!' || true)"; \
		fi; \
		return 0; \
	}; \
	report() { \
		if [ -n "$$QUERY_RESULT" ]; then \
			printf 'LEAK  %s:\n' "$$1"; \
			printf '%s\n' "$$QUERY_RESULT" | sed 's/^/        /'; \
			dirty=1; \
		elif [ "$$QUERY_TRUNCATED" -eq 0 ]; then \
			printf 'ok    %s: nothing\n' "$$1"; \
		fi; \
		if [ "$$QUERY_TRUNCATED" -ne 0 ]; then \
			{ printf '??    %s: the API truncated this list, so what came back is a\n' "$$1"; \
			  printf '      page and not the answer. This operation has no paginator in\n'; \
			  printf '      the CLI, so nothing follows the NextMarker it returned.\n'; \
			  printf '      Counting it as could-not-look rather than as clean.\n'; } >&2; \
		fi; \
	}; \
	sweep() { \
		local label="$$1"; \
		shift; \
		aws_query "$$label" "$$@" || return 0; \
		report "$$label"; \
	}; \
	policy_sweep() { \
		local label="$$1" subcommand="$$2" query="$$3" all total; \
		aws_query "$$label" aws cloudfront "$$subcommand" --region us-east-1 --type custom --output text --query "$$query" || return 0; \
		all="$$QUERY_RESULT"; \
		total="$$(lines "$$all")"; \
		QUERY_RESULT="$$(printf '%s\n' "$$all" | grep -e "^$$site" || true)"; \
		report "$$label"; \
		if [ "$$QUERY_TRUNCATED" -ne 0 ]; then \
			printf '      at least %s of the 20-per-account quota in use — the list was cut short\n' "$$total"; \
		else \
			printf '      %s of the 20-per-account quota in use, by everything in the account\n' "$$total"; \
		fi; \
	}; \
	if ! caller="$$(aws sts get-caller-identity --region us-east-1 --query '[Account,Arn]' --output text)"; then \
		echo "" >&2; \
		echo "make: verify-teardown has no usable AWS credentials." >&2; \
		echo "      The CLI's own message is immediately above this one." >&2; \
		echo "" >&2; \
		echo "      It asks AWS what is standing in the account, so there is no" >&2; \
		echo "      degraded answer it can give here. Authenticate and re-run — a" >&2; \
		echo "      profile is enough for the CLI, unlike the pulumi targets above:" >&2; \
		echo "" >&2; \
		echo '        export AWS_PROFILE=<profile>   # or aws sso login --profile <profile>' >&2; \
		finish incomplete 2; \
	fi; \
	printf 'account:     %s\n' "$$(printf '%s' "$$caller" | cut -f1)"; \
	printf 'caller:      %s\n' "$$(printf '%s' "$$caller" | cut -f2)"; \
	printf 'namePrefix:  %s   (read from envs/Pulumi.<env>.yaml)\n' "$$name_prefix"; \
	printf 'project:     %s\n' "$$project"; \
	printf 'matching:    %s*\n' "$$site"; \
	printf 'regions:     %s and us-east-1\n' "$$env_region"; \
	printf '\n'; \
	sweep "CloudFront distributions matching $${site}* (id|origin)" \
		aws cloudfront list-distributions --region us-east-1 --output text \
		--query "DistributionList.Items[?contains(not_null(Comment, ''), '$${site}') || contains(not_null(Origins.Items[0].DomainName, ''), '$${site}')].join('|', [Id, not_null(Origins.Items[0].DomainName, 'no-origin')])"; \
	policy_sweep "custom cache policies" list-cache-policies \
		"[CachePolicyList.NextMarker && '!TRUNCATED!', CachePolicyList.Items[].CachePolicy.CachePolicyConfig.Name] | []"; \
	policy_sweep "custom response headers policies" list-response-headers-policies \
		"[ResponseHeadersPolicyList.NextMarker && '!TRUNCATED!', ResponseHeadersPolicyList.Items[].ResponseHeadersPolicy.ResponseHeadersPolicyConfig.Name] | []"; \
	sweep "origin access controls" \
		aws cloudfront list-origin-access-controls --region us-east-1 --output text \
		--query "OriginAccessControlList.Items[?starts_with(Name, '$${site}')].Name"; \
	sweep "CloudFront Functions" \
		aws cloudfront list-functions --region us-east-1 --output text \
		--query "[FunctionList.NextMarker && '!TRUNCATED!', FunctionList.Items[?starts_with(Name, '$${site}')].Name] | []"; \
	sweep "access log groups (us-east-1)" \
		aws logs describe-log-groups --region us-east-1 --output text \
		--log-group-name-prefix "/aws/vendedlogs/cloudfront/$${site}" \
		--query 'logGroups[].logGroupName'; \
	sweep "delivery sources (us-east-1)" \
		aws logs describe-delivery-sources --region us-east-1 --output text \
		--query "deliverySources[?starts_with(name, '$${site}')].name"; \
	sweep "delivery destinations (us-east-1)" \
		aws logs describe-delivery-destinations --region us-east-1 --output text \
		--query "deliveryDestinations[?starts_with(name, '$${site}')].name"; \
	sweep "deliveries (us-east-1)" \
		aws logs describe-deliveries --region us-east-1 --output text \
		--query "deliveries[?starts_with(deliverySourceName, '$${site}')].id"; \
	if aws_query "ACM certificates awaiting validation (us-east-1)" \
		aws acm list-certificates --region us-east-1 --output text \
		--certificate-statuses PENDING_VALIDATION \
		--query 'CertificateSummaryList[].DomainName'; then \
		if [ -z "$$QUERY_RESULT" ]; then \
			printf 'ok    ACM certificates awaiting validation (us-east-1): nothing\n'; \
		else \
			printf 'note  ACM certificates awaiting validation (us-east-1):\n'; \
			printf '%s\n' "$$QUERY_RESULT" | sed 's/^/        /'; \
			printf '      Reported, not counted as a leak: a certificate is named by a\n'; \
			printf '      domain rather than by namePrefix, so nothing here can say\n'; \
			printf '      whose it is. One this component created carries tags and would\n'; \
			printf '      fail the inventory below. Its validation record lives in a\n'; \
			printf '      hosted zone this repository neither creates nor can read.\n'; \
		fi; \
	fi; \
	regions=("$$env_region"); \
	[ "$$env_region" = "us-east-1" ] || regions+=("us-east-1"); \
	for environment in "$${environments[@]}"; do \
		for region in "$${regions[@]}"; do \
			sweep "tagged Project=$${project} Env=$${environment} in $${region}" \
				aws resourcegroupstaggingapi get-resources --region "$$region" --output text \
				--tag-filters "Key=Project,Values=$${project}" "Key=Env,Values=$${environment}" \
				--query 'ResourceTagMappingList[].ResourceARN'; \
		done; \
	done; \
	if [ "$$(lines "$$state_bucket")" -ne 1 ]; then \
		echo "??    stranded locks: no single state bucket to look in." >&2; \
		echo "      The bucket carries a per-account random suffix, so its name is" >&2; \
		echo "      not in the tree. Export PULUMI_STATE_BUCKET, or create the gitignored" >&2; \
		echo "      backend.env from backend.env.example. Section 5.2 of docs/TEARDOWN.md" >&2; \
		echo "      is the check this skipped." >&2; \
		incomplete=1; \
	else \
		sweep "stranded lock objects under .pulumi/locks/ in $${state_bucket}" \
			aws s3api list-objects-v2 --region "$$state_region" --bucket "$$state_bucket" \
			--prefix .pulumi/locks/ --output text --query 'Contents[].Key'; \
	fi; \
	printf '\n'; \
	if [ "$$dirty" -ne 0 ]; then \
		echo "make: verify-teardown found resources matching this component's naming." >&2; \
		echo "      Each is listed above by name. Match it against the component's" >&2; \
		echo "      naming before removing anything by hand — section 6.3 of" >&2; \
		echo "      docs/TEARDOWN.md: this account may hold resources this" >&2; \
		echo "      repository did not create." >&2; \
		finish leak 1; \
	fi; \
	if [ "$$incomplete" -ne 0 ]; then \
		echo "make: verify-teardown could not run every check, so it is not saying" >&2; \
		echo "      the account is clean — it is saying it did not finish asking." >&2; \
		echo "      Each unfinished check is marked '??' above with the reason." >&2; \
		finish incomplete 2; \
	fi; \
	echo "verify-teardown: nothing matching $${site}* is standing in this account right now,"; \
	echo "                 and no resource carries Project=$${project} with any of this"; \
	echo "                 repository's Env values, in $${env_region} or us-east-1."; \
	echo ""; \
	echo "                 That is what this establishes and the whole of it. It looked"; \
	echo "                 for the resource types the component creates today, under the"; \
	echo "                 namePrefix this tree declares today. A type the component gains"; \
	echo "                 later needs a check added here, and a leak from a cycle run"; \
	echo "                 under a different prefix is outside every query above."; \
	echo ""; \
	finish clean 0
