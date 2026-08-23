"""Duplicate-aware find-by-resource lookup.

Pages are found by ``resource``, never by a guessed path.  A resource is
addressable only when exactly one document claims it: collisions remain fully
visible in ``members_by_resource`` and never acquire a first-in-walk winner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from okf_io import Bundle, Document

from code_wiki_okf.placement import filesystem_member_identity


@dataclass(frozen=True, slots=True)
class ResourceEntry:
    concept_id: str
    document: Document


@dataclass(frozen=True, slots=True)
class ResourceIndex:
    by_resource: Mapping[str, ResourceEntry]
    members_by_resource: Mapping[str, tuple[str, ...]]
    members_by_filesystem_identity: Mapping[str, tuple[str, ...]]
    paths_by_filesystem_identity: Mapping[str, tuple[tuple[str, bool], ...]]

    def get(self, resource: str) -> ResourceEntry | None:
        return self.by_resource.get(resource)

    def member_for(self, resource: str) -> str | None:
        """Return the sole member claiming *resource*, or ``None``.

        ``None`` covers both absence and ambiguity.  Callers that need to
        distinguish them inspect :attr:`members_by_resource` first.
        """
        members = self.members_by_resource.get(resource, ())
        return members[0] if len(members) == 1 else None

    def filesystem_members_for(self, member: str) -> tuple[str, ...]:
        """Return every live member equivalent to *member* on common filesystems."""
        return self.members_by_filesystem_identity.get(filesystem_member_identity(member), ())

    def filesystem_path_conflicts_for(self, member: str) -> tuple[str, ...]:
        """Return live paths that cannot have the required type for *member*.

        Each ancestor of a member must be a directory and the member itself
        must be a file.  The lookup uses the same NFC-plus-casefold policy as
        complete-member collision checks, so this catches paths that only
        collide on common case-insensitive or normalization-preserving
        filesystems.
        """
        parts = PurePosixPath(member).parts
        conflicts: list[str] = []
        for position in range(1, len(parts) + 1):
            component = PurePosixPath(*parts[:position]).as_posix()
            expected_directory = position < len(parts)
            for existing, is_directory in self.paths_by_filesystem_identity.get(
                filesystem_member_identity(component), ()
            ):
                if is_directory != expected_directory:
                    conflicts.append(existing)
        return tuple(sorted(set(conflicts)))


def resource_index(bundle: Bundle) -> ResourceIndex:
    """Walk *bundle* once, indexing every concept with a `resource` value.

    Unique resources populate ``by_resource``.  Duplicate resources populate
    only ``members_by_resource`` so no caller can silently update whichever
    page happened to sort first.
    """
    entries_by_resource: dict[str, list[ResourceEntry]] = {}
    for concept_id, document in bundle.concepts.items():
        resource = document.fm.resource
        if resource is None:
            continue
        entries_by_resource.setdefault(resource, []).append(ResourceEntry(concept_id=concept_id, document=document))

    by_resource = {resource: entries[0] for resource, entries in entries_by_resource.items() if len(entries) == 1}
    members_by_resource = {
        resource: tuple(f"{entry.concept_id}.md" for entry in entries)
        for resource, entries in entries_by_resource.items()
    }
    members_by_filesystem_identity: dict[str, list[str]] = {}
    paths_by_filesystem_identity: dict[str, list[tuple[str, bool]]] = {}
    for path in bundle.root.rglob("*"):
        is_file = path.is_file()
        is_directory = path.is_dir()
        if not is_file and not is_directory:
            continue
        member = path.relative_to(bundle.root).as_posix()
        identity = filesystem_member_identity(member)
        paths_by_filesystem_identity.setdefault(identity, []).append((member, is_directory))
        if is_file:
            members_by_filesystem_identity.setdefault(identity, []).append(member)
    return ResourceIndex(
        by_resource=MappingProxyType(by_resource),
        members_by_resource=MappingProxyType(members_by_resource),
        members_by_filesystem_identity=MappingProxyType(
            {identity: tuple(sorted(members)) for identity, members in members_by_filesystem_identity.items()}
        ),
        paths_by_filesystem_identity=MappingProxyType(
            {identity: tuple(sorted(paths)) for identity, paths in paths_by_filesystem_identity.items()}
        ),
    )
