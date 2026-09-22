"""Pulumi program for the ``static-site-bootstrap`` project.

Phase 0 stub. Phase 1 of IMPLEMENTATION_PLAN.md fills it in: read and validate
``Pulumi.bootstrap.yaml`` (``config.py``), declare the one explicit ``aws.Provider``
with ``default_tags`` (``Env=bootstrap``), create the state bucket (``state.py``)
and the OIDC provider, plan/apply roles and the app-deploy boundary (``oidc.py``),
and export what ``bootstrap/outputs.tf`` exported (backend URL, role ARNs,
boundary policy name).

Until then the program declares nothing, so ``pulumi preview`` reports only the
stack and ``mypy --strict bootstrap`` has a module to check.
"""

import pulumi

pulumi.log.info("static-site-bootstrap: no resources are declared until Phase 1")
