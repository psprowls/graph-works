"""Pair a vanished entity page with its renamed successor.

A Package/App/TestSuite/AgentPlugin page records its directory as ``path:``. When the
mirror lane detected that files moved from ``P_old/<x>`` to ``P_new/<x>`` and a page at
``P_old`` vanished while a page-less entity appeared at ``P_new``, the two are one entity
renamed, and its page should move with its prose rather than be pruned and recreated.
Pairing is strictly one-to-one; any ambiguity pairs nothing and warns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from pathlib import PurePosixPath

from okf_io import Bundle

from code_wiki_okf.entities.sync import EntityWrite
from code_wiki_okf.mirror.model import MirrorPlan
from code_wiki_okf.placement import PlacementError, context_from_resource
from code_wiki_okf.resources import resource_index

RENAMEABLE_TYPES = frozenset({"Package", "App", "TestSuite", "AgentPlugin"})


def _directory_splits(old: str, new: str) -> Set[tuple[str, str]]:
    """Every ``(P_old, P_new)`` such that ``old == P_old/x`` and ``new == P_new/x`` for one shared suffix ``x``."""
    old_parts = PurePosixPath(old).parts
    new_parts = PurePosixPath(new).parts
    splits: set[tuple[str, str]] = set()
    for count in range(1, min(len(old_parts), len(new_parts)) + 1):
        if old_parts[len(old_parts) - count :] != new_parts[len(new_parts) - count :]:
            break
        prefix_old = old_parts[: len(old_parts) - count]
        prefix_new = new_parts[: len(new_parts) - count]
        if prefix_old and prefix_new and prefix_old != prefix_new:
            splits.add(("/".join(prefix_old), "/".join(prefix_new)))
    return splits


def pair_entity_renames(
    bundle: Bundle,
    writes: Sequence[EntityWrite],
    mirrors: Sequence[MirrorPlan],
    current_resources: Set[str],
) -> tuple[Mapping[str, str], tuple[str, ...]]:
    """Return ``{old member: new member}`` for unambiguous renames, plus warnings."""
    renamed_dirs: set[tuple[str, str, str]] = set()
    for mirror in mirrors:
        for old, new in mirror.renames.items():
            renamed_dirs.update((mirror.repo, p_old, p_new) for p_old, p_new in _directory_splits(old, new))
    if not renamed_dirs:
        return {}, ()

    index = resource_index(bundle)
    vanished: dict[str, tuple[str, str, str]] = {}  # member -> (type, repository, path)
    for concept_id, document in bundle.concepts.items():
        type_name = (document.fm.type or "").strip()
        resource = document.fm.resource
        path = document.fm_raw.get("path")
        if type_name not in RENAMEABLE_TYPES or resource is None or resource in current_resources:
            continue
        if not isinstance(path, str) or not path:
            continue
        try:
            repository = context_from_resource(type_name, resource).repository
        except PlacementError:
            continue
        if repository is None:
            continue
        vanished[f"{concept_id}.md"] = (type_name, repository, path)

    fresh: dict[str, tuple[str, str, str]] = {}
    for write in writes:
        path = write.frontmatter.get("path")
        if write.context.type_name not in RENAMEABLE_TYPES or not isinstance(path, str) or not path:
            continue
        if index.member_for(write.context.resource) is not None or write.context.repository is None:
            continue
        fresh[write.member] = (write.context.type_name, write.context.repository, path)

    candidates: dict[str, set[str]] = {member: set() for member in vanished}
    reverse: dict[str, set[str]] = {member: set() for member in fresh}
    for old_member, (old_type, old_repo, old_path) in vanished.items():
        for new_member, (new_type, new_repo, new_path) in fresh.items():
            if old_type == new_type and old_repo == new_repo and (old_repo, old_path, new_path) in renamed_dirs:
                candidates[old_member].add(new_member)
                reverse[new_member].add(old_member)

    pairs: dict[str, str] = {}
    warnings: list[str] = []
    for old_member, targets in sorted(candidates.items()):
        if len(targets) > 1:
            warnings.append(
                f"{vanished[old_member][1]}: entity rename ambiguous for {old_member}: "
                f"candidates {', '.join(sorted(targets))}"
            )
        elif targets:
            (only,) = targets
            if len(reverse[only]) == 1:
                pairs[old_member] = only
    for new_member, sources in sorted(reverse.items()):
        if len(sources) > 1:
            warnings.append(
                f"{fresh[new_member][1]}: entity rename ambiguous for {new_member}: candidates "
                f"{', '.join(sorted(sources))}"
            )
    return pairs, tuple(sorted(set(warnings)))


__all__ = ["RENAMEABLE_TYPES", "pair_entity_renames"]
