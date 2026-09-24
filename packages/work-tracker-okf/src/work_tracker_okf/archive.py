"""Pure planning for top-level work-item archives."""

from __future__ import annotations

from collections.abc import Sequence

from okf_io import Bundle

from work_tracker_okf.hierarchy import sweep_eligible
from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.mutation import MutationRefusal, WorkMutationPlan, _plan_path_mutation, _subtree_items
from work_tracker_okf.vocabulary import TERMINAL_STATUSES


def _archive_destination(path: str) -> str:
    """Every archive lands in the one root archive lane."""
    return f"work/_archive/{path.rsplit('/', 1)[-1]}"


def _default_targets(items: Sequence[WorkItem]) -> tuple[str, ...]:
    """Top-level roots `sweep_eligible` accepts; every other item is skipped, never refused."""
    return tuple(sorted(item.path for item in items if sweep_eligible(items, item)))


def _archive_subtree_mapping(
    items: Sequence[WorkItem],
    target_path: str,
    destination: str,
) -> dict[str, str]:
    """Map one terminal subtree with an iterative parent-before-child walk.

    Descendants land at `<mapped>/children/<basename>`; only the root carries
    the `_archive` marker, and `parse_item_path` makes every descendant of an
    archived root archived by ancestry.
    """
    by_path = item_index(list(items))
    mapping: dict[str, str] = {}
    pending = [(target_path, destination)]
    while pending:
        path, mapped = pending.pop()
        if path in mapping:
            continue
        item = by_path.get(path)
        if item is None:
            continue
        mapping[path] = mapped
        for child_path in reversed(item.child_paths):
            child = by_path.get(child_path)
            if child is not None:
                pending.append((child_path, f"{mapped}/children/{child.basename}"))
    return mapping


def plan_archive(
    bundle: Bundle,
    items: Sequence[WorkItem],
    paths: Sequence[str] | str | None = None,
) -> WorkMutationPlan:
    """Archive top-level roots, carrying each whole subtree along unchanged."""
    by_path = item_index(list(items))
    if paths is None:
        selected = _default_targets(items)
    elif isinstance(paths, str):
        selected = (paths,)
    else:
        selected = tuple(dict.fromkeys(paths))

    refusals: list[MutationRefusal] = []
    mapping: dict[str, str] = {}
    roots: list[str] = []
    for path in selected:
        target = by_path.get(path)
        if target is None:
            refusals.append(MutationRefusal(path, "unknown-source", "no work item exists at this path"))
            continue
        if target.archived:
            refusals.append(MutationRefusal(path, "already-archived", "the item is already in an archive lane"))
            continue
        if target.ancestor_paths:
            root = target.ancestor_paths[0]
            refusals.append(
                MutationRefusal(
                    path,
                    "not-top-level",
                    f"only top-level items are archived; children move with their root — archive {root}",
                )
            )
            continue
        roots.append(path)
        if target.work_status not in TERMINAL_STATUSES:
            refusals.append(
                MutationRefusal(path, "not-terminal", f"work_status {target.work_status!r} is not terminal")
            )
        for descendant in _subtree_items(items, (path,)):
            if descendant.path != path and descendant.work_status not in TERMINAL_STATUSES:
                refusals.append(
                    MutationRefusal(
                        descendant.path,
                        "nonterminal-descendant",
                        f"descendant work_status {descendant.work_status!r} is not terminal",
                    )
                )
        mapping.update(_archive_subtree_mapping(items, path, _archive_destination(path)))

    return _plan_path_mutation(bundle, items, "archive", mapping, roots=tuple(roots), refusals=refusals)


__all__ = ["plan_archive"]
