# `static-site`

A private S3 bucket served through CloudFront over an Origin Access Control, with
access logging delivered to CloudWatch Logs — the Pulumi port of
`terraform-aws-static-site`'s `modules/static-site`, as the `StaticSite`
component resource.

```
viewer ──HTTPS──▶ CloudFront distribution ──OAC/SigV4──▶ S3 bucket (private)
                          │
                          ├─ viewer-request function ─▶ a URI naming no file
                          │                             becomes /index.html
                          └─ standard logging (v2) ───▶ CloudWatch Logs group
```

The bucket has no website configuration, no public read and no ACLs. The only
grant on it names the CloudFront service principal and is conditioned on this
distribution's ARN, so it is unreachable except through the distribution.

## Using it

Every program that instantiates the component reads the same ten `static-site:*`
stack keys through `static_site.stack_config` — the environment project, the
credential-free preview and the policy pack alike — so a key renamed in one
place cannot drift from the others. What the config does not carry is passed
explicitly: the OIDC provider ARN (composed from the partition and the account
id, never committed and never looked up by URL) and the optional custom-domain
inputs.

```python
import pulumi
import pulumi_aws as aws

from static_site import StaticSite, StaticSiteArgs, stack_config

config = stack_config.load()  # pulumi.Config("static-site") + pulumi.get_stack(); raises naming a missing key

# Two providers, because the component needs two of the same package: the
# environment's own region for everything that lives there, and us-east-1 for
# what CloudFront's control plane accepts only there. Both carry the same
# default_tags: an aliased provider inherits nothing, the component checks the
# contract on both, and a resource tagged by neither is invisible to the
# teardown verification.
tags = aws.ProviderDefaultTagsArgs(tags=config.default_tags())  # Project, Env, Owner, Repo, ManagedBy
provider = aws.Provider("aws", region=config.aws_region, default_tags=tags)
us_east_1 = aws.Provider("aws-us-east-1", region="us-east-1", default_tags=tags)

# The OIDC provider ARN is composed, never committed: it embeds the account id.
# Both invokes run on the explicit provider so no implicit default provider
# appears in the stack.
invoke_opts = pulumi.InvokeOptions(provider=provider)
partition = aws.get_partition(opts=invoke_opts).partition
account_id = aws.get_caller_identity(opts=invoke_opts).account_id

site = StaticSite(
    "site",
    StaticSiteArgs.from_stack_config(
        config,
        github_oidc_provider_arn=(
            f"arn:{partition}:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"
        ),
    ),
    provider=provider,
    us_east_1_provider=us_east_1,
)
```

`StaticSiteArgs` can also be constructed directly, one keyword per input; the
table below lists them. Either way every field is checked on construction — a
wrongly typed value raises `TypeError`, a value the Terraform module would have
refused at `terraform plan` raises `ValueError` — each naming the field and the
constraint, before any resource is declared.

## Generated reference

Everything below is generated from `StaticSiteArgs` and the `StaticSite`
output attributes by `scripts/gen_interface.py`, and the `docs-check` job fails
any pull request where it has drifted from them. So editing a table by hand is
reverted by the next `make docs` and red until then. To change what it says,
edit the documentation string under the field or output in question and
regenerate:

```bash
make docs        # rewrite the block
make docs-check  # what CI runs; fails instead of rewriting
```

