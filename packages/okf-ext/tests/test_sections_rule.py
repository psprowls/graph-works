"""`section_rule`: the four codes, and everything that must *not* fire."""

from __future__ import annotations

import pytest
from ext_helpers import SECTIONS_DIR, sectioned_bundle, write, write_bundle, write_tree
from okf_ext.sections import CODES, TOPIC, load_sections, section_rule
from okf_io import validate

DECLARATIONS = """
sections:
  - heading: Summary
    required: true
    placeholder: |
      <!-- fill me in -->
  - heading: Options considered
  - heading: Plan
    required: true
    seeded_is_complete: true
    placeholder: |
      | Action | Done when |
      | --- | --- |
  - heading: Notes / log
"""

STRICT = """
additional_sections: false
sections:
  - heading: Summary
    required: true
"""


def build(tmp_path, files, *, declarations=DECLARATIONS, extra=None):
    """A purpose-built corpus plus its declaration set, loaded together."""
    root = tmp_path / "kb"
    sections_dir = root / "sections"
    sections_dir.mkdir(parents=True)
    write(sections_dir / "Feature.yaml", declarations)
    if extra is not None:
        write_tree(sections_dir, extra)
    bundle = write_bundle(root, files, ignore=("sections/*",))
    return bundle, load_sections(sections_dir)


def codes(bundle, section_set, **kwargs):
    report = validate(bundle, today="2026-08-07", extra_rules=[section_rule(section_set, **kwargs)])
    return [f for f in report.findings if f.code.startswith("sections.")]


def doc(body, *, type_name="Feature"):
    return f"---\ntype: {type_name}\ntitle: T\ndescription: D\n---\n\n{body}"


def test_the_topic_is_the_module_name_and_every_code_carries_it():
    assert TOPIC == "sections"
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)


def test_the_four_codes_are_exactly_these():
    assert CODES == (
        "sections.missing",
        "sections.unfilled",
        "sections.unexpected",
        "sections.no-declaration-for-type",
    )


def test_a_missing_required_section_fires_missing_with_no_line(tmp_path):
    """The fault is the document's, not any line's, so naming one would be a
    guess -- `schemas/rule.py`'s `_where()` reasoning for the same shape."""
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n")})
    found = [f for f in codes(bundle, section_set) if f.code == "sections.missing"]
    assert [(f.path, f.line) for f in found] == [("a.md", None)]
    assert "Plan" in found[0].message
    assert found[0].spec == "Feature.yaml"


def test_an_optional_section_is_never_missing(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n\n## Plan\n\n| a |\n")})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_a_section_holding_its_placeholder_fires_unfilled(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\n<!-- fill me in -->\n\n## Plan\n\n| a |\n")})
    found = [f for f in codes(bundle, section_set) if f.code == "sections.unfilled"]
    assert len(found) == 1
    assert "placeholder" in found[0].message


def test_an_empty_required_section_fires_unfilled_too(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\n## Plan\n\n| a |\n")})
    found = [f for f in codes(bundle, section_set) if f.code == "sections.unfilled"]
    assert len(found) == 1
    assert "empty" in found[0].message


def test_unfilled_reports_the_headings_own_file_line(tmp_path):
    """Body-relative line 1 sits at file line 1 + `body_line_offset`. Five
    frontmatter lines and a blank put `## Summary` on file line 7."""
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\n## Plan\n\n| a |\n")})
    found = [f for f in codes(bundle, section_set) if f.code == "sections.unfilled"]
    assert found[0].line == 7


def test_whitespace_differences_alone_do_not_count_as_filled(tmp_path):
    """Detection is equality after normalising line endings, stripping each
    line on both sides, and dropping leading and trailing blanks."""
    body = "## Summary\n\n\n  <!-- fill me in -->   \n\n\n## Plan\n\n| a |\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)})
    assert [f.code for f in codes(bundle, section_set)] == ["sections.unfilled"]


def test_one_edited_word_marks_a_placeholder_filled(tmp_path):
    """The known, accepted cost of equality-based detection (spec §5.2). Pinned
    so a future change to the comparison is a deliberate one."""
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\n<!-- fill me in now -->\n\n## Plan\n\n| a |\n")})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_seeded_is_complete_suppresses_unfilled_for_that_section(tmp_path):
    """A file map gets its empty table and then stays quiet."""
    body = "## Summary\n\nReal.\n\n## Plan\n\n| Action | Done when |\n| --- | --- |\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_an_empty_seeded_section_is_also_quiet(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n\n## Plan\n")})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_an_optional_section_never_fires_unfilled(tmp_path):
    """Spec §3.3: optional sections exist so a legitimate heading is not
    'extra', and are never warned about -- otherwise the work lane's own
    `## Options considered` would warn forever, which is the outcome the
    binary required/extra model was rejected for."""
    body = "## Summary\n\nReal.\n\n## Options considered\n\n## Plan\n\n| a |\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_an_undeclared_heading_is_quiet_when_additional_sections_is_true(tmp_path):
    body = "## Summary\n\nReal.\n\n## Plan\n\n| a |\n\n## Appendix\n\nFine.\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_an_undeclared_heading_fires_unexpected_when_it_is_false(tmp_path):
    bundle, section_set = build(
        tmp_path,
        {"a.md": doc("## Summary\n\nReal.\n\n## Appendix\n\nNo.\n")},
        declarations=STRICT,
    )
    found = [f for f in codes(bundle, section_set) if f.code == "sections.unexpected"]
    assert len(found) == 1
    assert "Appendix" in found[0].message
    assert found[0].line == 11


