"""The validated stack's own ``static-site:*`` configuration, as the pack reads it, and the rule that it is complete.

A CrossGuard analyzer runs inside the stack it validates: the CLI hands it
``PULUMI_CONFIG``, ``PULUMI_STACK`` and ``PULUMI_PROJECT`` and ``pulumi_policy``
loads them before every ``validate`` call (IMPLEMENTATION_PLAN.md section 7,
V0-3). So the pack reads the ten keys ``envs/Pulumi.<env>.yaml`` carries - and
that ``scripts/preview_offline.sh`` sets on the throwaway ``test`` stack -
through the same :func:`static_site.stack_config.load` the programs call: one
list of key names, one parser, one set of error messages, so the pack and the
programs cannot disagree on a key. There is no ``--policy-pack-config`` and no
per-environment file under ``policy/``: a third copy of the values would need
its own consistency check.

Every program ``load()``s the same keys before registering a resource, so on a
well-formed program :data:`STACK_CONFIG_COMPLETE` is defence in depth for a key
one of them reads lazily, and on a program that reads nothing it is the whole
check. Its violation path is exercised by ``tests/unit/test_policy_stack_config.py``
against the runtime's config map, because no in-tree program can reach it.
"""

from pulumi_policy import ReportViolation, StackValidationArgs, StackValidationPolicy

from static_site.stack_config import (
    KEYS,
    NAMESPACE,
    MalformedStackConfigError,
    MissingStackConfigError,
    StackConfig,
    StackConfigError,
    load,
)

__all__ = [
    "KEYS",
    "NAMESPACE",
    "STACK_CONFIG_COMPLETE",
    "MalformedStackConfigError",
    "MissingStackConfigError",
    "StackConfig",
    "StackConfigError",
    "load",
    "validate_stack_config",
]


def validate_stack_config(_args: StackValidationArgs, report_violation: ReportViolation) -> None:
    """The ``stack-config-complete`` check: read every key; the first absent or malformed one is a violation."""
    try:
        load()
    except StackConfigError as exc:
        report_violation(str(exc), None)


STACK_CONFIG_COMPLETE = StackValidationPolicy(
    name="stack-config-complete",
    description=(
        f"The stack carries the ten {NAMESPACE}:* configuration keys the environment program and every other "
        f"rule of this pack read ({', '.join(KEYS)}), each with a well-formed value."
    ),
    validate=validate_stack_config,
)
"""The rule; ``policy/__main__.py`` puts it in the pack (constructing the pack starts the analyzer server, so the
rule lives here, where a unit test can import it)."""
