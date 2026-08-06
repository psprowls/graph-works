"""`plan_row`: what plans, what skips, what silently does nothing."""

from __future__ import annotations

import pytest
from ext_helpers import tabled_bundle, tabled_copy
from okf_ext.tables import Column, TableSpec, apply, plan_row
from okf_io import load_bundle

PLAN = TableSpec(columns=(Column("action"), Column("done_when", synonyms=("done when",)), Column("rationale")))
ROW = {"action": "a new action", "done_when": "it lands", "rationale": "because"}


def _plan(bundle, ids, **kwargs):
    return plan_row(bundle, ids, "Plan", PLAN, ROW, key="action", **kwargs)


def test_a_plan_over_the_four_plan_fixtures_names_every_action():
    bundle = tabled_bundle()
    plan = _plan(bundle, ["plan_ok", "plan_empty", "plan_prose", "plan_absent"])
    assert plan.concept_ids == ("plan_absent", "plan_empty", "plan_ok", "plan_prose")
    assert {s.concept_id: s.action for s in plan.splices} == {
        "plan_ok": "append",
        "plan_empty": "append",
        "plan_prose": "create-table",
        "plan_absent": "create-section",
    }


def test_a_row_already_present_plans_nothing():
    bundle = tabled_bundle()
    existing = {"action": "Port the tolerant reader", "done_when": "x", "rationale": "y"}
    plan = plan_row(bundle, ["plan_ok"], "Plan", PLAN, existing, key="action")
    assert plan.is_empty
    assert plan.skipped == ()


def test_replanning_after_an_apply_converges_to_an_empty_plan(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    assert apply(bundle, _plan(bundle, ["plan_ok"])).ok
    assert _plan(load_bundle(root), ["plan_ok"]).is_empty


def test_a_bad_key_raises_before_any_document_is_read():
    with pytest.raises(ValueError, match="not a column"):
        plan_row(tabled_bundle(), [], "Plan", PLAN, ROW, key="owner")


def test_a_concept_absent_from_the_bundle_is_skipped_not_raised():
    plan = _plan(tabled_bundle(), ["ghost"])
    assert plan.is_empty
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("ghost", "unreadable")]
    assert "not a member" in plan.skipped[0].detail


def test_a_missing_section_under_create_false_is_skipped_with_its_own_reason():
    plan = _plan(tabled_bundle(), ["plan_absent"], create=False)
    assert plan.is_empty
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("plan_absent", "section-missing")]


def test_a_missing_section_under_create_true_plans_a_new_one():
    plan = _plan(tabled_bundle(), ["plan_absent"])
    assert plan.skipped == ()
    assert plan.splices[0].action == "create-section"


def test_an_idempotent_hit_is_not_reported_as_a_skip():
    """A re-run of a generator must not look like a pile of skips."""
    existing = {"action": "Port the tolerant reader", "done_when": "x", "rationale": "y"}
    plan = plan_row(tabled_bundle(), ["plan_ok"], "Plan", PLAN, existing, key="action", create=False)
    assert plan.is_empty
    assert plan.skipped == ()


def test_a_parse_error_concept_is_skipped(tmp_path):
    root = tabled_copy(tmp_path)
    (root / "broken.md").write_text("---\ntype: [\n---\n\n# Broken\n", encoding="utf-8")
    plan = _plan(load_bundle(root), ["broken"])
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("broken", "parse-error")]


def test_duplicate_target_ids_are_deduplicated():
    plan = _plan(tabled_bundle(), ["plan_ok", "plan_ok"])
    assert len(plan.splices) == 1


def test_a_plan_records_the_digest_of_the_body_it_was_computed_against():
    import hashlib

    bundle = tabled_bundle()
    plan = _plan(bundle, ["plan_ok"])
    expected = hashlib.sha256(bundle.concepts["plan_ok"].body.encode("utf-8")).hexdigest()
    assert plan.splices[0].digest == expected


def test_planning_never_writes_and_never_dirties_a_document():
    bundle = tabled_bundle()
    before = bundle.concepts["plan_ok"].body
    _plan(bundle, ["plan_ok", "plan_prose", "plan_absent"])
    assert bundle.concepts["plan_ok"].body == before
    assert bundle.concepts["plan_ok"].serialize() == bundle.concepts["plan_ok"].raw_text
