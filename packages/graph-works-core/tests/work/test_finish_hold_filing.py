"""Intentional finish holds survive the journal without weakening ordinary lint."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from _transaction_helpers import _plan
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import transactions
from okf_io import load, load_bundle, validate
from test_hold_filing import FEATURE, TODAY, _add, _draft, _layout, _page, _snapshot
from work_tracker_okf.items import IGNORE
from work_tracker_okf.rules import lane_rules


def _finish_errors(layout):
    return [
        finding
        for finding in validate(
            load_bundle(layout.bundle_dir, ignore=IGNORE), today=TODAY, extra_rules=lane_rules()
        ).errors
        if finding.code == "decisions.open-at-finish"
    ]


@pytest.mark.parametrize("shape", ["question", "skip", "park"])
@pytest.mark.parametrize("redirected", [False, True])
def test_finish_hold_is_journaled_blocks_and_can_be_answered(tmp_path: Path, shape, redirected) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    target = f"{FEATURE}/children/bug-held" if redirected else FEATURE
    if redirected:
        _page(layout, target, type="Bug", phase="finish")
    kwargs = {"affects": (target,)} if shape == "question" else {"hold": shape, "phase": "finish"}
    draft = _draft(tmp_path, phase="finish", item=target) if shape == "park" else None
    if draft:
        kwargs["checkpoint"] = draft
    result = work.run_decision_add(
        layout, target, question="Pause finish?", on=TODAY, decided_by="coordinator", dry_run=False, **kwargs
    )
    assert result.plan.refusal is None
    assert result.application is not None and result.application.ok, result.application
    entry = result.plan.primary
    assert result.owner.owner_path == FEATURE
    records = [json.loads(line) for line in result.application.journal.read_text(encoding="utf-8").splitlines()]
    assert [r["state"] for r in records] == ["planned", "applying", "validating", "complete"]
    assert records[0]["allowed_new_findings"] == [[f"{FEATURE}.md", "decisions.open-at-finish"]]
    assert any(
        "operation-specific" in note and "decisions.open-at-finish" in note for note in result.application.warnings
    )
    assert not any(
        "pre-existing" in note and "decisions.open-at-finish" in note for note in result.application.warnings
    )
    assert any("operation-specific" in note for note in result.warnings)
    assert [f.path for f in _finish_errors(layout)] == [f"{FEATURE}.md"]
    assert work.run_next(layout, target).route.blockers[0].startswith(f"open decision {entry.id} ({shape})")
    assert stage.run_stage_advance(layout, target, today=TODAY, dry_run=False).outcome.plan.refusal == "blocked"
    checkpoint = layout.bundle_dir / target / "references/04-finish-checkpoint-D-001.md"
    if draft:
        assert checkpoint.read_text(encoding="utf-8") == draft.read_text(encoding="utf-8").replace(
            "decision: pending", "decision: D-001"
        )
    answered = work.run_decision_answer(
        layout, target, entry.id, answer="Continue", on=TODAY, decided_by="user", dry_run=False
    )
    assert answered.application is not None and answered.application.ok
    assert not _finish_errors(layout)
    assert not any("open decision" in b for b in work.run_next(layout, target).route.blockers)
    if draft:
        assert checkpoint.is_file()


def test_multiple_finish_holds_block_until_the_last_is_released(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    first = _add(layout, affects=(FEATURE,))
    second = _add(layout, hold="skip", phase="finish")
    assert first.application.ok and second.application.ok
    answered = work.run_decision_answer(
        layout, FEATURE, first.plan.primary.id, answer="go", on=TODAY, decided_by="user", dry_run=False
    )
    assert answered.application.ok
    assert second.plan.primary.id in work.run_next(layout, FEATURE).route.blockers[0]
    superseded = work.run_decision_supersede(
        layout,
        FEATURE,
        second.plan.primary.id,
        question="Resolved?",
        answer="go",
        on=TODAY,
        decided_by="user",
        dry_run=False,
    )
    assert superseded.application.ok
    assert not _finish_errors(layout)
    assert not any("open decision" in b for b in work.run_next(layout, FEATURE).route.blockers)


@pytest.mark.parametrize("affects", [(), ("work/missing",)])
def test_ordinary_finish_decision_without_a_real_hold_still_rolls_back(tmp_path: Path, affects) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    before = _snapshot(layout)
    result = _add(layout, affects=affects)
    assert result.application is not None and not result.application.ok
    assert "decisions.open-at-finish" in result.application.failures[0]
    assert _snapshot(layout) == before


def test_owner_can_file_a_named_question_for_its_child_at_finish(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    child = f"{FEATURE}/children/bug-held"
    _page(layout, child, type="Bug")
    result = _add(layout, affects=(child,))
    assert result.application is not None and result.application.ok
    assert "open decision D-001" in work.run_next(layout, child).route.blockers[0]


def test_finish_park_rolls_back_every_effect_on_unrelated_validation_error(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    before = _snapshot(layout)
    original = work.apply_mutation

    def invalid_owner(layout_, mutation, **kwargs):
        writes = tuple(
            replace(w, after=w.after.replace(b"description: d\n", b"")) if w.member == f"{FEATURE}.md" else w
            for w in mutation.writes
        )
        return original(layout_, replace(mutation, writes=writes), **kwargs)

    monkeypatch.setattr(work, "apply_mutation", invalid_owner)
    result = _add(layout, hold="park", phase="finish", checkpoint=_draft(tmp_path, phase="finish"))
    assert result.application is not None and not result.application.ok
    assert result.application.rolled_back
    assert "schemas." in result.application.failures[0]
    assert _snapshot(layout) == before


def test_finish_allowance_requires_a_captured_baseline(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    before = _snapshot(layout)

    def unavailable(*args, **kwargs):
        raise OSError("baseline unavailable")

    monkeypatch.setattr(transactions, "_capture_validation_state", unavailable)
    result = _add(layout, hold="skip", phase="finish")
    assert result.application is not None and not result.application.ok
    assert "decisions.open-at-finish" in result.application.failures[0]
    assert any("baseline capture failed" in note for note in result.application.warnings)
    assert _snapshot(layout) == before


def test_ordinary_advance_gets_no_finish_allowance(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout)
    result = _add(layout)  # unnamed: no routing hold, but ordinary lint still gates finish
    assert result.application.ok
    advanced = stage.run_stage_advance(layout, FEATURE, today=TODAY, dry_run=False)
    assert advanced.application is not None and not advanced.application.ok
    assert "decisions.open-at-finish" in advanced.application.failures[0]
    assert load(layout.bundle_dir / f"{FEATURE}.md").fm_data()["phase"] == "execute"


@pytest.mark.parametrize(
    ("findings", "expected_failures"),
    [
        (((f"{FEATURE}.md", "decisions.open-at-finish"),), 0),
        (((f"{FEATURE}.md", "decisions.open-at-finish"),) * 2, 1),
        (((f"{FEATURE}.md", "decisions.entry-invalid"),), 1),
        ((("work/feature-other.md", "decisions.open-at-finish"),), 1),
    ],
)
def test_transaction_allowance_is_counted_by_exact_member_and_code(tmp_path, monkeypatch, findings, expected_failures):
    from okf_io import Finding

    layout = _layout(tmp_path)
    _page(layout)
    _page(layout, "work/feature-other")
    plan = _plan(layout, validate_paths=(FEATURE, "work/feature-other"))

    def controlled_rule(ctx):
        for path, code in findings:
            yield Finding(code=code, severity="error", path=path, message="new finding", spec="test")

    monkeypatch.setattr(transactions, "_extra_rules", lambda *args: (controlled_rule,))
    notes = []
    failures = transactions._validate_postconditions(
        layout,
        plan,
        baseline=transactions._ValidationState(findings={}, conditions={}),
        excused=notes,
        allowed_new_findings=((f"{FEATURE}.md", "decisions.open-at-finish"),),
    )
    assert len(failures) == expected_failures
    assert not any("pre-existing" in note for note in notes)


@pytest.mark.parametrize("kind", ["foreign-owner", "resolved", "done"])
def test_question_naming_no_live_item_in_this_ledger_gets_no_allowance(tmp_path: Path, kind) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    target = "work/feature-other" if kind == "foreign-owner" else f"{FEATURE}/children/bug-held"
    _page(
        layout,
        target,
        type="Feature" if kind == "foreign-owner" else "Bug",
        phase="done" if kind == "done" else "finish",
        work_status="resolved" if kind == "resolved" else "in-progress",
    )
    before = _snapshot(layout)
    result = _add(layout, affects=(target,))
    assert result.application is not None and not result.application.ok
    assert "decisions.open-at-finish" in result.application.failures[0]
    assert _snapshot(layout) == before


def test_standalone_fallback_owner_has_the_same_finish_policy(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/bug-held"
    _page(layout, path, type="Bug", phase="finish")
    result = work.run_decision_add(
        layout, path, question="Pause?", hold="skip", phase="finish", on=TODAY, decided_by="coordinator", dry_run=False
    )
    assert result.application is not None and result.application.ok
    record = json.loads(result.application.journal.read_text(encoding="utf-8").splitlines()[0])
    assert record["allowed_new_findings"] == [["work/bug-held.md", "decisions.open-at-finish"]]
    assert [f.path for f in _finish_errors(layout)] == ["work/bug-held.md"]
    assert any(
        "operation-specific validation allowance" in note and "decisions.open-at-finish" in note
        for note in result.application.warnings
    )
    assert any("operation-specific validation allowance" in note for note in result.warnings)
    assert not any("pre-existing" in note for note in result.application.warnings)
    assert "open decision D-001 (skip)" in work.run_next(layout, path).route.blockers[0]


@pytest.mark.parametrize("tamper", [False, True])
def test_finish_hold_completion_evidence_includes_the_allowance(tmp_path: Path, monkeypatch, tamper) -> None:
    layout = _layout(tmp_path)
    _page(layout, phase="finish")
    before = _snapshot(layout)
    original = transactions._append_journal

    def uncertain_complete(journal, state, **details):
        original(journal, state, **details)
        if state == "complete":
            if tamper:
                records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
                records[0]["allowed_new_findings"] = []
                journal.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8", newline="")
            raise OSError("complete acknowledgement lost")

    monkeypatch.setattr(transactions, "_append_journal", uncertain_complete)
    result = _add(layout, hold="park", phase="finish", checkpoint=_draft(tmp_path, phase="finish"))
    assert result.application is not None
    assert result.application.ok is (not tamper)
    if tamper:
        assert result.application.rolled_back
        assert _snapshot(layout) == before
    else:
        assert (layout.bundle_dir / FEATURE / "references/04-finish-checkpoint-D-001.md").is_file()
        assert "open decision D-001" in work.run_next(layout, FEATURE).route.blockers[0]
