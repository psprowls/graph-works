from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


@dataclass(frozen=True)
class Roots:
    content: Path
    state: Path


@dataclass(frozen=True)
class SourceSpec:
    locator: str
    kind: Literal["local", "git"]
    revision: str | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Literal["warn", "error"]
    path: str | None
    line: int | None
    message: str


@dataclass(frozen=True)
class Result:
    operation: str
    variant_id: str | None
    preview_id: str | None
    applied: bool
    allowed: bool
    findings: tuple[Finding, ...]
    affected_paths: tuple[str, ...]
    data: Mapping[str, JsonValue]


@dataclass(frozen=True)
class SourceIdentity:
    kind: Literal["local", "git"]
    locator: str
    resolved_commit: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class SnapshotEntry:
    path: str
    content: bytes
    kind: Literal["file", "symlink", "directory"]
    mode: int

    @property
    def hash(self) -> str:
        from hashlib import sha256

        return sha256(self.content).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    source: SourceIdentity
    entries: tuple[SnapshotEntry, ...]

    @property
    def digest(self) -> str:
        import hashlib
        import json

        inventory = [
            (entry.path, entry.kind, entry.mode, entry.hash) for entry in sorted(self.entries, key=lambda e: e.path)
        ]
        return hashlib.sha256(
            json.dumps(inventory, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        ).hexdigest()


@dataclass(frozen=True)
class Component:
    source: str
    name: str


@dataclass(frozen=True)
class SourceMapping:
    source: str
    destination: str


@dataclass(frozen=True)
class Adaptation:
    kind: Literal["skill-name", "invocation", "resource-path", "materialize-link", "permission"]
    path: str
    start: int
    end: int
    expected: bytes
    replacement: bytes
    purpose: str


@dataclass(frozen=True)
class Dependency:
    kind: Literal["required-skill", "required-resource", "alternative", "optional", "host"]
    target: str
    path: str
    line: int
    start: int
    end: int
    evidence: Literal["markdown", "invocation", "required-statement", "manifest", "user", "agent", "heuristic"]
    required: bool = True
    group: str | None = None
    satisfied: bool = False


@dataclass(frozen=True)
class SourceLink:
    path: str
    action: Literal["materialize"]


@dataclass(frozen=True)
class DiscoveryContext:
    roots: tuple[Path, ...]
    limits: tuple[str, ...] = ()
    state_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Selection:
    skills: tuple[Component, ...]
    resources: tuple[SourceMapping, ...]
    dependencies: tuple[Dependency, ...]
    adaptations: tuple[Adaptation, ...]
    source_links: tuple[SourceLink, ...]

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> Selection:
        from .adaptations import decode_adaptation
        from .references import decode_dependency
        from .snapshots import validate_path
        from .validation import SKILL_NAME, closed_object, object_list, string

        closed_object(value, {"skills", "resources", "dependencies", "adaptations", "source_links"})
        skills: list[Component] = []
        for item in object_list(value["skills"]):
            closed_object(item, {"source", "name"})
            name = string(item["name"])
            if not SKILL_NAME.fullmatch(name) or len(name) > 64:
                raise ValueError("Invalid local skill name")
            skills.append(Component(validate_path(string(item["source"])), name))
        resources: list[SourceMapping] = []
        for item in object_list(value["resources"]):
            closed_object(item, {"source", "destination"})
            resources.append(
                SourceMapping(validate_path(string(item["source"])), validate_path(string(item["destination"])))
            )
        links: list[SourceLink] = []
        for item in object_list(value["source_links"]):
            closed_object(item, {"path", "action"})
            if item["action"] != "materialize":
                raise ValueError("Unsupported source link resolution")
            links.append(SourceLink(validate_path(string(item["path"])), "materialize"))
        return cls(
            tuple(skills),
            tuple(resources),
            tuple(decode_dependency(i) for i in object_list(value["dependencies"])),
            tuple(decode_adaptation(i) for i in object_list(value["adaptations"])),
            tuple(links),
        )


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    kind: Literal["file", "symlink", "directory"]
    mode: int
    hash: str


@dataclass(frozen=True)
class Inventory:
    root: str
    entries: tuple[InventoryEntry, ...]


@dataclass(frozen=True)
class AncestorIdentity:
    path: str
    device: int
    inode: int
    mode: int


@dataclass(frozen=True)
class Artifact:
    path: str
    digest: str


@dataclass(frozen=True)
class FileOperation:
    kind: Literal["write", "mkdir", "link", "delete"]
    destination: str
    artifact: str | None
    mode: int


@dataclass(frozen=True)
class Conflict:
    id: str
    path: str
    code: str
    base_hash: str | None
    local_hash: str | None
    incoming_hash: str | None
    base_mode: int | None
    local_mode: int | None
    incoming_mode: int | None
    base_kind: Literal["file", "symlink", "directory"] | None
    local_kind: Literal["file", "symlink", "directory"] | None
    incoming_kind: Literal["file", "symlink", "directory"] | None
    resolution: str


@dataclass(frozen=True)
class Preview:
    schema_version: int
    id: str
    operation: str
    variant_ids: tuple[str, ...]
    expected_generations: tuple[tuple[str, int | None], ...]
    observed: tuple[Inventory, ...]
    expected_absences: tuple[str, ...]
    ancestors: tuple[AncestorIdentity, ...]
    artifacts: tuple[Artifact, ...]
    candidate_path: str
    candidate_digest: str
    operations: tuple[FileOperation, ...]
    findings: tuple[Finding, ...]
    review_state: Literal["not_requested", "completed_with_findings", "completed_without_findings"]
    allowed: bool
    content_root: str
    state_root: str
    owned_paths: tuple[str, ...]
    selection: Selection
    source: SourceIdentity
    base_digest: str
    adaptations: tuple[Adaptation, ...]
    dependencies: tuple[Dependency, ...]
    intent: tuple[str, ...]
    candidate_dependencies: tuple[Dependency, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    source_mappings: tuple[SourceMapping, ...] = ()
    evidence_files: tuple[tuple[str, str], ...] = ()
    installation_targets: tuple[InstallationTarget, ...] = ()
    inventory_scopes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    discovery_observations: tuple[Inventory, ...] = ()
    owner_observations: tuple[tuple[str, str, int], ...] = ()
    lock_stores: tuple[str, ...] = ()
    link_mode: int = 0o777


@dataclass(frozen=True)
class Ledger:
    schema_version: int
    variant_id: str
    generation: int
    content_root: str | None
    source: SourceIdentity
    origin: Literal["known", "uncertain"]
    components: tuple[Component, ...]
    mappings: tuple[SourceMapping, ...]
    base_digest: str | None
    files: tuple[InventoryEntry, ...]
    adaptations: tuple[Adaptation, ...]
    intent: tuple[str, ...]
    unresolved: tuple[Dependency, ...]
    history: tuple[str, ...]
    dependencies: tuple[Dependency, ...] = ()


@dataclass(frozen=True)
class Change:
    destination: str
    before: SnapshotEntry | None
    after: SnapshotEntry | None


@dataclass(frozen=True)
class Journal:
    schema_version: int
    id: str
    preview_digest: str
    variant_ids: tuple[str, ...]
    phase: Literal["prepared", "applying", "complete", "rolled_back"]
    changes: tuple[Change, ...]
    completed: tuple[int, ...]
    created_directories: tuple[str, ...]
    ancestors: tuple[AncestorIdentity, ...]
    staged_changes: tuple[Change, ...]
    created_identities: tuple[AncestorIdentity, ...]
    operation: str = ""
    lock_stores: tuple[str, ...] = ()
    lock_owners: tuple[tuple[str, str, int], ...] = ()
    recovery_directories: tuple[tuple[str, AncestorIdentity], ...] = ()


@dataclass(frozen=True)
class RecoveryPreview:
    schema_version: int
    id: str
    journal_id: str
    journal_digest: str
    observed: tuple[Change, ...]
    ancestors: tuple[AncestorIdentity, ...]


@dataclass(frozen=True)
class History:
    schema_version: int
    id: str
    variant_id: str
    ledger: Ledger
    tracking: tuple[SnapshotEntry, ...]
    operation: str = "create"
    before_content: tuple[SnapshotEntry, ...] = ()
    before_tracking: tuple[SnapshotEntry, ...] = ()
    reverses: str | None = None
    review: Review | None = None
    resolutions: tuple[Resolution, ...] = ()


@dataclass(frozen=True)
class Resolution:
    conflict_id: str
    path: str
    hash: str | None
    deleted: bool = False


@dataclass(frozen=True)
class Review:
    candidate_digest: str
    findings: tuple[Finding, ...]
    resolutions: tuple[Resolution, ...] = ()
    evidence_path: str | None = None


@dataclass(frozen=True)
class AgentBinding:
    agent: str
    root: str
    scope: Literal["project", "personal"]


@dataclass(frozen=True)
class Binding:
    schema_version: int
    variant_id: str
    content_root: str
    targets: tuple[AgentBinding, ...]


@dataclass(frozen=True)
class InstallationTarget:
    root: str
    variant_id: str
    mode: Literal["shared", "copy"]
    agents: tuple[str, ...]
    owned_paths: tuple[str, ...]