<!-- BEGIN_INTERFACE -->
## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| `app_deploy_boundary_policy_name` | Name of the permissions boundary the deploy role must carry, published by the bootstrap project as its `appDeployBoundaryPolicyName` output.<br/><br/>The name rather than the ARN, because a name is the same string in every account and an ARN is not: this value reaches the component through a committed `Pulumi.<stack>.yaml`, and a policy ARN embeds the account id. The naming module composes the ARN back from the partition, the account id and this name.<br/><br/>It is an input rather than a re-derivation of the bootstrap's `<name_prefix>-app-deploy-boundary`, because the bootstrap publishes it as an output precisely so that no other project has to know how it is built. A mismatch here is not a preview error — it is an AccessDenied on CreateRole, from a condition naming an ARN that appears nowhere in the diff. | `str` | n/a | yes |
| `app_github_owner` | GitHub user or organisation that owns the app repository — the repository that deploys into this environment, not this one. Half of the OIDC subject the deploy role trusts.<br/><br/>Deliberately a separate input from the environment project's `githubOwner`, which names the owner of *this* repository and feeds the Owner and Repo tags. The two hold the same string today by coincidence rather than by construction: nothing requires the two repositories to share an owner, and wiring this from the tag value would mean transferring this repository to an organisation silently rewrote a trust policy in another one. It is also the half of an identity whose other half — app_github_owner_id — is already an input here, and one identity taken from two sources is two values that can disagree. | `str` | n/a | yes |
| `app_github_owner_id` | Numeric id of the GitHub account in app_github_owner. Embedded in the OIDC subject the deploy role trusts, in GitHub's immutable subject format, and not interchangeable with the account name. | `str` | n/a | yes |
| `app_github_repository_id` | Numeric id of the app repository. Embedded in the OIDC subject the deploy role trusts, alongside the repository name that the naming module holds as a constant. | `str` | n/a | yes |
| `environment` | Name of the environment this instance of the component belongs to, for example "stage" or "prod". It appears in the bucket name, in the delivery and log group names, and in the SSM parameter path the app repository reads. | `str` | n/a | yes |
| `github_oidc_provider_arn` | ARN of the GitHub Actions OIDC provider in this account, which the deploy role's trust policy names as its federated principal.<br/><br/>Composed by the caller from the partition and the account id (`arn:<partition>:iam::<account id>:oidc-provider/token.actions.githubusercontent.com`), never looked up by URL: `aws.iam.get_open_id_connect_provider(url=...)` calls `ListOpenIDConnectProviders` and scans the result, and that action takes no resource constraint — so granting it would mean an account-wide `Resource: "*"` on the plan role, which runs untrusted pull-request code.<br/><br/>It arrives as an input rather than being resolved here because resolving it is an API call, and the component's credential-free preview reaches no AWS account. | `str` | n/a | yes |
| `name_prefix` | Prefix for the globally unique site bucket name, which is composed as `<name_prefix>-site-<environment>-<8 hex characters>`. This must be the same value the bootstrap project was applied with: the CI apply role's S3 grant is scoped to `<name_prefix>-site-*`, so a bucket named outside that pattern cannot be created by CI. | `str` | n/a | yes |
| `acm_certificate_arn` | ARN of an existing, already-issued ACM certificate covering domain_name, for the case where this component cannot validate one itself because the domain's DNS is not in Route 53 in this account.<br/><br/>It must live in us-east-1 whatever region the environment deploys to, because that is the only region CloudFront reads viewer certificates from. This component does not manage its lifecycle: it is not created, renewed, tagged or destroyed here, and it survives `pulumi destroy`. | `str \| None` | `None` | no |
| `domain_name` | Fully qualified domain name to serve the site from, for example "app.example.com". Leave None — the default — to serve from the distribution's own `*.cloudfront.net` hostname on the default CloudFront certificate, which requires no domain and works in any account.<br/><br/>When set, exactly one of hosted_zone_id or acm_certificate_arn must be set too: the first has this component request and validate a certificate through Route 53, the second attaches a certificate the caller has already issued.<br/><br/>One exact name, never a wildcard. `*.example.com` is rejected deliberately: a wildcard certificate covers the subdomains and not the apex, so making it useful means adding example.com as a subject alternative name — and this component has no SANs by design, which is what lets the validation record be read as the single element of the certificate's validation options rather than a loop over a set that is not known until the certificate exists (see the certificate module). Accepting a wildcard here would advertise half a feature the rest of the file cannot complete. A caller who needs one issues the certificate themselves and attaches it through acm_certificate_arn, which is exactly what that mode is for. | `str \| None` | `None` | no |
| `force_destroy` | Whether `pulumi destroy` may delete the site bucket while it still holds objects. Defaults to false, which is the correct value for any environment whose contents are worth more than the convenience of tearing it down.<br/><br/>Every environment in this repository sets it to true, because every environment here is ephemeral and a bucket the app repository has deployed into refuses to delete otherwise. That is a property of this operating model, not of the component, which is exactly why it is an input. | `bool` | `False` | no |
| `hosted_zone_id` | Id of the Route 53 public hosted zone holding domain_name, when this component should request and validate the certificate itself. Requires that the zone lives in the same AWS account.<br/><br/>Leave None and set acm_certificate_arn instead when the domain's DNS is anywhere else — a registrar's nameservers, another provider, another account. | `str \| None` | `None` | no |
| `log_retention_days` | How long CloudFront access logs are retained in CloudWatch Logs. Applies to the log group this component creates; the log group is destroyed with the environment, so this bounds a window that in practice closes far sooner. | `int` | `30` | no |
| `seed_placeholder` | Whether to seed the bucket with a placeholder `index.html` so the site is servable before any application build is deployed to it. Defaults to true.<br/><br/>Set it to false only where something else is guaranteed to have written that key first. An empty bucket does not serve an empty page — it serves the origin's 403, on every route rather than only on the homepage, because the routing here answers a client-side route with that one missing document. | `bool` | `True` | no |
<!-- END_INTERFACE -->
