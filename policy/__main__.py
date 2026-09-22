"""The ``static-site`` CrossGuard policy pack (LOCKED-3, LOCKED-4).

Every preview and every up runs it at ``MANDATORY`` (``--policy-pack <absolute
path to policy/>``); a violation fails the operation and a policy the engine
cannot run at preview (``can't run policy ...``) is treated as a failure by
``scripts/preview_offline.sh``. Phase 2 of IMPLEMENTATION_PLAN.md adds the
``interface/`` (what ``plan.tftest.hcl`` asserted) and ``posture/`` (what trivy
used to supply) rule sets to ``POLICIES``.

Phase 0 ships the one rule every later rule depends on: the validated stack
carries all ten ``static-site:*`` keys with well-formed values
(``stack_config.STACK_CONFIG_COMPLETE``). ``PolicyPack`` refuses an empty
``policies`` list (``pulumi_policy`` 1.21.0 raises ``Missing policies
argument``), and a pack that could not be loaded would not prove the
``--policy-pack`` wiring the Phase 0 exit criteria require.

Constructing ``PolicyPack`` starts the analyzer's gRPC server and blocks, so
this module is never imported: the rules live in importable modules and
``tests/unit`` tests them there.
"""

import stack_config
from pulumi_policy import EnforcementLevel, Policy, PolicyPack

POLICIES: list[Policy] = [stack_config.STACK_CONFIG_COMPLETE]
"""Every rule of the pack. Phase 2 extends it with ``interface.ALL + posture.ALL``."""

PolicyPack(
    name="static-site",
    policies=POLICIES,
    enforcement_level=EnforcementLevel.MANDATORY,
)
