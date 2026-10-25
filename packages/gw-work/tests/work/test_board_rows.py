"""`run_work_board`: work-list rows carrying the effective placement."""

from __future__ import annotations

from pathlib import Path

from gw_kernel.workspace import provenance
from gw_pipeline.placement import branch_name
from gw_work import commands as work
from gw_work.affecting import run_work_affecting
from test_run_next import _layout, _write

PATH = "work/feature-a"


def _stamp(layout, path: str, extra: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace("affects:\n", extra + "affects:\n", 1), encoding="utf-8", newline="\n")


def _row(layout, path: str = PATH) -> work.BoardRow:
    return next(row for row in work.run_work_board(layout) if row.item.path == path)


def test_board_row_computed_placement(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, PATH)
    placement = _row(layout).placement
    assert placement is not None
    assert placement.source == "computed"
    assert placement.branch == branch_name(PATH, "Feature")
    assert placement.repo_name == "repo"


def test_board_row_legacy_placement(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, PATH)
    _stamp(layout, PATH, "worktree: /legacy/wt\nbranch: legacy-branch\n")
    placement = _row(layout).placement
    assert placement is not None
    assert (placement.source, placement.worktree, placement.branch) == ("legacy", "/legacy/wt", "legacy-branch")


def test_board_row_workspace_only_item_null(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, PATH)
    _stamp(layout, PATH, "repo: undeclared\n")
    assert _row(layout).placement is None


def test_default_base_probed_once_per_repo(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    for name in ("feature-a", "feature-b", "feature-c"):
        _write(layout, f"work/{name}")
    calls: list[Path | None] = []
    original = provenance.default_base

    def counting(repo, **kwargs):
        calls.append(repo)
        return original(repo, **kwargs)

    monkeypatch.setattr(provenance, "default_base", counting)
    rows = work.run_work_board(layout)
    assert len(rows) == 3
    assert len(calls) == 1


def test_affecting_rows_carry_placement(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, PATH)
    result = run_work_affecting(layout, "repo", "packages/a")
    assert [row.item.path for row in result.rows] == [PATH]
    assert result.rows[0].placement is not None
    assert result.rows[0].placement.source == "computed"
