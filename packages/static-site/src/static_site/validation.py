"""The ``validation {}`` blocks of ``modules/static-site/variables.tf``, as pure functions.

One function per variable that carries validation, applying that variable's blocks in the order the ``.tf``
file declares them and raising ``ValueError`` with the same message at the first failure. Every function
takes plain values and touches nothing else, so ``tests/unit/test_validation.py`` exercises each rule with no
Pulumi runtime, and ``StaticSiteArgs.__post_init__`` is the one caller that wires them to fields.

The rules refuse at construction what AWS would otherwise refuse at apply with a message naming no input:
an ``InvalidBucketName`` for a name composed past 63 characters, an ``InvalidViewerCertificate`` for a
certificate outside us-east-1, an ``InvalidParameterException`` quoting the retention list back. Each
message therefore names the field and the constraint.
"""

import re
from typing import Final

_NAME_PREFIX_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
_NAME_PREFIX_MIN_LENGTH: Final = 3
_NAME_PREFIX_MAX_LENGTH: Final = 40

_ENVIRONMENT_PATTERN: Final = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")

# "<name_prefix>-site-<environment>-<8 hex>" must stay within S3's 63-character bucket name limit: 63 minus
# "-site-" (6) minus "-" plus the 8-character suffix (9) leaves 48 for the two caller-supplied parts together.
_COMPOSED_NAME_BUDGET: Final = 48

_DOMAIN_NAME_PATTERN: Final = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# ACM caps a certificate's common name at 64 characters, and the domain name becomes one.
_DOMAIN_NAME_MAX_LENGTH: Final = 64

_HOSTED_ZONE_ID_PATTERN: Final = re.compile(r"^Z[A-Z0-9]{4,31}$")

# The region is pinned in the pattern rather than merely documented, because it is the mistake this input
# exists to invite: CloudFront reads viewer certificates from us-east-1 only.
_ACM_CERTIFICATE_ARN_PATTERN: Final = re.compile(r"^arn:aws[a-z-]*:acm:us-east-1:[0-9]{12}:certificate/[0-9a-f-]+$")

# The whole ARN rather than a prefix, and load-bearing rather than defensive: the naming module reads the account id
# out of the fifth field of this string, and the regex is what guarantees that field is a 12-digit account id.
_GITHUB_OIDC_PROVIDER_ARN_PATTERN: Final = re.compile(
    r"^arn:aws[a-z-]*:iam::[0-9]{12}:oidc-provider/token\.actions\.githubusercontent\.com$"
)

_GITHUB_ACCOUNT_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9](-?[A-Za-z0-9])*$")
_GITHUB_ACCOUNT_NAME_MAX_LENGTH: Final = 39

_DIGITS_PATTERN: Final = re.compile(r"^[0-9]+$")

_IAM_POLICY_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,128}$")

LOG_RETENTION_DAYS: Final[frozenset[int]] = frozenset(
    {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653}
)
"""The retention periods CloudWatch Logs accepts for a log group; anything else is refused at apply."""


def validate_name_prefix(name_prefix: str) -> None:
    """``variables.tf`` ``name_prefix``: the 3-to-40 length bound, then the bucket-name character class.

    Two checks rather than one, because they fail for unrelated reasons and a reader fixing the second should
    not have to re-read the first. Terraform reports both; a function raises once, so the length bound goes
    first: the character-class pattern needs two characters, and a one-character value would otherwise be
    told it broke a rule it satisfies as worded.
    """
    if not _NAME_PREFIX_MIN_LENGTH <= len(name_prefix) <= _NAME_PREFIX_MAX_LENGTH:
        raise ValueError(
            "The name_prefix must be between 3 and 40 characters. The composed bucket name adds "
            '"-site-", the environment name and a 9-character suffix on top of it, and S3 caps a bucket name '
            "at 63."
        )
    if not _NAME_PREFIX_PATTERN.fullmatch(name_prefix):
        raise ValueError(
            "The name_prefix must be lower case letters, digits and hyphens, and must start and end with a "
            "letter or digit. Dots are excluded deliberately: they break virtual-hosted-style TLS against the "
            "bucket."
        )


def validate_environment(environment: str, *, name_prefix: str) -> None:
    """``variables.tf`` ``environment``: the character class, then the composed bucket-name length.

    The same character class the bootstrap requires of its ``environments`` list, because this value appears
    verbatim in an S3 bucket name, a CloudWatch Logs delivery name and an OIDC subject. The optional tail keeps
    a one-character name acceptable, as the bootstrap admits one. The length check needs ``name_prefix`` too:
    the two share one 63-character budget and neither can be validated against it alone.
    """
    if not _ENVIRONMENT_PATTERN.fullmatch(environment):
        raise ValueError(
            "The environment must be lower case letters, digits and hyphens, must start with a letter, and "
            "must not end with a hyphen."
        )
    if len(name_prefix) + len(environment) > _COMPOSED_NAME_BUDGET:
        raise ValueError(
            "The name_prefix and environment are too long together: their combined length must be 48 "
            'characters or fewer, so that "<name_prefix>-site-<environment>-<8 hex>" stays within the '
            "63-character S3 bucket name limit."
        )


