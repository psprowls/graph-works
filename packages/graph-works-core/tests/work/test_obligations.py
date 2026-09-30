"""`run_obligation_add` plans by default and writes one page on apply."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from _transaction_helpers import _init_git, assert_workspace_commit
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from graph_works_core.work.obligations import run_obligation_add
from okf_io import load

TODAY = date(2026, 9, 29)


def _layout(tmp_path: Path, *, status: str = "in-progress", phase: str = "execute"):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Obligations")).layout
    page = layout.bundle_dir / "work" / "feature-a.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "work" / "feature-a").mkdir()
    page.write_text(
        "---\ntype: Feature\ntitle: A\ndescription: d\nstatus: stable\n"
        f"work_status: {status}\nphase: {phase}\neffort: medium\nowner: psprowls\nopened: 2026-09-01\n"
        "updated: 2026-09-01\naffects:\n- gw:workspace\n---\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    return layout, page


def test_dry_run_plans_and_writes_nothing(tmp_path: Path) -> None:
    layout, page = _layout(tmp_path)
    before = page.read_bytes()
    record = run_obligation_add(layout, "work/feature-a", text="Tag the release", on=TODAY)
    assert record.plan.changed and record.application is None and not record.written
    assert page.read_bytes() == before


def test_apply_appends_one_deferred_entry(tmp_path: Path) -> None:
    layout, page = _layout(tmp_path)
    record = run_obligation_add(layout, "work/feature-a", text="Tag the release", on=TODAY, dry_run=False)
    assert record.written
    assert record.application is not None and record.application.written == ("work/feature-a.md",)
    assert load(page).fm_data(dates="iso")["finish_obligations"] == [
        {"text": "Tag the release", "origin": "deferred", "recorded": "2026-09-29"}
    ]


def test_apply_commits_the_item_page(tmp_path: Path) -> None:
    layout, _ = _layout(tmp_path)
    _init_git(layout.root)
    record = run_obligation_add(layout, "work/feature-a", text="Tag the release", on=TODAY, dry_run=False)
    assert record.application is not None and record.application.commit is not None
    assert record.application.commit.status == "committed"
    assert_workspace_commit(layout.root, "workspace: record feature-a finish obligation")


def test_blank_text_is_refused_without_write(tmp_path: Path) -> None:
    layout, page = _layout(tmp_path)
    before = page.read_bytes()
    record = run_obligation_add(layout, "work/feature-a", text="  ", on=TODAY, dry_run=False)
    assert record.plan.refusal == "empty-text" and record.application is None
    assert page.read_bytes() == before


def test_resolved_item_is_refused(tmp_path: Path) -> None:
    layout, _ = _layout(tmp_path, status="resolved", phase="done")
    assert (
        run_obligation_add(layout, "work/feature-a", text="x", on=TODAY, dry_run=False).plan.refusal == "terminal-item"
    )


def test_unknown_path_is_a_refusal(tmp_path: Path) -> None:
    layout, _ = _layout(tmp_path)
    assert (
        run_obligation_add(layout, "work/feature-missing", text="x", on=TODAY, dry_run=False).plan.refusal
        == "unknown-path"
    )
