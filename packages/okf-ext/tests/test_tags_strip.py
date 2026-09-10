from __future__ import annotations

import pytest
from ext_helpers import bundle_copy, tagged_bundle, write
from okf_ext.tags.rename import apply, plan_strip
from okf_io import load_bundle


def edits_for(plan, concept_id):
    return sorted((e.index, e.old, e.new) for e in plan.edits if e.concept_id == concept_id)


def test_plan_strip_removes_every_occurrence():
    plan = plan_strip(tagged_bundle(), ["kpi"])
    assert plan.concept_ids == ("block", "merge_me", "underscore")
    assert edits_for(plan, "block") == [(2, "kpi", None)]
    assert edits_for(plan, "merge_me") == [(0, "kpi", None)]
    assert all(edit.new is None for edit in plan.edits)


def test_plan_strip_takes_several_tags_at_once():
    plan = plan_strip(tagged_bundle(), ["kpi", "metrics"])
    assert edits_for(plan, "merge_me") == [(0, "kpi", None), (2, "metrics", None)]


def test_plan_strip_of_an_absent_tag_is_empty():
    assert plan_strip(tagged_bundle(), ["nothing-uses-this"]).is_empty


def test_plan_strip_refuses_a_bare_string():
    with pytest.raises(TypeError, match="wrap it in a list"):
        plan_strip(tagged_bundle(), "kpi")


def test_plan_strip_reports_the_same_skips_as_every_other_planner():
    plan = plan_strip(tagged_bundle(), ["finance"])
    assert {(s.concept_id, s.reason) for s in plan.skipped} == {
        ("broken", "parse-error"),
        ("scalar_tags", "tags-not-a-sequence"),
    }
    # `broken.md` and `scalar_tags.md` both carry `finance`; neither is planned.
    assert "broken" not in plan.concept_ids
    assert "scalar_tags" not in plan.concept_ids


def test_plan_strip_never_matches_a_non_string_position(tmp_path):
    root = tmp_path / "b"
    write(
        root / "numeric.md",
        "---\ntype: Reference\ntitle: Numeric\ntags: [42, keep-me]\n---\n\n# Numeric\n",
    )
    plan = plan_strip(load_bundle(root), ["42"])
    assert plan.is_empty


def test_strip_applied_leaves_the_surviving_tags_and_nothing_else(tmp_path):
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    result = apply(bundle, plan_strip(bundle, ["kpi", "metrics"]))
    assert result.ok, result.failed
    assert load_bundle(root).concepts["merge_me"].fm.tags == ("metric",)
    assert load_bundle(root).concepts["block"].fm.tags == ("Data Quality", "ga4")


def test_stripping_every_tag_leaves_an_empty_sequence(tmp_path):
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    result = apply(bundle, plan_strip(bundle, ["metric", "metrics", "kpi"]))
    assert result.ok, result.failed
    assert load_bundle(root).concepts["merge_me"].fm.tags == ()
