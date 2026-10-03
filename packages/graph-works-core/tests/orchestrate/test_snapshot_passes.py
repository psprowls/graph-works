"""One index build per planning pass in graph-works-core (D-007)."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.dispatch_artifacts import routing_items
from test_orchestrate_plan import _item
from work_tracker_okf import snapshot as snapshot_module
from work_tracker_okf.pipeline import PACKAGED_DEFINITION
from work_tracker_okf.snapshot import WorkSnapshot, as_snapshot


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    builds: list[int] = []
    real = snapshot_module._build_indexes

    def counting(items):  # type: ignore[no-untyped-def]
        builds.append(len(items))
        return real(items)

    monkeypatch.setattr(snapshot_module, "_build_indexes", counting)
    return builds


def _epic_with_children(n: int) -> tuple:
    kids = tuple(_item(f"work/epic-x/children/bug-{i:03d}", type="Bug") for i in range(n))
    epic = _item("work/epic-x", type="Epic", phase="execute", child_paths=tuple(kid.path for kid in kids))
    return (epic, *kids)


def test_frontier_builds_indexes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    items = _epic_with_children(40)
    expected = orchestrate._frontier(items, "work/epic-x", definition=PACKAGED_DEFINITION)
    builds = _counting(monkeypatch)
    actual = orchestrate._frontier(items, "work/epic-x", definition=PACKAGED_DEFINITION)
    assert actual == expected
    assert len(builds) == 1


def test_frontier_walks_into_the_gated_epic() -> None:
    candidates, _, _ = orchestrate._frontier(_epic_with_children(3), "work/epic-x", definition=PACKAGED_DEFINITION)
    assert sorted(item.path for item, _ in candidates) == [f"work/epic-x/children/bug-{i:03d}" for i in range(3)]


def test_frontier_answers_match_on_tuple_and_snapshot() -> None:
    items = _epic_with_children(5)
    assert orchestrate._frontier(as_snapshot(items), "work/epic-x", definition=PACKAGED_DEFINITION) == (
        orchestrate._frontier(items, "work/epic-x", definition=PACKAGED_DEFINITION)
    )


def test_frontier_reports_unknown_root_as_before() -> None:
    _, _, blocked = orchestrate._frontier(_epic_with_children(1), "work/nope", definition=PACKAGED_DEFINITION)
    assert [b.reason for b in blocked] == ["unknown path 'work/nope'"]


def test_routing_items_returns_a_snapshot(tmp_path: Path) -> None:
    items = as_snapshot(_epic_with_children(2))
    routed = routing_items(tmp_path, items, definition=PACKAGED_DEFINITION)
    assert isinstance(routed, WorkSnapshot)
    assert routed is items  # nothing replaced: the input snapshot, no rebuild


def test_routing_items_reflects_the_overlay(tmp_path: Path) -> None:
    bare = _item("work/feature-a", has_design_artifact=False, phase="plan")
    design = tmp_path / "work/feature-a/references/01-design.md"
    design.parent.mkdir(parents=True)
    design.write_text("# Design\n", encoding="utf-8")
    routed = routing_items(tmp_path, (bare,), definition=PACKAGED_DEFINITION)
    assert isinstance(routed, WorkSnapshot)
    assert routed.by_path["work/feature-a"].has_design_artifact is True


def test_frontier_groups_by_physical_parent_when_child_paths_differ() -> None:
    physical = _item("work/epic-x/children/bug-physical", type="Bug")
    unrelated = _item("work/bug-unrelated", type="Bug")
    epic = _item("work/epic-x", type="Epic", phase="execute", child_paths=(unrelated.path,))
    candidates, _, _ = orchestrate._frontier((epic, physical, unrelated), epic.path, definition=PACKAGED_DEFINITION)
    assert [item.path for item, _ in candidates] == [physical.path]


def test_plan_builds_indexes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_orchestrate_plan import _plan

    builds = _counting(monkeypatch)
    result = _plan(_epic_with_children(5), "work/epic-x")
    assert result is not None
    assert builds == [6]
