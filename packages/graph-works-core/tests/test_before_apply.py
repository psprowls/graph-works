"""The caller can reject the actual candidate without any domain writes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.archive.commands import run_archive
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.proposals import run_proposal_decide

AT = datetime(2026, 9, 18, tzinfo=UTC)


@pytest.mark.parametrize("operation", ["archive", "advance", "decide", "missing-proposal"])
def test_before_apply_can_abort_and_is_not_called_for_dry_runs(tmp_path: Path, operation: str) -> None:
    layout = apply_init(plan_init(tmp_path / "workspace", today=AT.date(), topic="Candidate")).layout
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "proposals").mkdir(exist_ok=True)
    path = "work/feature-one"
    terminal = operation == "archive"
    (layout.bundle_dir / f"{path}.md").write_text(
        "---\ntype: Feature\ntitle: One\ndescription: d\nstatus: draft\n"
        f"work_status: {'resolved' if terminal else 'open'}\nphase: {'done' if terminal else 'design'}\n"
        "effort: medium\nopened: 2026-09-01\nupdated: 2026-09-01\naffects: []\n---\n",
        encoding="utf-8",
        newline="",
    )
    (layout.bundle_dir / "proposals/one.md").write_text(
        "---\ntype: Proposal\ntitle: One\ndescription: d\ntarget: concepts/one\npage_status: proposed\n---\n",
        encoding="utf-8",
        newline="",
    )

    def run(dry_run: bool, callback: Callable[[object], None]) -> object:
        if operation == "archive":
            return run_archive(layout, today=AT.date(), dry_run=dry_run, before_apply=callback)
        if operation == "advance":
            return run_stage_advance(
                layout, path, today=AT.date(), infer_worktree=False, dry_run=dry_run, before_apply=callback
            )
        return run_proposal_decide(
            layout,
            "missing" if operation == "missing-proposal" else "concepts/one",
            "approved",
            by="human",
            at=AT,
            dry_run=dry_run,
            before_apply=callback,
        )

    class Rejected(Exception):
        pass

    candidates: list[object] = []

    def reject(candidate: object) -> None:
        candidates.append(candidate)
        raise Rejected

    before = {p.relative_to(layout.bundle_dir): p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()}
    planned = run(True, reject)
    assert candidates == []
    with pytest.raises(Rejected):
        run(False, reject)
    assert candidates == [planned]
    assert {
        p.relative_to(layout.bundle_dir): p.read_bytes() for p in layout.bundle_dir.rglob("*") if p.is_file()
    } == before
    assert not (layout.cache_dir / "active-work.json").exists()
