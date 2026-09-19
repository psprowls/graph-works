"""Contract-test inputs for `graph_works_wire.util`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace as ns

from graph_works_core.util.commands import LogEntryRead, LogRead
from graph_works_core.util.platform import Capability, PlatformReport, ProbeResult
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
