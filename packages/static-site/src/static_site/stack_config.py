"""The ten ``static-site:*`` stack configuration keys, read once, typed, in one place.

Three programs read the same keys: ``envs/__main__.py`` (the real environments), ``tests/offline/__main__.py``
(the credential-free preview) and the CrossGuard pack under ``policy/``, which runs inside the stack it
validates and reads that stack's own config. Three literal copies of ten key names drift silently — a key
renamed in one program is a preview that fails in another with a message about a key nobody remembers — so
the names live here, in the one package all three already depend on, and each program calls :func:`load`.

The namespace is explicit (``static-site``), not the project name, so the same keys resolve from project
``static-site`` (``envs/``) and from project ``static-site-offline`` (``tests/offline/``), and there is no
``--policy-pack-config``: a per-environment file under ``policy/`` would be a third copy of the values.

:func:`parse` is the pure half — a mapping of raw strings and the stack name in, a :class:`StackConfig` out —
and is what the unit tests exercise; :func:`load` is the thin runtime half that feeds it ``pulumi.Config`` and
``pulumi.get_stack()``. Every failure is a :class:`StackConfigError` naming the key and the stack, so a stack
that forgot or mistyped a key fails its preview with that key in the message rather than a ``KeyError`` or a
``ConfigTypeError`` deep inside a program or a policy.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import pulumi

NAMESPACE: Final = "static-site"
"""The config namespace every stack carries its keys under (``static-site:<key>``)."""

STRING_KEYS: Final[tuple[str, ...]] = (
    "awsRegion",
    "namePrefix",
    "project",
    "githubOwner",
    "githubRepository",
    "appGithubOwner",
    "appGithubOwnerId",
    "appGithubRepositoryId",
    "appDeployBoundaryPolicyName",
)
"""The nine string-valued keys, in the order ``envs/Pulumi.<env>.yaml`` lists them."""

FORCE_DESTROY_KEY: Final = "forceDestroy"
"""The one boolean key, read with the SDK's ``Config.get_bool`` spellings: ``true``/``false`` (what a YAML-typed
``pulumi config set`` normalises to) and ``True``/``False`` (what ``pulumi config set … True`` stores verbatim)."""

KEYS: Final[tuple[str, ...]] = (*STRING_KEYS, FORCE_DESTROY_KEY)
"""All ten keys a stack must carry; the policy pack's ``stack-config-complete`` rule reads this list."""

MANAGED_BY_TAG: Final = "pulumi"
"""The ``ManagedBy`` default tag on both providers (cross-cutting finding 2)."""

_BOOLEANS: Final[Mapping[str, bool]] = {"true": True, "True": True, "false": False, "False": False}
"""Exactly the spellings ``pulumi.Config.get_bool`` accepts (``pulumi/config.py``), so this loader and the SDK agree."""


class StackConfigError(ValueError):
    """A ``static-site:*`` key the stack must carry is absent or malformed; the message names both."""

    def __init__(self, key: str, stack: str, problem: str) -> None:
        self.key = key
        self.stack = stack
        super().__init__(f"stack configuration '{NAMESPACE}:{key}' on stack '{stack}' {problem}")


class MissingStackConfigError(StackConfigError):
    """The key is not set on the stack."""

    def __init__(self, key: str, stack: str) -> None:
        super().__init__(key, stack, f"is missing: set it with `pulumi config set {NAMESPACE}:{key} <value>`")


class MalformedStackConfigError(StackConfigError):
    """The key is set but its value is not of the type the key requires."""

    def __init__(self, key: str, stack: str, value: str, expected: str) -> None:
        super().__init__(key, stack, f"is {value!r}, which is not {expected}")


@dataclass(frozen=True, kw_only=True, slots=True)
class StackConfig:
    """The ten keys of one stack plus its environment (the stack name), typed and read once."""

    environment: str
    aws_region: str
    name_prefix: str
    project: str
    github_owner: str
    github_repository: str
    app_github_owner: str
    app_github_owner_id: str
    app_github_repository_id: str
    app_deploy_boundary_policy_name: str
    force_destroy: bool

    def default_tags(self) -> dict[str, str]:
        """The ``default_tags`` both ``aws.Provider`` objects carry: the ``local.tags`` of ``envs/*/providers.tf``.

        ``Repo`` is ``<owner>/<repository>`` as ``providers.tf:41`` composes it; ``Env`` is the stack name, which
        the component's tags contract checks against its ``environment`` argument.
        """
        return {
            "Project": self.project,
            "Env": self.environment,
            "Owner": self.github_owner,
            "Repo": f"{self.github_owner}/{self.github_repository}",
            "ManagedBy": MANAGED_BY_TAG,
        }


def parse(values: Mapping[str, str | None], *, stack: str) -> StackConfig:
    """A :class:`StackConfig` from the raw ``key -> value`` mapping of one stack's ``static-site:*`` config.

    ``values`` maps bare key names (``namePrefix``, not ``static-site:namePrefix``) to the string Pulumi holds,
    or ``None`` for a key that is not set. The first absent key raises :class:`MissingStackConfigError`; a
    ``forceDestroy`` outside the four spellings ``Config.get_bool`` accepts raises :class:`MalformedStackConfigError`.
    """
    strings = {key: _require_string(values, key, stack) for key in STRING_KEYS}
    raw_force_destroy = _require_string(values, FORCE_DESTROY_KEY, stack)
    force_destroy = _BOOLEANS.get(raw_force_destroy)
    if force_destroy is None:
        raise MalformedStackConfigError(
            FORCE_DESTROY_KEY, stack, raw_force_destroy, 'a boolean ("true", "True", "false" or "False")'
        )
    return StackConfig(
        environment=stack,
        aws_region=strings["awsRegion"],
        name_prefix=strings["namePrefix"],
        project=strings["project"],
        github_owner=strings["githubOwner"],
        github_repository=strings["githubRepository"],
        app_github_owner=strings["appGithubOwner"],
        app_github_owner_id=strings["appGithubOwnerId"],
        app_github_repository_id=strings["appGithubRepositoryId"],
        app_deploy_boundary_policy_name=strings["appDeployBoundaryPolicyName"],
        force_destroy=force_destroy,
    )


def load() -> StackConfig:
    """The running stack's :class:`StackConfig`, from ``pulumi.Config(NAMESPACE)`` and ``pulumi.get_stack()``.

    Callable from a program and from a CrossGuard analyzer alike — the CLI hands the pack ``PULUMI_CONFIG``
    and ``PULUMI_STACK`` before every ``validate`` call, so the pack reads exactly what the program read.
    """
    config = pulumi.Config(NAMESPACE)
    return parse({key: config.get(key) for key in KEYS}, stack=pulumi.get_stack())


def _require_string(values: Mapping[str, str | None], key: str, stack: str) -> str:
    value = values.get(key)
    if value is None:
        raise MissingStackConfigError(key, stack)
    return value
