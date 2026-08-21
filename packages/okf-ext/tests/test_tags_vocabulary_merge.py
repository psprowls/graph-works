"""The vocabulary merge: classification, the splice, and the applier.

Byte-fidelity properties over the whole fixture corpus live in
`test_tags_vocabulary_roundtrip.py`, separate for the reason okf-io's
`test_roundtrip.py` is separate from `test_document.py`: those assert
properties over a corpus, these assert the behaviour of one branch.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import ext_helpers
import pytest
from okf_ext.tags import TagDefinition, TagDrift, VocabularyPlan, apply_vocabulary, plan_vocabulary_merge
from okf_ext.tags.merge import _Anchor, _locate, _render_entry, _scalar
from okf_ext.tags.vocabulary import VocabularyError, load_vocabulary


def test_a_tag_definition_is_frozen_and_defaults_to_live():
    definition = TagDefinition(name="security", description="A defect with a security impact.")
    assert definition.deprecated is False
    assert definition.replaced_by is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        definition.name = "other"  # type: ignore[misc]


def test_a_plan_with_no_refusals_is_ok_and_one_with_no_additions_is_empty():
    plan = VocabularyPlan(
        path=Path("_tags.yaml"),
        before="version: 1\ntags: []\n",
        after="version: 1\ntags: []\n",
        added=(),
        unchanged=("metric",),
        drift=(TagDrift(name="metric", ours="ours", theirs="theirs"),),
        refusals=(),
    )
    assert plan.ok
    assert plan.is_empty


def test_render_omits_the_two_defaults_the_loader_already_supplies():
    lines = _render_entry(
        TagDefinition(name="security", description="A defect with a security impact."),
        "  ",
    )
    assert lines == [
        "  - name: security",
        "    description: A defect with a security impact.",
    ]


def test_render_writes_the_instructions_when_they_are_not_defaults():
    lines = _render_entry(
        TagDefinition(name="kpi", description="Retired.", deprecated=True, replaced_by="metric"),
        "  ",
    )
    assert lines == [
        "  - name: kpi",
        "    description: Retired.",
        "    deprecated: true",
        "    replaced_by: metric",
    ]


def test_render_matches_the_indent_it_is_given():
    lines = _render_entry(TagDefinition(name="perf", description="Speed."), "    ")
    assert lines == ["    - name: perf", "      description: Speed."]


@pytest.mark.parametrize(
    "value",
    [
        "trailing space ",
        " leading space",
        "a comment: like this",
        "hash #tag",
        "true",
        "No",
        "~",
        "",
        "unicode — em dash is fine but a comma, is not",
    ],
)
def test_a_scalar_that_could_be_misread_is_double_quoted(value):
    """`json.dumps` is the minimal correct YAML double-quoted scalar -- the
    same call `code_wiki_okf.init` already makes, for the same reason."""
    import json

    assert _scalar(value) == json.dumps(value)


@pytest.mark.parametrize("value", ["metric", "A measurable quantity.", "data-quality", "v1.2/beta"])
def test_an_ordinary_scalar_is_written_plain(value):
    assert _scalar(value) == value


def test_locate_finds_the_scaffolds_flow_empty_sequence():
    anchor = _locate("version: 1\ntags: []\n# a note\n")
    assert anchor == _Anchor(key_line=2, insert_line=3, indent="  ", flow_empty=True)


def test_locate_appends_after_the_last_entry_of_a_block():
    text = "version: 1\ntags:\n  - name: metric\n    description: A measurable quantity.\n"
    assert _locate(text) == _Anchor(key_line=2, insert_line=5, indent="  ", flow_empty=False)


def test_locate_matches_a_wider_indent_rather_than_imposing_two():
    text = "version: 1\ntags:\n    - name: metric\n      description: A measurable quantity.\n"
    assert _locate(text) == _Anchor(key_line=2, insert_line=5, indent="    ", flow_empty=False)


def test_a_column_zero_comment_ends_the_block_so_entries_land_above_it():
    text = "version: 1\ntags:\n  - name: metric\n    description: A measurable quantity.\n\n# a trailing note\n"
    anchor = _locate(text)
    assert anchor is not None
    assert anchor.insert_line == 5  # before the blank line and the note


def test_an_indented_comment_does_not_extend_the_block():
    text = "version: 1\ntags:\n  - name: metric\n    description: A measurable quantity.\n  # a note inside\n"
    anchor = _locate(text)
    assert anchor is not None
    assert anchor.insert_line == 5


def test_a_bare_tags_key_with_no_entries_defaults_to_two_spaces():
    assert _locate("tags:\nversion: 1\n") == _Anchor(key_line=1, insert_line=2, indent="  ", flow_empty=False)


def test_a_bom_does_not_hide_a_tags_key_on_the_first_line():
    assert _locate("﻿tags:\nversion: 1\n") is not None


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\n",
        "version: 1\ntags: [{name: metric, description: A measurable quantity.}]\n",
        "{version: 1, tags: []}\n",
        "version: 1\n  tags:\n",
    ],
)
def test_locate_declines_a_shape_it_does_not_recognize(text):
    assert _locate(text) is None


SECURITY = TagDefinition(name="security", description="A defect with a security impact.")
PERF = TagDefinition(name="perf", description="A defect whose impact is performance.")


def plan_over(tmp_path, name, definitions):
    return plan_vocabulary_merge(ext_helpers.vocabulary_copy(tmp_path, name), definitions)


def test_an_absent_name_is_added_and_appears_in_after(tmp_path):
    plan = plan_over(tmp_path, "commented.yaml", [SECURITY, PERF])
    assert plan.added == ("security", "perf")
    assert plan.unchanged == ()
    assert plan.ok
    assert not plan.is_empty
    assert "  - name: security" in plan.after
    assert "  - name: perf" in plan.after


def test_the_added_entries_reparse_as_what_they_said(tmp_path):
    """The strongest single check on the splice: the merge's own output goes
    back through the strict loader that will read it on the next install."""
    plan = plan_over(tmp_path, "commented.yaml", [SECURITY, PERF])
    target = tmp_path / "reloaded.yaml"
    target.write_text(plan.after, encoding="utf-8", newline="")
    vocab = load_vocabulary(target)
    assert {"security", "perf"} <= vocab.allowed
    assert vocab.descriptions["security"] == SECURITY.description
    # The human's own entries are still there, unchanged.
    assert {"metric", "finance", "data-quality"} <= vocab.allowed


def test_an_identical_definition_is_unchanged_and_the_file_is_byte_identical(tmp_path):
    already = TagDefinition(name="metric", description="A measurable quantity.")
    plan = plan_over(tmp_path, "commented.yaml", [already])
    assert plan.added == ()
    assert plan.unchanged == ("metric",)
    assert plan.drift == ()
    assert plan.after == plan.before
    assert plan.is_empty


def test_a_reworded_description_is_drift_and_the_humans_wording_survives(tmp_path):
    ours = TagDefinition(name="metric", description="Something we measure.")
    plan = plan_over(tmp_path, "commented.yaml", [ours])
    assert plan.drift == (TagDrift(name="metric", ours="Something we measure.", theirs="A measurable quantity."),)
    assert plan.unchanged == ("metric",)  # drift still counts as present for write purposes
    assert plan.ok  # a description is prose the human owns; it is never a refusal
    assert plan.after == plan.before


def test_an_entry_with_no_description_at_all_drifts_against_the_empty_string(tmp_path):
    """`wide_indent.yaml`'s `kpi` carries no description. Reporting drift with
    `theirs=""` is more useful than pretending the field matched."""
    ours = TagDefinition(name="kpi", description="Retired.", deprecated=True, replaced_by="metric")
    plan = plan_over(tmp_path, "wide_indent.yaml", [ours])
    assert plan.drift == (TagDrift(name="kpi", ours="Retired.", theirs=""),)
    assert plan.ok


def test_a_differing_deprecated_flag_refuses_that_tag_alone(tmp_path):
    ours = TagDefinition(name="metric", description="A measurable quantity.", deprecated=True)
    plan = plan_over(tmp_path, "commented.yaml", [ours, SECURITY])
    assert [f.path for f in plan.refusals] == ["metric"]
    assert plan.refusals[0].kind == "foreign-content"
    assert not plan.ok
    assert plan.added == ("security",)  # the neighbour still lands
    assert "  - name: security" in plan.after


def test_a_differing_replaced_by_refuses_that_tag_alone(tmp_path):
    ours = TagDefinition(name="kpi", description="Retired.", deprecated=True, replaced_by="finance")
    plan = plan_over(tmp_path, "wide_indent.yaml", [ours])
    assert [f.path for f in plan.refusals] == ["kpi"]
    assert plan.after == plan.before


def test_a_malformed_vocabulary_refuses_the_whole_merge(tmp_path):
    target = tmp_path / "_tags.yaml"
    target.write_bytes(ext_helpers.BAD_VERSION.read_bytes())
    plan = plan_vocabulary_merge(target, [SECURITY])
    assert len(plan.refusals) == 1
    assert plan.refusals[0].kind == "foreign-content"
    assert plan.refusals[0].path == "_tags.yaml"
    assert plan.added == ()
    assert plan.after == plan.before


def test_invalid_utf8_bytes_refuse_the_whole_merge_rather_than_raising(tmp_path):
    """`load_vocabulary` treats bytes that are not valid UTF-8 as ordinary
    content, not a caller error -- this must not crash with
    `UnicodeDecodeError` either, and must refuse the whole file the same way
    any other malformed vocabulary does."""
    target = tmp_path / "_tags.yaml"
    target.write_bytes(b'version: 1\ntags:\n  - name: metric\n    description: "bad byte: \xff"\n')
    plan = plan_vocabulary_merge(target, [SECURITY])
    assert len(plan.refusals) == 1
    assert plan.refusals[0].kind == "foreign-content"
    assert plan.after == plan.before
    assert plan.added == ()


@pytest.mark.parametrize("name", ["no_tags_key.yaml", "flow_entries.yaml"])
def test_a_shape_the_locator_declines_refuses_the_whole_merge(tmp_path, name):
    plan = plan_over(tmp_path, name, [SECURITY])
    assert len(plan.refusals) == 1
    assert plan.refusals[0].kind == "foreign-content"
    assert plan.added == ()
    assert plan.after == plan.before


def test_a_missing_path_propagates_oserror(tmp_path):
    """A path that is not there is caller error, not content -- exactly what
    `load_vocabulary` already promises."""
    with pytest.raises(OSError):
        plan_vocabulary_merge(tmp_path / "nope.yaml", [SECURITY])


@pytest.mark.parametrize(
    ("definitions", "match"),
    [
        ([TagDefinition(name="  ", description="x")], "non-empty"),
        ([SECURITY, TagDefinition(name="security", description="again")], "twice"),
        ([TagDefinition(name="x", description="y", replaced_by="metric")], "deprecated"),
    ],
)
def test_a_malformed_definition_is_caller_error(tmp_path, definitions, match):
    """P-1: these would render entries `load_vocabulary` then rejects, so the
    *next* install would refuse the whole file as foreign content -- our own
    output breaking our own idempotence. Caller configuration is always an
    exception here (`vocabulary.py`'s own contract)."""
    with pytest.raises(VocabularyError, match=match):
        plan_over(tmp_path, "commented.yaml", definitions)


def test_the_scaffolds_flow_empty_sequence_becomes_a_block(tmp_path):
    plan = plan_over(tmp_path, "empty.yaml", [SECURITY])
    assert "tags: []" not in plan.after
    assert "tags:\n  - name: security\n" in plan.after
    # The scaffold's explanatory comment block is untouched, below the entry.
    assert "# Add tags here as the bundle needs them" in plan.after


def test_a_crlf_file_gains_crlf_lines(tmp_path):
    plan = plan_over(tmp_path, "crlf.yaml", [SECURITY])
    assert "\r\n  - name: security\r\n" in plan.after
    assert "\n" not in plan.after.replace("\r\n", "")


def test_a_bom_and_a_missing_trailing_newline_both_survive(tmp_path):
    plan = plan_over(tmp_path, "bom.yaml", [SECURITY])
    assert plan.after.startswith("﻿")
    assert not plan.after.endswith("\n")
    assert "  - name: security" in plan.after


def test_apply_writes_the_plan(tmp_path):
    target = ext_helpers.vocabulary_copy(tmp_path, "commented.yaml")
    plan = plan_vocabulary_merge(target, [SECURITY])
    result = apply_vocabulary(plan)
    assert result.ok
    assert result.written == ("_tags.yaml",)
    assert target.read_bytes().decode("utf-8") == plan.after


def test_apply_is_idempotent(tmp_path):
    target = ext_helpers.vocabulary_copy(tmp_path, "commented.yaml")
    apply_vocabulary(plan_vocabulary_merge(target, [SECURITY, PERF]))
    after_first = target.read_bytes()

    again = plan_vocabulary_merge(target, [SECURITY, PERF])
    assert again.is_empty
    assert again.unchanged == ("security", "perf")
    result = apply_vocabulary(again)
    assert result.written == ()
    assert target.read_bytes() == after_first


def test_apply_writes_nothing_for_an_empty_plan_and_still_reports_refusals(tmp_path):
    target = ext_helpers.vocabulary_copy(tmp_path, "commented.yaml")
    original = target.read_bytes()
    ours = TagDefinition(name="metric", description="A measurable quantity.", deprecated=True)
    result = apply_vocabulary(plan_vocabulary_merge(target, [ours]))
    assert not result.ok
    assert [f.path for f in result.failed] == ["metric"]
    assert target.read_bytes() == original


def test_apply_reports_a_refusal_once_not_twice(tmp_path):
    """`write_all` merges the plan's own refusals into its result. A caller
    reading both lists would report each one twice."""
    target = ext_helpers.vocabulary_copy(tmp_path, "commented.yaml")
    ours = TagDefinition(name="metric", description="A measurable quantity.", deprecated=True)
    result = apply_vocabulary(plan_vocabulary_merge(target, [ours, SECURITY]))
    assert len(result.failed) == 1
    assert result.written == ("_tags.yaml",)
