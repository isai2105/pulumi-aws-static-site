"""Shared fixtures and the import path of ``scripts/`` for the unit tests.

``scripts/`` is appended to ``sys.path`` (never prepended: a future ``scripts/<name>.py`` must not shadow the
standard library or a dependency for every test) so ``test_gen_interface.py`` can import ``gen_interface``.
mypy resolves the same import because ``make typecheck`` checks ``packages tests/unit scripts`` in one
invocation, and mypy adds each listed source directory to its search path; ``mypy --strict tests/unit`` alone
would need ``mypy_path = "scripts"`` in the root ``pyproject.toml``.
"""

import sys
from collections.abc import Iterator
from pathlib import Path

import pulumi.runtime
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.append(str(SCRIPTS_DIR))


@pytest.fixture
def pulumi_runtime() -> Iterator[None]:
    """The Pulumi runtime configured as the CLI configures it for stack ``test`` of the offline project.

    ``pulumi.runtime.SETTINGS`` is a process-wide object mutated in place by ``configure``, so the fields this
    fixture changes are snapshotted and restored: no test module inherits another's ``get_stack()``.
    """
    settings = pulumi.runtime.settings.SETTINGS
    previous = {name: getattr(settings, name) for name in ("project", "stack", "dry_run")}
    pulumi.runtime.configure(pulumi.runtime.Settings(project="static-site-offline", stack="test", dry_run=True))
    yield
    for name, value in previous.items():
        setattr(settings, name, value)
