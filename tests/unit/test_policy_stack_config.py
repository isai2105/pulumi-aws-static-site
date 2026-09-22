"""``policy/stack_config.py``: the ``stack-config-complete`` rule over the runtime's config map.

The rule cannot go red through any in-tree program - every program ``load()``s the same ten keys before
registering a resource, so a missing key fails the program first and the pack never runs. Its violation path
is exercised here against the Pulumi runtime directly: ``pulumi.runtime.set_all_config`` is what
``pulumi_policy`` fills from ``PULUMI_CONFIG`` before every ``validate`` call, and the ``pulumi_runtime`` fixture
of ``conftest.py`` sets the stack name the way ``pulumi.runtime.configure`` does. The parser itself
(``static_site.stack_config.parse``) has its own unit tests; this file checks that the rule reaches it through
``load()`` and turns each error into one violation.

``policy/`` is a Pulumi program directory, not a package, and ``policy/__main__.py`` starts the analyzer server
on import, so the module under test is loaded from its file rather than imported by name and bound as
``policy_stack_config``, distinct from the ``static_site.stack_config`` package module it re-exports from.
"""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pulumi
import pulumi.runtime
import pytest
from pulumi_policy import StackValidationArgs

from static_site.stack_config import KEYS, NAMESPACE, MalformedStackConfigError, MissingStackConfigError

POLICY_DIR = Path(__file__).resolve().parents[2] / "policy"


def load_policy_module(name: str) -> ModuleType:
    """Import ``policy/<name>.py`` from its path (the pack's modules are not on ``sys.path`` here)."""
    spec = importlib.util.spec_from_file_location(name, POLICY_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None, f"policy/{name}.py not found"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy_stack_config = load_policy_module("stack_config")

STACK = "test"

COMPLETE_CONFIG: dict[str, str] = {
    f"{NAMESPACE}:awsRegion": "eu-west-1",
    f"{NAMESPACE}:namePrefix": "offline",
    f"{NAMESPACE}:project": "pulumi-aws-static-site",
    f"{NAMESPACE}:githubOwner": "tftest-owner",
    f"{NAMESPACE}:githubRepository": "static-site",
    f"{NAMESPACE}:appGithubOwner": "tftest-owner",
    f"{NAMESPACE}:appGithubOwnerId": "1",
    f"{NAMESPACE}:appGithubRepositoryId": "2",
    f"{NAMESPACE}:appDeployBoundaryPolicyName": "published-by-the-bootstrap",
    f"{NAMESPACE}:forceDestroy": "true",
}
"""The ``config set-all`` line of ``scripts/preview_offline.sh``, as the runtime sees it."""


class Violations:
    """A ``ReportViolation`` callback that records what the rule reported."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None]] = []

    def __call__(self, message: str, urn: str | None = None) -> None:
        self.messages.append((message, urn))


@pytest.fixture(autouse=True)
def empty_runtime_config(pulumi_runtime: None) -> Iterator[None]:
    """The configured runtime of ``conftest.py`` (stack ``test``) with an empty config map, reset afterwards."""
    pulumi.runtime.set_all_config({})
    yield
    pulumi.runtime.set_all_config({})


def validate(config: dict[str, str]) -> list[tuple[str, str | None]]:
    """Run the rule's ``validate`` against ``config`` and return what it reported."""
    pulumi.runtime.set_all_config(config)
    violations = Violations()
    policy_stack_config.validate_stack_config(StackValidationArgs(resources=[]), violations)
    return violations.messages


def test_the_fixture_covers_exactly_the_shared_key_list() -> None:
    assert [key.removeprefix(f"{NAMESPACE}:") for key in COMPLETE_CONFIG] == list(KEYS)


def test_a_complete_config_reports_nothing_and_loads_every_key() -> None:
    assert validate(COMPLETE_CONFIG) == []
    loaded = policy_stack_config.load()
    assert loaded.environment == STACK
    assert loaded.aws_region == "eu-west-1"
    assert loaded.name_prefix == "offline"
    assert loaded.project == "pulumi-aws-static-site"
    assert loaded.github_owner == "tftest-owner"
    assert loaded.github_repository == "static-site"
    assert loaded.app_github_owner == "tftest-owner"
    assert loaded.app_github_owner_id == "1"
    assert loaded.app_github_repository_id == "2"
    assert loaded.app_deploy_boundary_policy_name == "published-by-the-bootstrap"
    assert loaded.force_destroy is True


def test_an_empty_config_names_the_first_key() -> None:
    assert validate({}) == [(str(MissingStackConfigError(KEYS[0], STACK)), None)]


@pytest.mark.parametrize("key", KEYS)
def test_each_missing_key_is_one_violation_naming_it(key: str) -> None:
    config = {k: v for k, v in COMPLETE_CONFIG.items() if k != f"{NAMESPACE}:{key}"}
    messages = validate(config)
    assert messages == [(str(MissingStackConfigError(key, STACK)), None)]
    message = messages[0][0]
    assert f"'{NAMESPACE}:{key}'" in message and f"pulumi config set {NAMESPACE}:{key} <value>" in message


@pytest.mark.parametrize("value", ["yes", "TRUE", "1", "", "on"])
def test_a_malformed_force_destroy_is_a_violation_not_a_crash(value: str) -> None:
    messages = validate({**COMPLETE_CONFIG, f"{NAMESPACE}:forceDestroy": value})
    assert len(messages) == 1
    message, urn = messages[0]
    assert urn is None, "the stack as a whole is in violation, not one resource"
    assert message.startswith(f"stack configuration '{NAMESPACE}:forceDestroy' on stack '{STACK}'")
    assert repr(value) in message


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("True", True), ("false", False), ("False", False)])
def test_force_destroy_reads_the_two_spellings_pulumi_config_set_writes(value: str, expected: bool) -> None:
    pulumi.runtime.set_all_config({**COMPLETE_CONFIG, f"{NAMESPACE}:forceDestroy": value})
    assert policy_stack_config.load().force_destroy is expected


def test_the_rule_is_the_one_the_pack_ships_and_uses_the_shared_errors() -> None:
    assert policy_stack_config.STACK_CONFIG_COMPLETE.name == "stack-config-complete"
    assert policy_stack_config.NAMESPACE == NAMESPACE
    assert policy_stack_config.MissingStackConfigError is MissingStackConfigError
    assert policy_stack_config.MalformedStackConfigError is MalformedStackConfigError
