"""The hoisted declaration surface, and the shim that keeps `sections`
working. The behaviour of the loader itself is `test_shape_loader.py`'s;
that it still behaves is `test_sections_loader.py`'s, unedited by the hoist.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from okf_ext import sections, shape

SRC = Path(shape.__file__).resolve().parent

MOVED = (
    "DEFAULT_IGNORE",
    "DEFAULT_SECTIONS_DIRNAME",
    "SECTION_SUFFIXES",
    "SectionError",
    "SectionSet",
    "SectionSpec",
    "TypeSections",
    "load_sections",
)


def test_every_moved_name_is_reachable_from_shape():
    for name in MOVED:
        assert hasattr(shape, name), f"okf_ext.shape is missing {name}"


def test_the_sections_shim_re_exports_the_same_objects():
    """Not merely "a name of that spelling exists": a shim that rebound a
    name to a copy would let the two drift, which is the whole failure the
    hoist exists to prevent."""
    for name in MOVED:
        assert getattr(sections, name) is getattr(shape, name), f"sections.{name} is not shape.{name}"


def test_shape_imports_no_other_okf_ext_module():
    """`shape` takes its own layer line because it imports neither a
    capability nor another shared module. The layers contract states this;
    asserted here so a broken boundary fails the suite, not only the opt-in
    `just contracts`."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and (node.module or "").startswith("okf_ext")
                and not (node.module or "").startswith("okf_ext.shape")
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.module}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{node.lineno} import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("okf_ext") and not alias.name.startswith("okf_ext.shape")
                )
    assert not offenders, "\n".join(offenders)


def test_the_defaults_are_unchanged_by_the_move():
    assert shape.DEFAULT_SECTIONS_DIRNAME == "sections"
    assert shape.SECTION_SUFFIXES == (".yaml", ".yml")
    assert shape.DEFAULT_IGNORE == ("sections/*", "*/sections/*")


def test_the_spec_defaults_are_unchanged_by_the_move():
    spec = shape.SectionSpec(heading="Summary")
    assert (spec.level, spec.required, spec.seeded_is_complete, spec.placeholder) == (2, False, False, "")


def test_the_new_fields_default_to_the_pre_existing_behaviour():
    """Restated at the model layer as well as the loader layer: a caller
    constructing a `SectionSpec` by hand gets `prose` too."""
    assert shape.SectionSpec(heading="Summary").ownership == "prose"
    assert shape.TypeSections(sections=()).frontmatter == shape.FrontmatterOwnership()


def test_frontmatter_ownership_is_frozen_and_holds_tuples():
    ownership = shape.FrontmatterOwnership(owned=("title",), provenance=("content_hash",))
    with pytest.raises(AttributeError):
        ownership.owned = ()  # type: ignore[misc]
