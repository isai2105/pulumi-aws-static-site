"""``static_site.stack_config``: the ten ``static-site:*`` keys, parsed once and typed, and what is built from them.

``parse`` is pure and gets every failure direction; ``load`` is exercised against the in-process Pulumi config
store (``pulumi.runtime.set_all_config`` under the ``pulumi_runtime`` fixture of ``conftest.py``), which is not
a mock — it is what the CLI fills from ``PULUMI_CONFIG``/``PULUMI_STACK`` — so the runtime half is covered
without a preview.
"""

import re
from collections.abc import Iterator

import pulumi
import pulumi.runtime
import pytest

from static_site import StaticSiteArgs, stack_config

OIDC_PROVIDER_ARN = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"

# The plan.tftest.hcl fixtures as scripts/preview_offline.sh writes them with `pulumi config set-all`.
FIXTURE_VALUES: dict[str, str] = {
    "awsRegion": "eu-west-1",
    "namePrefix": "offline",
    "project": "pulumi-aws-static-site",
    "githubOwner": "tftest-owner",
    "githubRepository": "static-site",
    "appGithubOwner": "tftest-owner",
    "appGithubOwnerId": "1",
    "appGithubRepositoryId": "2",
    "appDeployBoundaryPolicyName": "published-by-the-bootstrap",
    "forceDestroy": "true",
}


def test_keys_are_the_ten_the_plan_lists() -> None:
    assert tuple(FIXTURE_VALUES) == stack_config.KEYS
    assert len(set(stack_config.KEYS)) == 10


def test_parse_reads_every_key_and_the_stack_name() -> None:
    config = stack_config.parse(FIXTURE_VALUES, stack="test")
    assert config == stack_config.StackConfig(
        environment="test",
        aws_region="eu-west-1",
        name_prefix="offline",
        project="pulumi-aws-static-site",
        github_owner="tftest-owner",
        github_repository="static-site",
        app_github_owner="tftest-owner",
        app_github_owner_id="1",
        app_github_repository_id="2",
        app_deploy_boundary_policy_name="published-by-the-bootstrap",
        force_destroy=True,
    )


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("True", True), ("false", False), ("False", False)])
def test_parse_reads_force_destroy_with_the_sdk_get_bool_spellings(raw: str, expected: bool) -> None:
    """The four spellings ``pulumi.Config.get_bool`` accepts, and no others: the loader never disagrees with the SDK."""
    assert stack_config.parse({**FIXTURE_VALUES, "forceDestroy": raw}, stack="test").force_destroy is expected


@pytest.mark.parametrize("key", stack_config.KEYS)
def test_parse_names_the_first_missing_key_and_the_stack(key: str) -> None:
    values: dict[str, str | None] = {**FIXTURE_VALUES, key: None}
    with pytest.raises(stack_config.MissingStackConfigError) as excinfo:
        stack_config.parse(values, stack="stage")
    assert excinfo.value.key == key
    assert excinfo.value.stack == "stage"
    assert str(excinfo.value) == (
        f"stack configuration 'static-site:{key}' on stack 'stage' is missing: "
        f"set it with `pulumi config set static-site:{key} <value>`"
    )


def test_parse_treats_an_absent_key_like_an_unset_one() -> None:
    with pytest.raises(stack_config.MissingStackConfigError, match=re.escape("'static-site:forceDestroy'")):
        stack_config.parse({key: value for key, value in FIXTURE_VALUES.items() if key != "forceDestroy"}, stack="x")


@pytest.mark.parametrize("raw", ["yes", "TRUE", "1", "", "on", " true"])
def test_parse_refuses_a_force_destroy_that_is_not_a_boolean(raw: str) -> None:
    with pytest.raises(stack_config.MalformedStackConfigError) as excinfo:
        stack_config.parse({**FIXTURE_VALUES, "forceDestroy": raw}, stack="prod")
    assert excinfo.value.key == "forceDestroy"
    assert str(excinfo.value) == (
        f"stack configuration 'static-site:forceDestroy' on stack 'prod' is {raw!r}, "
        'which is not a boolean ("true", "True", "false" or "False")'
    )


def test_every_stack_config_error_is_a_value_error() -> None:
    assert issubclass(stack_config.MissingStackConfigError, stack_config.StackConfigError)
    assert issubclass(stack_config.MalformedStackConfigError, stack_config.StackConfigError)
    assert issubclass(stack_config.StackConfigError, ValueError)


def test_default_tags_follow_the_providers_tf_contract() -> None:
    tags = stack_config.parse(FIXTURE_VALUES, stack="test").default_tags()
    assert tags == {
        "Project": "pulumi-aws-static-site",
        "Env": "test",
        "Owner": "tftest-owner",
        "Repo": "tftest-owner/static-site",
        "ManagedBy": "pulumi",
    }


def test_static_site_args_from_stack_config_maps_the_config_and_takes_the_rest() -> None:
    config = stack_config.parse(FIXTURE_VALUES, stack="test")
    args = StaticSiteArgs.from_stack_config(config, github_oidc_provider_arn=OIDC_PROVIDER_ARN)
    assert args == StaticSiteArgs(
        name_prefix="offline",
        environment="test",
        force_destroy=True,
        github_oidc_provider_arn=OIDC_PROVIDER_ARN,
        app_github_owner="tftest-owner",
        app_github_owner_id="1",
        app_github_repository_id="2",
        app_deploy_boundary_policy_name="published-by-the-bootstrap",
    )


def test_static_site_args_from_stack_config_passes_the_optional_inputs_through() -> None:
    config = stack_config.parse(FIXTURE_VALUES, stack="test")
    args = StaticSiteArgs.from_stack_config(
        config,
        github_oidc_provider_arn=OIDC_PROVIDER_ARN,
        seed_placeholder=False,
        domain_name="app.example.com",
        hosted_zone_id="Z1D633PJN98FT9",
        log_retention_days=90,
    )
    assert (args.seed_placeholder, args.domain_name, args.hosted_zone_id, args.log_retention_days) == (
        False,
        "app.example.com",
        "Z1D633PJN98FT9",
        90,
    )


@pytest.fixture
def runtime_config(pulumi_runtime: None) -> Iterator[None]:
    """The in-process config store filled the way the CLI fills it for stack ``test``, cleared afterwards."""
    pulumi.runtime.set_all_config({f"{stack_config.NAMESPACE}:{key}": value for key, value in FIXTURE_VALUES.items()})
    yield
    pulumi.runtime.set_all_config({})


def test_load_reads_the_running_stack(runtime_config: None) -> None:
    assert stack_config.load() == stack_config.parse(FIXTURE_VALUES, stack="test")


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("True", True), ("false", False), ("False", False)])
def test_load_and_the_sdk_read_the_same_force_destroy(runtime_config: None, raw: str, expected: bool) -> None:
    pulumi.runtime.set_all_config(
        {
            **{f"{stack_config.NAMESPACE}:{k}": v for k, v in FIXTURE_VALUES.items()},
            f"{stack_config.NAMESPACE}:forceDestroy": raw,
        }
    )
    assert stack_config.load().force_destroy is expected
    assert pulumi.Config(stack_config.NAMESPACE).get_bool("forceDestroy") is expected


def test_load_names_a_missing_key_on_the_running_stack(runtime_config: None) -> None:
    pulumi.runtime.set_all_config(
        {f"{stack_config.NAMESPACE}:{key}": value for key, value in FIXTURE_VALUES.items() if key != "namePrefix"}
    )
    with pytest.raises(
        stack_config.MissingStackConfigError, match=re.escape("'static-site:namePrefix' on stack 'test'")
    ):
        stack_config.load()
