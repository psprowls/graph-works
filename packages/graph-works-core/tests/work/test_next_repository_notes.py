"""Repository pin lag is advisory for the selected work item."""

from __future__ import annotations

from pathlib import Path

import pytest
from gitrepo import git, make_upstream
from graph_works_core.repositories.commands import run_repo_add
from graph_works_core.work.commands import run_next
from workspace_fixture import NOW, make_workspace

ITEM = "work/feature-a"


def _item(layout, extra: str = "") -> None:
    page = layout.bundle_dir / f"{ITEM}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: a\ndescription: d\nstatus: stable\n"
        "work_status: open\nphase: plan\neffort: medium\n"
        f"opened: 2026-09-29\nupdated: 2026-09-29\n{extra}affects:\n- src\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )


@pytest.mark.parametrize("dry_run", [True, False])
def test_next_notes_a_managed_repository_behind_its_track(tmp_path: Path, dry_run: bool) -> None:
    (tmp_path / "up").mkdir()
    upstream = make_upstream(tmp_path / "up")
    upstream.commit({"src/a.py": "a = 1\n"}, "c1")
    layout = make_workspace(tmp_path)
    assert run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW).ok
    _item(layout, "repo: demo\n")
    assert run_next(layout, ITEM, dry_run=dry_run).repository_notes == ()
    git(layout.worktrees_dir / "demo" / "main", "commit", "-q", "--allow-empty", "-m", "merged")

    result = run_next(layout, ITEM, dry_run=dry_run)
    (note,) = result.repository_notes
    assert "gw repo advance demo" in note
    assert not any("demo" in blocker for blocker in result.route.blockers)


def test_next_without_a_lane_has_no_notes(tmp_path: Path) -> None:
    layout = make_workspace(tmp_path)
    _item(layout)
    assert run_next(layout, ITEM).repository_notes == ()
