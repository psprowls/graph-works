"""Disposable read-index paths and their canonical workspace fingerprint."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from graph_works_core.workspace.layout import WorkspaceLayout

SESSION_FORMAT = 1
BUSY_TIMEOUT_MS = 5000
ENABLED_KEY = "read_index.enabled"
READ_INDEX_SUBDIR = "read-index"
DB_NAME = "bundle.db"


def database_path(layout: WorkspaceLayout) -> Path:
    """Return the read database location without creating any cache files."""
    return layout.cache_dir / READ_INDEX_SUBDIR / DB_NAME


def sidecar_paths(db_path: Path) -> tuple[Path, Path, Path]:
    """Return the database and SQLite WAL/shared-memory sidecars."""
    return db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")


def fingerprint(layout: WorkspaceLayout) -> str:
    """Hash the canonical format, load policy and bundle location payload."""
    try:
        rel = layout.bundle_dir.relative_to(layout.root).as_posix()
    except ValueError:
        rel = layout.bundle_dir.as_posix()
    payload = {"format": SESSION_FORMAT, "ignore": list(CLONE_IGNORE), "prune": list(CLONE_PRUNE), "bundle_dir": rel}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
