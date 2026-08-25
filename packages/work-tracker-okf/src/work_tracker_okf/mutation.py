"""Pure, unified filesystem effects for work-item path mutations."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal, Protocol

from okf_ext.moves import Move, MovePlan, ReferenceField, materialize, plan_move_many, stranded
from okf_io import Bundle, Document
from okf_io.bundle import INDEX_NAME, LOG_NAME, canonical_id
from okf_io.links import resolve_reference

from work_tracker_okf.indexes import plan_indexes, reconcile_marked_index
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import MANAGED_ARTIFACTS, child_lane, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES

Operation = Literal["file", "reparent", "adopt", "archive", "migrate", "indexes"]

_REFERENCE_FIELDS = (
    ReferenceField(("depends_on", "*", "path"), target="concept"),
    ReferenceField(("superseded_by",), target="concept"),
)
_CANONICAL_FILENAMES = frozenset(MANAGED_ARTIFACTS.values())


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    member: str
    before_digest: str | None
    after: bytes
    source_member: str | None = None


@dataclass(frozen=True, slots=True)
class DirectoryPrecondition:
    """Expected recursive state: ``None`` means *member* must stay absent."""

    member: str
    before_digest: str | None


@dataclass(frozen=True, slots=True)
class MutationRefusal:
    path: str
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class WorkMutationPlan:
    root: Path
    operation: Operation
    path_mapping: Mapping[str, str]
    move_plan: MovePlan | None
    moves: tuple[Move, ...]
    writes: tuple[PlannedWrite, ...]
    deletes: tuple[str, ...]
    mkdirs: tuple[str, ...]
    warnings: tuple[str, ...]
    refusals: tuple[MutationRefusal, ...]
    validate_paths: tuple[str, ...]
    directory_preconditions: tuple[DirectoryPrecondition, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals and (self.move_plan is None or self.move_plan.ok)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _HashSink(Protocol):
    def update(self, content: bytes) -> None: ...


def _manifest_field(hasher: _HashSink, content: bytes) -> None:
    hasher.update(len(content).to_bytes(8, "big"))
    hasher.update(content)


def directory_manifest_digest(path: Path) -> str:
    """Hash a deterministic recursive lstat manifest.

    Every length-framed entry carries its relative byte name and lstat kind.
    Symlinks contribute their target and regular files their content digest;
    directory symlinks are never followed. The iterative walk is byte-sorted.
    """
    hasher = hashlib.sha256()
    pending = [(".", path)]
    while pending:
        relative, current = pending.pop()
        mode = current.lstat().st_mode
        if stat.S_ISDIR(mode):
            kind = b"directory"
            payload = b""
        elif stat.S_ISREG(mode):
            kind = b"file"
            payload = hashlib.sha256(current.read_bytes()).digest()
        elif stat.S_ISLNK(mode):
            kind = b"symlink"
            payload = os.fsencode(current.readlink())
        else:
            kind = f"other:{stat.S_IFMT(mode):o}".encode("ascii")
            payload = b""
        _manifest_field(hasher, relative.encode("utf-8", "surrogateescape"))
        _manifest_field(hasher, kind)
        _manifest_field(hasher, payload)
        if kind == b"directory":
            entries = sorted(current.iterdir(), key=lambda entry: os.fsencode(entry.name), reverse=True)
            pending.extend(
                (
                    entry.name if relative == "." else f"{relative}/{entry.name}",
                    entry,
                )
                for entry in entries
            )
    return hasher.hexdigest()


def _all_members(bundle: Bundle) -> tuple[str, ...]:
    members = {f"{concept_id}.md" for concept_id in bundle.concepts}
    members.update(bundle.assets)
    members.update(f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME for directory in bundle.indexes)
    members.update(f"{directory}/{LOG_NAME}" if directory else LOG_NAME for directory in bundle.logs)
    members.update(bundle.ignored)
    members.update(bundle.unreadable)
    return tuple(sorted(members))


def _parsed_markdown_members(bundle: Bundle) -> frozenset[str]:
    members = {f"{concept_id}.md" for concept_id in bundle.concepts}
    members.update(f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME for directory in bundle.indexes)
    members.update(f"{directory}/{LOG_NAME}" if directory else LOG_NAME for directory in bundle.logs)
    return frozenset(members)


def _subtree_items(items: Sequence[WorkItem], roots: Sequence[str]) -> tuple[WorkItem, ...]:
    """Own every projected descendant with an iterative walk."""
    by_path = {item.path: item for item in items}
    found: list[WorkItem] = []
    seen: set[str] = set()
    pending = list(reversed(tuple(roots)))
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        item = by_path.get(path)
        if item is None:
            continue
        found.append(item)
        pending.extend(reversed((*item.active_child_paths, *item.archived_child_paths)))
    return tuple(sorted(found, key=lambda item: item.path))


def _member_mapping(bundle: Bundle, path_mapping: Mapping[str, str]) -> dict[str, str]:
    ordered = sorted(path_mapping, key=lambda path: (path.count("/"), len(path)), reverse=True)
    mapping: dict[str, str] = {}
    for member in _all_members(bundle):
        if PurePosixPath(member).name == INDEX_NAME and "/references/" not in member:
            continue
        for source in ordered:
            destination = path_mapping[source]
            if member == f"{source}.md":
                mapping[member] = f"{destination}.md"
                break
            if member.startswith(f"{source}/"):
                mapping[member] = f"{destination}{member[len(source) :]}"
                break
    return mapping


def _directory_mapping(
    root: Path,
    roots: Sequence[str],
    path_mapping: Mapping[str, str],
) -> tuple[dict[str, str], tuple[MutationRefusal, ...]]:
    """Map every existing owned directory with an iterative disk walk."""
    directories: set[str] = set()
    refusals: list[MutationRefusal] = []
    pending = [root / source for source in reversed(tuple(roots))]
    seen: set[Path] = set()
    while pending:
        directory = pending.pop()
        if directory in seen or not directory.is_dir() or directory.is_symlink():
            continue
        seen.add(directory)
        relative = directory.relative_to(root).as_posix()
        directories.add(relative)
        try:
            entries = tuple(directory.iterdir())
        except OSError as exc:
            refusals.append(MutationRefusal(relative, "directory-read", str(exc)))
            continue
        pending.extend(entry for entry in reversed(entries) if entry.is_dir() and not entry.is_symlink())

    ordered = sorted(path_mapping, key=lambda path: (path.count("/"), len(path)), reverse=True)
    mapping: dict[str, str] = {}
    for source_directory in sorted(directories):
        for source in ordered:
            if source_directory == source or source_directory.startswith(f"{source}/"):
                mapping[source_directory] = f"{path_mapping[source]}{source_directory[len(source) :]}"
                break
    return mapping, tuple(refusals)


def _canonical_member_lookup(bundle: Bundle) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for member in _all_members(bundle):
        grouped.setdefault(canonical_id(member), []).append(member)
    return {canonical: tuple(members) for canonical, members in grouped.items()}


def _matching_members(
    bundle: Bundle,
    path: str,
    canonical_members: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    member = bundle.member_id(path)
    if member is not None:
        return (member,)
    return canonical_members.get(canonical_id(path), ())


def _reference_owners(bundle: Bundle, items: Sequence[WorkItem]) -> dict[str, frozenset[str]]:
    owners: dict[str, set[str]] = {}
    canonical_members = _canonical_member_lookup(bundle)
    for item in items:
        for source in item.sources:
            if not source.resource:
                continue
            resolved = resolve_reference(source.resource, source_id=item.path)
            if resolved is not None:
                members = _matching_members(bundle, resolved, canonical_members) or (resolved,)
                for member in members:
                    owners.setdefault(member, set()).add(item.path)
        for filename in _CANONICAL_FILENAMES:
            if filename == MANAGED_ARTIFACTS["decisions"] and item.type not in PARENT_TYPES:
                continue
            canonical = f"{item.path}/references/{filename}"
            for canonical_member in _matching_members(bundle, canonical, canonical_members):
                owners.setdefault(canonical_member, set()).add(item.path)
    return {member: frozenset(paths) for member, paths in owners.items()}


def _opaque_members(
    bundle: Bundle,
    reference_owners: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    return frozenset(
        member for member in _all_members(bundle) if "/references/" in member and member not in reference_owners
    )


def _opaque_warnings(root: Path, opaque: Sequence[str], old_paths: Sequence[str]) -> tuple[str, ...]:
    warnings: list[str] = []
    for member in sorted(opaque):
        path = root / member
        if path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        mentioned = next((old for old in old_paths if old in text), None)
        if mentioned is not None:
            warnings.append(
                f"{member}: opaque UTF-8 content mentions old canonical path {mentioned!r}; bytes remain unchanged"
            )
    return tuple(warnings)


def _resolved_inside(root: Path, path: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def _symlink_refusals(root: Path, roots: Sequence[str], member_mapping: Mapping[str, str]) -> list[MutationRefusal]:
    refusals: list[MutationRefusal] = []
    checked: set[Path] = set()
    for source in roots:
        candidates = [root / f"{source}.md", root / source]
        owned = root / source
        if owned.is_dir():
            candidates.extend(owned.rglob("*"))
        for candidate in candidates:
            if candidate in checked or not candidate.is_symlink():
                continue
            checked.add(candidate)
            if not _resolved_inside(root, candidate):
                refusals.append(
                    MutationRefusal(
                        candidate.relative_to(root).as_posix(),
                        "symlink-escape",
                        "a source symlink resolves outside the bundle root",
                    )
                )

    for destination in sorted(member_mapping.values()):
        cursor = root
        for segment in PurePosixPath(destination).parent.parts:
            cursor /= segment
            if cursor.is_symlink() and not _resolved_inside(root, cursor):
                relative = cursor.relative_to(root).as_posix()
                refusals.append(
                    MutationRefusal(
                        relative,
                        "symlink-escape",
                        "a destination ancestor resolves outside the bundle root",
                    )
                )
                break
    return refusals


def _collision_refusals(root: Path, member_mapping: Mapping[str, str]) -> list[MutationRefusal]:
    refusals: list[MutationRefusal] = []
    destinations: dict[str, str] = {}
    sources = set(member_mapping)
    for source, destination in sorted(member_mapping.items()):
        claimed = destinations.get(destination)
        if claimed is not None and claimed != source:
            refusals.append(MutationRefusal(source, "dest-exists", f"{destination!r} is also claimed by {claimed!r}"))
        destinations[destination] = source
        if destination not in sources and os.path.lexists(root / destination):
            refusals.append(MutationRefusal(source, "dest-exists", f"{destination!r} already exists"))
    return refusals


def _directory_collision_refusals(root: Path, mapping: Mapping[str, str]) -> list[MutationRefusal]:
    refusals: list[MutationRefusal] = []
    destinations: dict[str, str] = {}
    sources = set(mapping)
    for source, destination in sorted(mapping.items()):
        claimed = destinations.get(destination)
        if claimed is not None and claimed != source:
            refusals.append(
                MutationRefusal(
                    source,
                    "directory-dest-exists",
                    f"directory {destination!r} is also claimed by {claimed!r}",
                )
            )
        destinations[destination] = source
        if destination not in sources and os.path.lexists(root / destination):
            refusals.append(
                MutationRefusal(source, "directory-dest-exists", f"directory {destination!r} already exists")
            )
    return refusals


def _directory_preconditions(
    root: Path,
    directory_mapping: Mapping[str, str],
    path_mapping: Mapping[str, str],
) -> tuple[tuple[DirectoryPrecondition, ...], tuple[MutationRefusal, ...]]:
    conditions: dict[str, DirectoryPrecondition] = {}
    refusals: list[MutationRefusal] = []
    existing_sources = set(directory_mapping) | {source for source in path_mapping if os.path.lexists(root / source)}
    for source in sorted(existing_sources):
        try:
            digest = directory_manifest_digest(root / source)
        except OSError as exc:
            refusals.append(MutationRefusal(source, "directory-read", str(exc)))
        else:
            conditions[source] = DirectoryPrecondition(source, digest)
    for source in sorted(set(path_mapping) - existing_sources):
        conditions[source] = DirectoryPrecondition(source, None)
    destinations = set(directory_mapping.values()) | set(path_mapping.values())
    for destination in sorted(destinations):
        if os.path.lexists(root / destination):
            refusals.append(
                MutationRefusal(
                    destination,
                    "directory-dest-exists",
                    f"owned destination directory {destination!r} already exists",
                )
            )
        conditions[destination] = DirectoryPrecondition(destination, None)
    return tuple(conditions[member] for member in sorted(conditions)), tuple(refusals)


def _add_absent_mkdir_ancestors(
    root: Path,
    mkdirs: Sequence[str],
    conditions: Mapping[str, DirectoryPrecondition],
) -> tuple[DirectoryPrecondition, ...]:
    complete = dict(conditions)
    for member in mkdirs:
        parts = PurePosixPath(member).parts
        for length in range(1, len(parts) + 1):
            ancestor = PurePosixPath(*parts[:length]).as_posix()
            if not os.path.lexists(root / ancestor):
                complete.setdefault(ancestor, DirectoryPrecondition(ancestor, None))
    return tuple(complete[member] for member in sorted(complete))


def _lane(path: str) -> str:
    return path.rsplit("/", 1)[0]


def _future_items(items: Sequence[WorkItem], path_mapping: Mapping[str, str]) -> tuple[WorkItem, ...]:
    remapped: list[WorkItem] = []
    for item in items:
        path = path_mapping.get(item.path, item.path)
        parent = path_mapping.get(item.parent_path, item.parent_path) if item.parent_path is not None else None
        ancestors = tuple(path_mapping.get(ancestor, ancestor) for ancestor in item.ancestor_paths)
        remapped.append(
            replace(
                item,
                path=path,
                page_path=f"{path}.md",
                basename=path.rsplit("/", 1)[-1],
                archived="/_archive/" in f"/{path}/",
                parent_path=parent,
                ancestor_paths=ancestors,
                active_child_paths=tuple(path_mapping.get(child, child) for child in item.active_child_paths),
                archived_child_paths=tuple(path_mapping.get(child, child) for child in item.archived_child_paths),
            )
        )
    return tuple(remapped)


def _lane_mapping(items: Sequence[WorkItem], path_mapping: Mapping[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        destination = path_mapping.get(item.path)
        if destination is None or item.type not in PARENT_TYPES:
            continue
        mapping[child_lane(item.path)] = child_lane(destination)
        mapping[child_lane(item.path, archived=True)] = child_lane(destination, archived=True)
    return mapping


def _index_effects(
    root: Path,
    items: Sequence[WorkItem],
    path_mapping: Mapping[str, str],
    rendered: Mapping[str, bytes],
) -> tuple[
    tuple[PlannedWrite, ...],
    tuple[str, ...],
    frozenset[str],
    tuple[MutationRefusal, ...],
]:
    moved_items = tuple(item for item in items if item.path in path_mapping)
    lane_mapping = _lane_mapping(moved_items, path_mapping)
    affected = {_lane(item.path) for item in moved_items}
    affected.update(_lane(path_mapping[item.path]) for item in moved_items)
    affected.update(lane_mapping)
    affected.update(lane_mapping.values())
    inverse: dict[str, str] = {}
    refusals: list[MutationRefusal] = []
    for source, destination in sorted(lane_mapping.items()):
        claimed = inverse.get(destination)
        if claimed is not None and claimed != source:
            refusals.append(
                MutationRefusal(source, "dest-exists", f"index lane {destination!r} is also claimed by {claimed!r}")
            )
        inverse[destination] = source

    final_items = _future_items(items, path_mapping)
    writes: list[PlannedWrite] = []
    deletes: list[str] = []
    index_members: set[str] = set()
    final_lanes = (affected - set(lane_mapping)) | set(lane_mapping.values())
    for destination_lane in sorted(final_lanes):
        source_lane = inverse.get(destination_lane, destination_lane)
        source_member = f"{source_lane}/index.md"
        destination_member = f"{destination_lane}/index.md"
        index_members.update((source_member, destination_member))
        source_path = root / source_member
        destination_path = root / destination_member
        moved = source_member != destination_member
        if moved and os.path.lexists(destination_path):
            refusals.append(MutationRefusal(source_member, "dest-exists", f"{destination_member!r} already exists"))
            continue
        try:
            before_bytes = source_path.read_bytes()
        except FileNotFoundError:
            before_bytes = None
        except OSError as exc:
            refusals.append(MutationRefusal(source_member, "index-read", str(exc)))
            continue
        content_bytes = rendered.get(source_member, before_bytes)
        try:
            before = content_bytes.decode("utf-8") if content_bytes is not None else None
        except UnicodeDecodeError as exc:
            refusals.append(MutationRefusal(source_member, "parse-error", f"index is not UTF-8: {exc}"))
            continue
        try:
            lane_plan = plan_indexes(root, final_items, lanes=(destination_lane,))[0]
        except UnicodeDecodeError as exc:
            refusals.append(MutationRefusal(destination_member, "parse-error", f"index is not UTF-8: {exc}"))
            continue
        except OSError as exc:
            refusals.append(MutationRefusal(destination_member, "index-read", str(exc)))
            continue
        after = reconcile_marked_index(before, lane_plan.entries).encode("utf-8")
        if moved or before_bytes != after:
            before_digest = _digest(before_bytes) if before_bytes is not None else None
            preimage_source = (
                source_member if before_bytes is not None and source_member != destination_member else None
            )
            writes.append(PlannedWrite(destination_member, before_digest, after, preimage_source))
        if before_bytes is not None and moved:
            deletes.append(source_member)
    return (
        tuple(sorted(writes, key=lambda write: write.member)),
        tuple(sorted(set(deletes))),
        frozenset(index_members),
        tuple(refusals),
    )


def _move_refusals(plan: MovePlan) -> tuple[MutationRefusal, ...]:
    return tuple(MutationRefusal(refusal.path, refusal.kind, refusal.detail) for refusal in plan.refusals)


@dataclass(frozen=True, slots=True)
class _AliasedBundle(Bundle):
    aliases: Mapping[str, str]

    def member_id(self, path: str) -> str | None:
        member = path.strip()
        alias = self.aliases.get(member)
        if alias is not None:
            return alias
        resolved = Bundle.member_id(self, path)
        return None if resolved is None else self.aliases.get(resolved, resolved)


def _reserved_aliases(
    bundle: Bundle,
    mapping: Mapping[str, str],
    opaque: frozenset[str],
) -> tuple[_AliasedBundle, dict[str, str], dict[str, str], dict[str, str]]:
    """Represent domain-owned reserved Markdown under virtual ordinary names."""
    concepts = dict(bundle.concepts)
    indexes = dict(bundle.indexes)
    logs = dict(bundle.logs)
    ignored = set(bundle.ignored)
    member_aliases: dict[str, str] = {}
    source_aliases: dict[str, str] = {}
    destination_aliases: dict[str, str] = {}
    used = set(_all_members(bundle))

    for source, destination in sorted(mapping.items()):
        pure = PurePosixPath(source)
        directory = pure.parent.as_posix()
        directory = "" if directory == "." else directory
        suffix = hashlib.sha256(source.encode("utf-8", "surrogateescape")).hexdigest()[:12]
        counter = 0
        while True:
            extra = "" if counter == 0 else f"-{counter}"
            name = f".work-tracker-reserved-{pure.stem}-{suffix}{extra}.md"
            source_alias = (pure.parent / name).as_posix()
            destination_alias = (PurePosixPath(destination).parent / name).as_posix()
            if source_alias not in used and destination_alias not in used:
                break
            counter += 1
        used.update((source_alias, destination_alias))
        ignored.discard(source)
        if source in opaque:
            ignored.add(source_alias)
        else:
            documents = indexes if pure.name == INDEX_NAME else logs
            concepts[source_alias.removesuffix(".md")] = documents.pop(directory)
        member_aliases[source] = source_alias
        source_aliases[source_alias] = source
        destination_aliases[destination_alias] = destination

    virtual = _AliasedBundle(
        root=bundle.root,
        concepts=MappingProxyType(dict(sorted(concepts.items()))),
        indexes=MappingProxyType(dict(sorted(indexes.items()))),
        logs=MappingProxyType(dict(sorted(logs.items()))),
        assets=bundle.assets,
        ignored=frozenset(ignored),
        unreadable=bundle.unreadable,
        _canonical=bundle._canonical,
        canonical_collisions=bundle.canonical_collisions,
        aliases=MappingProxyType(member_aliases),
    )
    return virtual, member_aliases, source_aliases, destination_aliases


def _restore_reserved_text(
    value: str,
    source_aliases: Mapping[str, str],
    destination_aliases: Mapping[str, str],
) -> str:
    replacements: dict[str, str] = {}
    for aliases in (source_aliases, destination_aliases):
        for alias, actual in aliases.items():
            replacements[alias] = actual
            replacements[alias.removesuffix(".md")] = actual.removesuffix(".md")
            replacements[PurePosixPath(alias).name] = PurePosixPath(actual).name
            replacements[PurePosixPath(alias).stem] = PurePosixPath(actual).stem
    restored = value
    for alias, actual in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        restored = restored.replace(alias, actual)
    return restored


def _plan_with_domain_reserved(
    bundle: Bundle,
    ordinary_mapping: Mapping[str, str],
    reserved_mapping: Mapping[str, str],
    opaque: frozenset[str],
) -> MovePlan:
    if not reserved_mapping:
        return plan_move_many(
            bundle,
            ordinary_mapping,
            extra_reference_fields=_REFERENCE_FIELDS,
            opaque_members=opaque,
        )
    virtual, member_aliases, source_aliases, destination_aliases = _reserved_aliases(
        bundle,
        reserved_mapping,
        opaque,
    )
    virtual_mapping = dict(ordinary_mapping)
    for source, destination in reserved_mapping.items():
        virtual_mapping[member_aliases[source]] = next(
            alias for alias, actual in destination_aliases.items() if actual == destination
        )
    virtual_opaque = (opaque - set(reserved_mapping)) | {
        member_aliases[source] for source in reserved_mapping if source in opaque
    }
    planned = plan_move_many(
        virtual,
        virtual_mapping,
        extra_reference_fields=_REFERENCE_FIELDS,
        opaque_members=frozenset(virtual_opaque),
    )

    def actual_source(member: str) -> str:
        return source_aliases.get(member) or member

    def actual_destination(member: str) -> str:
        return destination_aliases.get(member) or member

    return replace(
        planned,
        root=bundle.root,
        moves=tuple(
            sorted(
                (
                    replace(move, source=actual_source(move.source), dest=actual_destination(move.dest))
                    for move in planned.moves
                ),
                key=lambda move: (move.source, move.dest),
            )
        ),
        edits=tuple(
            sorted(
                (
                    replace(
                        edit,
                        member=actual_source(edit.member),
                        target=actual_source(edit.target),
                        new=_restore_reserved_text(edit.new, source_aliases, destination_aliases),
                    )
                    for edit in planned.edits
                ),
                key=lambda edit: (edit.member, edit.where, edit.line or 0, edit.column or 0, edit.key or ""),
            )
        ),
        refusals=tuple(
            sorted(
                (
                    replace(
                        refusal,
                        path=actual_source(refusal.path),
                        detail=_restore_reserved_text(refusal.detail, source_aliases, destination_aliases),
                    )
                    for refusal in planned.refusals
                ),
                key=lambda refusal: (refusal.path, refusal.kind),
            )
        ),
        unrebased=tuple(
            sorted(
                (replace(entry, member=actual_source(entry.member)) for entry in planned.unrebased),
                key=lambda entry: (entry.member, entry.raw),
            )
        ),
        digests=MappingProxyType(
            dict(sorted((actual_source(member), digest) for member, digest in planned.digests.items()))
        ),
        stranded=stranded(bundle, {**ordinary_mapping, **reserved_mapping}, opaque_members=opaque),
    )


def _prepare_reference_markdown(
    bundle: Bundle,
    parsed_members: frozenset[str],
    opaque: frozenset[str],
) -> tuple[Bundle, tuple[MutationRefusal, ...]]:
    """Make reference Markdown classification independent of the loader lens."""
    concepts = {
        concept_id: document for concept_id, document in bundle.concepts.items() if f"{concept_id}.md" not in opaque
    }
    indexes = {
        directory: document
        for directory, document in bundle.indexes.items()
        if (f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME) not in opaque
    }
    logs = {
        directory: document
        for directory, document in bundle.logs.items()
        if (f"{directory}/{LOG_NAME}" if directory else LOG_NAME) not in opaque
    }
    loaded = {
        *(f"{concept_id}.md" for concept_id in concepts),
        *(f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME for directory in indexes),
        *(f"{directory}/{LOG_NAME}" if directory else LOG_NAME for directory in logs),
    }
    refusals: list[MutationRefusal] = []
    for member in sorted(parsed_members):
        if not member.endswith(".md") or member in loaded or not os.path.lexists(bundle.root / member):
            continue
        try:
            document = Document.load(bundle.root / member)
        except UnicodeDecodeError as exc:
            refusals.append(MutationRefusal(member, "parse-error", f"member is not UTF-8: {exc}"))
        except OSError as exc:
            refusals.append(MutationRefusal(member, "member-read", str(exc)))
        else:
            pure = PurePosixPath(member)
            directory = pure.parent.as_posix()
            directory = "" if directory == "." else directory
            if pure.name == INDEX_NAME:
                indexes[directory] = document
            elif pure.name == LOG_NAME:
                logs[directory] = document
            else:
                concepts[member.removesuffix(".md")] = document
    return (
        replace(
            bundle,
            concepts=MappingProxyType(dict(sorted(concepts.items()))),
            indexes=MappingProxyType(dict(sorted(indexes.items()))),
            logs=MappingProxyType(dict(sorted(logs.items()))),
            ignored=bundle.ignored | opaque,
        ),
        tuple(refusals),
    )


def _empty_plan(
    root: Path,
    operation: Operation,
    path_mapping: Mapping[str, str],
    move_plan: MovePlan | None,
    warnings: Sequence[str],
    refusals: Sequence[MutationRefusal],
) -> WorkMutationPlan:
    return WorkMutationPlan(
        root=root,
        operation=operation,
        path_mapping=MappingProxyType(dict(sorted(path_mapping.items()))),
        move_plan=move_plan,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=tuple(warnings),
        refusals=tuple(sorted(set(refusals), key=lambda refusal: (refusal.path, refusal.kind, refusal.detail))),
        validate_paths=(),
    )


def _non_canonical_destination_refusals(path_mapping: Mapping[str, str]) -> tuple[MutationRefusal, ...]:
    """Refuse a mapping whose destination is not itself a canonical item path.

    Postcondition validation already rejects one, but only after the apply has
    written -- the plan reports `ok` and the caller's dry run says the move is
    safe. A destination the grammar cannot name is a planning fault, so it is
    refused where the spec's mutation contract puts it: before any write.
    """
    return tuple(
        MutationRefusal(source, "non-canonical-destination", f"{destination!r} is not a canonical work-item path")
        for source, destination in sorted(path_mapping.items())
        if parse_item_path(destination) is None
    )


def _plan_path_mutation(
    bundle: Bundle,
    items: Sequence[WorkItem],
    operation: Operation,
    path_mapping: Mapping[str, str],
    *,
    roots: Sequence[str],
    refusals: Sequence[MutationRefusal] = (),
) -> WorkMutationPlan:
    """Materialize one path mapping without applying any filesystem effect."""
    frozen_mapping = MappingProxyType(dict(sorted(path_mapping.items())))
    members = _member_mapping(bundle, frozen_mapping)
    directories, directory_refusals = _directory_mapping(bundle.root, roots, frozen_mapping)
    directory_preconditions, precondition_refusals = _directory_preconditions(
        bundle.root,
        directories,
        frozen_mapping,
    )
    reference_owners = _reference_owners(bundle, items)
    opaque = _opaque_members(bundle, reference_owners)
    warnings = _opaque_warnings(bundle.root, tuple(opaque), tuple(frozen_mapping))
    all_refusals = [*refusals]
    all_refusals.extend(_non_canonical_destination_refusals(frozen_mapping))
    all_refusals.extend(directory_refusals)
    all_refusals.extend(precondition_refusals)
    all_refusals.extend(_symlink_refusals(bundle.root, roots, members))
    all_refusals.extend(_collision_refusals(bundle.root, members))
    all_refusals.extend(_directory_collision_refusals(bundle.root, directories))
    all_refusals.extend(
        MutationRefusal(member, "parse-error", bundle.unreadable[member])
        for member in sorted(members)
        if member in bundle.unreadable and member not in opaque
    )
    planning_bundle, promotion_refusals = _prepare_reference_markdown(
        bundle,
        frozenset(reference_owners),
        opaque,
    )
    all_refusals.extend(promotion_refusals)
    unreadable = frozenset(member for member in members if member in bundle.unreadable)
    planning_bundle = replace(planning_bundle, ignored=planning_bundle.ignored | unreadable)
    reserved_opaque = {
        source: destination
        for source, destination in members.items()
        if source in opaque and PurePosixPath(source).name in {INDEX_NAME, LOG_NAME}
    }
    reserved_registered_candidates = {
        source: destination
        for source, destination in members.items()
        if source in reference_owners and PurePosixPath(source).name in {INDEX_NAME, LOG_NAME}
    }
    parsed_markdown = _parsed_markdown_members(planning_bundle)
    reserved_registered = {
        source: destination
        for source, destination in reserved_registered_candidates.items()
        if source in parsed_markdown
    }
    reserved_members = {**reserved_opaque, **reserved_registered}
    domain_reserved = set(reserved_opaque) | set(reserved_registered_candidates)
    generic_members = {source: destination for source, destination in members.items() if source not in domain_reserved}
    generic = _plan_with_domain_reserved(
        planning_bundle,
        generic_members,
        reserved_members,
        opaque,
    )
    all_refusals.extend(_move_refusals(generic))
    effects = None
    try:
        if generic.ok:
            effects = materialize(planning_bundle, generic)
    except ValueError as exc:
        all_refusals.append(MutationRefusal("", "materialize-error", str(exc)))
    rendered = {} if effects is None else effects.writes
    index_writes, index_deletes, index_members, index_refusals = _index_effects(
        bundle.root,
        items,
        frozen_mapping,
        rendered,
    )
    all_refusals.extend(index_refusals)
    if all_refusals:
        return _empty_plan(bundle.root, operation, frozen_mapping, generic, warnings, all_refusals)
    assert effects is not None

    writes: dict[str, PlannedWrite] = {}
    source_by_destination = {move.dest: move.source for move in generic.moves if not move.is_asset and not move.opaque}
    for member, after in effects.writes.items():
        if member in index_members:
            continue
        source_member = source_by_destination.get(member)
        preimage_member = source_member or member
        try:
            before = (bundle.root / preimage_member).read_bytes()
        except FileNotFoundError:
            before = None
        writes[member] = PlannedWrite(
            member,
            _digest(before) if before is not None else None,
            after,
            source_member if before is not None else None,
        )
    writes.update({write.member: write for write in index_writes})
    deletes = tuple(sorted(set((*effects.deletes, *index_deletes, *directories))))
    write_parents = {
        parent
        for member in (*writes, *(move.dest for move in effects.renames))
        if (parent := PurePosixPath(member).parent.as_posix()) != "."
    }
    mkdirs = tuple(sorted(set(directories.values()) | write_parents))
    directory_preconditions = _add_absent_mkdir_ancestors(
        bundle.root,
        mkdirs,
        {condition.member: condition for condition in directory_preconditions},
    )
    validate = set(frozen_mapping.values())
    for edit in generic.edits:
        validate.update(frozen_mapping.get(owner, owner) for owner in reference_owners.get(edit.member, ()))
        final_member = members.get(edit.member, edit.member)
        if final_member.endswith(".md"):
            concept_path = final_member.removesuffix(".md")
            if parse_item_path(concept_path) is not None:
                validate.add(concept_path)
    return WorkMutationPlan(
        root=bundle.root,
        operation=operation,
        path_mapping=frozen_mapping,
        move_plan=generic,
        moves=effects.renames,
        writes=tuple(sorted(writes.values(), key=lambda write: write.member)),
        deletes=deletes,
        mkdirs=mkdirs,
        warnings=warnings,
        refusals=(),
        validate_paths=tuple(sorted(validate)),
        directory_preconditions=directory_preconditions,
    )


__all__ = [
    "DirectoryPrecondition",
    "MutationRefusal",
    "Operation",
    "PlannedWrite",
    "WorkMutationPlan",
    "directory_manifest_digest",
]
