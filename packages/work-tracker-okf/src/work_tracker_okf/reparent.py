"""Pure planners for subtree reparenting and Release adoption."""

from __future__ import annotations

from collections.abc import Sequence

from okf_io import Bundle

from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.mutation import MutationRefusal, WorkMutationPlan, _plan_path_mutation, _subtree_items
from work_tracker_okf.paths import child_lane
from work_tracker_okf.vocabulary import PARENT_TYPES, TERMINAL_STATUSES


def _subtree_mapping(items: Sequence[WorkItem], source_path: str, destination: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in _subtree_items(items, (source_path,)):
        mapping[item.path] = f"{destination}{item.path[len(source_path) :]}"
    return mapping


def _parent_refusals(parent: WorkItem | None, parent_path: str) -> list[MutationRefusal]:
    if parent is None:
        return [MutationRefusal(parent_path, "unknown-parent", "no work item exists at the destination parent")]
    refusals: list[MutationRefusal] = []
    if parent.type not in PARENT_TYPES:
        refusals.append(
            MutationRefusal(parent.path, "invalid-parent-type", f"type {parent.type!r} cannot own a child lane")
        )
    if parent.type == "Release" and parent.parent_path is not None:
        refusals.append(MutationRefusal(parent.path, "nested-release", "Release items remain root-only"))
    if parent.archived or parent.work_status in TERMINAL_STATUSES:
        refusals.append(MutationRefusal(parent.path, "inactive-parent", "the destination parent is inactive"))
    return refusals


def plan_reparent(
    bundle: Bundle,
    items: Sequence[WorkItem],
    source_path: str,
    parent_path: str,
) -> WorkMutationPlan:
    """Move *source_path*'s complete owned subtree below *parent_path*."""
    by_path = item_index(list(items))
    source = by_path.get(source_path)
    parent = by_path.get(parent_path)
    refusals = _parent_refusals(parent, parent_path)
    if source is None:
        refusals.append(MutationRefusal(source_path, "unknown-source", "no work item exists at the source path"))
        return _plan_path_mutation(bundle, items, "reparent", {}, roots=(), refusals=refusals)

    subtree = _subtree_items(items, (source_path,))
    if source.archived:
        refusals.append(MutationRefusal(source.path, "archived-source", "an archived item cannot be reparented"))
    if source_path == parent_path or parent_path.startswith(f"{source_path}/children/"):
        refusals.append(
            MutationRefusal(parent_path, "descendant-destination", "a subtree cannot be placed beneath itself")
        )
    for item in subtree:
        if item.type == "Release":
            refusals.append(MutationRefusal(item.path, "nested-release", "Release items remain root-only"))

    destination = f"{child_lane(parent_path)}/{source.basename}"
    mapping = _subtree_mapping(items, source_path, destination)
    return _plan_path_mutation(
        bundle,
        items,
        "reparent",
        mapping,
        roots=(source_path,),
        refusals=refusals,
    )


def plan_release_adoption(
    bundle: Bundle,
    items: Sequence[WorkItem],
    source_path: str,
    release_path: str,
) -> WorkMutationPlan:
    """Adopt one active root subtree beneath a root Release."""
    by_path = item_index(list(items))
    release = by_path.get(release_path)
    refusals: list[MutationRefusal] = []
    if release is None:
        refusals.append(MutationRefusal(release_path, "unknown-release", "no work item exists at this path"))
    else:
        if release.type != "Release":
            refusals.append(MutationRefusal(release.path, "not-a-release", f"type is {release.type!r}"))
        if release.parent_path is not None:
            refusals.append(MutationRefusal(release.path, "nested-release", "Release items remain root-only"))
        if release.archived or release.work_status in TERMINAL_STATUSES:
            refusals.append(MutationRefusal(release.path, "inactive-parent", "the Release is inactive"))

    mapping: dict[str, str] = {}
    source = by_path.get(source_path)
    if source is None:
        refusals.append(MutationRefusal(source_path, "unknown-source", "no work item exists at this path"))
    else:
        subtree = _subtree_items(items, (source_path,))
        for item in subtree:
            if item.type == "Release":
                refusals.append(MutationRefusal(item.path, "nested-release", "Release items remain root-only"))
        if source.parent_path is not None or source.archived:
            refusals.append(
                MutationRefusal(source.path, "not-a-root", "Release adoption accepts active root items only")
            )
        if source.work_status in TERMINAL_STATUSES:
            refusals.append(MutationRefusal(source.path, "inactive-source", "the source root is inactive"))
        destination = f"{child_lane(release_path)}/{source.basename}"
        mapping.update(_subtree_mapping(items, source.path, destination))

    return _plan_path_mutation(
        bundle,
        items,
        "adopt",
        mapping,
        roots=(source_path,) if source is not None else (),
        refusals=refusals,
    )


__all__ = ["plan_release_adoption", "plan_reparent"]
