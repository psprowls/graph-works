"""Layered workspace dispatch configuration and stale-safe writes."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from config_io import (
    ConfigEntry,
    Fingerprint,
    PlainYamlStore,
    Resolved,
    StoreValidationError,
    dotted,
    resolve_key,
    unset_key,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from graph_works_core.workspace.dispatch import DispatchRule, parse_rules
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import dispatch_store, workspace_store

_ATTRIBUTES = frozenset({"stage", "variant", "type", "effort", "blast_radius", "has_spec", "has_plan"})
_RETIRED = (
    "workflow.pipeline",
    "workflow.auto_drive.models",
    "workflow.auto_drive.overrides",
    "workflow.auto_drive.permission_mode",
)
_READER = YAML(typ="safe")


@dataclass(frozen=True, slots=True)
class DispatchConfig:
    shared_path: Path
    local_path: Path
    attributes: frozenset[str]
    rules: tuple[DispatchRule, ...]
    source_fingerprints: Mapping[Path, Fingerprint | None]


@dataclass(frozen=True, slots=True)
class DispatchWritePlan:
    layout: WorkspaceLayout
    layer: Literal["shared", "local"]
    path: Path
    shared_path: Path
    local_path: Path
    document: Mapping[str, object]
    source_fingerprints: Mapping[Path, Fingerprint | None]


def _strict_read(path: Path, *, optional: bool = False) -> dict[str, object]:
    if optional and not path.exists():
        return {}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkspaceError(f"{path}: cannot read dispatch configuration: {exc}") from exc
    if not raw.strip():
        raise WorkspaceError(f"{path}: must hold a mapping at the top level, got empty document")
    try:
        loaded = _READER.load(raw)
    except (YAMLError, UnicodeError) as exc:
        raise WorkspaceError(f"{path}: is not valid YAML: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise WorkspaceError(f"{path}: must hold a mapping at the top level, got {type(loaded).__name__}")
    return dict(loaded)


def _retired_key(layer: Mapping[str, object]) -> str | None:
    return next((key for key in _RETIRED if dotted.has(layer, key)), None)


def is_retired_config_key(key: str) -> bool:
    return any(key == prefix or key.startswith(prefix + ".") for prefix in _RETIRED)


def remove_retired_config_key(layout: WorkspaceLayout, key: str, *, local: bool = False) -> Resolved:
    """Remove one explicitly selected retired key without requiring valid dispatch files.

    The temporary catalog is removal-only: it never admits a retired key to
    normal reads, writes, or projections. config-io preserves other YAML data
    and prunes empty parent mappings, allowing successive cleanup operations.
    """
    if not is_retired_config_key(key):
        raise WorkspaceError(f"{key}: not a retired dispatch setting")
    catalog = (ConfigEntry(key=key, type="str", default=None, description="Retired dispatch setting (removal only)."),)
    store = workspace_store(layout)
    unset_key(catalog, key, store=store.overlay if local else store.base)
    return resolve_key(catalog, key, store=store, environ={})


def validate_workspace_dispatch_layers(
    layout: WorkspaceLayout,
    *,
    base: Mapping[str, object],
    overlay: Mapping[str, object],
) -> Path:
    """Validate explicit manifest layers and resolve their dispatch reference.

    This accepts prospective mappings so manifest mutation callers can validate
    removals and reference changes before writing either layer.
    """
    for path, layer in ((layout.manifest_path, base), (layout.local_manifest_path, overlay)):
        retired = _retired_key(layer)
        if retired is not None:
            raise WorkspaceError(
                f"{path}: retired key {retired}; remove it, then initialize or recreate dispatch rules"
            )
    combined = dotted.merge(base, overlay)
    reference = dotted.get(combined, "workflow.dispatch_rules")
    if not isinstance(reference, str) or not reference.strip():
        raise WorkspaceError(
            f"{layout.manifest_path}: workflow.dispatch_rules must be a non-empty path; "
            "remove retired keys, then initialize or recreate dispatch rules"
        )
    expanded = Path(reference).expanduser()
    return expanded if expanded.is_absolute() else layout.root / expanded


def _rules(document: Mapping[str, object], *, source: Path, attributes: frozenset[str]) -> tuple[DispatchRule, ...]:
    if "pipeline" not in document:
        return ()
    pipeline = document["pipeline"]
    if not isinstance(pipeline, Mapping):
        raise WorkspaceError(f"{source}: pipeline must be a mapping")
    if "rules" not in pipeline:
        return ()
    return parse_rules(pipeline["rules"], source=str(source), attributes=attributes)


def _compose(
    shared_path: Path,
    local_path: Path,
    shared: Mapping[str, object],
    local: Mapping[str, object],
    fingerprints: Mapping[Path, Fingerprint | None],
) -> DispatchConfig:
    for source, document in ((shared_path, shared), (local_path, local)):
        if "pipeline" not in document:
            continue
        pipeline = document["pipeline"]
        if not isinstance(pipeline, Mapping):
            raise WorkspaceError(f"{source}: pipeline must be a mapping")
        if "attributes" in pipeline and pipeline["attributes"] is None:
            raise WorkspaceError(f"{source}: attributes must be a list of strings")
    merged = dotted.merge(shared, local)
    merged_pipeline = merged.get("pipeline")
    if merged_pipeline is not None and not isinstance(merged_pipeline, Mapping):
        source = local_path if "pipeline" in local else shared_path
        raise WorkspaceError(f"{source}: pipeline must be a mapping")
    declaration = None if merged_pipeline is None else merged_pipeline.get("attributes")
    local_pipeline = local.get("pipeline")
    declaration_source = (
        local_path if isinstance(local_pipeline, Mapping) and "attributes" in local_pipeline else shared_path
    )
    if declaration is None:
        attributes = _ATTRIBUTES
    elif not isinstance(declaration, list) or not all(isinstance(item, str) for item in declaration):
        raise WorkspaceError(f"{declaration_source}: attributes must be a list of strings")
    else:
        attributes = frozenset(declaration)
    unknown = attributes - _ATTRIBUTES
    if unknown:
        raise WorkspaceError(f"{declaration_source}: unknown declared attributes: {sorted(unknown)}")
    rules = _rules(shared, source=shared_path, attributes=attributes) + _rules(
        local, source=local_path, attributes=attributes
    )
    return DispatchConfig(shared_path, local_path, attributes, rules, MappingProxyType(dict(fingerprints)))


def source_fingerprint(path: Path) -> Fingerprint | None:
    """Capture content identity, including absence, without constructing a store."""
    try:
        raw = path.read_bytes()
        return Fingerprint(path.stat().st_mtime, hashlib.sha256(raw).hexdigest())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkspaceError(f"{path}: cannot read configuration: {exc}") from exc


def check_dispatch_inputs(fingerprints: Mapping[Path, Fingerprint | None]) -> None:
    for path, expected in fingerprints.items():
        if source_fingerprint(path) != expected:
            raise WorkspaceError(f"{path}: configuration changed since reading; re-plan or run gw config sync")


def restore_owned_snapshot(target: PlainYamlStore, snapshot: bytes | None, *, owned: Fingerprint | None) -> None:
    """Restore only while the selected layer still matches our completed write."""
    if target.fingerprint() != owned:
        raise WorkspaceError(f"{target.path}: rollback refused to preserve concurrent configuration changes")
    target.restore(snapshot)


def load_prospective_dispatch_config(
    layout: WorkspaceLayout,
    *,
    base: Mapping[str, object],
    overlay: Mapping[str, object],
    seed: Mapping[str, object] | None = None,
) -> DispatchConfig:
    """Validate a selected pair before publishing prospective manifest layers.

    Only init supplies a seed, and only for an absent shared document.
    Authored documents always pass the strict reader.
    """
    shared_path = validate_workspace_dispatch_layers(layout, base=base, overlay=overlay)
    store = dispatch_store(shared_path)
    fingerprints = {
        path: source_fingerprint(path)
        for path in (layout.manifest_path, layout.local_manifest_path, store.base.path, store.overlay.path)
    }
    shared = seed if seed is not None and fingerprints[store.base.path] is None else _strict_read(store.base.path)
    local = _strict_read(store.overlay.path, optional=True)
    result = _compose(store.base.path, store.overlay.path, shared, local, fingerprints)
    check_dispatch_inputs(fingerprints)
    return result


def load_dispatch_config(layout: WorkspaceLayout) -> DispatchConfig:
    manifest = workspace_store(layout)
    before = {path: source_fingerprint(path) for path in (layout.manifest_path, layout.local_manifest_path)}
    try:
        base = manifest.read_base_explicit()
        overlay = manifest.read_overlay_explicit()
    except StoreValidationError as exc:
        raise WorkspaceError(str(exc)) from exc
    result = load_prospective_dispatch_config(layout, base=base, overlay=overlay)
    check_dispatch_inputs(before)
    return result


def plan_dispatch_write(
    layout: WorkspaceLayout,
    *,
    layer: Literal["shared", "local"],
    document: Mapping[str, object],
) -> DispatchWritePlan:
    current = load_dispatch_config(layout)
    shared = _strict_read(current.shared_path)
    local = _strict_read(current.local_path, optional=True)
    prospective_shared = document if layer == "shared" else shared
    prospective_local = document if layer == "local" else local
    _compose(
        current.shared_path,
        current.local_path,
        prospective_shared,
        prospective_local,
        current.source_fingerprints,
    )
    path = current.shared_path if layer == "shared" else current.local_path
    copied = deepcopy(dict(document))
    return DispatchWritePlan(
        layout,
        layer,
        path,
        current.shared_path,
        current.local_path,
        MappingProxyType(copied),
        current.source_fingerprints,
    )


def apply_dispatch_write(plan: DispatchWritePlan) -> None:
    manifest = workspace_store(plan.layout)
    dispatch = dispatch_store(plan.shared_path)
    fingerprints = {
        plan.layout.manifest_path: manifest.fingerprint(),
        plan.layout.local_manifest_path: manifest.overlay_fingerprint(),
        dispatch.base.path: dispatch.base.fingerprint(),
        dispatch.overlay.path: dispatch.overlay.fingerprint(),
    }
    if fingerprints != dict(plan.source_fingerprints):
        raise WorkspaceError(f"{plan.path}: dispatch configuration changed since planning; re-plan")
    load_dispatch_config(plan.layout)
    target = dispatch.base if plan.layer == "shared" else dispatch.overlay
    snapshot = target.snapshot()
    # Reload and snapshot can race with an author. Keep the planned target and
    # recheck every planned input before taking ownership of any output.
    check_dispatch_inputs(plan.source_fingerprints)
    owned = None
    written = False
    try:
        try:
            target.write(plan.document)
        finally:
            # Capture partial output as well, so an interrupted write can be
            # restored while later post-validation edits remain protected.
            owned = target.fingerprint()
            written = True
        load_dispatch_config(plan.layout)
    except Exception:
        if written:
            restore_owned_snapshot(target, snapshot, owned=owned)
        raise


__all__ = [
    "DispatchConfig",
    "DispatchWritePlan",
    "apply_dispatch_write",
    "check_dispatch_inputs",
    "load_dispatch_config",
    "load_prospective_dispatch_config",
    "plan_dispatch_write",
    "restore_owned_snapshot",
    "source_fingerprint",
    "validate_workspace_dispatch_layers",
]