def validate_domain_name(
    domain_name: str | None,
    *,
    hosted_zone_id: str | None,
    acm_certificate_arn: str | None,
) -> None:
    """``variables.tf`` ``domain_name``: shape, length, then the two directions of the certificate-mode rule.

    The shape check is deliberately not a full RFC 1035 grammar: it catches the class of mistake that fails
    confusingly much later — a scheme, a path, a trailing dot, an underscore, a bare label, or a wildcard.
    The mode rule is checked here rather than on either companion, so a caller who set none of this gets one
    error naming the whole interface instead of two errors describing halves of it; both directions are
    checked because both mistakes are real, and the quieter one — a zone or certificate with no domain — would
    otherwise be silently ignored.
    """
    if domain_name is not None:
        if not _DOMAIN_NAME_PATTERN.fullmatch(domain_name):
            raise ValueError(
                'The domain_name must be a bare fully qualified domain name in lower case — "app.example.com", '
                'not "https://app.example.com", not "app.example.com." and not a single label. A wildcard such '
                'as "*.example.com" is rejected on purpose: this component issues no subject alternative names, '
                "so it cannot also cover the apex. Issue a wildcard certificate yourself and attach it with "
                "acm_certificate_arn."
            )
        if len(domain_name) > _DOMAIN_NAME_MAX_LENGTH:
            raise ValueError(
                "The domain_name must be 64 characters or fewer, which is the limit ACM places on a "
                "certificate's common name."
            )
        if (hosted_zone_id is not None) == (acm_certificate_arn is not None):
            raise ValueError(
                "With domain_name set, set exactly one of hosted_zone_id (this component requests and validates "
                "the certificate through Route 53) or acm_certificate_arn (the certificate already exists, which "
                "is the path for a domain whose DNS is not in Route 53 in this account)."
            )
    elif hosted_zone_id is not None or acm_certificate_arn is not None:
        raise ValueError(
            "The hosted_zone_id and acm_certificate_arn are only meaningful alongside domain_name. Set "
            "domain_name, or unset both."
        )


def validate_hosted_zone_id(hosted_zone_id: str | None) -> None:
    """``variables.tf`` ``hosted_zone_id``: a Route 53 zone id — never the zone's name, never its ARN."""
    if hosted_zone_id is not None and not _HOSTED_ZONE_ID_PATTERN.fullmatch(hosted_zone_id):
        raise ValueError(
            'The hosted_zone_id must be a Route 53 hosted zone id — a leading "Z" followed by upper case '
            'letters and digits, for example "Z1D633PJN98FT9". It is not the zone\'s name, and it is not the '
            "zone ARN."
        )


def validate_acm_certificate_arn(acm_certificate_arn: str | None) -> None:
    """``variables.tf`` ``acm_certificate_arn``: an ACM certificate ARN whose region is us-east-1."""
    if acm_certificate_arn is not None and not _ACM_CERTIFICATE_ARN_PATTERN.fullmatch(acm_certificate_arn):
        raise ValueError(
            "The acm_certificate_arn must be an ACM certificate ARN in us-east-1, for example "
            '"arn:aws:acm:us-east-1:123456789012:certificate/abcd1234-...". CloudFront reads viewer '
            "certificates from us-east-1 only, whatever region the rest of the environment is in."
        )


def validate_log_retention_days(log_retention_days: int) -> None:
    """``variables.tf`` ``log_retention_days``: one of the periods CloudWatch Logs accepts."""
    if log_retention_days not in LOG_RETENTION_DAYS:
        raise ValueError(
            "The log_retention_days must be one of the retention periods CloudWatch Logs accepts: 1, 3, 5, 7, "
            "14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288 or 3653."
        )


def validate_github_oidc_provider_arn(github_oidc_provider_arn: str) -> None:
    """``variables.tf`` ``github_oidc_provider_arn``: the full provider ARN, which also refuses the issuer URL."""
    if not _GITHUB_OIDC_PROVIDER_ARN_PATTERN.fullmatch(github_oidc_provider_arn):
        raise ValueError(
            "The github_oidc_provider_arn must be the full ARN of this account's GitHub Actions OIDC provider, "
            'for example "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com". '
            "Compose it from the partition and the account id; it is not the issuer URL."
        )


def validate_app_github_owner(app_github_owner: str) -> None:
    """``variables.tf`` ``app_github_owner``: a GitHub account name."""
    if (
        not _GITHUB_ACCOUNT_NAME_PATTERN.fullmatch(app_github_owner)
        or len(app_github_owner) > _GITHUB_ACCOUNT_NAME_MAX_LENGTH
    ):
        raise ValueError(
            "The app_github_owner must be a valid GitHub account name: alphanumerics separated by single "
            "hyphens, 39 characters or fewer."
        )


def validate_app_github_owner_id(app_github_owner_id: str) -> None:
    """``variables.tf`` ``app_github_owner_id``: digits only."""
    if not _DIGITS_PATTERN.fullmatch(app_github_owner_id):
        raise ValueError(
            "The app_github_owner_id must be the numeric account id, digits only. Read it with: "
            "gh api repos/<owner>/react-cloudfront-app --jq .owner.id"
        )


def validate_app_github_repository_id(app_github_repository_id: str) -> None:
    """``variables.tf`` ``app_github_repository_id``: digits only."""
    if not _DIGITS_PATTERN.fullmatch(app_github_repository_id):
        raise ValueError(
            "The app_github_repository_id must be the numeric repository id, digits only. Read it with: "
            "gh api repos/<owner>/react-cloudfront-app --jq .id"
        )


def validate_app_deploy_boundary_policy_name(app_deploy_boundary_policy_name: str) -> None:
    """``variables.tf`` ``app_deploy_boundary_policy_name``: an IAM policy name — not an ARN, not a path."""
    if not _IAM_POLICY_NAME_PATTERN.fullmatch(app_deploy_boundary_policy_name):
        raise ValueError(
            "The app_deploy_boundary_policy_name must be a valid IAM policy name: letters, digits and the "
            "characters +=,.@_- , 128 characters or fewer. It is the policy's name, not its ARN and not a path."
        )
