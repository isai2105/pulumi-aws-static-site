"""The ``StaticSite`` component resource: the port of ``modules/static-site``.

Phase 0 of the implementation plan: the component takes its final interface — a name, a validated
``StaticSiteArgs`` and the two ``aws.Provider`` objects ``modules/static-site/versions.tf`` declares as
``configuration_aliases`` — and registers no child resources. Phase 2 adds the children in the order the
``.tf`` files declare them, each with ``parent=self`` and the provider it needs, and fills ``register_outputs``.
"""

import pulumi
import pulumi_aws as aws

from static_site.args import StaticSiteArgs

COMPONENT_TYPE = "static-site:index:StaticSite"
"""The type token of every ``StaticSite`` URN: ``<package>:<module>:<type>``, as the Pulumi registry shapes it."""


class StaticSite(pulumi.ComponentResource):
    """A private S3 bucket served through CloudFront over an Origin Access Control, with access logs in CloudWatch.

    Two providers rather than a ``ResourceOptions.providers`` map, because that map holds one provider per
    package and this component needs two of the same package: the environment's own region for everything
    that lives there, and us-east-1 for what CloudFront's control plane accepts only there — the viewer
    certificate and the access-log delivery.
    """

    provider: aws.Provider
    """The provider for the environment's own region: the bucket, the distribution and its policies, the
    SSM parameters and the deploy role."""

    us_east_1_provider: aws.Provider
    """The us-east-1 provider: the access-log group and its delivery, and the viewer certificate when the
    component requests one."""

    def __init__(
        self,
        name: str,
        args: StaticSiteArgs,
        *,
        provider: aws.Provider,
        us_east_1_provider: aws.Provider,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__(COMPONENT_TYPE, name, None, opts)
        self.provider = provider
        self.us_east_1_provider = us_east_1_provider
        self.register_outputs({})
