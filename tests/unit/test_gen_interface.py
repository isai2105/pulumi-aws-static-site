"""``scripts/gen_interface.py``: the interface table, its sort, the marker injection and the three exit codes.

The script is importable because ``conftest.py`` puts ``scripts/`` on ``sys.path``. The live README is never
touched: every ``run`` goes against a file under ``tmp_path``, and the rendered block is compared with what the
same functions render, so a change in the table shape fails here before it fails ``make docs-check``.
"""

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

import gen_interface
import pulumi
import pulumi_aws as aws
import pytest
from pulumi import Output

from static_site import StaticSiteArgs

README_TEXT = (
    "# `static-site`\n\nprose that mentions <!-- BEGIN_INTERFACE --> in passing\n\n"
    f"{gen_interface.BEGIN_MARKER}\nstale\n{gen_interface.END_MARKER}\n\ntrailing prose\n"
)


class DocumentedComponent(pulumi.ComponentResource):
    """A class body shaped like the Phase 2 component: two outputs, two spellings, and attributes that are not."""

    provider: aws.Provider
    """Not an output: a provider handle."""

    _bucket: str

    zeta: Output[str | None]
    """The last output alphabetically, spelled with the bare import."""

    bucket_name: pulumi.Output[str]
    """Name of the bucket.

    A second paragraph, with a | pipe."""

    optional: pulumi.Output[int] | None
    """An output inside a union."""


class UndocumentedOutput(pulumi.ComponentResource):
    bucket_name: pulumi.Output[str]


# --- what is read -----------------------------------------------------------


def test_body_attributes_keep_the_source_annotation_and_the_docstring() -> None:
    attributes = gen_interface.body_attributes(DocumentedComponent)
    assert attributes["bucket_name"] == gen_interface.BodyAttribute(
        "pulumi.Output[str]", "Name of the bucket.\n\nA second paragraph, with a | pipe."
    )
    assert attributes["_bucket"] == gen_interface.BodyAttribute("str", None)
    assert attributes["zeta"].annotation == "Output[str | None]"


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (pulumi.Output[str], True),
        (Output[str | None], True),
        (pulumi.Output[int] | None, True),
        (aws.Provider, False),
        (str, False),
        (str | None, False),
        (list[str], False),
        (dict[str, list[int]] | None, False),
    ],
)
def test_is_output_annotation_selects_output_alone_or_in_a_union(annotation: object, expected: bool) -> None:
    assert gen_interface.is_output_annotation(annotation) is expected


@pytest.mark.parametrize(
    "annotation",
    [list[pulumi.Output[str]], dict[str, Output[str]], tuple[str, pulumi.Output[int]], list[Output[str]] | None],
)
def test_an_output_nested_in_a_container_is_an_error_not_a_silent_skip(annotation: object) -> None:
    with pytest.raises(gen_interface.InterfaceError, match=r"nests an Output inside a container"):
        gen_interface.is_output_annotation(annotation)


class ContainerOfOutputs(pulumi.ComponentResource):
    parameter_arns: list[pulumi.Output[str]]
    """Would vanish from the table if it were skipped."""


def test_outputs_name_the_attribute_that_nests_an_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_interface, "StaticSite", ContainerOfOutputs)
    with pytest.raises(gen_interface.InterfaceError, match=r"ContainerOfOutputs\.parameter_arns: annotation"):
        gen_interface.outputs()


if TYPE_CHECKING:
    # Defined for the type checker only, so the forward reference below is exactly the shape N3 describes: valid
    # to mypy, unresolvable when the generator evaluates it at runtime.
    NotImportedAnywhere = list


class UnresolvableAnnotation(pulumi.ComponentResource):
    site_url: "NotImportedAnywhere[str]"
    """Documented, but the annotation cannot be evaluated."""


def test_an_unresolvable_annotation_is_an_interface_error_not_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_interface, "StaticSite", UnresolvableAnnotation)
    with pytest.raises(gen_interface.InterfaceError, match=r"UnresolvableAnnotation: cannot evaluate an annotation"):
        gen_interface.outputs()


def test_outputs_of_the_phase_0_component_are_none() -> None:
    assert gen_interface.outputs() == []


def test_outputs_are_the_output_annotated_attributes_alphabetical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_interface, "StaticSite", DocumentedComponent)
    assert [row.name for row in gen_interface.outputs()] == ["bucket_name", "optional", "zeta"]


def test_an_undocumented_output_is_an_error_but_an_undocumented_other_attribute_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gen_interface, "StaticSite", UndocumentedOutput)
    with pytest.raises(gen_interface.InterfaceError, match=r"UndocumentedOutput\.bucket_name has no documentation"):
        gen_interface.outputs()


def test_inputs_are_every_field_required_first_then_alphabetical() -> None:
    rows = gen_interface.inputs()
    assert [row.name for row in rows] == [
        "app_deploy_boundary_policy_name",
        "app_github_owner",
        "app_github_owner_id",
        "app_github_repository_id",
        "environment",
        "github_oidc_provider_arn",
        "name_prefix",
        "acm_certificate_arn",
        "domain_name",
        "force_destroy",
        "hosted_zone_id",
        "log_retention_days",
        "seed_placeholder",
    ]
    assert {row.name for row in rows} == {field.name for field in dataclasses.fields(StaticSiteArgs)}


