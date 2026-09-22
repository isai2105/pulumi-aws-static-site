"""``static_site.validation`` and ``StaticSiteArgs``: every ``variables.tf`` validation block, both directions.

Pure functions over plain values, so no Pulumi runtime and no mocks. Each block gets the values it accepts and
one representative of each mistake its message describes; the messages are matched on the fragment that names
the constraint. The two refused-input run blocks of ``plan.tftest.hcl`` (a domain with no certificate source,
a certificate source with no domain) are the last two tests, through ``StaticSiteArgs`` as the tftest went
through the module.
"""

import dataclasses
import re

import pytest

from static_site import StaticSiteArgs, validation

OIDC_PROVIDER_ARN = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"

# The plan.tftest.hcl fixtures: the smallest valid interface, with every optional input at its default.
# ``dataclasses.replace`` re-runs ``__post_init__``, so a variant of it is validated as a fresh construction is.
VALID_ARGS = StaticSiteArgs(
    name_prefix="tftest",
    environment="test",
    github_oidc_provider_arn=OIDC_PROVIDER_ARN,
    app_github_owner="tftest-owner",
    app_github_owner_id="1",
    app_github_repository_id="2",
    app_deploy_boundary_policy_name="published-by-the-bootstrap",
)

ZONE_ID = "Z1D633PJN98FT9"
CERTIFICATE_ARN = "arn:aws:acm:us-east-1:123456789012:certificate/abcd1234-ab12-cd34-ef56-abcdef123456"


def construct(**overrides: object) -> StaticSiteArgs:
    """``VALID_ARGS`` with ``overrides`` applied through the untyped keyword path a config loader would take.

    ``dataclasses.asdict`` yields ``dict[str, Any]``, so a deliberately wrong type reaches ``__post_init__`` the
    way a ``pulumi.Config.get()`` string would, where mypy would refuse the same call written literally.
    """
    return StaticSiteArgs(**{**dataclasses.asdict(VALID_ARGS), **overrides})


def raises(fragment: str) -> pytest.RaisesExc[ValueError]:
    """``pytest.raises(ValueError)`` matching a literal fragment of the message."""
    return pytest.raises(ValueError, match=re.escape(fragment))


# --- name_prefix -------------------------------------------------------------


@pytest.mark.parametrize("name_prefix", ["abc", "isai2105", "a-b-c", "0ab", "a" * 40])
def test_name_prefix_accepts_bucket_safe_names(name_prefix: str) -> None:
    validation.validate_name_prefix(name_prefix)


@pytest.mark.parametrize("name_prefix", ["Abc", "a.b", "-abc", "abc-", "a_b", "ab c"])
def test_name_prefix_rejects_characters_outside_the_bucket_class(name_prefix: str) -> None:
    with raises("The name_prefix must be lower case letters, digits and hyphens"):
        validation.validate_name_prefix(name_prefix)


@pytest.mark.parametrize("name_prefix", ["", "a", "ab", "a" * 41])
def test_name_prefix_rejects_lengths_outside_3_to_40(name_prefix: str) -> None:
    with raises("The name_prefix must be between 3 and 40 characters"):
        validation.validate_name_prefix(name_prefix)


# --- environment -------------------------------------------------------------


@pytest.mark.parametrize("environment", ["stage", "prod", "s", "stage-2", "e2e"])
def test_environment_accepts_lower_case_names_starting_with_a_letter(environment: str) -> None:
    validation.validate_environment(environment, name_prefix="tftest")


@pytest.mark.parametrize("environment", ["Stage", "2stage", "stage-", "-stage", "st_age", "st.age", ""])
def test_environment_rejects_names_outside_the_shared_character_class(environment: str) -> None:
    with raises("The environment must be lower case letters, digits and hyphens, must start with a letter"):
        validation.validate_environment(environment, name_prefix="tftest")


def test_environment_accepts_a_composed_length_of_exactly_48() -> None:
    validation.validate_environment("e" * 8, name_prefix="p" * 40)


