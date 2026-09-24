"""`gw work touch-active-work`: stamp the pointer for the session about to run.

The regression half is the spec's Phase-4 item 4: drive consecutive stage
sessions' worth of calls, in the order `/gw:workflow` makes them, and assert
what the pointer says at each `SessionEnd` -- not what one hand-built pointer
produces.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import provenance

TODAY = date(2026, 9, 22)


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Touch")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Bug", phase: str | None = "plan", work_status: str = "open") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    phase_line = f"phase: {phase}\n" if phase is not None else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\n{phase_line}effort: medium\nopened: 2026-09-01\n"
        "updated: 2026-09-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="\n",
    )


def _pointer(layout) -> dict[str, str] | None:
    target = layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def test_touch_stamps_the_current_phase(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-a", phase="plan")
    result = work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    assert result.refusal is None and result.phase == "plan"
    assert result.pointer_path == layout.cache_dir / provenance.ACTIVE_WORK_FILENAME
    assert _pointer(layout) == {"path": "work/bug-a", "phase": "plan", "updated": TODAY.isoformat()}


def test_touch_is_idempotent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-a", phase="execute", work_status="in-progress")
    work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    before = (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).read_bytes()
    work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    assert (layout.cache_dir / provenance.ACTIVE_WORK_FILENAME).read_bytes() == before


def test_touch_does_not_modify_the_item_page(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-a", phase="plan")
    page = layout.bundle_dir / "work/bug-a.md"
    before = page.read_bytes()
    work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    assert page.read_bytes() == before


@pytest.mark.parametrize(
    ("phase", "work_status", "refusal"),
    [
        (None, "open", "inactive-phase"),
        ("done", "resolved", "terminal"),
        ("plan", "wontfix", "terminal"),
        ("execute", "superseded", "terminal"),
    ],
)
def test_touch_refuses_and_leaves_the_existing_pointer(
    tmp_path: Path, phase: str | None, work_status: str, refusal: str
) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/other", phase="plan")
    provenance.write_active_work(layout, "work/other", "plan", updated="2026-09-01")
    before = _pointer(layout)
    _write(layout, "work/bug-a", phase=phase, work_status=work_status)
    result = work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    assert result.refusal == refusal and result.pointer_path is None and result.detail
    assert _pointer(layout) == before


def test_touch_refuses_an_unknown_item(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    result = work.run_touch_active_work(layout, "work/nope", today=TODAY)
    assert result.refusal == "unknown-item" and result.pointer_path is None
    assert _pointer(layout) is None


def test_touch_refuses_an_unreadable_item_and_leaves_the_existing_pointer(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/other", phase="plan")
    provenance.write_active_work(layout, "work/other", "plan", updated="2026-09-01")
    before = _pointer(layout)

    page = layout.bundle_dir / "work/bug-a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"\xff")

    result = work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    assert result.refusal == "unreadable" and result.pointer_path is None and result.detail
    assert _pointer(layout) == before


def test_touch_degrades_when_the_write_fails(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-a", phase="plan")
    monkeypatch.setattr(provenance, "write_active_work", lambda *a, **k: None)
    result = work.run_touch_active_work(layout, "work/bug-a", today=TODAY)
    assert result.refusal is None and result.phase == "plan" and result.pointer_path is None


# --- the regression: consecutive sessions, as /gw:workflow drives them -------


def _session_end_label(layout) -> str | None:
    """What `transcript_capture._copy_transcript` would read at SessionEnd."""
    pointer = _pointer(layout)
    return None if pointer is None else pointer["phase"]


def test_each_session_ends_labelled_with_the_phase_it_ran(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/bug-a"
    _write(layout, path, phase="plan", work_status="open")
    artifact = layout.bundle_dir / path / "references" / "02-plan.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Plan\n", encoding="utf-8", newline="")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok

    # plan session: step 3 touch, stage runs, step 5 exit advance (plan -> execute)
    work.run_touch_active_work(layout, path, today=TODAY)
    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)
    assert result.application is not None and result.application.ok
    assert _session_end_label(layout) == "plan"

    # execute session: step 2 dispatch advance (owner), step 3 touch
    result = stage.run_stage_advance(layout, path, today=TODAY, owner="pat", dry_run=False)
    assert result.application is not None and result.application.ok
    work.run_touch_active_work(layout, path, today=TODAY)
    assert _session_end_label(layout) == "execute"


def test_finish_session_keeps_its_own_label_through_done(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = "work/bug-a"
    _write(layout, path, phase="finish", work_status="in-progress")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok

    work.run_touch_active_work(layout, path, today=TODAY)
    result = stage.run_stage_advance(layout, path, today=TODAY, resolved_in="abc1234", dry_run=False)
    assert result.application is not None and result.application.ok
    assert _session_end_label(layout) == "finish"
