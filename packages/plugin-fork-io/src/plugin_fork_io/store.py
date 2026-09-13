"""Validated portable ledgers and read-only ownership inventories."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import cast

from .machine import Services
from .previews import PreviewError, _decode, _encode
from .records import Binding, Inventory, InventoryEntry, Ledger, SnapshotEntry
from .snapshots import validate_path


def identifier(value: str) -> str:
    validate_path(value)
    if "/" in value:
        raise PreviewError("Identifier must be one path component")
    return value


def record_bytes(value: object) -> bytes:
    return json.dumps(_encode(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def sealed_bytes(value: object) -> bytes:
    payload = record_bytes(value)
    return json.dumps(
        {"digest": hashlib.sha256(payload).hexdigest(), "record": json.loads(payload)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def unseal(content: bytes) -> object:
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {"digest", "record"}:
        raise PreviewError("Invalid sealed record envelope")
    payload = json.dumps(value["record"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(payload).hexdigest() != value["digest"]:
        raise PreviewError("Persisted record digest mismatch")
    return value["record"]


def load_ledger(state: Path, variant_id: str) -> Ledger:
    path = state / "forks" / identifier(variant_id) / "ledger.json"
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise PreviewError("Tracking records must not traverse links")
    ledger = cast(Ledger, _decode(json.loads(path.read_bytes()), Ledger))
    if ledger.schema_version != 1 or ledger.variant_id != variant_id or ledger.generation < 1:
        raise PreviewError("Unknown ledger schema or invalid identity/generation")
    if ledger.content_root is not None and Path(ledger.content_root).is_absolute():
        raise PreviewError("Portable content_root must be relative to state")
    from .adaptations import adaptation_json, decode_adaptation
    from .references import decode_dependency, dependency_json
    from .validation import SKILL_NAME

    for component in ledger.components:
        validate_path(component.source)
        if not SKILL_NAME.fullmatch(component.name) or len(component.name) > 64:
            raise PreviewError("Invalid component name")
    for adaptation in ledger.adaptations:
        decode_adaptation(adaptation_json(adaptation))
    for dependency in ledger.unresolved:
        decode_dependency(dependency_json(dependency))
    for history_id in ledger.history:
        identifier(history_id)
    for mapping in ledger.mappings:
        validate_path(mapping.source)
        validate_path(mapping.destination)
    for entry in ledger.files:
        validate_path(entry.path)
        if len(entry.hash) != 64 or any(c not in "0123456789abcdef" for c in entry.hash):
            raise PreviewError("Invalid inventory hash")
    if ledger.origin == "known" and ledger.base_digest is None:
        raise PreviewError("Known provenance requires a base digest")
    if ledger.origin == "uncertain" and ledger.base_digest is not None:
        raise PreviewError("Uncertain provenance must not claim a base")
    return ledger


def observe(path: Path, *, services: Services) -> SnapshotEntry | None:
    try:
        mode = services.filesystem.mode(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if stat.S_ISLNK(mode):
        return SnapshotEntry(
            str(path), services.filesystem.readlink(path).encode("utf-8"), "symlink", stat.S_IMODE(mode)
        )
    if stat.S_ISDIR(mode):
        return SnapshotEntry(str(path), b"", "directory", stat.S_IMODE(mode))
    if stat.S_ISREG(mode):
        return SnapshotEntry(str(path), services.filesystem.read_bytes(path), "file", stat.S_IMODE(mode))
    raise PreviewError(f"Unsupported file kind: {path}")


def inventory(root: Path, paths: tuple[str, ...], *, services: Services) -> Inventory:
    entries: dict[str, InventoryEntry] = {}

    def walk(path: Path) -> None:
        entry = observe(path, services=services)
        if entry is None:
            return
        relative = path.relative_to(root).as_posix()
        if relative != ".":
            entries[relative] = InventoryEntry(relative, entry.kind, entry.mode, entry.hash)
        if entry.kind == "directory":
            for child in services.filesystem.children(path):
                walk(child)

    for relative in paths:
        walk(root / relative)
    return Inventory(str(root), tuple(entries[key] for key in sorted(entries)))


def ledgers(state: Path) -> tuple[Ledger, ...]:
    directory = state / "forks"
    if not directory.exists():
        return ()
    return tuple(load_ledger(state, path.name) for path in sorted(directory.iterdir()))


def owned_paths(ledger: Ledger) -> tuple[str, ...]:
    paths = tuple(dict.fromkeys(mapping.destination for mapping in ledger.mappings))
    return tuple(path for path in paths if not any(path.startswith(other + "/") for other in paths if other != path))


def load_binding(state: Path, variant_id: str) -> Binding | None:
    path = state / "bindings" / (identifier(variant_id) + ".json")
    if not path.exists():
        return None
    if any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)):
        raise PreviewError("Machine bindings must not traverse links")
    binding = cast(Binding, _decode(unseal(path.read_bytes()), Binding))
    if binding.schema_version != 1 or binding.variant_id != variant_id or not Path(binding.content_root).is_absolute():
        raise PreviewError("Invalid machine binding identity")
    for target in binding.targets:
        if target.agent not in {"codex", "claude", "pi"} or not Path(target.root).is_absolute():
            raise PreviewError("Invalid machine binding target")
    return binding


def resolve_content_root(state: Path, ledger: Ledger) -> Path:
    binding = load_binding(state, ledger.variant_id)
    if binding is not None:
        return Path(binding.content_root)
    if ledger.content_root is None:
        raise PreviewError("External content requires an explicit local binding")
    return (state / ledger.content_root).resolve()
