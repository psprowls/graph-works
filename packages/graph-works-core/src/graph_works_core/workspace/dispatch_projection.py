"""Validated workspace projection assembly and explicit manifest mutations."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from config_io import PROJECTION_FILENAME, Fingerprint, PlainYamlStore, dotted, set_key, unset_key

from graph_works_core.workspace.dispatch_config import (
    check_dispatch_inputs,
    load_dispatch_config,
    load_prospective_dispatch_config,
    restore_owned_snapshot,
    source_fingerprint,
)
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import CATALOG, resolve_checked_all, workspace_store


def build_dispatch_projection(layout: WorkspaceLayout) -> dict[str, object]:
    """Project configuration and source provenance, never a resolved profile."""
    before = {path: source_fingerprint(path) for path in (layout.manifest_path, layout.local_manifest_path)}
    resolve_checked_all(layout, environ={})
    payload = workspace_store(layout).read_explicit()
    config = load_dispatch_config(layout)
    inputs = {
        name: {
            "path": str(path),
            "exists": fingerprint is not None,
            "sha256": fingerprint.sha256 if fingerprint is not None else None,
        }
        for name, path in (
            ("manifest", layout.manifest_path),
            ("manifest_local", layout.local_manifest_path),
            ("shared", config.shared_path),
            ("local", config.local_path),
        )
        for fingerprint in (config.source_fingerprints[path],)
    }
    base = config.source_fingerprints[layout.manifest_path]
    overlay = config.source_fingerprints[layout.local_manifest_path]
    payload["_meta"] = {
        "source_mtime": base.mtime if base else None,
        "source_sha256": base.sha256 if base else None,
        "overlay_mtime": overlay.mtime if overlay else None,
        "overlay_sha256": overlay.sha256 if overlay else None,
        "dispatch_inputs": inputs,
    }
    payload["dispatch"] = {
        "reference": dotted.get(payload, "workflow.dispatch_rules"),
        "shared_path": str(config.shared_path),
        "local_path": str(config.local_path),
        "attributes": sorted(config.attributes),
        "rules": [
            {"match": dict(rule.match), "fields": dict(rule.fields), "origin": asdict(rule.origin)}
            for rule in config.rules
        ],
    }
    check_dispatch_inputs(before)
    check_dispatch_inputs(config.source_fingerprints)
    return payload


def _check_projection_inputs(payload: dict[str, object]) -> None:
    meta = payload["_meta"]
    assert isinstance(meta, dict)
    inputs = meta["dispatch_inputs"]
    assert isinstance(inputs, dict)
    for item in inputs.values():
        path = Path(item["path"])
        actual = source_fingerprint(path)
        if (actual is not None) != item["exists"] or (actual.sha256 if actual else None) != item["sha256"]:
            raise WorkspaceError(f"{path}: configuration changed before projection publication; run gw config sync")


def write_dispatch_projection(layout: WorkspaceLayout) -> Path:
    payload = build_dispatch_projection(layout)
    target = layout.cache_dir / PROJECTION_FILENAME
    rendered = json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        temporary.chmod(0o644)
        _check_projection_inputs(payload)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


@dataclass
class _ValidatedManifestStore:
    """Validate the prospective pair before config-io's explicit-layer write."""

    layout: WorkspaceLayout
    target: PlainYamlStore
    local: bool
    inputs: Mapping[Path, Fingerprint | None]
    _written: bool = False
    _owned: Fingerprint | None = None

    def read(self) -> dict[str, object]:
        return self.target.read()

    def read_explicit(self) -> dict[str, object]:
        return self.target.read_explicit()

    def snapshot(self) -> bytes | None:
        return self.target.snapshot()

    def restore(self, snapshot: bytes | None) -> None:
        if self._written:
            # config-io may request rollback itself; relinquish ownership so
            # the outer error handler cannot repeat a refused or completed one.
            self._written = False
            restore_owned_snapshot(self.target, snapshot, owned=self._owned)

    def fingerprint(self) -> Fingerprint | None:
        return self.target.fingerprint()

    def write(self, data: Mapping[str, object]) -> None:
        store = workspace_store(self.layout)
        config = load_prospective_dispatch_config(
            self.layout,
            base=store.read_base_explicit() if self.local else data,
            overlay=data if self.local else store.read_overlay_explicit(),
        )
        check_dispatch_inputs(self.inputs)
        check_dispatch_inputs(config.source_fingerprints)
        try:
            self.target.write(data)
        finally:
            self._owned = self.target.fingerprint()
            self._written = True


def mutate_workspace_config(layout: WorkspaceLayout, key: str, value: str | None, *, local: bool) -> None:
    """Apply a validated catalog mutation and refresh; restore on sync failure."""
    store = workspace_store(layout)
    target = store.overlay if local else store.base
    inputs = {path: source_fingerprint(path) for path in (layout.manifest_path, layout.local_manifest_path)}
    checked = _ValidatedManifestStore(layout, target, local, inputs)
    snapshot = target.snapshot()
    try:
        if value is None:
            unset_key(CATALOG, key, store=checked)
        else:
            set_key(CATALOG, key, value, store=checked)
        write_dispatch_projection(layout)
    except Exception:
        checked.restore(snapshot)
        raise
