"""pytest over the ``pulumi preview --json`` documents ``scripts/preview_offline.sh`` writes.

The script previews ``tests/offline/__main__.py`` once per variant and writes
``preview.<variant>.json`` at the repository root (``.gitignore`` lists them),
then runs ``uv run --locked --all-packages pytest tests/offline`` from the root.
These tests read those documents; they never run Pulumi themselves. Set
``PREVIEW_JSON_DIR`` to read them from another directory.

Phase 0 (IMPLEMENTATION_PLAN.md): the component declares nothing, so every
variant's preview is exactly the stack, the ``StaticSite`` component and the
two fake-key ``aws`` providers, both carrying the default tags. Phase 2
replaces this with the interface assertions ``plan.tftest.hcl`` made
(parameter names, 2+2 edge policies, provider binding, explicit names,
behaviour-to-policy wiring, the custom-domain branches).
"""

import json
import os
from pathlib import Path
from typing import Any, TypedDict

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
"""``tests/offline/test_preview.py`` -> the repository root, where the script writes its outputs."""

VARIANTS = ("default", "managed-cert", "supplied-cert")
"""The ``static-site-offline:variant`` values the script iterates (``tests/offline/__main__.py`` ``VARIANTS``)."""

AWS_PROVIDER_TYPE = "pulumi:providers:aws"
COMPONENT_TYPE = "static-site:index:StaticSite"
STACK_TYPE = "pulumi:pulumi:Stack"


class NewState(TypedDict, total=False):
    """``steps[].newState`` as ``pulumi preview --json`` (3.263.0) writes it; property names are camelCase."""

    urn: str
    type: str
    custom: bool
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None
    parent: str
    provider: str
    propertyDependencies: dict[str, list[str]]


class Step(TypedDict):
    """One entry of ``steps``."""

    op: str
    urn: str
    newState: NewState


class PreviewDocument(TypedDict, total=False):
    """The top-level keys (``config`` is present on 3.263.0; the plan's list of four omits it)."""

    config: dict[str, str]
    steps: list[Step]
    diagnostics: list[dict[str, Any]]
    duration: int
    changeSummary: dict[str, int]


def preview_path(variant: str) -> Path:
    """Where ``scripts/preview_offline.sh`` wrote the ``--json`` preview of ``variant``."""
    directory = Path(os.environ.get("PREVIEW_JSON_DIR", REPOSITORY_ROOT))
    return directory / f"preview.{variant}.json"


def load_preview(variant: str) -> PreviewDocument:
    """The parsed ``preview.<variant>.json``; a missing file names the script that produces it."""
    path = preview_path(variant)
    if not path.is_file():
        pytest.fail(f"{path} does not exist: run scripts/preview_offline.sh (make preview-offline) first")
    with path.open(encoding="utf-8") as handle:
        document: PreviewDocument = json.load(handle)
    return document


def steps_of(document: PreviewDocument) -> list[Step]:
    """The ``steps`` array, or a failure naming the keys the document does have."""
    steps = document.get("steps")
    assert isinstance(steps, list), f"preview document has no `steps` array: keys are {sorted(document)}"
    return steps


def of_type(steps: list[Step], type_token: str) -> list[Step]:
    return [step for step in steps if step["newState"]["type"] == type_token]


def default_tags_of(provider: Step) -> dict[str, str]:
    """The provider's ``defaultTags.tags``; the input is a JSON string in the document."""
    default_tags = provider["newState"]["inputs"].get("defaultTags")
    assert isinstance(default_tags, str), f"{provider['urn']} carries no defaultTags input"
    tags: dict[str, str] = json.loads(default_tags)["tags"]
    return tags


@pytest.mark.parametrize("variant", VARIANTS)
def test_empty_component_previews_exactly_the_stack_the_component_and_the_two_providers(variant: str) -> None:
    """Phase 0: the real ``pulumi-aws`` plugin loaded both fake-key providers and nothing else was declared."""
    document = load_preview(variant)
    steps = steps_of(document)
    config = document.get("config", {})
    assert config.get("static-site-offline:variant") == variant, "the document is the preview of this variant"

    assert len(steps) == 4, [step["urn"] for step in steps]
    assert {step["op"] for step in steps} == {"create"}, "the throwaway stack starts empty, so every step is a create"
    assert len(of_type(steps, STACK_TYPE)) == 1
    assert len(of_type(steps, COMPONENT_TYPE)) == 1, "tests/offline/__main__.py instantiates exactly one StaticSite"

    providers = of_type(steps, AWS_PROVIDER_TYPE)
    assert len(providers) == 2
    assert [step for step in steps if step["newState"].get("custom")] == providers, (
        "Phase 0 declares no child resources; the only custom resources are the two providers"
    )

    by_region = {step["newState"]["inputs"]["region"]: step for step in providers}
    assert set(by_region) == {config["static-site:awsRegion"], "us-east-1"}, (
        "the fixture region and CloudFront's control-plane region"
    )
    for provider in providers:
        inputs = provider["newState"]["inputs"]
        assert inputs["skipCredentialsValidation"] == "true"
        assert inputs["skipMetadataApiCheck"] == "true"
        assert inputs["skipRequestingAccountId"] == "true"
        assert inputs["accessKey"] == "[secret]", "the placeholder key is a provider secret, never in the document"
        assert inputs["secretKey"] == "[secret]"
        # Both providers, not only the default one: an aliased provider inherits nothing (tags.tf:70-96).
        assert default_tags_of(provider) == {
            "Project": config["static-site:project"],
            "Env": "test",
            "Owner": config["static-site:githubOwner"],
            "Repo": f"{config['static-site:githubOwner']}/{config['static-site:githubRepository']}",
            "ManagedBy": "pulumi",
        }
