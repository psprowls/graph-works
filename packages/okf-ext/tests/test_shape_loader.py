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
    root = tmp_path / "sections"
    root.mkdir()
    (root / "feature.yaml").write_text("sections:\n  - heading: Summary\n    required: true\n", encoding="utf-8")
    declaration = load_sections(root).types["feature"]
    assert declaration.sections[0].ownership == "prose"
    assert declaration.frontmatter == FrontmatterOwnership()


def test_ownership_is_read_and_frontmatter_lists_become_tuples(tmp_path):
    root = tmp_path / "sections"
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
    root = tmp_path / "sections"
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
    root = tmp_path / "sections"
    root.mkdir()
    (root / "entity.yaml").write_text(
        "frontmatter:\n  owned: [title]\n  future_class: [x]\nsections: []\n", encoding="utf-8"
    )
    assert load_sections(root).types["entity"].frontmatter.owned == ("title",)


def test_an_empty_frontmatter_block_is_the_empty_ownership(tmp_path):
    root = tmp_path / "sections"
    root.mkdir()
    (root / "entity.yaml").write_text("frontmatter: {}\nsections: []\n", encoding="utf-8")
    assert load_sections(root).types["entity"].frontmatter == FrontmatterOwnership()


def _declared(tmp_path, files):
    """A `sections` directory written from `{filename: text}`."""
    root = tmp_path / "sections"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


_ROOT_INDEX = """\
directories:
  "":
    sections:
      - heading: Repositories
        ownership: generated
        required: true
        placeholder: |
          _(not yet generated)_
"""


def test_a_directories_key_becomes_an_index_declaration(tmp_path):
    root = _declared(tmp_path, {"_index.yaml": _ROOT_INDEX, "Feature.yaml": "sections:\n  - heading: Summary\n"})
    section_set = load_sections(root)
    assert tuple(section_set.indexes) == ("",)
    spec = section_set.indexes[""].sections[0]
    assert (spec.heading, spec.ownership, spec.required) == ("Repositories", "generated", True)
    assert spec.placeholder == "_(not yet generated)_\n"
    # The type table is untouched: a `_`-prefixed file never claims a type.
    assert section_set.type_names == ("Feature",)


def test_a_declaration_set_with_no_directories_key_has_no_indexes(tmp_path):
    """The additive promise: this is what every declaration written before
    this feature looks like."""
    root = _declared(tmp_path, {"Feature.yaml": "sections:\n  - heading: Summary\n"})
    assert load_sections(root).indexes == {}


def test_a_directory_id_is_normalized_to_the_bundle_indexes_key(tmp_path):
    """`Bundle.indexes` is keyed by directory id with no trailing slash, and a
    declaration that writes one anyway must not key a second, unreachable
    entry."""
    root = _declared(
        tmp_path,
        {"_index.yaml": 'directories:\n  "packages/":\n    sections:\n      - heading: Inventory\n'},
    )
    assert tuple(load_sections(root).indexes) == ("packages",)


def test_two_underscore_files_may_each_declare_their_own_root_section(tmp_path):
    """The merge this exists for: three tier-3 packages share one bundle and
    each declares its own root section without arbitrating a shared file."""
    root = _declared(
        tmp_path,
        {
            "_a.yaml": 'directories:\n  "":\n    sections:\n      - heading: Repositories\n',
            "_b.yaml": 'directories:\n  "":\n    sections:\n      - heading: Work\n',
        },
    )
    assert [s.heading for s in load_sections(root).indexes[""].sections] == ["Repositories", "Work"]


BAD_DIRECTORIES = {
    "directories_in_typed_file": "underscore-prefixed",
    "directories_heading_collision": "already declared by",
    "directories_not_a_mapping": "`directories` must be a mapping",
    "index_frontmatter": "not declarable for a directory index",
}


@pytest.mark.parametrize(("name", "expected"), sorted(BAD_DIRECTORIES.items()))
def test_every_bad_directories_declaration_raises_a_legible_section_error(name, expected):
    with pytest.raises(SectionError, match=expected):
        load_sections(SECTIONS_BAD / name)


def test_a_directory_entry_that_is_not_a_mapping_raises(tmp_path):
    root = _declared(tmp_path, {"_index.yaml": 'directories:\n  "": 7\n'})
    with pytest.raises(SectionError, match="must be a mapping"):
        load_sections(root)


def test_a_non_string_directory_id_raises(tmp_path):
    root = _declared(tmp_path, {"_index.yaml": "directories:\n  7:\n    sections: []\n"})
    with pytest.raises(SectionError, match="must be a string"):
        load_sections(root)


def test_two_directory_keys_in_one_file_that_normalize_alike_raise(tmp_path):
    """`"packages/"` and `"packages"` are two YAML keys but one directory id
    once normalized -- caught within a single file, not only across files."""
    root = _declared(
        tmp_path,
        {
            "_index.yaml": (
                'directories:\n  "packages/":\n    sections:\n      - heading: A\n'
                '  "packages":\n    sections:\n      - heading: B\n'
            )
        },
    )
    with pytest.raises(SectionError, match="already declared in this file"):
        load_sections(root)