def test_environment_rejects_a_composed_length_over_48() -> None:
    with raises("The name_prefix and environment are too long together"):
        validation.validate_environment("e" * 9, name_prefix="p" * 40)


# --- domain_name and its companions ------------------------------------------


def test_domain_name_accepts_none_with_no_companions() -> None:
    validation.validate_domain_name(None, hosted_zone_id=None, acm_certificate_arn=None)


@pytest.mark.parametrize("domain_name", ["app.example.com", "example.com", "a.b.c.d", "x1-y2.example.co.uk"])
def test_domain_name_accepts_bare_lower_case_fqdns(domain_name: str) -> None:
    validation.validate_domain_name(domain_name, hosted_zone_id=ZONE_ID, acm_certificate_arn=None)


@pytest.mark.parametrize(
    "domain_name",
    [
        "https://app.example.com",
        "app.example.com/",
        "app.example.com.",
        "app_1.example.com",
        "localhost",
        "*.example.com",
        "App.Example.com",
        "-app.example.com",
        "",
    ],
)
def test_domain_name_rejects_schemes_paths_trailing_dots_labels_and_wildcards(domain_name: str) -> None:
    with raises("The domain_name must be a bare fully qualified domain name in lower case"):
        validation.validate_domain_name(domain_name, hosted_zone_id=ZONE_ID, acm_certificate_arn=None)


def test_domain_name_accepts_exactly_64_characters() -> None:
    validation.validate_domain_name("a" * 52 + ".example.com", hosted_zone_id=ZONE_ID, acm_certificate_arn=None)


def test_domain_name_rejects_more_than_64_characters() -> None:
    with raises("The domain_name must be 64 characters or fewer"):
        validation.validate_domain_name("a" * 53 + ".example.com", hosted_zone_id=ZONE_ID, acm_certificate_arn=None)


@pytest.mark.parametrize(
    ("hosted_zone_id", "acm_certificate_arn"),
    [(ZONE_ID, None), (None, CERTIFICATE_ARN)],
    ids=["zone", "certificate"],
)
def test_domain_name_accepts_either_certificate_source_alone(
    hosted_zone_id: str | None, acm_certificate_arn: str | None
) -> None:
    validation.validate_domain_name(
        "app.example.com", hosted_zone_id=hosted_zone_id, acm_certificate_arn=acm_certificate_arn
    )


@pytest.mark.parametrize(
    ("hosted_zone_id", "acm_certificate_arn"),
    [(None, None), (ZONE_ID, CERTIFICATE_ARN)],
    ids=["neither", "both"],
)
def test_domain_name_rejects_any_number_of_certificate_sources_but_one(
    hosted_zone_id: str | None, acm_certificate_arn: str | None
) -> None:
    with raises("With domain_name set, set exactly one of hosted_zone_id"):
        validation.validate_domain_name(
            "app.example.com", hosted_zone_id=hosted_zone_id, acm_certificate_arn=acm_certificate_arn
        )


@pytest.mark.parametrize(
    ("hosted_zone_id", "acm_certificate_arn"),
    [(ZONE_ID, None), (None, CERTIFICATE_ARN), (ZONE_ID, CERTIFICATE_ARN)],
    ids=["zone", "certificate", "both"],
)
def test_domain_name_rejects_a_certificate_source_without_a_domain(
    hosted_zone_id: str | None, acm_certificate_arn: str | None
) -> None:
    with raises("The hosted_zone_id and acm_certificate_arn are only meaningful alongside domain_name"):
        validation.validate_domain_name(None, hosted_zone_id=hosted_zone_id, acm_certificate_arn=acm_certificate_arn)


# --- hosted_zone_id ----------------------------------------------------------


@pytest.mark.parametrize("hosted_zone_id", [None, ZONE_ID, "Z0123", "Z" + "A" * 31])
def test_hosted_zone_id_accepts_none_and_route53_zone_ids(hosted_zone_id: str | None) -> None:
    validation.validate_hosted_zone_id(hosted_zone_id)


