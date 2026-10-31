"""Pure planners for subtree reparenting and Release adoption.

The inventory closure is the single place to widen when an equivalence test diverges.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence

from okf_io import Bundle

from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.mutation import MutationRefusal, WorkMutationPlan, _plan_path_mutation, _subtree_items
from work_tracker_okf.paths import child_lane, item_page, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES, TERMINAL_STATUSES


def _subtree_mapping(items: Sequence[WorkItem], source_path: str, destination: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in _subtree_items(items, (source_path,)):
        mapping[item.path] = f"{destination}{item.path[len(source_path) :]}"
    return mapping


def dependency_closure(seeds: Iterable[str], edges: Callable[[str], Sequence[str]]) -> frozenset[str]:
    """Every item path reachable from *seeds* through *edges*, seeds included; cycles terminate."""
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        stack.extend(edges(path))
    return frozenset(seen)


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
    return plan_reparent_many(bundle, items, (source_path,), parent_path)


def _source_refusals_and_mapping(
    items: Sequence[WorkItem],
    source: WorkItem | None,
    source_path: str,
    parent_path: str,
) -> tuple[list[MutationRefusal], dict[str, str]]:
    refusals: list[MutationRefusal] = []
    if source is None:
        refusals.append(MutationRefusal(source_path, "unknown-source", "no work item exists at the source path"))
        return refusals, {}

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
    return refusals, _subtree_mapping(items, source_path, destination)


def plan_reparent_many(
    bundle: Bundle,
    items: Sequence[WorkItem],
    source_paths: Sequence[str],
    parent_path: str,
) -> WorkMutationPlan:
    """Plan one all-or-nothing reparenting of complete, disjoint subtrees."""
    by_path = item_index(list(items))
    refusals = _parent_refusals(by_path.get(parent_path), parent_path)
    if not source_paths:
        refusals.append(MutationRefusal("", "empty-batch", "at least one source path is required"))
    seen: set[str] = set()
    for source_path in source_paths:
        if source_path in seen:
            refusals.append(MutationRefusal(source_path, "duplicate-source", "the source path is given twice"))
        seen.add(source_path)
    for source_path in sorted(seen):
        ancestor = next((other for other in sorted(seen) if source_path.startswith(f"{other}/children/")), None)
        if ancestor is not None:
            refusals.append(
                MutationRefusal(source_path, "overlapping-sources", f"the source lies beneath {ancestor!r}")
            )
    mapping: dict[str, str] = {}
    for source_path in source_paths:
        source_refusals, source_mapping = _source_refusals_and_mapping(
            items,
            by_path.get(source_path),
            source_path,
            parent_path,
        )
        refusals.extend(source_refusals)
        mapping.update(source_mapping)
    return _plan_path_mutation(
        bundle,
        items,
        "reparent",
        mapping,
        roots=tuple(source_paths) if mapping else (),
        refusals=refusals,
    )


def reparent_load_set(
    inventory: Iterable[str],
    touched: Collection[str],
    source_paths: Sequence[str],
    parent_path: str,
) -> frozenset[str]:
    """Close raw inventory names over the item context needed by reparenting.

    This is the single place to widen when an equivalence test diverges.
    Sources, touched items, and reference owners need their sibling lanes;
    ancestors need their own pages and direct children, never their siblings.
    The destination additionally needs its direct children. No content is read.
    """
    members = frozenset(inventory)
    lane_members: dict[str, set[str]] = {}
    for member in members:
        location = parse_item_path(member[:-3]) if member.endswith(".md") else None
        if location is not None:
            lane_members.setdefault(location.lane, set()).add(member)
    selected = set(touched)
    lanes: set[str] = set()

    def add_context(path: str, *, siblings: bool) -> None:
        location = parse_item_path(path)
        if location is None:
            return
        if location.page in members:
            selected.add(location.page)
        if siblings:
            lanes.add(location.lane)
        for ancestor in location.ancestor_paths:
            page = item_page(ancestor).rel
            if page in members:
                selected.add(page)
            lanes.add(child_lane(ancestor))

    for member in touched:
        if member.endswith(".md") and parse_item_path(member[:-3]) is not None:
            add_context(member[:-3], siblings=True)
        else:
            owner, separator, _reference = member.partition("/references/")
            if separator:
                add_context(owner, siblings=True)
    for source in source_paths:
        add_context(source, siblings=True)
    add_context(parent_path, siblings=False)
    if parse_item_path(parent_path) is not None:
        lanes.add(child_lane(parent_path))
    for lane in lanes:
        selected.update(lane_members.get(lane, ()))
        index = f"{lane}/index.md"
        if index in members:
            selected.add(index)
    if "work/index.md" in members:
        selected.add("work/index.md")
    return frozenset(selected)


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


__all__ = ["dependency_closure", "plan_release_adoption", "plan_reparent", "plan_reparent_many", "reparent_load_set"]
