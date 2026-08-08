"""Find-by-resource lookup.

Pages are found by `resource`, never by expected path. `resource_index()`
walks a loaded `okf_io.Bundle` once and returns a frozen index; callers doing
find-by-resource use this instead of re-walking the bundle themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from okf_io import Bundle, Document


@dataclass(frozen=True, slots=True)
class ResourceEntry:
    concept_id: str
    document: Document


@dataclass(frozen=True, slots=True)
class ResourceIndex:
    by_resource: Mapping[str, ResourceEntry]
    duplicates: tuple[str, ...]

    def get(self, resource: str) -> ResourceEntry | None:
        return self.by_resource.get(resource)


def resource_index(bundle: Bundle) -> ResourceIndex:
    """Walk *bundle* once, indexing every concept with a `resource` value.

    First-in-bundle-order wins a collision; every colliding resource is
    reported in `.duplicates` (each resource string appears once, regardless
    of how many extra documents claimed it).
    """
    by_resource: dict[str, ResourceEntry] = {}
    duplicates: list[str] = []
    for concept_id, document in bundle.concepts.items():
        resource = document.fm.resource
        if resource is None:
            continue
        if resource in by_resource:
            if resource not in duplicates:
                duplicates.append(resource)
            continue
        by_resource[resource] = ResourceEntry(concept_id=concept_id, document=document)
    return ResourceIndex(by_resource=MappingProxyType(by_resource), duplicates=tuple(duplicates))
