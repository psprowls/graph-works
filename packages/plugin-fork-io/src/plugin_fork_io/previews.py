"""Immutable preview evidence with validated byte inventories.

Update candidate directories alone are editable. Their recorded initial digest is
historical evidence; a later accept preview binds their freshly observed bytes.
"""

from __future__ import annotations

import hashlib
import json
import stat
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Literal, Union, cast, get_args, get_origin, get_type_hints

from .machine import Services
from .records import AncestorIdentity, JsonValue, Preview, SourceSpec
from .snapshots import capture, read_snapshot, validate_path


class PreviewError(ValueError):
    """A preview or one of its immutable artifacts failed validation."""


def _encode(value: object) -> JsonValue:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise PreviewError("Unsupported persisted value")


def _decode(value: object, kind: object) -> object:
    origin, args = get_origin(kind), get_args(kind)
    if origin in {Union, types.UnionType}:
        for arm in args:
            try:
                return _decode(value, arm)
            except PreviewError:
                continue
        raise PreviewError("Invalid union field")
    if origin is Literal:
        if value not in args or type(value) not in {type(a) for a in args}:
            raise PreviewError("Invalid literal field")
        return value
    if origin is tuple:
        if not isinstance(value, list):
            raise PreviewError("Expected array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(item, args[0]) for item in value)
        if len(value) != len(args):
            raise PreviewError("Wrong array size")
        return tuple(_decode(item, arm) for item, arm in zip(value, args, strict=True))
    if isinstance(kind, type) and is_dataclass(kind):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(kind)}:
            raise PreviewError("Unknown or missing record fields")
        hints = get_type_hints(kind)
        return kind(**{key: _decode(item, hints[key]) for key, item in value.items()})
    if kind is bytes:
        if not isinstance(value, str):
            raise PreviewError("Expected hexadecimal byte string")
        try:
            return bytes.fromhex(value)
        except ValueError as exc:
            raise PreviewError("Invalid byte string") from exc
    if kind in {str, int, bool, type(None)} and type(value) is kind:
        return value
    raise PreviewError("Wrong field type")


def preview_bytes(preview: Preview) -> bytes:
    payload = _encode(preview)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return json.dumps(
        {"digest": hashlib.sha256(canonical).hexdigest(), "preview": payload}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def ancestors(
    paths: tuple[Path, ...], *, services: Services, replacing: tuple[Path, ...] = ()
) -> tuple[AncestorIdentity, ...]:
    found: dict[str, AncestorIdentity] = {}
    for path in paths:
        for parent in reversed(path.parents):
            try:
                identity = services.filesystem.identity(parent)
            except FileNotFoundError:
                break
            if not stat.S_ISDIR(identity.mode) and parent in replacing:
                break
            if not stat.S_ISDIR(identity.mode):
                raise PreviewError(f"Destination ancestor is not a real directory: {parent}")
            found[str(parent)] = identity
    return tuple(found[key] for key in sorted(found))


def load_preview(state: Path, preview_id: str) -> Preview:
    validate_path(preview_id)
    if "/" in preview_id:
        raise PreviewError("Preview ID must be one path component")
    root = state / "previews" / preview_id
    try:
        data = json.loads((root / "preview.json").read_bytes())
        if not isinstance(data, dict) or set(data) != {"digest", "preview"}:
            raise PreviewError("Invalid preview envelope")
        canonical = json.dumps(data["preview"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != data["digest"]:
            raise PreviewError("Preview digest mismatch")
        preview = cast(Preview, _decode(data["preview"], Preview))
        if preview.schema_version != 1 or preview.id != preview_id or preview.state_root != str(state):
            raise PreviewError("Unknown schema or preview identity mismatch")
        if preview.operation not in {"fork", "adopt", "update", "accept", "rollback", "install"}:
            raise PreviewError("Unsupported preview operation")
        from .adaptations import adaptation_json, decode_adaptation
        from .records import Selection
        from .references import decode_dependency, dependency_json

        encoded_selection = _encode(preview.selection)
        assert isinstance(encoded_selection, dict)
        Selection.from_json(encoded_selection)
        for edit in preview.adaptations:
            decode_adaptation(adaptation_json(edit))
        for dependency in (*preview.dependencies, *preview.candidate_dependencies):
            decode_dependency(dependency_json(dependency))
        for mapping in preview.source_mappings:
            validate_path(mapping.source)
            validate_path(mapping.destination)
        for conflict in preview.conflicts:
            validate_path(conflict.path)
            if conflict.code not in {
                "merge.add-add",
                "merge.modify-delete",
                "merge.binary",
                "merge.text",
                "merge.mode",
                "merge.kind",
            }:
                raise PreviewError("Unknown merge conflict")
            for digest in (conflict.id, conflict.base_hash, conflict.local_hash, conflict.incoming_hash):
                if digest is not None and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
                    raise PreviewError("Invalid conflict input hash")
            if not conflict.resolution:
                raise PreviewError("Conflict requires explicit resolution instructions")
        for owned in preview.owned_paths:
            validate_path(owned)
        if any(
            not Path(path).is_absolute()
            for path in (preview.content_root, preview.state_root, *preview.expected_absences)
        ):
            raise PreviewError("Persisted machine paths must be absolute")
        for target in preview.installation_targets:
            if not Path(target.root).is_absolute() or target.variant_id not in preview.variant_ids:
                raise PreviewError("Invalid installation target identity or root")
            if not target.agents or any(agent not in {"codex", "claude", "pi"} for agent in target.agents):
                raise PreviewError("Invalid installation agents")
            for owned in target.owned_paths:
                validate_path(owned)
        for inventory_root, paths in preview.inventory_scopes:
            if not Path(inventory_root).is_absolute():
                raise PreviewError("Inventory root must be absolute")
            for relative in paths:
                if relative != ".":
                    validate_path(relative)
        if any(not Path(root).is_absolute() for root in preview.lock_stores):
            raise PreviewError("Lock stores must be absolute")
        for name, digest in preview.evidence_files:
            path = Path(name)
            if not path.is_absolute() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise PreviewError("Acceptance review evidence changed")
        for artifact in preview.artifacts:
            validate_path(artifact.path)
            path = root / artifact.path
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.digest:
                raise PreviewError("Immutable staged artifact changed")
        if preview.operation == "update":
            required = {"base.tar.gz", "incoming.tar.gz", "local.tar.gz", "comparisons.json"}
            if not required.issubset({artifact.path for artifact in preview.artifacts}):
                raise PreviewError("Update original input evidence is missing")
            incoming = read_snapshot(root / "incoming.tar.gz")
            if incoming.digest != preview.base_digest or incoming.source != preview.source:
                raise PreviewError("Original incoming identity mismatch")
        if preview.operation == "fork" and read_snapshot(root / "base.tar.gz").digest != preview.base_digest:
            raise PreviewError("Original base digest mismatch")
        candidate = Path(preview.candidate_path)
        if candidate != root / "candidate":
            raise PreviewError("Candidate path mismatch")
        if preview.operation != "update":
            snapshot = capture(SourceSpec(str(candidate), "local"), (), services=Services.local())
            if snapshot.digest != preview.candidate_digest:
                raise PreviewError("Immutable application candidate changed")
        return preview
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, PreviewError):
            raise
        raise PreviewError(f"Cannot load preview: {exc}") from exc
