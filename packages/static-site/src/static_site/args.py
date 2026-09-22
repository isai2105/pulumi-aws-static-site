"""Inputs to the ``StaticSite`` component, one field per ``modules/static-site/variables.tf`` variable.

Nothing here carries a default that encodes an environment-specific choice. A default is a decision made
silently, and a silent decision is how stage configuration reaches prod. The one value that carries a default
for a decision a caller might get wrong is ``force_destroy``, and its default is the refusing one.

The component deliberately exposes no knob for anything it can decide correctly on its own. A field is an
interface: every one of them is a value some caller can get wrong, has to be documented, and has to keep
working. Region, price class, protocol policy and the cache behaviour are properties of "a static site served
from S3 through CloudFront", not properties of a particular deployment of one.

Every field is a plain ``str``, ``bool`` or ``int`` rather than a ``pulumi.Input``: every value is known at
construction (stack config, the stack name, one composed ARN), which is what lets ``validation`` and ``naming``
stay pure functions and the credential-free preview stay invoke-free. ``__post_init__`` first refuses a value
of the wrong type with a ``TypeError`` naming the field — the analogue of Terraform's ``type = bool``, and the
only thing that stops a ``pulumi.Config.get()`` string such as ``"false"`` from landing in a boolean field and
reading as true — and then runs every validation, so an instance that exists is one every rule accepted: the
analogue of a variable ``validation`` block, which refuses the value before any resource is planned.

:meth:`StaticSiteArgs.from_stack_config` builds the instance from the stack's ten ``static-site:*`` keys
(:mod:`static_site.stack_config`), which is how ``envs/__main__.py`` and ``tests/offline/__main__.py`` construct it.

The string literal under each field is its documentation: ``scripts/gen_interface.py`` reads these literals to
render the interface table in ``packages/static-site/README.md``, so a field without one fails ``make docs``.
"""

from dataclasses import dataclass
from typing import Self

from static_site import validation
from static_site.stack_config import StackConfig


