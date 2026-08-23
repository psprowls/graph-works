"""Pure planning for root and nearest-owner work-item archives."""

from __future__ import annotations

from collections.abc import Sequence

from okf_io import Bundle

from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.mutation import MutationRefusal, WorkMutationPlan, _plan_path_mutation, _subtree_items
from work_tracker_okf.vocabulary import TERMINAL_STATUSES


def _lane(path: str) -> str:
    return path.rsplit("/", 1)[0]


def _archive_destination(path: str) -> str:
    lane = _lane(path)
    return f"{lane}/_archive/{path.rsplit('/', 1)[-1]}"


def _default_targets(items: Sequence[WorkItem]) -> tuple[str, ...]:
    by_path = item_index(list(items))
    chosen: list[str] = []
    for item in items:
        if item.archived or item.work_status not in TERMINAL_STATUSES:
            continue
        if any(
            (ancestor := by_path.get(path)) is not None
            and not ancestor.archived
            and ancestor.work_status in TERMINAL_STATUSES
            for path in item.ancestor_paths
        ):
            continue
        chosen.append(item.path)
    return tuple(sorted(chosen))


def _archive_subtree_mapping(
    items: Sequence[WorkItem],
    target_path: str,
    destination: str,
) -> dict[str, str]:
    """Map one terminal subtree with an iterative parent-before-child walk."""
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
        for child_path in reversed((*item.active_child_paths, *item.archived_child_paths)):
            child = by_path.get(child_path)
            if child is not None:
                pending.append((child_path, f"{mapped}/children/_archive/{child.basename}"))
    return mapping


def plan_archive(
    bundle: Bundle,
    items: Sequence[WorkItem],
    paths: Sequence[str] | str | None = None,
) -> WorkMutationPlan:
    """Archive selected paths, normalizing terminal descendants locally first."""
    by_path = item_index(list(items))
    if paths is None:
        selected = _default_targets(items)
    elif isinstance(paths, str):
        selected = (paths,)
    else:
        selected = tuple(dict.fromkeys(paths))

    refusals: list[MutationRefusal] = []
    for index, path in enumerate(selected):
        for other in selected[index + 1 :]:
            if other.startswith(f"{path}/children/") or path.startswith(f"{other}/children/"):
                refusals.append(
                    MutationRefusal(path, "overlapping-targets", f"archive targets {path!r} and {other!r} overlap")
                )

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
        roots.append(path)
        if target.work_status not in TERMINAL_STATUSES:
            refusals.append(
                MutationRefusal(
                    path,
                    "not-terminal",
                    f"work_status {target.work_status!r} is not terminal",
                )
            )

        subtree = _subtree_items(items, (path,))
        for descendant in subtree:
            if descendant.path == path:
                continue
            if descendant.work_status not in TERMINAL_STATUSES:
                refusals.append(
                    MutationRefusal(
                        descendant.path,
                        "nonterminal-descendant",
                        f"descendant work_status {descendant.work_status!r} is not terminal",
                    )
                )

        destination = _archive_destination(path)
        mapping.update(_archive_subtree_mapping(items, path, destination))

    return _plan_path_mutation(
        bundle,
        items,
        "archive",
        mapping,
        roots=tuple(roots),
        refusals=refusals,
    )


__all__ = ["plan_archive"]
