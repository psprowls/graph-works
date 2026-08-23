"""One-time conversion of legacy flat/hybrid work bundles.

This is the only module that understands the retired filename, hierarchy, and
frontmatter dialect.  Path-native readers deliberately do not fall back to any
of it: callers opt into this planner, inspect its complete manifest, and hand
the resulting :class:`WorkMutationPlan` to the core transaction executor.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from okf_ext.moves import materialize
from okf_io import Bundle, Document, load_bundle, parse, validate

from work_tracker_okf.compose import rule_set
from work_tracker_okf.dependencies import DEFAULT_BLOCKS, DEFAULT_NEEDS, DependencyEdge
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import (
    MutationRefusal,
    PlannedWrite,
    WorkMutationPlan,
    _opaque_members,
    _plan_path_mutation,
    _plan_with_domain_reserved,
    _prepare_reference_markdown,
    _reference_owners,
)
from work_tracker_okf.paths import MANAGED_ARTIFACTS, parse_item_path, source_id_for_filename
from work_tracker_okf.resources import assets_root
from work_tracker_okf.vocabulary import PARENT_TYPES, ROOT_ONLY_TYPES, SLUG_PREFIXES, TYPES

LEGACY_IGNORE: tuple[str, ...] = IGNORE

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_BASENAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_LEGACY_ARTIFACTS = MappingProxyType(
    {
        "01-design-spec.md": MANAGED_ARTIFACTS["design"],
        "02-plan-plan.md": MANAGED_ARTIFACTS["plan"],
    }
)


@dataclass(frozen=True, slots=True)
class MigrationManifestEntry:
    old_path: str
    new_path: str
    type: str
    parent_old_path: str | None


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    mutation: WorkMutationPlan
    manifest: tuple[MigrationManifestEntry, ...]
    frontmatter_edits: tuple[str, ...]
    artifact_renames: tuple[tuple[str, str], ...]
    opaque_warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.mutation.ok

    def diff(self) -> str:
        lines = [
            *(f"{entry.old_path} -> {entry.new_path} [{entry.type}]" for entry in self.manifest),
            *self.frontmatter_edits,
            *(f"{old} -> {new}" for old, new in self.artifact_renames),
            *(f"warning: {warning}" for warning in self.opaque_warnings),
            *(f"refusal: {refusal.path}: {refusal.kind}: {refusal.detail}" for refusal in self.mutation.refusals),
        ]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _LegacyNode:
    old_path: str
    page_path: str
    basename: str
    archived: bool
    type: str
    document: Document
    parent_declared: bool
    parent_raw: str | None
    children_declared: bool
    children_raw: tuple[str, ...]


def _empty_mutation(
    root: Path,
    refusals: Sequence[MutationRefusal],
    *,
    path_mapping: Mapping[str, str] = MappingProxyType({}),
) -> WorkMutationPlan:
    return WorkMutationPlan(
        root=root,
        operation="migrate",
        path_mapping=MappingProxyType(dict(sorted(path_mapping.items()))),
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=tuple(sorted(refusals, key=lambda refusal: (refusal.path, refusal.kind, refusal.detail))),
        validate_paths=(),
    )


def _refused(
    bundle: Bundle,
    refusals: Sequence[MutationRefusal],
    *,
    manifest: Sequence[MigrationManifestEntry] = (),
    path_mapping: Mapping[str, str] = MappingProxyType({}),
) -> MigrationPlan:
    return MigrationPlan(
        mutation=_empty_mutation(bundle.root, refusals, path_mapping=path_mapping),
        manifest=tuple(manifest),
        frontmatter_edits=(),
        artifact_renames=(),
        opaque_warnings=(),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _legacy_children(value: object) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        return None
    children = tuple(child for child in value if isinstance(child, str) and child.strip())
    return children if len(children) == len(value) else None


def _with_ignored_work_documents(bundle: Bundle) -> Bundle:
    concepts = dict(bundle.concepts)
    unreadable = dict(bundle.unreadable)
    ignored = set(bundle.ignored)
    for member in sorted(bundle.ignored):
        if not member.endswith(".md"):
            continue
        concept_id = member.removesuffix(".md")
        if parse_item_path(concept_id) is None:
            continue
        try:
            document = Document.load(bundle.root / member)
        except UnicodeDecodeError as exc:
            unreadable[member] = f"not valid UTF-8: {exc}"
        except OSError as exc:
            unreadable[member] = f"could not be read: {exc}"
        else:
            concepts[concept_id] = document
        ignored.remove(member)
    return replace(
        bundle,
        concepts=MappingProxyType(dict(sorted(concepts.items()))),
        ignored=frozenset(ignored),
        unreadable=MappingProxyType(dict(sorted(unreadable.items()))),
    )


def _nodes(bundle: Bundle) -> tuple[tuple[_LegacyNode, ...], tuple[MutationRefusal, ...]]:
    nodes: list[_LegacyNode] = []
    refusals: list[MutationRefusal] = []
    for member, detail in sorted(bundle.unreadable.items()):
        if member.endswith(".md") and parse_item_path(member.removesuffix(".md")) is not None:
            refusals.append(MutationRefusal(member, "unreadable", detail))
    for concept_id, document in sorted(bundle.concepts.items()):
        if not concept_id.startswith("work/"):
            continue
        location = parse_item_path(concept_id)
        if document.parse_error is not None:
            if location is not None:
                refusals.append(MutationRefusal(f"{concept_id}.md", "parse-error", document.parse_error.message))
            continue
        data = document.fm_data(dates="iso")
        raw_type = data.get("type")
        type_name = _text(raw_type)
        if location is None:
            if type_name in TYPES:
                refusals.append(
                    MutationRefusal(
                        concept_id,
                        "legacy-path-invalid",
                        "legacy work item is outside a recognized work lane",
                    )
                )
            continue
        if raw_type is not None and not isinstance(raw_type, str):
            refusals.append(MutationRefusal(concept_id, "type-invalid", "legacy work item type must be text"))
            continue
        if type_name is None:
            refusals.append(MutationRefusal(concept_id, "missing-type", "legacy work item has no type"))
            continue
        if type_name not in TYPES:
            refusals.append(MutationRefusal(concept_id, "unknown-type", f"unknown work item type {type_name!r}"))
            continue
        parent_declared = "parent" in data
        parent_raw = _text(data.get("parent"))
        if parent_declared and parent_raw is None:
            refusals.append(MutationRefusal(concept_id, "hierarchy-invalid", "parent must name one work item"))
        children_declared = "children" in data
        children = _legacy_children(data.get("children"))
        if children_declared and children is None:
            refusals.append(MutationRefusal(concept_id, "hierarchy-invalid", "children must be a list of work items"))
            children = ()
        nodes.append(
            _LegacyNode(
                old_path=concept_id,
                page_path=f"{concept_id}.md",
                basename=location.basename,
                archived=location.archived,
                type=type_name,
                document=document,
                parent_declared=parent_declared,
                parent_raw=parent_raw,
                children_declared=children_declared,
                children_raw=children or (),
            )
        )
    if not nodes:
        refusals.append(MutationRefusal("work", "legacy-empty", "no legacy work items were found"))
    return tuple(nodes), tuple(refusals)


def _date_free_basename(node: _LegacyNode) -> str | None:
    remainder = _DATE_PREFIX.sub("", node.basename)
    prefix = SLUG_PREFIXES[node.type]
    basename = remainder if remainder == prefix or remainder.startswith(f"{prefix}-") else f"{prefix}-{remainder}"
    return basename if _BASENAME.fullmatch(basename) is not None else None


def _aliases(nodes: Sequence[_LegacyNode]) -> Mapping[str, tuple[str, ...]]:
    aliases: dict[str, set[str]] = {}
    for node in nodes:
        date_free = _DATE_PREFIX.sub("", node.basename)
        values = {
            node.old_path,
            node.page_path,
            node.basename,
            date_free,
            f"/{node.old_path}",
            f"/{node.page_path}",
        }
        for value in values:
            normalized = value.strip().removeprefix("/").removesuffix(".md")
            aliases.setdefault(normalized, set()).add(node.old_path)
    return MappingProxyType({key: tuple(sorted(value)) for key, value in sorted(aliases.items())})


def _resolve_legacy(
    raw: str,
    aliases: Mapping[str, tuple[str, ...]],
    *,
    owner: str,
    field: str,
) -> tuple[str | None, MutationRefusal | None]:
    normalized = raw.strip().removeprefix("/").removesuffix(".md")
    matches = aliases.get(normalized, ())
    if not matches:
        return None, MutationRefusal(owner, "missing-node", f"{field} names missing legacy item {raw!r}")
    if len(matches) != 1:
        return None, MutationRefusal(owner, "ambiguous-node", f"{field} names ambiguous legacy item {raw!r}")
    return matches[0], None


def _hierarchy(
    nodes: Sequence[_LegacyNode], aliases: Mapping[str, tuple[str, ...]]
) -> tuple[dict[str, str | None], tuple[MutationRefusal, ...]]:
    by_path = {node.old_path: node for node in nodes}
    parents: dict[str, str | None] = {node.old_path: None for node in nodes}
    parent_claims: dict[str, str] = {}
    child_claims: dict[str, str] = {}
    children_by_parent: dict[str, set[str]] = {}
    refusals: list[MutationRefusal] = []

    for node in nodes:
        if node.parent_raw is not None:
            parent, refusal = _resolve_legacy(node.parent_raw, aliases, owner=node.old_path, field="parent")
            if refusal is not None:
                refusals.append(refusal)
            elif parent is not None:
                parent_claims[node.old_path] = parent
        for raw_child in node.children_raw:
            child, refusal = _resolve_legacy(raw_child, aliases, owner=node.old_path, field="children")
            if refusal is not None:
                refusals.append(refusal)
                continue
            assert child is not None
            prior = child_claims.get(child)
            if prior is not None and prior != node.old_path:
                refusals.append(
                    MutationRefusal(
                        child,
                        "hierarchy-disagreement",
                        f"children lists claim both {prior!r} and {node.old_path!r}",
                    )
                )
            child_claims[child] = node.old_path
            children_by_parent.setdefault(node.old_path, set()).add(child)

    for node in nodes:
        physical = parse_item_path(node.old_path)
        physical_parent = None if physical is None else physical.parent_path
        candidates = {
            candidate
            for candidate in (parent_claims.get(node.old_path), child_claims.get(node.old_path), physical_parent)
            if candidate is not None
        }
        if len(candidates) > 1:
            refusals.append(
                MutationRefusal(
                    node.old_path,
                    "hierarchy-disagreement",
                    f"legacy hierarchy claims disagree: {sorted(candidates)!r}",
                )
            )
            continue
        parents[node.old_path] = next(iter(candidates), None)
        parent = parents[node.old_path]
        if parent is not None and parent not in by_path:
            refusals.append(
                MutationRefusal(
                    node.old_path,
                    "missing-node",
                    f"physical parent {parent!r} is not a legacy work item",
                )
            )
            continue
        if (
            parent is not None
            and by_path[parent].children_declared
            and node.old_path not in children_by_parent.get(parent, set())
        ):
            refusals.append(
                MutationRefusal(
                    node.old_path,
                    "hierarchy-disagreement",
                    f"parent {parent!r} omits child from its declared children list",
                )
            )
        if parent is not None and by_path[parent].type not in PARENT_TYPES:
            refusals.append(
                MutationRefusal(node.old_path, "invalid-parent-type", f"parent {parent!r} cannot own children")
            )
        if node.type in ROOT_ONLY_TYPES and parent is not None:
            refusals.append(MutationRefusal(node.old_path, "nested-release", "Release items remain root-only"))

    visiting: set[str] = set()
    visited: set[str] = set()
    for start in sorted(parents):
        cursor: str | None = start
        trail: list[str] = []
        while cursor is not None and cursor not in visited:
            if cursor in visiting:
                cycle = trail[trail.index(cursor) :] if cursor in trail else [cursor]
                refusals.extend(
                    MutationRefusal(path, "hierarchy-cycle", "legacy parent/children graph contains a cycle")
                    for path in cycle
                )
                break
            visiting.add(cursor)
            trail.append(cursor)
            cursor = parents.get(cursor)
        visiting.difference_update(trail)
        visited.update(trail)
    return parents, tuple(refusals)


def _targets(
    nodes: Sequence[_LegacyNode], parents: Mapping[str, str | None]
) -> tuple[dict[str, str], tuple[MutationRefusal, ...]]:
    by_path = {node.old_path: node for node in nodes}
    basenames: dict[str, str] = {}
    refusals: list[MutationRefusal] = []
    for node in nodes:
        basename = _date_free_basename(node)
        if basename is None:
            refusals.append(
                MutationRefusal(node.old_path, "basename-invalid", "cannot derive a date-free type-prefixed basename")
            )
        else:
            basenames[node.old_path] = basename

    targets: dict[str, str] = {}

    def target_for(old_path: str) -> str:
        existing = targets.get(old_path)
        if existing is not None:
            return existing
        node = by_path[old_path]
        parent = parents[old_path]
        if parent is None:
            lane = "work/_archive" if node.archived else "work"
        else:
            lane = f"{target_for(parent)}/children"
            if node.archived:
                lane = f"{lane}/_archive"
        target = f"{lane}/{basenames[old_path]}"
        targets[old_path] = target
        return target

    if refusals:
        return targets, tuple(refusals)
    for node in nodes:
        target_for(node.old_path)
    claimed: dict[str, str] = {}
    for source, destination in sorted(targets.items()):
        prior = claimed.get(destination)
        if prior is not None and prior != source:
            refusals.append(
                MutationRefusal(source, "destination-collision", f"{destination!r} is also claimed by {prior!r}")
            )
        claimed[destination] = source
    return targets, tuple(refusals)


def _dependency_edges(
    node: _LegacyNode,
    aliases: Mapping[str, tuple[str, ...]],
    targets: Mapping[str, str],
) -> tuple[tuple[DependencyEdge, ...], tuple[MutationRefusal, ...]]:
    raw = node.document.fm_data(dates="iso").get("depends_on")
    if raw is None:
        return (), ()
    entries: Sequence[object]
    if isinstance(raw, (str, Mapping)):
        entries = (raw,)
    elif isinstance(raw, Sequence):
        entries = raw
    else:
        return (), (MutationRefusal(node.old_path, "dependency-invalid", "depends_on must be a list"),)
    edges: list[DependencyEdge] = []
    refusals: list[MutationRefusal] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            raw_target = entry
            blocks = DEFAULT_BLOCKS
            needs = DEFAULT_NEEDS
        elif isinstance(entry, Mapping):
            raw_target = entry.get("slug", entry.get("path"))
            blocks = entry.get("blocks", DEFAULT_BLOCKS)
            needs = entry.get("needs", DEFAULT_NEEDS)
            if not isinstance(raw_target, str) or not isinstance(blocks, str) or not isinstance(needs, str):
                refusals.append(
                    MutationRefusal(node.old_path, "dependency-invalid", f"depends_on[{index}] has invalid values")
                )
                continue
        else:
            refusals.append(
                MutationRefusal(node.old_path, "dependency-invalid", f"depends_on[{index}] is not a string or mapping")
            )
            continue
        resolved, refusal = _resolve_legacy(raw_target, aliases, owner=node.old_path, field=f"depends_on[{index}]")
        if refusal is not None:
            refusals.append(replace(refusal, kind="dependency-" + refusal.kind))
            continue
        assert resolved is not None
        edges.append(DependencyEdge(targets[resolved], blocks, needs))
    return tuple(edges), tuple(refusals)


def _legacy_items(
    bundle: Bundle,
    nodes: Sequence[_LegacyNode],
    parents: Mapping[str, str | None],
    dependencies: Mapping[str, tuple[DependencyEdge, ...]],
) -> tuple[WorkItem, ...]:
    projected = {item.path: item for item in load_items(bundle)}
    children: dict[str, list[str]] = {}
    archived_children: dict[str, list[str]] = {}
    for child, parent in parents.items():
        if parent is None:
            continue
        node = next(candidate for candidate in nodes if candidate.old_path == child)
        (archived_children if node.archived else children).setdefault(parent, []).append(child)

    def ancestors(path: str) -> tuple[str, ...]:
        found: list[str] = []
        cursor = parents[path]
        while cursor is not None:
            found.append(cursor)
            cursor = parents[cursor]
        return tuple(reversed(found))

    result: list[WorkItem] = []
    for node in nodes:
        item = projected[node.old_path]
        data = node.document.fm_data(dates="iso")
        result.append(
            replace(
                item,
                work_status=_text(data.get("workflow_status")) or item.work_status,
                parent_path=parents[node.old_path],
                ancestor_paths=ancestors(node.old_path),
                active_child_paths=tuple(sorted(children.get(node.old_path, ()))),
                archived_child_paths=tuple(sorted(archived_children.get(node.old_path, ()))),
                dependency_edges=dependencies[node.old_path],
                dependency_issues=(),
            )
        )
    return tuple(sorted(result, key=lambda item: item.path))


def _artifact_name(name: str) -> str:
    return _LEGACY_ARTIFACTS.get(name, name)


def _desired_member(
    source: str,
    nodes: Sequence[_LegacyNode],
    targets: Mapping[str, str],
    artifact_sources: frozenset[str],
) -> str:
    for node in sorted(nodes, key=lambda candidate: len(candidate.old_path), reverse=True):
        if source == node.page_path:
            return f"{targets[node.old_path]}.md"
        prefix = f"{node.old_path}/"
        if not source.startswith(prefix):
            continue
        relative = source[len(prefix) :]
        if relative.startswith("children/"):
            return f"{targets[node.old_path]}/{relative}"
        if relative.startswith("references/"):
            rest = relative.removeprefix("references/")
            parts = PurePosixPath(rest).parts
            name = _artifact_name(parts[-1]) if source in artifact_sources else parts[-1]
            renamed = PurePosixPath(*parts[:-1], name).as_posix()
            return f"{targets[node.old_path]}/references/{renamed}"
        parts = PurePosixPath(relative).parts
        name = _artifact_name(parts[-1]) if source in artifact_sources else parts[-1]
        renamed = PurePosixPath(*parts[:-1], name).as_posix()
        return f"{targets[node.old_path]}/references/{renamed}"
    return source


def _source_entries(data: object) -> list[dict[str, Any]] | None:
    if not isinstance(data, Sequence) or isinstance(data, str | bytes):
        return None
    converted: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, Mapping):
            return None
        entry = {str(key): value for key, value in raw.items()}
        resource = entry.get("resource")
        if isinstance(resource, str):
            name = PurePosixPath(resource).name
            if name in MANAGED_ARTIFACTS.values():
                entry["id"] = source_id_for_filename(name)
        converted.append(entry)
    return converted


def _convert_document(
    content: bytes,
    *,
    path: Path,
    node: _LegacyNode | None,
    edges: Sequence[DependencyEdge],
    superseded_by: str | None,
) -> tuple[bytes | None, MutationRefusal | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, MutationRefusal(path.as_posix(), "parse-error", f"planned Markdown is not UTF-8: {exc}")
    document = parse(text, path=path)
    if document.parse_error is not None:
        return None, MutationRefusal(path.as_posix(), "parse-error", document.parse_error.message)
    if node is not None:
        data = document.fm_data(dates="iso")
        legacy_status = _text(data.get("workflow_status"))
        current_status = _text(data.get("work_status"))
        if legacy_status is not None and current_status is not None and legacy_status != current_status:
            return None, MutationRefusal(
                node.old_path,
                "frontmatter-disagreement",
                "workflow_status and work_status disagree",
            )
        if legacy_status is not None:
            document.set("work_status", legacy_status)
        document.delete("workflow_status")
        document.delete("parent")
        document.delete("children")
        document.delete("slug")
        if "depends_on" in data:
            document.set(
                "depends_on",
                [{"path": edge.path, "blocks": edge.blocks, "needs": edge.needs} for edge in edges],
            )
        sources = _source_entries(data.get("sources"))
        if sources is None and "sources" in data:
            return None, MutationRefusal(node.old_path, "sources-invalid", "sources must be a list of mappings")
        if sources is not None:
            document.set("sources", sources)
        if superseded_by is not None:
            document.set("superseded_by", superseded_by)
    return document.serialize().encode("utf-8"), None


def _projected_refusals(
    plan: WorkMutationPlan,
    manifest: Sequence[MigrationManifestEntry],
) -> tuple[MutationRefusal, ...]:
    files = {
        path.relative_to(plan.root).as_posix(): path.read_bytes() for path in plan.root.rglob("*") if path.is_file()
    }
    for move in plan.moves:
        content = files.pop(move.source, None)
        if content is not None:
            files[move.dest] = content
    for write in plan.writes:
        if write.source_member is not None:
            files.pop(write.source_member, None)
        files[write.member] = write.after
    for deleted in sorted(plan.deletes, key=lambda member: member.count("/"), reverse=True):
        files.pop(deleted, None)
        prefix = f"{deleted}/"
        files = {member: content for member, content in files.items() if not member.startswith(prefix)}

    with tempfile.TemporaryDirectory(prefix="work-migration-projection-") as temporary:
        root = Path(temporary)
        for member, content in files.items():
            target = root / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        try:
            bundle = load_bundle(root, ignore=IGNORE)
            bundle = _with_ignored_work_documents(bundle)
            items = load_items(bundle)
            report = validate(
                bundle,
                today=date.max,
                extra_rules=rule_set(root, declarations_dir=Path(str(assets_root()))),
            )
        except (OSError, ValueError) as exc:
            return (MutationRefusal("", "projection-load", str(exc)),)
        expected = {entry.new_path for entry in manifest}
        actual = {item.path for item in items}
        _, raw_refusals = _nodes(bundle)
        refusals = [
            MutationRefusal(path, "projection-missing", "migrated work item did not reload")
            for path in sorted(expected - actual)
        ]
        refusals.extend(
            MutationRefusal(path, "projection-unexpected", "unmanifested work item remains after migration")
            for path in sorted(actual - expected)
        )
        refusals.extend(
            MutationRefusal(member, "projection-unreadable", detail)
            for member, detail in sorted(bundle.unreadable.items())
            if member.endswith(".md") and parse_item_path(member.removesuffix(".md")) is not None
        )
        refusals.extend(
            replace(refusal, kind="projection-" + refusal.kind)
            for refusal in raw_refusals
            if refusal.kind != "unreadable"
        )
        refusals.extend(
            MutationRefusal(
                finding.path or "",
                "projection-lint",
                f"{finding.code}: {finding.message}",
            )
            for finding in report.errors
            if finding.path is not None
            and (finding.path.removesuffix(".md") in expected or finding.path.endswith("index.md"))
        )
        return tuple(refusals)


def plan_migration(bundle: Bundle) -> MigrationPlan:
    """Plan a complete legacy-to-path-native conversion without touching *bundle*."""
    bundle = _with_ignored_work_documents(bundle)
    nodes, node_refusals = _nodes(bundle)
    aliases = _aliases(nodes)
    parents, hierarchy_refusals = _hierarchy(nodes, aliases)
    if node_refusals or hierarchy_refusals:
        return _refused(bundle, (*node_refusals, *hierarchy_refusals))
    targets, target_refusals = _targets(nodes, parents)
    manifest = tuple(
        MigrationManifestEntry(node.old_path, targets.get(node.old_path, ""), node.type, parents.get(node.old_path))
        for node in sorted(nodes, key=lambda candidate: candidate.old_path)
        if node.old_path in targets
    )
    if target_refusals:
        return _refused(bundle, target_refusals, manifest=manifest, path_mapping=targets)

    dependencies: dict[str, tuple[DependencyEdge, ...]] = {}
    superseded: dict[str, str | None] = {}
    dependency_refusals: list[MutationRefusal] = []
    for node in nodes:
        edges, refusals = _dependency_edges(node, aliases, targets)
        dependencies[node.old_path] = edges
        dependency_refusals.extend(refusals)
        raw_superseded = node.document.fm_data(dates="iso").get("superseded_by")
        if raw_superseded is None:
            superseded[node.old_path] = None
        elif not isinstance(raw_superseded, str):
            dependency_refusals.append(
                MutationRefusal(node.old_path, "superseded-invalid", "superseded_by must name one work item")
            )
        else:
            resolved, refusal = _resolve_legacy(
                raw_superseded,
                aliases,
                owner=node.old_path,
                field="superseded_by",
            )
            if refusal is not None:
                dependency_refusals.append(replace(refusal, kind="superseded-" + refusal.kind))
            else:
                assert resolved is not None
                superseded[node.old_path] = targets[resolved]
    if dependency_refusals:
        return _refused(bundle, dependency_refusals, manifest=manifest, path_mapping=targets)

    items = _legacy_items(bundle, nodes, parents, dependencies)
    base = _plan_path_mutation(
        bundle,
        items,
        "migrate",
        targets,
        roots=tuple(sorted(targets)),
    )
    if not base.ok or base.move_plan is None:
        return MigrationPlan(base, manifest, (), (), base.warnings)

    reference_owners = _reference_owners(bundle, items)
    artifact_sources = frozenset(reference_owners)
    desired = {
        move.source: _desired_member(move.source, nodes, targets, artifact_sources) for move in base.move_plan.moves
    }
    claimed: dict[str, str] = {}
    collision_refusals: list[MutationRefusal] = []
    sources = set(desired)
    for source, destination in sorted(desired.items()):
        prior = claimed.get(destination)
        if prior is not None and prior != source:
            collision_refusals.append(
                MutationRefusal(source, "destination-collision", f"{destination!r} is also claimed by {prior!r}")
            )
        claimed[destination] = source
        if destination not in sources and os.path.lexists(bundle.root / destination):
            collision_refusals.append(
                MutationRefusal(source, "destination-collision", f"{destination!r} already exists")
            )
    if collision_refusals:
        return _refused(bundle, collision_refusals, manifest=manifest, path_mapping=targets)

    opaque = _opaque_members(bundle, reference_owners)
    planning_bundle, promotion_refusals = _prepare_reference_markdown(
        bundle,
        frozenset(reference_owners),
        opaque,
    )
    if promotion_refusals:
        return _refused(bundle, promotion_refusals, manifest=manifest, path_mapping=targets)
    move_plan = _plan_with_domain_reserved(planning_bundle, desired, {}, opaque)
    if not move_plan.ok:
        refusals = tuple(MutationRefusal(item.path, item.kind, item.detail) for item in move_plan.refusals)
        return _refused(bundle, refusals, manifest=manifest, path_mapping=targets)
    try:
        effects = materialize(planning_bundle, move_plan)
    except ValueError as exc:
        return _refused(
            bundle,
            (MutationRefusal("", "materialize-error", str(exc)),),
            manifest=manifest,
            path_mapping=targets,
        )

    base_destinations = {move.source: move.dest for move in base.move_plan.moves}
    node_by_destination = {f"{targets[node.old_path]}.md": node for node in nodes}
    source_by_destination = {
        move.dest: move.source for move in move_plan.moves if not move.is_asset and not move.opaque
    }
    base_preimages = {write.source_member or write.member: write for write in base.writes}
    writes: list[PlannedWrite] = []
    conversion_refusals: list[MutationRefusal] = []
    index_members = {write.member for write in base.writes if PurePosixPath(write.member).name == "index.md"}
    for member, after in sorted(effects.writes.items()):
        if member in index_members:
            continue
        item_node = node_by_destination.get(member)
        converted, refusal = _convert_document(
            after,
            path=bundle.root / member,
            node=item_node,
            edges=() if item_node is None else dependencies[item_node.old_path],
            superseded_by=None if item_node is None else superseded[item_node.old_path],
        )
        if refusal is not None:
            conversion_refusals.append(refusal)
            continue
        assert converted is not None
        source_member = source_by_destination.get(member)
        preimage = source_member or member
        planned_preimage = base_preimages.get(preimage)
        if planned_preimage is None:
            conversion_refusals.append(
                MutationRefusal(preimage, "preimage-missing", "domain planner did not retain this write preimage")
            )
            continue
        writes.append(
            PlannedWrite(
                member,
                planned_preimage.before_digest,
                converted,
                source_member if planned_preimage.before_digest is not None and source_member != member else None,
            )
        )
    writes.extend(
        replace(write, source_member=None) if write.source_member == write.member else write
        for write in base.writes
        if write.member in index_members
    )
    if conversion_refusals:
        return _refused(bundle, conversion_refusals, manifest=manifest, path_mapping=targets)

    mkdirs = tuple(
        sorted(
            set(base.mkdirs)
            | {
                parent
                for member in (*[write.member for write in writes], *[move.dest for move in effects.renames])
                if (parent := PurePosixPath(member).parent.as_posix()) != "."
            }
        )
    )
    warnings = tuple(base.warnings)
    mutation = WorkMutationPlan(
        root=bundle.root,
        operation="migrate",
        path_mapping=MappingProxyType(dict(sorted(targets.items()))),
        move_plan=move_plan,
        moves=effects.renames,
        writes=tuple(sorted(writes, key=lambda write: write.member)),
        deletes=tuple(sorted(set((*base.deletes, *effects.deletes)))),
        mkdirs=mkdirs,
        warnings=warnings,
        refusals=(),
        validate_paths=tuple(sorted(targets.values())),
        directory_preconditions=base.directory_preconditions,
    )
    projection_refusals = _projected_refusals(mutation, manifest)
    if projection_refusals:
        mutation = replace(mutation, moves=(), writes=(), deletes=(), mkdirs=(), refusals=projection_refusals)

    artifact_renames = tuple(
        sorted(
            (source, destination)
            for source, destination in desired.items()
            if base_destinations.get(source) != destination
        )
    )
    frontmatter_edits = tuple(
        f"{node.page_path}: workflow_status -> work_status; remove parent/children; normalize dependencies/sources"
        for node in sorted(nodes, key=lambda candidate: candidate.old_path)
    )
    return MigrationPlan(mutation, manifest, frontmatter_edits, artifact_renames, warnings)


__all__ = [
    "LEGACY_IGNORE",
    "MigrationManifestEntry",
    "MigrationPlan",
    "plan_migration",
]
