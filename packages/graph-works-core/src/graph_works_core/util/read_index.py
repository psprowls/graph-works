"""Inspect a disposable index without disk writes, or explicitly maintain it."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import okf_io
from okf_ext import readindex
from okf_ext.readindex.store import PROJECTION_VERSION, SCHEMA_VERSION

from graph_works_core.read_session import location
from graph_works_core.read_session.model import Backend, FallbackReason
from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_bool

ReadIndexMode = Literal["inspect", "verify", "rebuild"]
TABLES = ("members", "tags", "links", "headings", "unreadable_dirs", "pruned", "collisions")


class ReadIndexBusy(OSError):
    """Explicit index maintenance could not acquire the producer's lock."""


class ReadIndexUnavailable(OSError):
    """Explicit index maintenance could not open or reconcile the index."""


@dataclass(frozen=True, slots=True)
class ReadIndexReport:
    """Cache diagnostics and maintenance results, independent of interfaces."""

    mode: ReadIndexMode
    db_path: Path
    exists: bool
    enabled: bool
    backend: Backend
    backend_reason: FallbackReason | None
    current_fingerprint: str
    stored_fingerprint: str | None
    schema_version: str | None
    projection_version: str | None
    okf_io_version: str | None
    generation: int | None
    last_reconcile_ns: int | None
    tables: Mapping[str, int]
    kinds: Mapping[str, int]
    unreadable_files: int
    rebuilt_reason: str | None
    drift: tuple[str, ...] | None
    elapsed_s: float | None
    parsed: int | None
    error: str | None


def _snapshot(db_path: Path) -> bytes:
    """Capture a checkpointed main file, refusing detectable concurrent changes.

    Opening a WAL database even with mode=ro can create sidecars. Inspect only
    deserializes a stable main-file copy; it never opens SQLite on disk and
    never uses immutable=1, which could silently omit live WAL transactions.
    """
    wal = location.sidecar_paths(db_path)[1]
    before = db_path.stat()
    if wal.exists():
        raise ValueError("WAL journal present; cannot inspect an uncheckpointed index")
    image = db_path.read_bytes()
    after = db_path.stat()
    if wal.exists():
        raise ValueError("WAL journal appeared during index snapshot capture")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or len(image) != after.st_size:
        raise ValueError("Index file changed during snapshot capture")
    # A deserialized WAL header still expects a WAL file. Only the memory copy
    # switches to rollback format; the checkpointed disk image stays untouched.
    if image[:16] == b"SQLite format 3\x00" and image[18:20] == b"\x02\x02":
        image = image[:18] + b"\x01\x01" + image[20:]
    return image


def _stats(report: ReadIndexReport) -> ReadIndexReport:
    try:
        report.db_path.stat()
    except FileNotFoundError:
        return report
    except OSError as exc:
        return replace(report, exists=True, error=str(exc))
    report = replace(report, exists=True)
    connection: sqlite3.Connection | None = None
    try:
        image = _snapshot(report.db_path)
        connection = sqlite3.connect(":memory:")
        connection.deserialize(image)
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        for key, expected in (
            ("schema_version", str(SCHEMA_VERSION)),
            ("projection_version", str(PROJECTION_VERSION)),
            ("okf_io_version", okf_io.__version__),
        ):
            if meta.get(key) != expected:
                raise ValueError(f"Missing or unsupported {key}: {meta.get(key)!r}")
        numbers = {}
        for key in ("generation", "last_reconcile_ns"):
            value = meta.get(key)
            if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
                raise ValueError(f"Missing or malformed numeric {key}: {value!r}")
            numbers[key] = int(value)
        tables = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}
        kinds = dict(connection.execute("SELECT kind, count(*) FROM members WHERE unreadable IS NULL GROUP BY kind"))
        unreadable = connection.execute("SELECT count(*) FROM members WHERE unreadable IS NOT NULL").fetchone()[0]
        return replace(
            report,
            stored_fingerprint=meta.get("fingerprint"),
            schema_version=meta["schema_version"],
            projection_version=meta["projection_version"],
            okf_io_version=meta["okf_io_version"],
            generation=numbers["generation"],
            last_reconcile_ns=numbers["last_reconcile_ns"],
            tables=MappingProxyType(tables),
            kinds=MappingProxyType(kinds),
            unreadable_files=unreadable,
        )
    except (sqlite3.Error, OSError, ValueError) as exc:
        return replace(report, error=str(exc))
    finally:
        if connection is not None:
            connection.close()


def run_read_index(
    layout: WorkspaceLayout,
    *,
    verify: bool = False,
    rebuild: bool = False,
    clock: Callable[[], float] = time.perf_counter,
) -> ReadIndexReport:
    """Inspect without creating files; verify/rebuild regardless of enablement."""
    if verify and rebuild:
        raise ValueError("--verify and --rebuild cannot be combined")
    mode: ReadIndexMode = "rebuild" if rebuild else "verify" if verify else "inspect"
    enabled = checked_bool(layout, location.ENABLED_KEY)
    db_path = location.database_path(layout)
    fingerprint = location.fingerprint(layout)
    report = ReadIndexReport(
        mode=mode,
        db_path=db_path,
        exists=False,
        enabled=enabled,
        backend="index" if enabled else "bundle",
        backend_reason=None if enabled else "disabled",
        current_fingerprint=fingerprint,
        stored_fingerprint=None,
        schema_version=None,
        projection_version=None,
        okf_io_version=None,
        generation=None,
        last_reconcile_ns=None,
        tables=MappingProxyType({}),
        kinds=MappingProxyType({}),
        unreadable_files=0,
        rebuilt_reason=None,
        drift=None,
        elapsed_s=None,
        parsed=None,
        error=None,
    )
    if mode == "inspect":
        return _stats(report)
    try:
        if rebuild:
            for path in location.sidecar_paths(db_path):
                path.unlink(missing_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        start = clock() if rebuild else None
        with readindex.open_index(
            db_path,
            layout.bundle_dir,
            ignore=CLONE_IGNORE,
            prune=CLONE_PRUNE,
            fingerprint=fingerprint,
            busy_timeout_ms=location.BUSY_TIMEOUT_MS,
        ) as index:
            result = readindex.reconcile(index)
            drift = readindex.verify(index) if verify else None
            report = replace(report, parsed=result.parsed, drift=drift, rebuilt_reason=index.rebuilt_reason)
    except readindex.IndexBusy as exc:
        raise ReadIndexBusy(str(exc)) from exc
    except readindex.IndexUnavailable as exc:
        raise ReadIndexUnavailable(str(exc)) from exc
    report = _stats(report)
    return replace(report, elapsed_s=clock() - start) if start is not None else report
