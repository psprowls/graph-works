"""`load_sections`: the format, the fragment mechanism, and every refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ext_helpers import SECTIONS_BAD, write
from okf_ext.sections import (
    DEFAULT_IGNORE,
    DEFAULT_SECTIONS_DIRNAME,
    SECTION_SUFFIXES,
    SectionError,
    load_sections,
)
from test_shape_loader import BAD_DIRECTORIES

COMMON = """
fragments:
  plan-table: |
    | Action | Done when | Rationale |
    | --- | --- | --- |
"""

FEATURE = """
additional_sections: false
sections:
  - heading: Summary
    required: true
    placeholder: |
      <!-- one paragraph: what this changes and why -->
  - heading: Options considered
  - heading: Plan
    required: true
    seeded_is_complete: true
    placeholder_ref: plan-table
  - heading: Notes / log
"""

REFERENCE = """
sections:
  - heading: Entries
    required: true
    level: 3
"""


def write_set(root: Path, **files: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        write(root / name.replace("__", "."), body)
    return root


def standard_set(tmp_path: Path):
    root = write_set(
        tmp_path / "sections",
        **{"_common__yaml": COMMON, "feature__yaml": FEATURE, "Reference__yml": REFERENCE},
    )
    return load_sections(root)


def test_types_come_from_filename_stems_across_both_suffixes(tmp_path):
    assert standard_set(tmp_path).type_names == ("Reference", "feature")


def test_an_underscore_prefixed_file_is_a_fragment_source_never_a_type(tmp_path):
    """`_common.yaml` is read so a `placeholder_ref` resolves, and kept out of
    the dispatch table so no concept can match `type: _common` -- exactly what
    `load_schemas` does with `_base.schema.yaml`."""
    section_set = standard_set(tmp_path)
    assert "_common" not in section_set.types
    assert "plan-table" in section_set.fragments


def test_sources_name_the_file_that_says_so(tmp_path):
    section_set = standard_set(tmp_path)
    assert section_set.sources["feature"] == "feature.yaml"
    assert section_set.sources["Reference"] == "Reference.yml"


def test_a_str_path_is_accepted(tmp_path):
    write_set(tmp_path / "sections", **{"feature__yaml": FEATURE, "_common__yaml": COMMON})
    assert load_sections(str(tmp_path / "sections")).type_names == ("feature",)


def test_refs_resolve_at_load_so_a_spec_needs_no_registry(tmp_path):
    plan = standard_set(tmp_path).types["feature"].sections[2]
    assert plan.heading == "Plan"
    assert plan.placeholder.startswith("| Action | Done when | Rationale |")
    assert plan.seeded_is_complete is True


def test_defaults_are_applied_per_section(tmp_path):
    options = standard_set(tmp_path).types["feature"].sections[1]
    assert (options.level, options.required, options.seeded_is_complete, options.placeholder) == (2, False, False, "")


def test_declaration_order_is_preserved(tmp_path):
    headings = [s.heading for s in standard_set(tmp_path).types["feature"].sections]
    assert headings == ["Summary", "Options considered", "Plan", "Notes / log"]


def test_additional_sections_defaults_true_and_is_read_when_given(tmp_path):
    section_set = standard_set(tmp_path)
    assert section_set.types["feature"].additional_sections is False
    assert section_set.types["Reference"].additional_sections is True


def test_a_declared_level_is_read(tmp_path):
    assert standard_set(tmp_path).types["Reference"].sections[0].level == 3


def test_the_string_mapping_fields_survive_json_dumps(tmp_path):
    """`json.dumps` rejects a bare `mappingproxy`, so the claim is about the
    values, checked the way `test_schemas_loader.py` already checks it."""
    section_set = standard_set(tmp_path)
    assert json.loads(json.dumps(dict(section_set.sources)))["feature"] == "feature.yaml"
    assert "plan-table" in json.loads(json.dumps(dict(section_set.fragments)))


def test_the_constants_are_what_a_caller_splices_into_load_bundle():
    assert DEFAULT_SECTIONS_DIRNAME == "sections"
    assert SECTION_SUFFIXES == (".yaml", ".yml")
    assert DEFAULT_IGNORE == ("sections/*", "*/sections/*")


def test_a_missing_directory_raises_oserror_not_sectionerror(tmp_path):
    """A missing directory is a different caller error and should not be
    dressed up as a format one -- `load_schemas`'s rule, unchanged."""
    with pytest.raises(OSError):
        load_sections(tmp_path / "nope")


