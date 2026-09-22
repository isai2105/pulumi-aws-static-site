"""``static_site``: the Pulumi port of ``terraform-aws-static-site``'s ``modules/static-site``.

A private S3 bucket served through CloudFront over an Origin Access Control, with access logging delivered to
CloudWatch Logs and an OIDC-trusted deploy role for the app repository. The component is ``StaticSite``; its
inputs are a ``StaticSiteArgs``, validated on construction, built from the stack's ``static-site:*`` config
through ``stack_config`` by every program that instantiates it.
"""

from static_site import stack_config
from static_site.args import StaticSiteArgs
from static_site.component import StaticSite

__all__ = ["StaticSite", "StaticSiteArgs", "stack_config"]