@dataclass(frozen=True, kw_only=True)
class StaticSiteArgs:
    """Arguments of one ``StaticSite`` instance, validated on construction."""

    name_prefix: str
    """Prefix for the globally unique site bucket name, which is composed as
    `<name_prefix>-site-<environment>-<8 hex characters>`. This must be the same
    value the bootstrap project was applied with: the CI apply role's S3 grant is
    scoped to `<name_prefix>-site-*`, so a bucket named outside that pattern
    cannot be created by CI."""

    environment: str
    """Name of the environment this instance of the component belongs to, for example "stage" or "prod". It
    appears in the bucket name, in the delivery and log group names, and in the SSM parameter path the app
    repository reads."""

    force_destroy: bool = False
    """Whether `pulumi destroy` may delete the site bucket while it still holds
    objects. Defaults to false, which is the correct value for any environment
    whose contents are worth more than the convenience of tearing it down.

    Every environment in this repository sets it to true, because every
    environment here is ephemeral and a bucket the app repository has deployed
    into refuses to delete otherwise. That is a property of this operating model,
    not of the component, which is exactly why it is an input."""

    seed_placeholder: bool = True
    """Whether to seed the bucket with a placeholder `index.html` so the site is
    servable before any application build is deployed to it. Defaults to true.

    Set it to false only where something else is guaranteed to have written that
    key first. An empty bucket does not serve an empty page — it serves the
    origin's 403, on every route rather than only on the homepage, because the
    routing here answers a client-side route with that one missing document."""

    domain_name: str | None = None
    """Fully qualified domain name to serve the site from, for example
    "app.example.com". Leave None — the default — to serve from the
    distribution's own `*.cloudfront.net` hostname on the default CloudFront
    certificate, which requires no domain and works in any account.

    When set, exactly one of hosted_zone_id or acm_certificate_arn must be set
    too: the first has this component request and validate a certificate through
    Route 53, the second attaches a certificate the caller has already issued.

    One exact name, never a wildcard. `*.example.com` is rejected deliberately:
    a wildcard certificate covers the subdomains and not the apex, so making it
    useful means adding example.com as a subject alternative name — and this
    component has no SANs by design, which is what lets the validation record be
    read as the single element of the certificate's validation options rather
    than a loop over a set that is not known until the certificate exists (see
    the certificate module). Accepting a wildcard here would advertise half a
    feature the rest of the file cannot complete. A caller who needs one issues the
    certificate themselves and attaches it through acm_certificate_arn, which is
    exactly what that mode is for."""

    hosted_zone_id: str | None = None
    """Id of the Route 53 public hosted zone holding domain_name, when this component
    should request and validate the certificate itself. Requires that the zone
    lives in the same AWS account.

    Leave None and set acm_certificate_arn instead when the domain's DNS is
    anywhere else — a registrar's nameservers, another provider, another account."""

    acm_certificate_arn: str | None = None
    """ARN of an existing, already-issued ACM certificate covering domain_name, for
    the case where this component cannot validate one itself because the domain's
    DNS is not in Route 53 in this account.

    It must live in us-east-1 whatever region the environment deploys to, because
    that is the only region CloudFront reads viewer certificates from. This
    component does not manage its lifecycle: it is not created, renewed, tagged or
    destroyed here, and it survives `pulumi destroy`."""

    log_retention_days: int = 30
    """How long CloudFront access logs are retained in CloudWatch Logs. Applies to the log group this component
    creates; the log group is destroyed with the environment, so this bounds a window that in practice closes
    far sooner."""

    github_oidc_provider_arn: str
    """ARN of the GitHub Actions OIDC provider in this account, which the deploy
    role's trust policy names as its federated principal.

    Composed by the caller from the partition and the account id
    (`arn:<partition>:iam::<account id>:oidc-provider/token.actions.githubusercontent.com`),
    never looked up by URL: `aws.iam.get_open_id_connect_provider(url=...)` calls
    `ListOpenIDConnectProviders` and scans the result, and that action takes no
    resource constraint — so granting it would mean an account-wide
    `Resource: "*"` on the plan role, which runs untrusted pull-request code.

    It arrives as an input rather than being resolved here because resolving it
    is an API call, and the component's credential-free preview reaches no AWS
    account."""

    app_github_owner: str
    """GitHub user or organisation that owns the app repository — the repository
    that deploys into this environment, not this one. Half of the OIDC subject
    the deploy role trusts.

    Deliberately a separate input from the environment project's `githubOwner`,
    which names the owner of *this* repository and feeds the Owner and Repo
    tags. The two hold the same string today by coincidence rather than by
    construction: nothing requires the two repositories to share an owner, and
    wiring this from the tag value would mean transferring this repository to an
    organisation silently rewrote a trust policy in another one. It is also the
    half of an identity whose other half — app_github_owner_id — is already an
    input here, and one identity taken from two sources is two values that can
    disagree."""

    app_github_owner_id: str
    """Numeric id of the GitHub account in app_github_owner. Embedded in the OIDC subject the deploy role
    trusts, in GitHub's immutable subject format, and not interchangeable with the account name."""

    app_github_repository_id: str
    """Numeric id of the app repository. Embedded in the OIDC subject the deploy role trusts, alongside the
    repository name that the naming module holds as a constant."""

    app_deploy_boundary_policy_name: str
    """Name of the permissions boundary the deploy role must carry, published by
    the bootstrap project as its `appDeployBoundaryPolicyName` output.

    The name rather than the ARN, because a name is the same string in every
    account and an ARN is not: this value reaches the component through a
    committed `Pulumi.<stack>.yaml`, and a policy ARN embeds the account id. The
    naming module composes the ARN back from the partition, the account id and
    this name.

    It is an input rather than a re-derivation of the bootstrap's
    `<name_prefix>-app-deploy-boundary`, because the bootstrap publishes it as an
    output precisely so that no other project has to know how it is built. A
    mismatch here is not a preview error — it is an AccessDenied on CreateRole,
    from a condition naming an ARN that appears nowhere in the diff."""

    @classmethod
    def from_stack_config(
        cls,
        config: StackConfig,
        *,
        github_oidc_provider_arn: str,
        seed_placeholder: bool = True,
        domain_name: str | None = None,
        hosted_zone_id: str | None = None,
        acm_certificate_arn: str | None = None,
        log_retention_days: int = 30,
    ) -> Self:
        """The arguments one stack's ``static-site:*`` config stands for, plus what the config does not carry.

        The config supplies ``name_prefix``, ``environment`` (the stack name), ``force_destroy`` and the four
        app-deploy identity values. ``github_oidc_provider_arn`` is composed by the caller from the partition and
        the account id — an ARN embedding an account id is never committed — and the optional custom-domain
        inputs are the caller's choice per variant.
        """
        return cls(
            name_prefix=config.name_prefix,
            environment=config.environment,
            force_destroy=config.force_destroy,
            seed_placeholder=seed_placeholder,
            domain_name=domain_name,
            hosted_zone_id=hosted_zone_id,
            acm_certificate_arn=acm_certificate_arn,
            log_retention_days=log_retention_days,
            github_oidc_provider_arn=github_oidc_provider_arn,
            app_github_owner=config.app_github_owner,
            app_github_owner_id=config.app_github_owner_id,
            app_github_repository_id=config.app_github_repository_id,
            app_deploy_boundary_policy_name=config.app_deploy_boundary_policy_name,
        )

    def __post_init__(self) -> None:
        """Refuse a wrongly typed field, then apply every ``variables.tf`` validation block; the first failure raises.

        The type guards come first because the validators assume the types they are annotated with, and a
        value of the wrong type must be told so by the field's name rather than by ``re``.
        """
        _require_str("name_prefix", self.name_prefix)
        _require_str("environment", self.environment)
        _require_bool("force_destroy", self.force_destroy)
        _require_bool("seed_placeholder", self.seed_placeholder)
        _require_optional_str("domain_name", self.domain_name)
        _require_optional_str("hosted_zone_id", self.hosted_zone_id)
        _require_optional_str("acm_certificate_arn", self.acm_certificate_arn)
        _require_int("log_retention_days", self.log_retention_days)
        _require_str("github_oidc_provider_arn", self.github_oidc_provider_arn)
        _require_str("app_github_owner", self.app_github_owner)
        _require_str("app_github_owner_id", self.app_github_owner_id)
        _require_str("app_github_repository_id", self.app_github_repository_id)
        _require_str("app_deploy_boundary_policy_name", self.app_deploy_boundary_policy_name)

        validation.validate_name_prefix(self.name_prefix)
        validation.validate_environment(self.environment, name_prefix=self.name_prefix)
        validation.validate_domain_name(
            self.domain_name,
            hosted_zone_id=self.hosted_zone_id,
            acm_certificate_arn=self.acm_certificate_arn,
        )
        validation.validate_hosted_zone_id(self.hosted_zone_id)
        validation.validate_acm_certificate_arn(self.acm_certificate_arn)
        validation.validate_log_retention_days(self.log_retention_days)
        validation.validate_github_oidc_provider_arn(self.github_oidc_provider_arn)
        validation.validate_app_github_owner(self.app_github_owner)
        validation.validate_app_github_owner_id(self.app_github_owner_id)
        validation.validate_app_github_repository_id(self.app_github_repository_id)
        validation.validate_app_deploy_boundary_policy_name(self.app_deploy_boundary_policy_name)


def _type_error(field: str, value: object, expected: str) -> TypeError:
    return TypeError(f"The {field} must be {expected}, got {type(value).__name__} {value!r}.")


def _require_str(field: str, value: object) -> None:
    if not isinstance(value, str):
        raise _type_error(field, value, "a str")


def _require_optional_str(field: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise _type_error(field, value, "a str or None")


def _require_bool(field: str, value: object) -> None:
    # `type(...) is bool` rather than isinstance: nothing else is a bool, and a str such as "false" is truthy.
    if type(value) is not bool:
        raise _type_error(field, value, "a bool")


def _require_int(field: str, value: object) -> None:
    # `type(...) is int` rather than isinstance: bool is a subclass of int, and True would pass as 1.
    if type(value) is not int:
        raise _type_error(field, value, "an int")
