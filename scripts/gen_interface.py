"""Render the ``StaticSite`` interface into ``packages/static-site/README.md`` — the ``terraform-docs`` analogue.

A component's interface is its inputs and its outputs, and both change. The prose above the generated block is
the part a person has to write; the tables below it are a mechanical restatement of ``StaticSiteArgs`` and the
component's output attributes, and a mechanical restatement kept by hand is wrong within three commits. So the
tables are generated here, and ``make docs-check`` (this script with ``--check``, a required status check) fails
a pull request whose block does not match the code.

What is read, and from where:

* Inputs: every dataclass field of ``StaticSiteArgs`` — its annotation, its default (or ``n/a`` when required)
  and the string literal that follows it in ``args.py``, which is the field's documentation.
* Outputs: every attribute of ``StaticSite`` whose evaluated annotation is ``pulumi.Output[...]`` (spelled
  either way, optionally in a union) and its string literal in ``component.py``. Attributes of any other type —
  the two ``aws.Provider`` handles, a typed private child — are not part of the interface and need no docstring.

An exported input or output without a docstring is an error, not a blank cell. Required inputs come first,
then optional ones, each group alphabetical, as ``.terraform-docs.yml`` sorted the Terraform table. An empty
section is omitted rather than rendered as "none", so the Outputs table appears on its own once the component
publishes one.

Run as ``uv run --locked --all-packages python scripts/gen_interface.py`` to rewrite the block, or with
``--check`` to exit 1 with a diff instead of writing. Exit 2 means the README or the code is malformed in a way
regenerating cannot fix: a missing or repeated marker, or an exported field with no documentation.
"""

import argparse
import ast
import dataclasses
import difflib
import inspect
import sys
import textwrap
import types
import typing
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pulumi

from static_site import StaticSite, StaticSiteArgs

BEGIN_MARKER: Final = "<!-- BEGIN_INTERFACE -->"
END_MARKER: Final = "<!-- END_INTERFACE -->"
README_PATH: Final = Path(__file__).resolve().parent.parent / "packages" / "static-site" / "README.md"


class InterfaceError(Exception):
    """The code or the README is shaped so that no regeneration can produce a correct block."""


@dataclasses.dataclass(frozen=True)
class Input:
    """One row of the Inputs table; ``default`` is ``None`` for a required field."""

    name: str
    description: str
    type: str
    default: str | None


@dataclasses.dataclass(frozen=True)
class Output:
    """One row of the Outputs table."""

    name: str
    description: str


def _docstring_of(node: ast.stmt | None) -> str | None:
    """The string literal a statement is, when it is one — the attribute-docstring convention."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return inspect.cleandoc(node.value.value)
    return None


@dataclasses.dataclass(frozen=True)
class BodyAttribute:
    """One annotated attribute of a class body: the annotation as written, and its docstring when it has one."""

    annotation: str
    description: str | None


def body_attributes(cls: type) -> dict[str, BodyAttribute]:
    """Every annotated attribute in ``cls``'s own body, by name, with its source annotation and docstring."""
    module = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    class_def = module.body[0]
    if not isinstance(class_def, ast.ClassDef):
        raise InterfaceError(f"{cls.__qualname__}: the source read back is not a class definition")
    body = class_def.body
    attributes: dict[str, BodyAttribute] = {}
    for index, node in enumerate(body):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        following = body[index + 1] if index + 1 < len(body) else None
        attributes[node.target.id] = BodyAttribute(ast.unparse(node.annotation), _docstring_of(following))
    return attributes


def _documented(cls: type, attributes: dict[str, BodyAttribute], name: str) -> tuple[str, str]:
    """The source annotation and the docstring of an exported attribute; either missing is an error."""
    attribute = attributes.get(name)
    if attribute is None:
        raise InterfaceError(
            f"{cls.__qualname__}.{name} is not annotated in the class body, so it cannot be documented there."
        )
    if attribute.description is None:
        raise InterfaceError(
            f"{cls.__qualname__}.{name} has no documentation: add a string literal on the line after the "
            "annotation, which is what this table is rendered from."
        )
    return attribute.annotation, attribute.description


def _default_of(field: dataclasses.Field[object]) -> str | None:
    """The field's default as source text, or ``None`` when the field is required."""
    if field.default is not dataclasses.MISSING:
        return repr(field.default)
    if field.default_factory is not dataclasses.MISSING:
        return repr(field.default_factory())
    return None


def inputs() -> list[Input]:
    """The ``StaticSiteArgs`` fields as table rows, required first, each group alphabetical.

    The dataclass is the authority on which attributes are fields and what they default to; the class body is
    read only for the annotation text and the documentation.
    """
    attributes = body_attributes(StaticSiteArgs)
    rows: list[Input] = []
    for field in dataclasses.fields(StaticSiteArgs):
        annotation, description = _documented(StaticSiteArgs, attributes, field.name)
        rows.append(Input(name=field.name, description=description, type=annotation, default=_default_of(field)))
    return sorted(rows, key=lambda row: (row.default is not None, row.name))


def is_output_annotation(annotation: object) -> bool:
    """Whether an evaluated annotation is ``pulumi.Output[...]``, alone or as a member of a union.

    An ``Output`` nested inside any other generic — ``list[Output[str]]``, ``dict[str, Output[str]]`` — is neither
    an output nor a plain attribute: it would vanish from the table without a word, so it is an error. The
    component publishes ``Output[list[str]]`` instead, which is what a stack export takes.
    """
    origin = typing.get_origin(annotation)
    if origin is pulumi.Output:
        return True
    members = typing.get_args(annotation)
    if origin is types.UnionType or origin is typing.Union:
        return any(is_output_annotation(member) for member in members)
    if any(_contains_output(member) for member in members):
        raise InterfaceError(
            f"annotation {annotation!r} nests an Output inside a container; publish an Output[...] of the "
            "container instead, so the table can list it."
        )
    return False


