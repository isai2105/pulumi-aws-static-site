"""Pulumi program for the ``static-site`` project (one stack per environment).

Phase 0 stub. Phase 3 of IMPLEMENTATION_PLAN.md fills it in: ``environment =
pulumi.get_stack()``, the ten ``static-site:*`` config keys read through
``pulumi.Config("static-site")``, two ``aws.Provider`` instances with
``default_tags`` (the stack's region and ``us-east-1``), the GitHub OIDC provider
ARN composed from the caller identity and read back by ARN, one ``StaticSite``,
and the six stack outputs of ``envs/stage/outputs.tf``.

Until then the program declares nothing, so ``pulumi preview`` reports only the
stack and ``mypy --strict envs`` has a module to check.
"""

import pulumi

pulumi.log.info("static-site: no resources are declared until Phase 3")
