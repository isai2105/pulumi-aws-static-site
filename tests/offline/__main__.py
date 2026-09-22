"""The credential-free preview program (LOCKED-3): ``StaticSite`` on fake-key providers.

``scripts/preview_offline.sh`` runs this against a throwaway ``file://`` backend
with the stack named ``test`` - so ``pulumi.get_stack()`` is the fixture
environment - and the ``plan.tftest.hcl`` fixtures written as stack config by one
``pulumi config set-all``: the same ten ``static-site:*`` keys, read through the
same ``static_site.stack_config.load()`` as ``envs/__main__.py`` and the policy
pack, so all three see one list of keys and one source of values.

The two providers carry placeholder keys and the three ``skip_*`` flags
(``plan.tftest.hcl:127-171``): the real ``pulumi-aws`` plugin runs its ``Check``
on every resource, and nothing reaches AWS - a mistaken ``pulumi up`` fails at
the provider instead of creating anything. Both carry the same ``default_tags``,
because an aliased provider inherits nothing and an untagged resource is
invisible to the teardown assertion (``tags.tf:70-96``).

``static-site-offline:variant`` selects the optional-domain branch
(``plan.tftest.hcl:603-660``): ``default`` (no custom domain - what every
environment in this repository deploys), ``managed-cert`` (domain + hosted zone,
the component requests the certificate) or ``supplied-cert`` (domain + a
caller-supplied ACM ARN). Phase 0's component declares no children; Phase 2 of
IMPLEMENTATION_PLAN.md fills it and ``test_preview.py`` asserts the result.
"""

from dataclasses import dataclass

import pulumi
import pulumi_aws as aws

from static_site import StaticSite, StaticSiteArgs, stack_config

GITHUB_OIDC_PROVIDER_ARN = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
"""The tftest's provider ARN fixture (``plan.tftest.hcl:187``). The account id in it is what the composed
permissions-boundary ARN is asserted against: this program holds fake keys, so nothing can ask STS."""

FIXTURE_DOMAIN_NAME = "app.example.com"
FIXTURE_HOSTED_ZONE_ID = "Z1D633PJN98FT9"
FIXTURE_ACM_CERTIFICATE_ARN = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555"


@dataclass(frozen=True, slots=True)
class DomainVariant:
    """The optional-domain inputs one ``static-site-offline:variant`` value stands for."""

    domain_name: str | None = None
    hosted_zone_id: str | None = None
    acm_certificate_arn: str | None = None


VARIANTS: dict[str, DomainVariant] = {
    "default": DomainVariant(),
    "managed-cert": DomainVariant(domain_name=FIXTURE_DOMAIN_NAME, hosted_zone_id=FIXTURE_HOSTED_ZONE_ID),
    "supplied-cert": DomainVariant(domain_name=FIXTURE_DOMAIN_NAME, acm_certificate_arn=FIXTURE_ACM_CERTIFICATE_ARN),
}
"""The three preview variants ``scripts/preview_offline.sh`` iterates; ``test_preview.py`` reads the same names."""


def select_variant(name: str) -> DomainVariant:
    """The :class:`DomainVariant` for ``static-site-offline:variant``, or a ``ValueError`` naming the valid ones."""
    try:
        return VARIANTS[name]
    except KeyError:
        raise ValueError(f"static-site-offline:variant must be one of {sorted(VARIANTS)}, got {name!r}") from None


def fake_provider(name: str, region: str, tags: dict[str, str]) -> aws.Provider:
    """An ``aws.Provider`` that never authenticates: placeholder keys and the three ``skip_*`` flags."""
    return aws.Provider(
        name,
        region=region,
        access_key="mock-access-key-id",
        secret_key="mock-secret-access-key",
        skip_credentials_validation=True,
        skip_requesting_account_id=True,
        skip_metadata_api_check=True,
        default_tags=aws.ProviderDefaultTagsArgs(tags=tags),
    )


def main() -> None:
    """Build the fixture ``StaticSite`` the way ``envs/__main__.py`` builds the real one."""
    config = stack_config.load()
    variant = select_variant(pulumi.Config().require("variant"))

    tags = config.default_tags()
    default_provider = fake_provider("aws", config.aws_region, tags)
    us_east_1_provider = fake_provider("aws-us-east-1", "us-east-1", tags)

    args = StaticSiteArgs.from_stack_config(
        config,
        github_oidc_provider_arn=GITHUB_OIDC_PROVIDER_ARN,
        domain_name=variant.domain_name,
        hosted_zone_id=variant.hosted_zone_id,
        acm_certificate_arn=variant.acm_certificate_arn,
    )
    StaticSite("site", args, provider=default_provider, us_east_1_provider=us_east_1_provider)


main()
