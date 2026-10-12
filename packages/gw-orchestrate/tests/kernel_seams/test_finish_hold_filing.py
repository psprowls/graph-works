"""Intentional finish holds survive the journal without weakening ordinary lint."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from gw_kernel import apply_init, plan_init
from gw_orchestrate import stage_advance as stage
from gw_work import commands as work
from okf_io import load, load_bundle, validate
from work_tracker_okf.items import IGNORE
from work_tracker_okf.rules import lane_rules

TODAY = date(2026, 9, 13)

FEATURE = "work/feature-held"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Holds")).layout


def _page(
    layout,
    path: str = FEATURE,
    *,
    type: str = "Feature",
    phase: str | None = "execute",
    work_status: str = "in-progress",
    affects: str = "packages/a",
):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    phase_line = f"phase: {phase}\n" if phase else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: Held\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\nowner: pat\n{phase_line}effort: medium\n"
        f"opened: 2026-08-01\nupdated: 2026-08-01\naffects:\n- {affects}\n---\n\n## Summary\n"
        f"d\n\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )
    (layout.bundle_dir / path / "references").mkdir(parents=True, exist_ok=True)


def _draft(tmp_path: Path, *, phase: str = "execute", decision: str = "pending", item: str = FEATURE) -> Path:
    draft = tmp_path / f"draft-{phase}-{decision}.md"
    draft.write_text(
        f"---\ntitle: 'Checkpoint: Held ({phase})'\nitem: {item}\ndecision: {decision}\n"
        f"phase: {phase}\ndispatch_key: gw-execute-held-00000000\nbranch: feature/held\n"
        f"worktree: /tmp/wt\nbase: main\nhead: none\ncreated: 2026-09-13T10:00:00Z\n---\n\n"
        f"## Completed work\n\nhalf\n\n## Remaining actions\n\nrest\n\n## Question\n\nwhich?\n"
        f"\n- a\n- b\n\n## Placement\n\nclean\n\n## Validation evidence\n\n"
        f"none run: parked before tests\n",
        encoding="utf-8",
        newline="",
    )
    return draft


def _snapshot(layout) -> dict[str, bytes]:
    refs = layout.bundle_dir / FEATURE / "references"
    files = {p.relative_to(layout.bundle_dir).as_posix(): p.read_bytes() for p in refs.rglob("*") if p.is_file()}
    files["page"] = (layout.bundle_dir / f"{FEATURE}.md").read_bytes()
    return files


def _add(layout, **kwargs):
    base = {"question": "Stop here?", "on": TODAY, "decided_by": "coordinator", "dry_run": False}
    return work.run_decision_add(layout, FEATURE, **{**base, **kwargs})


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
    assert work.run_next(layout, target).route.blockers[0].message.startswith(f"open decision {entry.id} ({shape})")
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
    assert not any("open decision" in b.message for b in work.run_next(layout, target).route.blockers)
    if draft:
        assert checkpoint.is_file()


def test_ordinary_advance_gets_no_finish_allowance(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _page(layout, affects="gw:workspace")
    result = _add(layout)  # unnamed: no routing hold, but ordinary lint still gates finish
    assert result.application.ok
    advanced = stage.run_stage_advance(layout, FEATURE, today=TODAY, dry_run=False)
    assert advanced.application is not None and not advanced.application.ok
    assert "decisions.open-at-finish" in advanced.application.failures[0]
    assert load(layout.bundle_dir / f"{FEATURE}.md").fm_data()["phase"] == "execute"
