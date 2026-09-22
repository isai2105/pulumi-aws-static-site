#!/usr/bin/env bash
# The credential-free preview (LOCKED-3): the replacement for `terraform test`
# and, together with the policy pack it loads, for `terraform validate` and the
# trivy misconfiguration rules. `make preview-offline` is the only caller in
# the tree, locally and in validate.yml alike; nothing here depends on make.
#
# What it proves, on every run rather than once by hand: the component
# previews through the real pulumi-aws plugin with placeholder credentials, the
# policy pack runs every policy to a verdict (a skipped one is a failure), and
# the pytest over the preview JSON passes — with no AWS variable set and every
# outbound HTTP request black-holed after a two-line cache warm-up. That is
# plan.tftest.hcl:40-44's property from the Terraform repository, made
# mechanical: a cold plugin cache, a cold uv cache or a leaked AWS call is a
# red run with a named line, here and on a runner with a working network.
#
# Two knobs, both environment variables, both for `make policy`'s inner loop
# and neither for CI:
#
#   PREVIEW_OFFLINE_VARIANTS      space-separated variants to preview
#                                 (default: all three)
#   PREVIEW_OFFLINE_POLICY_ONLY   1 = stop after the plain policy previews:
#                                 no --json run and no pytest
#
# Outputs, at the repository root and gitignored: preview.<variant>.txt (the
# plain-text preview with the Policies block) and preview.<variant>.json (the
# document pytest reads). They are left in place on failure for inspection.
set -euo pipefail

# Absolute paths throughout. `pulumi -C <dir>` chdirs before any flag is
# resolved, so a relative --policy-pack would be looked for under tests/offline
# (measured 2026-09-21: `failed to load Pulumi policy project located at
# "policy"`), and a file:// backend URL is an absolute path by definition. The
# root is derived from this file's own location rather than from $PWD so the
# script means the same thing from any working directory.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

variants="${PREVIEW_OFFLINE_VARIANTS:-default managed-cert supplied-cert}"
policy_only="${PREVIEW_OFFLINE_POLICY_ONLY:-0}"

die() {
  printf 'preview_offline: %s\n' "$@" >&2
  exit 1
}

# Throwaway state, so every run starts empty. The file:// backend never
# creates its directory (measured 2026-09-21 on 3.263.0: `stack init`,
# `stack select --create` and `pulumi login` all fail with `unable to open
# state directory`), so it is made here. The stack's config file is removed
# with it: `stack rm` at the end deletes it on a green run, and a red run
# leaves one behind whose encryptionsalt would otherwise be paired with a
# fresh, empty backend.
rm -rf tests/offline/.state tests/offline/Pulumi.test.yaml
mkdir -p tests/offline/.state

# The backend is the variable, never `pulumi login`: an exported
# PULUMI_BACKEND_URL wins over a login (measured 2026-09-16), so setting it is
# the only way to be sure which backend a command sees. The passphrase is a
# literal because there is nothing to protect — every value is a fixture —
# and an unset one makes the CLI prompt (or fail under --non-interactive)
# rather than fall through. UV_LOCKED=1 is the guard for the `uv sync
# --inexact` the CLI runs on its own before every preview: it takes no flag,
# and without this it rewrites uv.lock on drift instead of failing.
export PULUMI_BACKEND_URL="file://$root/tests/offline/.state"
export PULUMI_CONFIG_PASSPHRASE=offline
export UV_LOCKED=1
export PULUMI_SKIP_UPDATE_CHECK=true

# Cache warm-up: the only two lines that may reach the network (PyPI and
# get.pulumi.com). `uv sync --locked --all-packages` is the one install
# anywhere — `--locked` refuses a drifted lock, `--all-packages` keeps every
# workspace member's closure in the shared .venv. `pulumi plugin install`
# with no arguments reads the program's required plugins and is a no-op once
# the cache is warm (measured 2026-09-21).
echo "==> warm-up: uv sync --locked --all-packages"
uv sync --locked --all-packages
echo "==> warm-up: pulumi -C tests/offline plugin install"
pulumi -C tests/offline plugin install

# No AWS credential of any kind from here on. Everything the SDK inside the
# pulumi-aws plugin could read is cleared or pointed at /dev/null, and the
# instance metadata endpoint is disabled so a stray 169.254.169.254 call cannot
# hang. Every other AWS_* variable in the environment is unset first — the
# plan names these six; a session token or a default region left behind by
# an `aws configure export-credentials` would otherwise ride along.
while IFS= read -r name; do
  [ -n "$name" ] && unset "$name"
done < <(compgen -e AWS_ || true)
export AWS_EC2_METADATA_DISABLED=true
export AWS_ACCESS_KEY_ID='' AWS_SECRET_ACCESS_KEY='' AWS_PROFILE=''
export AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null