@pytest.mark.parametrize(
    "hosted_zone_id",
    [
        "example.com",
        "arn:aws:route53:::hostedzone/Z1D633PJN98FT9",
        "/hostedzone/Z1D633PJN98FT9",
        "z1d633pjn98ft9",
        "Z123",
        "Z" + "A" * 32,
        "",
    ],
)
def test_hosted_zone_id_rejects_names_arns_paths_and_malformed_ids(hosted_zone_id: str) -> None:
    with raises("The hosted_zone_id must be a Route 53 hosted zone id"):
        validation.validate_hosted_zone_id(hosted_zone_id)


# --- acm_certificate_arn -----------------------------------------------------


@pytest.mark.parametrize(
    "acm_certificate_arn",
    [None, CERTIFICATE_ARN, "arn:aws-us-gov:acm:us-east-1:123456789012:certificate/0123abcd"],
)
def test_acm_certificate_arn_accepts_none_and_us_east_1_certificates(acm_certificate_arn: str | None) -> None:
    validation.validate_acm_certificate_arn(acm_certificate_arn)


@pytest.mark.parametrize(
    "acm_certificate_arn",
    [
        "arn:aws:acm:eu-west-1:123456789012:certificate/abcd1234-ab12-cd34-ef56-abcdef123456",
        "arn:aws:acm:us-east-1:12345:certificate/abcd1234",
        "arn:aws:acm:us-east-1:123456789012:certificate/ABCD",
        "arn:aws:iam::123456789012:server-certificate/example",
        "abcd1234-ab12-cd34-ef56-abcdef123456",
        "",
    ],
)
def test_acm_certificate_arn_rejects_other_regions_and_shapes(acm_certificate_arn: str) -> None:
    with raises("The acm_certificate_arn must be an ACM certificate ARN in us-east-1"):
        validation.validate_acm_certificate_arn(acm_certificate_arn)


# --- log_retention_days ------------------------------------------------------


@pytest.mark.parametrize("log_retention_days", sorted(validation.LOG_RETENTION_DAYS))
def test_log_retention_days_accepts_every_cloudwatch_period(log_retention_days: int) -> None:
    validation.validate_log_retention_days(log_retention_days)


@pytest.mark.parametrize("log_retention_days", [0, 2, 31, 100, 3654, -1])
def test_log_retention_days_rejects_periods_cloudwatch_refuses(log_retention_days: int) -> None:
    with raises("The log_retention_days must be one of the retention periods CloudWatch Logs accepts"):
        validation.validate_log_retention_days(log_retention_days)


# --- github_oidc_provider_arn ------------------------------------------------


@pytest.mark.parametrize(
    "github_oidc_provider_arn",
    [
        OIDC_PROVIDER_ARN,
        "arn:aws-cn:iam::000000000000:oidc-provider/token.actions.githubusercontent.com",
    ],
)
def test_github_oidc_provider_arn_accepts_the_full_provider_arn(github_oidc_provider_arn: str) -> None:
    validation.validate_github_oidc_provider_arn(github_oidc_provider_arn)


@pytest.mark.parametrize(
    "github_oidc_provider_arn",
    [
        "https://token.actions.githubusercontent.com",
        "token.actions.githubusercontent.com",
        "arn:aws:iam::12345:oidc-provider/token.actions.githubusercontent.com",
        "arn:aws:iam::123456789012:oidc-provider/token-actions.githubusercontent.com",
        "arn:aws:iam::123456789012:oidc-provider/example.com",
        "arn:aws:iam::123456789012:role/react-cloudfront-app-deploy-stage",
        "",
    ],
)
def test_github_oidc_provider_arn_rejects_the_issuer_url_and_other_arns(github_oidc_provider_arn: str) -> None:
    with raises("The github_oidc_provider_arn must be the full ARN of this account's GitHub Actions OIDC provider"):
        validation.validate_github_oidc_provider_arn(github_oidc_provider_arn)


