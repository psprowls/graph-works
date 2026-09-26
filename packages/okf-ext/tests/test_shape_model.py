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


#: The one other okf-ext module `shape` may import. `views.py` walks a body's
#: headings; `okf_ext.body` sits below `shape` in the layers contract, so the
#: edge is legal there -- this pin keeps it the *only* one.
SHAPE_MAY_IMPORT = ("okf_ext.shape", "okf_ext.body")


def test_shape_imports_nothing_in_okf_ext_but_body():
    """`shape` imports neither a capability nor any shared module except
    `okf_ext.body`. The layers contract states the direction; asserted here so
    a broken boundary fails the suite, not only `just contracts`."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module.startswith("okf_ext") and not module.startswith(SHAPE_MAY_IMPORT):
                    offenders.append(f"{path.name}:{node.lineno} {module}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{node.lineno} import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("okf_ext") and not alias.name.startswith(SHAPE_MAY_IMPORT)
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


def test_the_audience_fields_default_to_human_and_unbounded():
    spec = shape.SectionSpec(heading="Summary")
    assert (spec.audience, spec.phases, spec.max_words) == ("human", (), None)