def test_a_file_that_is_not_valid_utf8_is_refused(tmp_path):
    """Built here rather than committed: `.gitattributes` marks only okf-io's
    fixtures `-text`, so a byte-exact file under okf-ext could be normalized
    on someone else's checkout."""
    root = tmp_path / "sections"
    root.mkdir()
    (root / "feature.yaml").write_bytes(b"sections:\n  - heading: \xff\xfe\n")
    with pytest.raises(SectionError, match="not valid UTF-8"):
        load_sections(root)


#: Every on-disk refusal fixture, and the substring its message must carry.
#: Asserted from this table rather than restated per test, so a fixture change
#: cannot leave a test quietly asserting the old corpus.
BAD = {
    "not_a_mapping": "must be a mapping",
    "not_valid_yaml": "not valid YAML",
    "sections_not_a_list": "`sections` must be a list",
    "entry_not_a_mapping": "must be a mapping",
    "no_heading": "non-empty `heading`",
    "empty_heading": "non-empty `heading`",
    "both_placeholders": "mutually exclusive",
    "dangling_ref": "names no fragment",
    "heading_collision": "collides",
    "type_collision": "already claimed by",
    "fragment_collision": "already declared by",
    "empty": "no declaration files found",
    "fragments_not_a_mapping": "`fragments` must be a mapping",
    "fragment_not_a_string": "must be a string",
    "placeholder_not_a_string": "`placeholder` must be a string",
    "additional_not_bool": "`additional_sections` must be true or false",
    "bad_level": "`level` must be an integer",
    "bad_required": "`required` must be true or false",
    "bad_seeded": "`seeded_is_complete` must be true or false",
    "bad_ownership": "must be one of",
    "ownership_not_a_string": "must be a string",
    "template_without_placeholder": "needs a non-empty `placeholder`",
    "frontmatter_not_a_mapping": "`frontmatter` must be a mapping",
    "owned_not_a_list": "`owned` must be a list",
    "owned_entry_not_a_string": "non-empty string, got 7",
    "owned_duplicate": "names `title` twice",
    "owned_and_provenance": "is in both `owned` and `provenance`",
}


@pytest.mark.parametrize(("name", "expected"), sorted(BAD.items()))
def test_every_bad_directory_raises_a_legible_section_error(name, expected):
    with pytest.raises(SectionError, match=expected):
        load_sections(SECTIONS_BAD / name)


#: Fixture directories under `sections_bad/` covered by `test_shape_loader.py`'s
#: own `BAD_DIRECTORIES` table instead of this file's `BAD` -- the
#: `directories:` index declaration (§8), added after this table, and never
#: routed through the legacy `okf_ext.sections` import path this file exists
#: to cover. Imported, not restated: a name added to or dropped from
#: `BAD_DIRECTORIES` without a matching change here would otherwise leave a
#: fixture directory asserting nothing and nothing would catch it.
COVERED_BY_SHAPE_LOADER_TESTS = set(BAD_DIRECTORIES)


def test_every_bad_fixture_directory_is_covered_by_the_table():
    """A fixture directory added without a matching entry above would sit on
    disk asserting nothing -- the `SCHEMA_EXPECTED` habit, one layer down."""
    on_disk = {p.name for p in SECTIONS_BAD.iterdir() if p.is_dir()}
    assert on_disk == set(BAD) | COVERED_BY_SHAPE_LOADER_TESTS


def test_a_directory_of_fragments_alone_is_a_set_with_no_types(tmp_path):
    """Not an error: `load_schemas` accepts a directory of `$ref` targets for
    the same reason. The empty-directory refusal is about a wrong path."""
    section_set = load_sections(write_set(tmp_path / "sections", **{"_common__yaml": COMMON}))
    assert section_set.type_names == ()
    assert section_set.fragments["plan-table"]


def test_a_non_declaration_file_is_ignored(tmp_path):
    root = write_set(tmp_path / "sections", **{"feature__yaml": FEATURE, "_common__yaml": COMMON})
    write(root / "README.md", "not a declaration\n")
    assert load_sections(root).type_names == ("feature",)


def test_a_subdirectory_is_not_walked(tmp_path):
    """A declaration set is a directory of declarations, not a tree."""
    root = write_set(tmp_path / "sections", **{"feature__yaml": FEATURE, "_common__yaml": COMMON})
    write_set(root / "nested", **{"other__yaml": REFERENCE})
    assert load_sections(root).type_names == ("feature",)


def test_a_file_named_exactly_dot_yaml_claims_no_type(tmp_path):
    root = write_set(tmp_path / "sections", **{"feature__yaml": FEATURE, "_common__yaml": COMMON})
    write(root / ".yaml", "sections: []\n")
    assert load_sections(root).type_names == ("feature",)