def test_only_declared_levels_are_considered(tmp_path):
    """Without this, `additional_sections: false` makes every nested heading in
    the corpus a finding, and the setting is unusable on real prose."""
    body = "## Summary\n\nReal.\n\n### A sub-heading\n\nStill fine.\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)}, declarations=STRICT)
    assert [f.code for f in codes(bundle, section_set)] == []


def test_a_declared_level_three_section_is_matched_at_level_three(tmp_path):
    declarations = "sections:\n  - heading: Entries\n    level: 3\n    required: true\n"
    bundle, section_set = build(tmp_path, {"a.md": doc("### Entries\n\nReal.\n")}, declarations=declarations)
    assert [f.code for f in codes(bundle, section_set)] == []


def test_a_heading_at_an_undeclared_level_does_not_satisfy_a_required_section(tmp_path):
    declarations = "sections:\n  - heading: Entries\n    level: 3\n    required: true\n"
    bundle, section_set = build(tmp_path, {"a.md": doc("## Entries\n\nReal.\n")}, declarations=declarations)
    assert [f.code for f in codes(bundle, section_set)] == ["sections.missing"]


def test_matching_is_casefolded_and_stripped(tmp_path):
    body = "##    sUMMARY   \n\nReal.\n\n## plan\n\n| a |\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)})
    assert [f.code for f in codes(bundle, section_set)] == []


def test_a_blockquoted_heading_is_neither_matched_nor_reported(tmp_path):
    """`okf_ext.body.sections()` excludes it already, following `okf_io._md`'s
    `quoted` precedent: a heading in a blockquote is somebody else's content."""
    body = "## Summary\n\nReal.\n\n> ## Appendix\n>\n> Quoted.\n"
    bundle, section_set = build(tmp_path, {"a.md": doc(body)}, declarations=STRICT)
    assert [f.code for f in codes(bundle, section_set)] == []


def test_a_type_with_no_declaration_reports_a_coverage_gap(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n", type_name="Glossary")})
    found = codes(bundle, section_set)
    assert [(f.code, f.path) for f in found] == [("sections.no-declaration-for-type", "a.md")]
    assert found[0].line == 2  # the `type:` key's own file line


def test_a_parse_error_is_skipped_rather_than_re_reported(tmp_path):
    """okf-io's catalog already reported it."""
    bundle, section_set = build(tmp_path, {"a.md": "---\ntype: [unclosed\n---\n\n## X\n"})
    assert codes(bundle, section_set) == []


@pytest.mark.parametrize("frontmatter", ["title: T\n", "type: ''\ntitle: T\n", "type: '   '\ntitle: T\n"])
def test_a_document_with_no_usable_type_is_skipped(tmp_path, frontmatter):
    bundle, section_set = build(tmp_path, {"a.md": f"---\n{frontmatter}---\n\n## X\n"})
    assert codes(bundle, section_set) == []


def test_severity_defaults_to_warn(tmp_path):
    """`Report.ok` is a claim about OKF v0.2 conformance, and a house rule has
    no business making a conformant bundle look otherwise."""
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n")})
    assert {f.severity for f in codes(bundle, section_set)} == {"warn"}


def test_severity_passes_through_to_the_first_three_codes(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n")})
    assert {f.severity for f in codes(bundle, section_set, severity="error")} == {"error"}


def test_no_declaration_for_type_stays_warn_regardless(tmp_path):
    """It reports a coverage gap in the declaration set, not a violation by the
    document -- mirroring `schemas.no-schema-for-type`."""
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n", type_name="Glossary")})
    assert [f.severity for f in codes(bundle, section_set, severity="error")] == ["warn"]


def test_the_declaration_filename_is_the_citation(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nReal.\n")})
    assert {f.spec for f in codes(bundle, section_set)} == {"Feature.yaml"}


def test_the_set_directory_name_is_the_citation_for_a_coverage_gap(tmp_path):
    bundle, section_set = build(tmp_path, {"a.md": doc("## Summary\n\nR.\n", type_name="Glossary")})
    assert [f.spec for f in codes(bundle, section_set)] == ["sections"]


def test_the_rule_runs_over_the_committed_corpus_without_raising():
    """Nothing on the content path raises -- a malformed concept is a
    `Finding` or a skip, never an exception. `report.ok` is not asserted here:
    the corpus deliberately includes `broken.md` and `untyped.md` to exercise
    the skip paths, and those trip okf-io's own `frontmatter.unparseable` and
    `frontmatter.missing-type` -- unrelated to this rule, but still `error`
    severity in the same `Report`."""
    section_set = load_sections(SECTIONS_DIR)
    report = validate(sectioned_bundle(), today="2026-08-07", extra_rules=[section_rule(section_set)])
    assert all(f.severity == "warn" for f in report.findings if f.code.startswith("sections."))