# --- app_github_owner --------------------------------------------------------


@pytest.mark.parametrize("app_github_owner", ["isai2105", "tftest-owner", "A", "a-b-c", "a" * 39])
def test_app_github_owner_accepts_github_account_names(app_github_owner: str) -> None:
    validation.validate_app_github_owner(app_github_owner)


@pytest.mark.parametrize("app_github_owner", ["-owner", "owner-", "own--er", "own_er", "own.er", "a" * 40, ""])
def test_app_github_owner_rejects_names_github_would_refuse(app_github_owner: str) -> None:
    with raises("The app_github_owner must be a valid GitHub account name"):
        validation.validate_app_github_owner(app_github_owner)


# --- app_github_owner_id / app_github_repository_id ---------------------------


@pytest.mark.parametrize("value", ["1", "22457760", "0"])
def test_numeric_github_ids_accept_digits(value: str) -> None:
    validation.validate_app_github_owner_id(value)
    validation.validate_app_github_repository_id(value)


@pytest.mark.parametrize("value", ["isai2105", "1e3", "-1", "1 ", " 1", ""])
def test_app_github_owner_id_rejects_anything_but_digits(value: str) -> None:
    with raises("The app_github_owner_id must be the numeric account id, digits only"):
        validation.validate_app_github_owner_id(value)


@pytest.mark.parametrize("value", ["react-cloudfront-app", "1e3", "-1", "1 ", " 1", ""])
def test_app_github_repository_id_rejects_anything_but_digits(value: str) -> None:
    with raises("The app_github_repository_id must be the numeric repository id, digits only"):
        validation.validate_app_github_repository_id(value)


# --- app_deploy_boundary_policy_name -----------------------------------------


@pytest.mark.parametrize(
    "app_deploy_boundary_policy_name",
    ["isai2105-app-deploy-boundary", "a", "A+=,.@_-b", "x" * 128],
)
def test_app_deploy_boundary_policy_name_accepts_iam_policy_names(app_deploy_boundary_policy_name: str) -> None:
    validation.validate_app_deploy_boundary_policy_name(app_deploy_boundary_policy_name)


@pytest.mark.parametrize(
    "app_deploy_boundary_policy_name",
    [
        "arn:aws:iam::123456789012:policy/isai2105-app-deploy-boundary",
        "/isai2105-app-deploy-boundary",
        "with space",
        "x" * 129,
        "",
    ],
)
def test_app_deploy_boundary_policy_name_rejects_arns_paths_and_bad_lengths(
    app_deploy_boundary_policy_name: str,
) -> None:
    with raises("The app_deploy_boundary_policy_name must be a valid IAM policy name"):
        validation.validate_app_deploy_boundary_policy_name(app_deploy_boundary_policy_name)


# --- StaticSiteArgs ----------------------------------------------------------


def test_static_site_args_defaults_are_the_variables_tf_defaults() -> None:
    assert VALID_ARGS.force_destroy is False
    assert VALID_ARGS.seed_placeholder is True
    assert VALID_ARGS.domain_name is None
    assert VALID_ARGS.hosted_zone_id is None
    assert VALID_ARGS.acm_certificate_arn is None
    assert VALID_ARGS.log_retention_days == 30


def test_static_site_args_required_fields_carry_no_default() -> None:
    required = {field.name for field in dataclasses.fields(StaticSiteArgs) if field.default is dataclasses.MISSING}
    assert required == {
        "name_prefix",
        "environment",
        "github_oidc_provider_arn",
        "app_github_owner",
        "app_github_owner_id",
        "app_github_repository_id",
        "app_deploy_boundary_policy_name",
    }


