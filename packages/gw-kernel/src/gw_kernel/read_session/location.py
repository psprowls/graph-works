"""Disposable read-index paths and their canonical workspace fingerprint."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import okf_ext.readindex as readindex

from gw_kernel.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from gw_kernel.workspace.layout import WorkspaceLayout

SESSION_FORMAT = 1
BUSY_TIMEOUT_MS = 5000
ENABLED_KEY = "read_index.enabled"
READ_INDEX_SUBDIR = "read-index"
DB_PREFIX = "bundle"
KEEP_IDENTITIES = 3


def database_path(layout: WorkspaceLayout) -> Path:
    """Return this build's identity-keyed read database path without creating any cache files.

    Builds whose schema, projection, okf-io version or patterns differ get different files, so
    they never reset one another's index.
    """
    key = readindex.identity_key(ignore=CLONE_IGNORE, prune=CLONE_PRUNE, fingerprint=fingerprint(layout))
    return layout.cache_dir / READ_INDEX_SUBDIR / f"{DB_PREFIX}-{key}.db"


def prune_siblings(db_path: Path, keep: int = KEEP_IDENTITIES) -> None:
    """Best effort: drop identity files (and a legacy `bundle.db`) beyond the `keep` newest. Never raises."""
    try:
        candidates = [p for p in db_path.parent.glob(f"{DB_PREFIX}*.db") if p != db_path]
        dated = sorted(((p.stat().st_mtime_ns, p) for p in candidates), reverse=True)
        for _, stale in dated[max(keep - 1, 0) :]:
            for path in sidecar_paths(stale):
                path.unlink(missing_ok=True)
    except OSError:
        return


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


logger = logging.getLogger("gw_kernel.read_session")


def warn_fallback(reason: str, db_path: Path, exc: BaseException | None = None) -> None:
    """Report one backend selection without hiding later pinned-query errors."""
    logger.warning("read index %s; serving the full bundle load (%s)%s", reason, db_path, f": {exc}" if exc else "")
