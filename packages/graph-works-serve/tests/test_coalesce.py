"""Net change per file is decided by the disk at flush; batches are classified, de-duplicated, sorted."""

from __future__ import annotations

from functools import partial
from pathlib import Path, PurePath

import pytest
from graph_works_core.events import Change, ChangeEvent, EventKind, classify
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for
from graph_works_serve.coalesce import build_batch, net_changes

A = "/ws/okf/work/a.md"


@pytest.mark.parametrize(
    ("seen", "present", "expected"),
    [
        ({"added"}, True, Change.ADDED),
        ({"modified"}, True, Change.MODIFIED),
        ({"added", "modified"}, True, Change.MODIFIED),
        ({"added", "deleted"}, True, Change.MODIFIED),
        ({"deleted"}, True, Change.MODIFIED),
        ({"added"}, False, Change.DELETED),
        ({"modified"}, False, Change.DELETED),
        ({"added", "deleted"}, False, Change.DELETED),
        ({"deleted"}, False, Change.DELETED),
    ],
)
def test_net_change_table(seen: set[str], present: bool, expected: Change) -> None:
    raw = [(name, A) for name in seen]
    assert net_changes(raw, lambda _: present) == [(expected, PurePath(A))]


def test_each_path_is_statted_once_and_output_is_sorted_by_path() -> None:
    calls: list[str] = []

    def exists(path: str) -> bool:
        calls.append(path)
        return True

    raw = [("modified", "/ws/b"), ("added", "/ws/a"), ("modified", "/ws/b")]
    assert net_changes(raw, exists) == [(Change.ADDED, PurePath("/ws/a")), (Change.MODIFIED, PurePath("/ws/b"))]
    assert sorted(calls) == ["/ws/a", "/ws/b"]


def test_empty_window_is_empty() -> None:
    assert net_changes([], lambda _: True) == []


def test_unknown_change_name_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        net_changes([("renamed", A)], lambda _: True)


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return layout_for(tmp_path / "ws")


def _classify(layout: WorkspaceLayout, path: PurePath, change: Change) -> ChangeEvent | None:
    return classify(layout, path, change)


def test_build_batch_drops_unclassified_dedupes_and_sorts(layout: WorkspaceLayout) -> None:
    bundle = layout.bundle_dir
    net = [
        (Change.MODIFIED, bundle / "work" / "b.md"),
        (Change.MODIFIED, bundle / ".git" / "HEAD"),  # noise -> None
        (Change.ADDED, bundle / "concepts" / "x.md"),
        (Change.MODIFIED, bundle / "work" / "a.md"),
        (Change.MODIFIED, bundle / "work" / "a" / "references" / "01-design.md"),
    ]
    batch = build_batch(net, partial(_classify, layout))
    assert [(e.kind, e.path, e.member) for e in batch] == [
        (EventKind.PAGE, "concepts/x.md", "concepts/x.md"),
        (EventKind.WORK_ITEM, "work/a", "work/a.md"),
        (EventKind.WORK_ITEM, "work/a", "work/a/references/01-design.md"),
        (EventKind.WORK_ITEM, "work/b", "work/b.md"),
    ]


def test_build_batch_dedupes_on_identity(layout: WorkspaceLayout) -> None:
    event = ChangeEvent(kind=EventKind.LOG, path="log.md", member="log.md", change=Change.MODIFIED)
    batch = build_batch([(Change.MODIFIED, PurePath("/x")), (Change.MODIFIED, PurePath("/y"))], lambda *_: event)
    assert batch == (event,)


def test_config_events_sort_with_null_member(layout: WorkspaceLayout) -> None:
    net = [
        (Change.MODIFIED, layout.manifest_path),
        (Change.MODIFIED, layout.cache_dir / "config.json"),
        (Change.MODIFIED, layout.bundle_dir / "log.md"),
    ]
    batch = build_batch(net, partial(_classify, layout))
    assert [(e.kind, e.path, e.member) for e in batch] == [
        (EventKind.CONFIG, "manifest", None),
        (EventKind.CONFIG, "projection", None),
        (EventKind.LOG, "log.md", "log.md"),
    ]


def test_empty_net_is_empty_batch() -> None:
    assert build_batch([], lambda *_: None) == ()
