"""The loader in its new home. Refusal coverage for the fields that existed
before the hoist stays in `test_sections_loader.py`, which the hoist must not
touch -- a test edited to accommodate a move is evidence the move changed
behaviour.
"""

from __future__ import annotations

import pytest
from ext_helpers import SECTIONS_BAD, SECTIONS_DIR
from okf_ext.shape import FrontmatterOwnership, SectionError, load_sections


def test_the_committed_declaration_set_loads_from_shape():
    section_set = load_sections(SECTIONS_DIR)
    assert section_set.type_names == ("Feature", "Reference")
    assert section_set.sources["Feature"] == "Feature.yaml"
    assert "plan-table" in section_set.fragments


def test_a_str_path_is_accepted():
    """The forgiveness `load_vocabulary` and `load_schemas` both extend."""
    assert load_sections(str(SECTIONS_DIR)).type_names == ("Feature", "Reference")


#: One directory per refusal in spec §4.3, and a fragment of the message each
#: must carry -- specific enough to pin the one branch that raises it. Two
#: adjacent checks in `_section_spec` (`isinstance(..., str)` then `in
#: _OWNERSHIP_VALUES`) both mention "ownership" in their message, so a bare
#: "ownership" fragment would pass no matter which of the two fired; likewise
#: three of these touch `owned`. Each fragment below is unique across this
#: table, not just non-empty.
BAD_OWNERSHIP = {
    "bad_ownership": "must be one of",
    "ownership_not_a_string": "must be a string",
    "template_without_placeholder": "needs a non-empty `placeholder`",
    "frontmatter_not_a_mapping": "`frontmatter` must be a mapping",
    "owned_not_a_list": "`owned` must be a list",
    "owned_entry_not_a_string": "non-empty string, got 7",
    "owned_duplicate": "names `title` twice",
    "owned_and_provenance": "is in both `owned` and `provenance`",
}


@pytest.mark.parametrize(("name", "expected"), sorted(BAD_OWNERSHIP.items()))
def test_every_new_bad_directory_raises_a_legible_section_error(name, expected):
    with pytest.raises(SectionError, match=expected):
        load_sections(SECTIONS_BAD / name)


def test_a_declaration_with_neither_new_field_is_unchanged(tmp_path):
    """The additive promise, stated as a test rather than as a comment: this
    is exactly what a declaration written before this capability looks like."""
    root = tmp_path / "_sections"
    root.mkdir()
    (root / "feature.yaml").write_text("sections:\n  - heading: Summary\n    required: true\n", encoding="utf-8")
    declaration = load_sections(root).types["feature"]
    assert declaration.sections[0].ownership == "prose"
    assert declaration.frontmatter == FrontmatterOwnership()


def test_ownership_is_read_and_frontmatter_lists_become_tuples(tmp_path):
    root = tmp_path / "_sections"
    root.mkdir()
    (root / "entity.yaml").write_text(
        "frontmatter:\n"
        "  owned: [title, sources]\n"
        "  provenance: [content_hash]\n"
        "sections:\n"
        "  - heading: Sources\n"
        "    ownership: generated\n"
        "  - heading: About\n"
        "    ownership: template\n"
        "    placeholder: |\n"
        "      Generated from the code graph.\n",
        encoding="utf-8",
    )
    declaration = load_sections(root).types["entity"]
    assert declaration.frontmatter == FrontmatterOwnership(owned=("title", "sources"), provenance=("content_hash",))
    assert declaration.sections[0].ownership == "generated"
    assert declaration.sections[1].ownership == "template"


def test_template_implies_seeded_is_complete(tmp_path):
    """A template section's content *is* its placeholder, so without this
    every required template section reports `sections.unfilled` on every walk,
    forever, for being in exactly the state it is supposed to be in."""
    root = tmp_path / "_sections"
    root.mkdir()
    (root / "entity.yaml").write_text(
        "sections:\n"
        "  - heading: About\n"
        "    required: true\n"
        "    ownership: template\n"
        "    seeded_is_complete: false\n"
        "    placeholder: |\n"
        "      Generated.\n",
        encoding="utf-8",
    )
    assert load_sections(root).types["entity"].sections[0].seeded_is_complete is True


def test_an_unknown_key_inside_frontmatter_is_tolerated(tmp_path):
    """Matching the loader's existing policy for the same reason: a
    declaration written against a newer field must not refuse to load in an
    older reader."""
    root = tmp_path / "_sections"
    root.mkdir()
    (root / "entity.yaml").write_text(
        "frontmatter:\n  owned: [title]\n  future_class: [x]\nsections: []\n", encoding="utf-8"
    )
    assert load_sections(root).types["entity"].frontmatter.owned == ("title",)


def test_an_empty_frontmatter_block_is_the_empty_ownership(tmp_path):
    root = tmp_path / "_sections"
    root.mkdir()
    (root / "entity.yaml").write_text("frontmatter: {}\nsections: []\n", encoding="utf-8")
    assert load_sections(root).types["entity"].frontmatter == FrontmatterOwnership()