# From here on every outbound HTTP request is black-holed. Port 9 is discard;
# nothing listens on it, so a proxied connection fails immediately with
# `proxyconnect tcp: dial tcp 127.0.0.1:9: connect: connection refused`
# (measured 2026-09-21 on 3.263.0; the same result under Docker
# --network none). The CLI's plugin download, the AWS SDK inside the plugin
# and uv all honour these; UV_OFFLINE=1 turns any PyPI reach into a failure
# rather than a silent download. Both spellings are exported because Go's
# net/http reads the upper-case pair and Python's urllib the lower-case one.
#
# Loopback is excluded, and this is the one line that differs from the
# command block in IMPLEMENTATION_PLAN.md (which exported NO_PROXY= empty):
# measured on 3.263.0, with an empty NO_PROXY the Python SDK's gRPC channel to
# the resource monitor on 127.0.0.1:<port> is routed through the black-hole
# proxy too, and every preview fails with `ipv4:127.0.0.1:9: Failed to connect
# … Resource monitor has terminated`. Loopback carries only the SDK/engine
# conversation, so excluding it costs the property nothing: the network the
# rule is about is the one beyond this host.
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9
export http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
export UV_OFFLINE=1

# The throwaway stack. Its name is the fixture environment: the program reads
# `pulumi.get_stack()` as the environment exactly as envs/__main__.py does,
# so the composed names below carry `test` where a real stack carries `stage`.
echo "==> pulumi -C tests/offline stack select --create test"
pulumi -C tests/offline stack select --create test

# The plan.tftest.hcl:126-202 fixtures, one source for tests/offline/__main__.py
# and the policy pack: the same ten static-site:* keys, under the same
# `pulumi.Config("static-site")` namespace, that envs/Pulumi.<env>.yaml
# carries for a real stack — which is what lets the same pack run against
# both without a config file of its own. Values are chosen to be checkable:
# `published-by-the-bootstrap` bears no relation to the prefix, so a component
# that re-derived the boundary name from namePrefix instead of reading the
# input would fail the assertion rather than pass by coincidence.
echo "==> pulumi -C tests/offline config set-all (fixtures)"
pulumi -C tests/offline config set-all \
  --plaintext static-site:awsRegion=eu-west-1 \
  --plaintext static-site:namePrefix=offline \
  --plaintext static-site:project=pulumi-aws-static-site \
  --plaintext static-site:githubOwner=tftest-owner \
  --plaintext static-site:githubRepository=static-site \
  --plaintext static-site:appGithubOwner=tftest-owner \
  --plaintext static-site:appGithubOwnerId=1 \
  --plaintext static-site:appGithubRepositoryId=2 \
  --plaintext static-site:appDeployBoundaryPolicyName=published-by-the-bootstrap \
  --plaintext static-site:forceDestroy=true

for variant in $variants; do
  pulumi -C tests/offline config set static-site-offline:variant "$variant"

  # Run 1, plain text: the only place the Policies block exists. A skipped
  # policy is an `[advisory]` entry reading
  #   can't run policy '<name>' from policy pack '<pack>' during preview: <type> value at .<prop> can't be known during preview
  # (measured 2026-09-16); it is not on stderr and it is not in the --json
  # document at all, which is why this run exists beside the next one.
  # Mandatory violations exit 1 on their own. stdout and stderr both go to
  # the file and to the terminal, so a CI log shows the block a reviewer
  # would look for and a failure keeps the CLI's own message in view.
  echo "==> [$variant] pulumi -C tests/offline preview --policy-pack $root/policy"
  if ! pulumi -C tests/offline preview --non-interactive --policy-pack "$root/policy" 2>&1 | tee "preview.$variant.txt"; then
    die "[$variant] the policy preview failed; its output is above and in preview.$variant.txt"
  fi
  # LOCKED-3: a skipped policy is a failure. The grep is fixed-string on the
  # CLI's own wording, so a policy reading a prop that is unknown at preview
  # cannot pass by never having run.
  if grep -F "can't run policy" "preview.$variant.txt"; then
    die "[$variant] a policy was skipped during preview (see the line above): every policy must read only preview-known props (LOCKED-3)"
  fi

  if [ "$policy_only" = 1 ]; then
    continue
  fi

  # Run 2, JSON, for the pytest: top-level `steps`, `diagnostics`,
  # `duration`, `changeSummary`; no policy output and no stack outputs
  # (measured 2026-09-16). No --policy-pack here: the pack's verdict was run
  # 1's, and the pack's presence changes nothing in this document.
  echo "==> [$variant] pulumi -C tests/offline preview --json > preview.$variant.json"
  if ! pulumi -C tests/offline preview --non-interactive --json > "preview.$variant.json"; then
    die "[$variant] the JSON preview failed; see preview.$variant.json"
  fi
done

if [ "$policy_only" != 1 ]; then
  # pytest exits 5 on an empty collection, which is read as a failure here on
  # purpose: a suite that asserts nothing over the preview is not a green
  # check.
  echo "==> uv run --locked --all-packages pytest tests/offline"
  uv run --locked --all-packages pytest tests/offline
fi

# The stack is throwaway: `--force` removes it with its (empty) checkpoint and
# `stack rm` also deletes the gitignored tests/offline/Pulumi.test.yaml. Only
# reached on a green run; a red run leaves the state and the outputs in place
# for inspection, and the next run starts by removing them.
echo "==> pulumi -C tests/offline stack rm test --yes --force"
pulumi -C tests/offline stack rm test --yes --force

echo "preview_offline: ok (${variants})"