def test_inputs_render_type_default_and_required_from_the_dataclass() -> None:
    by_name = {row.name: row for row in gen_interface.inputs()}
    assert (by_name["name_prefix"].type, by_name["name_prefix"].default) == ("str", None)
    assert (by_name["domain_name"].type, by_name["domain_name"].default) == ("str | None", "None")
    assert (by_name["force_destroy"].type, by_name["force_destroy"].default) == ("bool", "False")
    assert (by_name["log_retention_days"].type, by_name["log_retention_days"].default) == ("int", "30")


def test_a_default_factory_field_is_optional_not_required() -> None:
    @dataclasses.dataclass(kw_only=True)
    class WithFactory:
        tags: dict[str, str] = dataclasses.field(default_factory=dict)
        name: str = "x"
        required: int

    fields = {field.name: field for field in dataclasses.fields(WithFactory)}
    assert gen_interface._default_of(fields["tags"]) == "{}"
    assert gen_interface._default_of(fields["name"]) == "'x'"
    assert gen_interface._default_of(fields["required"]) is None


# --- rendering ----------------------------------------------------------------


def test_cell_joins_paragraphs_lines_and_escapes_pipes() -> None:
    assert gen_interface._cell("one\n two\n\nthree | four") == "one two<br/><br/>three \\| four"


def test_render_block_is_delimited_and_omits_an_empty_outputs_section() -> None:
    block = gen_interface.render_block()
    lines = block.splitlines()
    assert lines[0] == gen_interface.BEGIN_MARKER
    assert lines[-1] == gen_interface.END_MARKER
    assert "## Inputs" in lines
    assert "## Outputs" not in lines
    assert block.endswith("\n")
    assert "`str \\| None`" in block


def test_render_block_includes_outputs_once_there_are_any(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_interface, "StaticSite", DocumentedComponent)
    block = gen_interface.render_block()
    assert "## Outputs" in block
    assert "| `bucket_name` | Name of the bucket.<br/><br/>A second paragraph, with a \\| pipe. |" in block


# --- injection ----------------------------------------------------------------


def test_inject_replaces_only_the_block_and_ignores_a_marker_mentioned_in_prose() -> None:
    block = f"{gen_interface.BEGIN_MARKER}\nfresh\n{gen_interface.END_MARKER}\n"
    assert gen_interface.inject(README_TEXT, block) == README_TEXT.replace("stale", "fresh")


def test_inject_does_not_eat_what_follows_the_end_marker() -> None:
    readme = f"{gen_interface.BEGIN_MARKER}\nstale\n{gen_interface.END_MARKER}"
    block = f"{gen_interface.BEGIN_MARKER}\nfresh\n{gen_interface.END_MARKER}\n"
    assert gen_interface.inject(readme, block) == block
    assert gen_interface.inject(readme + "\ntail\n", block) == block + "tail\n"


@pytest.mark.parametrize(
    ("readme", "fragment"),
    [
        ("no markers at all\n", "expected exactly one line reading <!-- BEGIN_INTERFACE -->, found 0"),
        (f"{gen_interface.BEGIN_MARKER}\n{gen_interface.BEGIN_MARKER}\n{gen_interface.END_MARKER}\n", "found 2"),
        (
            f"{gen_interface.BEGIN_MARKER}\nno end\n",
            "expected exactly one line reading <!-- END_INTERFACE -->, found 0",
        ),
        (f"{gen_interface.END_MARKER}\n{gen_interface.BEGIN_MARKER}\n", "<!-- END_INTERFACE --> precedes"),
    ],
    ids=["none", "duplicate-begin", "no-end", "reversed"],
)
def test_inject_refuses_a_malformed_readme(readme: str, fragment: str) -> None:
    with pytest.raises(gen_interface.InterfaceError, match=fragment):
        gen_interface.inject(readme, "")


# --- exit codes ---------------------------------------------------------------


@pytest.fixture
def readme(tmp_path: Path) -> Path:
    path = tmp_path / "README.md"
    path.write_text(README_TEXT, encoding="utf-8")
    return path


def test_run_writes_the_block_and_then_reports_fresh(readme: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gen_interface.run(readme, check=False) == 0
    assert readme.read_text(encoding="utf-8") == gen_interface.inject(README_TEXT, gen_interface.render_block())
    assert gen_interface.run(readme, check=True) == 0
    assert capsys.readouterr().err == ""


def test_run_check_reports_stale_with_a_diff_and_writes_nothing(
    readme: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gen_interface.run(readme, check=True) == 1
    assert readme.read_text(encoding="utf-8") == README_TEXT
    err = capsys.readouterr().err
    assert "-stale" in err
    assert "+## Inputs" in err
    assert "make docs" in err


def test_run_exits_2_for_a_readme_it_cannot_regenerate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing.md"
    assert gen_interface.run(missing, check=True) == 2
    no_markers = tmp_path / "no-markers.md"
    no_markers.write_text("nothing here\n", encoding="utf-8")
    assert gen_interface.run(no_markers, check=False) == 2
    assert no_markers.read_text(encoding="utf-8") == "nothing here\n"
    err = capsys.readouterr().err
    assert str(missing) in err
    assert "expected exactly one line reading" in err


def test_main_targets_the_live_readme_which_is_fresh() -> None:
    assert gen_interface.README_PATH.is_file()
    assert gen_interface.main(["--check"]) == 0
