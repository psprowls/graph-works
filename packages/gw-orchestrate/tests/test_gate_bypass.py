"""`gw work advance --skip-gate`: one code, one reason, one actor, one ledger entry."""

from __future__ import annotations

from pathlib import Path

import pytest
from _gate_helpers import gate_ready, place_item
from gw_orchestrate import stage_advance as stage
from test_orchestrate_shell import TODAY, _code_repo, _dirty, _initialized_workspace, _ready
from work_tracker_okf import decisions as _decisions

PATH = "work/feature-a"


def _ledger(layout) -> Path:
    return _decisions.ledger_ref(PATH).path(layout.bundle_dir)


def _setup(tmp_path: Path):
    layout = _initialized_workspace(tmp_path)
    repo, fork = _code_repo(tmp_path / "c")
    _ready(layout, PATH)
    return layout, repo, fork


def test_a_matching_attributed_bypass_advances_and_records_one_entry(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    wt = place_item(layout, repo, PATH, commit=False)
    gate_ready(layout, repo, PATH, tree_of=wt)
    result = stage.run_stage_advance(
        layout,
        PATH,
        today=TODAY,
        repo=repo,
        dry_run=False,
        skip_gate="no-commits",
        skip_reason="no commits expected",
        actor="pat",
    )
    assert result.outcome.plan.refusal is None and result.outcome.written
    assert result.gate_bypass is not None and result.gate_bypass.code == "no-commits"
    [entry] = _decisions.load(_ledger(layout)).entries
    assert entry.status == "answered" and "no-commits" in entry.question
    assert entry.decided is not None and "pat" in entry.decided
    assert "no commits expected" in entry.prose
    assert result.gate_bypass.decision_id == entry.id
    assert "phase: finish" in (layout.bundle_dir / f"{PATH}.md").read_text(encoding="utf-8")


def test_a_mismatched_bypass_code_refuses_and_writes_nothing(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    _dirty(place_item(layout, repo, PATH))
    page = layout.bundle_dir / f"{PATH}.md"
    before = page.read_bytes()
    result = stage.run_stage_advance(
        layout,
        PATH,
        today=TODAY,
        repo=repo,
        dry_run=False,
        skip_gate="no-commits",
        skip_reason="r",
        actor="pat",
    )
    assert result.outcome.plan.refusal == "gate-bypass-mismatch"
    assert "uncommitted-work" in result.outcome.plan.detail
    assert page.read_bytes() == before and not _ledger(layout).exists()


def test_a_bypass_of_a_passing_gate_is_unused(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    wt = place_item(layout, repo, PATH)
    gate_ready(layout, repo, PATH, tree_of=wt)
    result = stage.run_stage_advance(
        layout,
        PATH,
        today=TODAY,
        repo=repo,
        dry_run=False,
        skip_gate="no-commits",
        skip_reason="r",
        actor="pat",
    )
    assert result.outcome.plan.refusal == "gate-bypass-unused" and not _ledger(layout).exists()


@pytest.mark.parametrize(
    ("reason", "actor", "code"),
    [("", "pat", "no-commits"), ("r", " ", "no-commits"), ("r", "pat", "finish-incomplete")],
)
def test_an_invalid_bypass_request_refuses(tmp_path: Path, reason: str, actor: str, code: str) -> None:
    layout, repo, _fork = _setup(tmp_path)
    result = stage.run_stage_advance(
        layout, PATH, today=TODAY, repo=repo, dry_run=False, skip_gate=code, skip_reason=reason, actor=actor
    )
    assert result.outcome.plan.refusal == "gate-bypass-invalid" and not _ledger(layout).exists()


def test_an_unrecordable_bypass_leaves_the_item_at_execute(tmp_path: Path, monkeypatch) -> None:
    layout, repo, _fork = _setup(tmp_path)
    wt = place_item(layout, repo, PATH, commit=False)
    gate_ready(layout, repo, PATH, tree_of=wt)
    monkeypatch.setattr(
        stage._decisions,
        "plan_append",
        lambda ledger, **_kw: _decisions.plan_refusal(ledger, "answer-required", "forced"),
    )
    result = stage.run_stage_advance(
        layout, PATH, today=TODAY, repo=repo, dry_run=False, skip_gate="no-commits", skip_reason="r", actor="pat"
    )
    assert result.outcome.plan.refusal == "gate-bypass-unrecorded"
    assert "phase: execute" in (layout.bundle_dir / f"{PATH}.md").read_text(encoding="utf-8")


def test_a_dry_run_bypass_plans_and_writes_nothing(tmp_path: Path) -> None:
    layout, repo, _fork = _setup(tmp_path)
    wt = place_item(layout, repo, PATH, commit=False)
    gate_ready(layout, repo, PATH, tree_of=wt)
    result = stage.run_stage_advance(
        layout, PATH, today=TODAY, repo=repo, dry_run=True, skip_gate="no-commits", skip_reason="r", actor="pat"
    )
    assert result.outcome.plan.refusal is None and result.gate_bypass is not None
    assert result.gate_bypass.decision_id is None and not _ledger(layout).exists()


def test_a_bypass_outside_execute_to_finish_is_unused(tmp_path: Path) -> None:
    layout = _initialized_workspace(tmp_path)
    repo, _fork = _code_repo(tmp_path / "c")
    _ready(layout, PATH, phase="plan")
    result = stage.run_stage_advance(
        layout, PATH, today=TODAY, repo=repo, dry_run=False, skip_gate="no-commits", skip_reason="r", actor="pat"
    )
    assert result.outcome.plan.refusal == "gate-bypass-unused"