def _contains_output(annotation: object) -> bool:
    return typing.get_origin(annotation) is pulumi.Output or any(
        _contains_output(member) for member in typing.get_args(annotation)
    )


def _evaluated_annotations(cls: type) -> dict[str, object]:
    """``cls``'s own annotations, evaluated; an unresolvable forward reference is an error naming the class."""
    try:
        return inspect.get_annotations(cls, eval_str=True)
    except NameError as error:
        raise InterfaceError(f"{cls.__qualname__}: cannot evaluate an annotation: {error}") from error


def outputs() -> list[Output]:
    """The ``StaticSite`` attributes annotated ``pulumi.Output[...]`` as table rows, alphabetical."""
    attributes = body_attributes(StaticSite)
    rows: list[Output] = []
    for name, annotation in _evaluated_annotations(StaticSite).items():
        try:
            is_output = is_output_annotation(annotation)
        except InterfaceError as error:
            raise InterfaceError(f"{StaticSite.__qualname__}.{name}: {error}") from None
        if is_output:
            rows.append(Output(name=name, description=_documented(StaticSite, attributes, name)[1]))
    return sorted(rows, key=lambda row: row.name)


def _escape_pipes(text: str) -> str:
    """GFM reads an unescaped pipe as a cell boundary even inside a code span, so ``str | None`` needs this."""
    return text.replace("|", "\\|")


def _cell(text: str) -> str:
    """One table cell: paragraphs joined by ``<br/><br/>``, lines within a paragraph by a space, pipes escaped."""
    paragraphs = [" ".join(line.strip() for line in paragraph.splitlines()) for paragraph in text.split("\n\n")]
    return _escape_pipes("<br/><br/>".join(paragraphs))


def _inputs_table(rows: Sequence[Input]) -> list[str]:
    lines = [
        "## Inputs",
        "",
        "| Name | Description | Type | Default | Required |",
        "| ---- | ----------- | ---- | ------- | :------: |",
    ]
    for row in rows:
        default = "n/a" if row.default is None else f"`{row.default}`"
        required = "yes" if row.default is None else "no"
        lines.append(
            f"| `{row.name}` | {_cell(row.description)} | `{_escape_pipes(row.type)}` | {default} | {required} |"
        )
    return lines


def _outputs_table(rows: Sequence[Output]) -> list[str]:
    lines = ["## Outputs", "", "| Name | Description |", "| ---- | ----------- |"]
    lines.extend(f"| `{row.name}` | {_cell(row.description)} |" for row in rows)
    return lines


def render_block() -> str:
    """The text between the markers, markers included, ending with a newline."""
    lines = [BEGIN_MARKER, *_inputs_table(inputs())]
    output_rows = outputs()
    if output_rows:
        lines.extend(["", *_outputs_table(output_rows)])
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def _marker_offset(readme: str, marker: str) -> int:
    """The offset of the one line that is exactly ``marker``; a mention inside prose is not a marker."""
    offsets = [offset for offset, line in _lines_with_offsets(readme) if line.strip() == marker]
    if len(offsets) != 1:
        raise InterfaceError(f"expected exactly one line reading {marker}, found {len(offsets)}")
    return offsets[0]


def _lines_with_offsets(text: str) -> list[tuple[int, str]]:
    offsets: list[tuple[int, str]] = []
    position = 0
    for line in text.splitlines(keepends=True):
        offsets.append((position, line))
        position += len(line)
    return offsets


def inject(readme: str, block: str) -> str:
    """``readme`` with the marker-delimited block replaced by ``block``; everything outside the markers stays."""
    begin = _marker_offset(readme, BEGIN_MARKER)
    end_line = _marker_offset(readme, END_MARKER)
    if end_line < begin:
        raise InterfaceError(f"{END_MARKER} precedes {BEGIN_MARKER}")
    end = end_line + len(END_MARKER)
    if readme.startswith("\n", end):
        end += 1
    return readme[:begin] + block + readme[end:]


def run(readme_path: Path, *, check: bool) -> int:
    """Rewrite the block in ``readme_path`` (exit 0), or with ``check`` report staleness instead (0 fresh, 1 stale)."""
    try:
        current = readme_path.read_text(encoding="utf-8")
        regenerated = inject(current, render_block())
    except (InterfaceError, OSError) as error:
        print(f"gen_interface: {readme_path}: {error}", file=sys.stderr)
        return 2
    if regenerated == current:
        return 0
    if check:
        sys.stderr.writelines(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                regenerated.splitlines(keepends=True),
                fromfile=f"{readme_path} (committed)",
                tofile=f"{readme_path} (regenerated)",
            )
        )
        print(
            f"gen_interface: {readme_path} no longer matches StaticSiteArgs and StaticSite. The block between "
            f"{BEGIN_MARKER} and {END_MARKER} is generated, so regenerate it rather than editing it: make docs. "
            "To change what it says, edit the documentation on the field or output in question and regenerate.",
            file=sys.stderr,
        )
        return 1
    readme_path.write_text(regenerated, encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Regenerate the interface block of {README_PATH}.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 with a diff when the block is stale instead of rewriting it; what `make docs-check` runs",
    )
    options = parser.parse_args(argv)
    return run(README_PATH, check=options.check)


if __name__ == "__main__":
    sys.exit(main())
