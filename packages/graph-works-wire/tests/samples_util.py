"""Contract-test inputs for `graph_works_wire.util`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace as ns

from graph_works_core.util.commands import LogEntryRead, LogRead
from graph_works_core.util.platform import Capability, PlatformReport, ProbeResult
from graph_works_core.util.read_index import ReadIndexReport
from graph_works_wire import util

_REPORT = PlatformReport(
    schema_version=1,
    platform="linux",
    python="3.12.7",
    capabilities=(Capability("durability", "posix", "available", "d", ("g",), "m"),),
    probes=(ProbeResult("durability", "available", "ok", agrees_with_declared=True),),
)

UTIL: dict[str, tuple[Callable[[], object], ...]] = {
    "util.log_read_payload": (
        lambda: util.log_read_payload(
            LogRead(Path("/ws/okf/log.md"), True, (LogEntryRead(date(2026, 9, 18), 3, 3, "scan", "scan"),), ())
        ),
        lambda: util.log_read_payload(LogRead(Path("/ws/okf/log.md"), False, (), ())),
    ),
    "util.log_payload": (
        lambda: util.log_payload(
            ns(
                path=Path("/ws/okf/log.md"),
                day=date(2026, 9, 18),
                op="note",
                title="t",
                detail="d",
                entry="e",
                written=False,
            )
        ),
    ),
    "util.platform_payload": (lambda: util.platform_payload(_REPORT),),
    "util.tokens_payload": (
        lambda: util.tokens_payload(
            ns(dry_run=False, updated=(ns(page="a", tokens=1),), unchanged=(), skipped=(ns(page="b", reason="r"),))
        ),
    ),
    "util.line_endings_payload": (
        lambda: util.line_endings_payload(ns(fixed=False, findings=(ns(member="a.md", crlf_count=1),))),
    ),
}


READ_INDEX_MISSING = ReadIndexReport(
    mode="inspect",
    db_path=Path("/ws/.gw/cache/read-index.sqlite"),
    exists=False,
    enabled=False,
    backend="bundle",
    backend_reason="disabled",
    current_fingerprint="c",
    stored_fingerprint=None,
    schema_version=None,
    projection_version=None,
    okf_io_version=None,
    generation=None,
    last_reconcile_ns=None,
    tables={},
    kinds={},
    unreadable_files=0,
    rebuilt_reason=None,
    drift=None,
    elapsed_s=None,
    parsed=None,
    error=None,
)
READ_INDEX_REBUILD = ReadIndexReport(
    mode="rebuild",
    db_path=Path("/ws/.gw/cache/read-index.sqlite"),
    exists=True,
    enabled=True,
    backend="index",
    backend_reason=None,
    current_fingerprint="c",
    stored_fingerprint="c",
    schema_version="1",
    projection_version="1",
    okf_io_version="0.2.0",
    generation=2,
    last_reconcile_ns=1_000_000_000,
    tables={"tags": 2, "members": 1},
    kinds={"file": 1},
    unreadable_files=0,
    rebuilt_reason="missing",
    drift=None,
    elapsed_s=0.5,
    parsed=1,
    error=None,
)
READ_INDEX_VERIFY = ReadIndexReport(
    mode="verify",
    db_path=Path("/ws/.gw/cache/read-index.sqlite"),
    exists=True,
    enabled=True,
    backend="index",
    backend_reason=None,
    current_fingerprint="c",
    stored_fingerprint="s",
    schema_version="1",
    projection_version="1",
    okf_io_version="0.2.0",
    generation=3,
    last_reconcile_ns=2_000_000_000,
    tables={"tags": 2, "members": 1},
    kinds={"z": 2, "a": 1},
    unreadable_files=4,
    rebuilt_reason=None,
    drift=("a.md",),
    elapsed_s=None,
    parsed=0,
    error="inspection error",
)
UTIL["util.read_index_payload"] = (
    lambda: util.read_index_payload(READ_INDEX_MISSING),
    lambda: util.read_index_payload(READ_INDEX_REBUILD),
    lambda: util.read_index_payload(READ_INDEX_VERIFY),
)
