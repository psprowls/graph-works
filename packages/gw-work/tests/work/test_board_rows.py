"""`run_work_board`: work-list rows carrying the effective placement."""

from __future__ import annotations

from pathlib import Path

from gw_kernel.workspace import provenance
from gw_kernel.workspace import repos as repos_module
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


def test_board_row_workspace_only_item_null(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, PATH)
    _stamp(layout, PATH, "repo: undeclared\n")
    assert _row(layout).placement is None


def test_board_never_probes_default_base_and_reads_map_once(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    for name in ("feature-a", "feature-b", "feature-c"):
        _write(layout, f"work/{name}")

    def forbidden(*args, **kwargs):
        raise AssertionError("default_base must not run during a board build")

    reads: list[int] = []
    original = work.declared_repositories

    def counting(layout):
        reads.append(1)
        return original(layout)

    monkeypatch.setattr(provenance, "default_base", forbidden)
    monkeypatch.setattr(work, "declared_repositories", counting)
    rows = work.run_work_board(layout)
    assert len(rows) == 3
    assert all(row.placement is not None for row in rows)
    assert len(reads) == 1


def test_board_malformed_manifest_nulls_every_row(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    for name in ("feature-a", "feature-b"):
        _write(layout, f"work/{name}")
    items = work.run_work_list(layout)
    layout.manifest_path.write_text("repositories: [unclosed\n", encoding="utf-8", newline="\n")
    rows = work.board_rows(layout, items)
    assert len(rows) == 2
    assert all(row.placement is None for row in rows)


def test_board_undeclared_repo_nulls_only_its_row(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a")
    _write(layout, "work/feature-b")
    _stamp(layout, "work/feature-b", "repo: undeclared\n")
    by_path = {row.item.path: row.placement for row in work.run_work_board(layout)}
    assert by_path["work/feature-a"] is not None
    assert by_path["work/feature-b"] is None


def test_affecting_reads_map_a_bounded_number_of_times(tmp_path: Path, monkeypatch) -> None:
    layout = _layout(tmp_path)
    for name in ("feature-a", "feature-b", "feature-c", "feature-d"):
        _write(layout, f"work/{name}")
    reads: list[int] = []
    original = repos_module.load_workspace_config

    def counting(layout):
        reads.append(1)
        return original(layout)

    monkeypatch.setattr(repos_module, "load_workspace_config", counting)
    result = run_work_affecting(layout, "repo", "packages/a")
    assert len(result.rows) == 4
    assert len(reads) <= 2


def test_affecting_rows_carry_placement(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, PATH)
    result = run_work_affecting(layout, "repo", "packages/a")
    assert [row.item.path for row in result.rows] == [PATH]
    assert result.rows[0].placement is not None
    assert result.rows[0].placement.source == "computed"
