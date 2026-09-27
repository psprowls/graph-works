"""Transitive-dependent rank over a run's dependency graph (pure)."""

from __future__ import annotations

import sys

from graph_works_core.orchestrate.rank import dependent_counts
from test_orchestrate_plan import _item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import WorkItem

ROOT = "work/epic-rank"


def _child(name: str, *needs: str, **overrides: object) -> WorkItem:
    edges = tuple(DependencyEdge(f"{ROOT}/children/{dep}", blocks="execute", needs="resolved") for dep in needs)
    return _item(f"{ROOT}/children/{name}", dependency_edges=edges, **overrides)


def _run(*children: WorkItem, extra: tuple[WorkItem, ...] = ()) -> tuple[WorkItem, ...]:
    direct = tuple(child.path for child in children if child.parent_path == ROOT)
    return (_item(ROOT, type="Epic", phase="execute", child_paths=direct), *children, *extra)


def _counts(items: tuple[WorkItem, ...]) -> dict[str, int]:
    return {path.rsplit("/", 1)[-1]: count for path, count in dependent_counts(items, ROOT).items() if count}


def test_direct_dependents_count_once_each() -> None:
    items = _run(_child("up"), _child("d0", "up"), _child("d1", "up"))
    assert _counts(items) == {"up": 2}


def test_rank_is_transitive_along_a_chain() -> None:
    items = _run(_child("a"), _child("b", "a"), _child("c", "b"), _child("d", "c"))
    assert _counts(items) == {"a": 3, "b": 2, "c": 1}


def test_a_diamond_counts_its_shared_descendant_once() -> None:
    items = _run(_child("a"), _child("b", "a"), _child("c", "a"), _child("d", "b", "c"))
    assert _counts(items) == {"a": 3, "b": 1, "c": 1}


def test_duplicate_phase_edges_between_one_pair_count_once() -> None:
    up = f"{ROOT}/children/up"
    dependent = _item(
        f"{ROOT}/children/dep",
        dependency_edges=(
            DependencyEdge(up, blocks="plan", needs="plan"),
            DependencyEdge(up, blocks="execute", needs="resolved"),
        ),
    )
    assert _counts(_run(_child("up"), dependent)) == {"up": 1}


def test_self_edges_and_cycles_terminate_and_exclude_the_origin() -> None:
    items = _run(_child("self", "self"), _child("x", "y"), _child("y", "x"), _child("z", "x"))
    assert _counts(items) == {"x": 2, "y": 2}


def test_a_chain_longer_than_the_recursion_limit_terminates() -> None:
    length = sys.getrecursionlimit() + 50
    chain = [_child("n0000")] + [_child(f"n{i:04d}", f"n{i - 1:04d}") for i in range(1, length)]
    counts = dependent_counts(_run(*chain), ROOT)
    assert counts[f"{ROOT}/children/n0000"] == length - 1
    assert counts[f"{ROOT}/children/n{length - 1:04d}"] == 0


def test_nested_descendants_under_a_terminal_parent_are_nodes() -> None:
    mid = f"{ROOT}/children/epic-mid"
    grandchild = _item(
        f"{mid}/children/deep",
        dependency_edges=(DependencyEdge(f"{ROOT}/children/up", blocks="execute", needs="resolved"),),
    )
    mid_item = _item(mid, type="Epic", work_status="resolved", phase="done", child_paths=(grandchild.path,))
    items = _run(_child("up"), mid_item, grandchild)
    assert _counts(items) == {"up": 1}


def test_terminal_archived_and_outside_items_neither_count_nor_bridge() -> None:
    outside = _item(
        "work/feature-outside",
        dependency_edges=(DependencyEdge(f"{ROOT}/children/up", blocks="execute", needs="resolved"),),
    )
    items = _run(
        _child("up"),
        _child("done", "up", work_status="resolved", phase="done"),
        _child("after-done", "done"),
        _child("gone", "up", archived=True),
        _child("dangling", "missing"),
        _child("isolated"),
        extra=(outside,),
    )
    assert dict(dependent_counts(items, ROOT)) == {
        ROOT: 0,
        f"{ROOT}/children/up": 0,
        f"{ROOT}/children/after-done": 0,
        f"{ROOT}/children/dangling": 0,
        f"{ROOT}/children/isolated": 0,
    }


def test_satisfied_edges_still_contribute_rank() -> None:
    up = _child("up", work_status="accepted", phase="execute")
    satisfied = _item(
        f"{ROOT}/children/dep",
        dependency_edges=(DependencyEdge(up.path, blocks="design", needs="plan"),),
    )
    assert _counts(_run(up, satisfied)) == {"up": 1}


def test_the_root_is_a_node_and_a_terminal_root_ranks_nothing() -> None:
    child = _item(
        f"{ROOT}/children/dep",
        dependency_edges=(DependencyEdge(ROOT, blocks="execute", needs="resolved"),),
    )
    live_root = _item(ROOT, type="Epic", phase="execute", child_paths=(child.path,))
    assert dependent_counts((live_root, child), ROOT)[ROOT] == 1
    terminal_root = _item(ROOT, type="Epic", work_status="resolved", phase="done", child_paths=(child.path,))
    assert ROOT not in dependent_counts((terminal_root, child), ROOT)


def test_an_unknown_root_ranks_nothing() -> None:
    assert dict(dependent_counts((), "work/nope")) == {}


def test_repository_identity_does_not_gate_rank() -> None:
    items = _run(_child("up", repo="code"), _child("ui", "up", repo="ui"))
    assert _counts(items) == {"up": 1}