def test_static_site_args_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        VALID_ARGS.force_destroy = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "bad_value", "fragment"),
    [
        ("name_prefix", "Abc", "The name_prefix must be lower case letters"),
        ("environment", "Stage", "The environment must be lower case letters"),
        ("domain_name", "*.example.com", "The domain_name must be a bare fully qualified domain name"),
        ("hosted_zone_id", "example.com", "The hosted_zone_id must be a Route 53 hosted zone id"),
        (
            "acm_certificate_arn",
            "arn:aws:acm:eu-west-1:123456789012:certificate/abcd",
            "must be an ACM certificate ARN in us-east-1",
        ),
        ("log_retention_days", 31, "The log_retention_days must be one of the retention periods"),
        (
            "github_oidc_provider_arn",
            "https://token.actions.githubusercontent.com",
            "The github_oidc_provider_arn must be the full ARN",
        ),
        ("app_github_owner", "own--er", "The app_github_owner must be a valid GitHub account name"),
        ("app_github_owner_id", "isai2105", "The app_github_owner_id must be the numeric account id"),
        (
            "app_github_repository_id",
            "react-cloudfront-app",
            "The app_github_repository_id must be the numeric repository id",
        ),
        (
            "app_deploy_boundary_policy_name",
            "with space",
            "The app_deploy_boundary_policy_name must be a valid IAM policy name",
        ),
    ],
)
def test_static_site_args_validates_every_field_on_construction(field: str, bad_value: object, fragment: str) -> None:
    """Every validator is wired to its field: one refused value per field reaches its own rule's message.

    ``hosted_zone_id`` and ``acm_certificate_arn`` are given a domain so the shape rule, not the mode rule, is
    the one that fires; the mode rule has its own tests below.
    """
    domain = {"domain_name": "app.example.com"} if field in {"hosted_zone_id", "acm_certificate_arn"} else {}
    with raises(fragment):
        construct(**domain, **{field: bad_value})


@pytest.mark.parametrize(
    ("field", "bad_value", "expected"),
    [
        ("name_prefix", None, "a str"),
        ("environment", 1, "a str"),
        ("force_destroy", "false", "a bool"),
        ("force_destroy", 0, "a bool"),
        ("seed_placeholder", "true", "a bool"),
        ("domain_name", 1, "a str or None"),
        ("hosted_zone_id", b"Z1D633PJN98FT9", "a str or None"),
        ("acm_certificate_arn", ["arn"], "a str or None"),
        ("log_retention_days", True, "an int"),
        ("log_retention_days", 30.0, "an int"),
        ("log_retention_days", "30", "an int"),
        ("github_oidc_provider_arn", None, "a str"),
        ("app_github_owner", None, "a str"),
        ("app_github_owner_id", 1, "a str"),
        ("app_github_repository_id", 2, "a str"),
        ("app_deploy_boundary_policy_name", None, "a str"),
    ],
)
def test_static_site_args_refuses_a_wrongly_typed_field_by_name(field: str, bad_value: object, expected: str) -> None:
    """A ``pulumi.Config.get()`` string in a bool field, or a bool in the int field, is a TypeError naming the field."""
    with pytest.raises(TypeError, match=re.escape(f"The {field} must be {expected}, got {type(bad_value).__name__}")):
        construct(**{field: bad_value})


def test_static_site_args_accepts_each_custom_domain_mode() -> None:
    dataclasses.replace(VALID_ARGS, domain_name="app.example.com", hosted_zone_id=ZONE_ID)
    dataclasses.replace(VALID_ARGS, domain_name="app.example.com", acm_certificate_arn=CERTIFICATE_ARN)


def test_a_domain_with_no_certificate_source_is_refused() -> None:
    with raises("With domain_name set, set exactly one of hosted_zone_id"):
        dataclasses.replace(VALID_ARGS, domain_name="app.example.com")


def test_a_certificate_source_with_no_domain_is_refused() -> None:
    with raises("The hosted_zone_id and acm_certificate_arn are only meaningful alongside domain_name"):
        dataclasses.replace(VALID_ARGS, hosted_zone_id=ZONE_ID)
