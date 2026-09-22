# Changelog

All notable changes to this repository are recorded here, in the format of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This file carries **interface** changes — the things a consumer builds against and breaks on. The
app repository's deploy contract (`docs/DEPLOY_CONTRACT.md`), the `StaticSite` component's
arguments and outputs, the per-environment stack configuration, the repository variables and
secret the workflows read, and the grants the bootstrap makes on a consumer's behalf are all
interface. Internal scoping, workflow mechanics and comment accuracy are not, and are left to git
history.

## [Unreleased]

### Added

- The `StaticSite` component's interface: `StaticSiteArgs`, one field per variable of the
  Terraform module this repository ports (`name_prefix`, `environment`, `force_destroy=False`,
  `seed_placeholder=True`, `domain_name`, `hosted_zone_id`, `acm_certificate_arn`,
  `log_retention_days=30`, `github_oidc_provider_arn`, `app_github_owner`, `app_github_owner_id`,
  `app_github_repository_id`, `app_deploy_boundary_policy_name`) with the module's validation
  rules. The component declares no resources yet; the resource set and the outputs land in
  Phase 2.

### Not recorded here

- The deploy contract is unchanged from `terraform-aws-static-site`: the app repository sees the
  same role name, trust subject, three SSM parameters, four-command upload sequence and CSP. That
  it is unchanged is the point, and an entry saying so would be recording a non-change.
- The toolchain and its pins (`.pulumi.version`, `.python-version`, `[tool.uv] required-version`,
  `uv.lock`), the Makefile, the validation workflow and its required checks, the empty bootstrap,
  environment, policy-pack and offline-preview projects. They change what the repository checks
  and how it is built, not what it provides.

A `[Unreleased]: <compare url>` link reference is added when the repository has a remote to point
it at.
