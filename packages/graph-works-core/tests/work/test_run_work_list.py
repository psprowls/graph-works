"""`run_work_list`: every active item, sorted, archived items excluded."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.work import commands as work
from test_run_next import CHILD, EPIC, _layout, _write


def test_active_items_sorted_by_path_with_parent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-b")
    _write(layout, EPIC, type="Epic")
    _write(layout, CHILD)

    items = work.run_work_list(layout)

    assert [item.path for item in items] == sorted([EPIC, CHILD, "work/feature-b"])
    by_path = {item.path: item for item in items}
    assert by_path[CHILD].parent_path == EPIC
    assert by_path[EPIC].parent_path is None


def test_archived_items_are_excluded_like_the_rollup(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-live")
    _write(layout, "work/_archive/feature-old")

    assert [item.path for item in work.run_work_list(layout)] == ["work/feature-live"]
    assert work.run_status(layout).rollup.total == 1
