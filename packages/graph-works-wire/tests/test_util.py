"""`gw util` projections: exact keys in their long-standing order."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from graph_works_core.util.commands import (
    LineEndingFinding,
    LineEndingsReport,
    LogAppendResult,
    TokenStamp,
    TokensUpdate,
)
from graph_works_core.util.platform import Capability, PlatformReport, ProbeResult
from graph_works_wire.util import line_endings_payload, log_payload, platform_payload, tokens_payload


def test_log_payload_isoformats_the_day() -> None:
    result = LogAppendResult(
        path=Path("/ws/okf/log.md"),
        day=date(2026, 9, 18),
        op="note",
        title="Hi",
        detail=None,
        entry="**note** Hi",
        written=True,
    )
    assert log_payload(result) == {
        "path": str(Path("/ws/okf/log.md")),
        "day": "2026-09-18",
        "op": "note",
        "title": "Hi",
        "detail": None,
        "entry": "**note** Hi",
        "written": True,
    }


def test_platform_payload_keeps_probes_beside_declarations() -> None:
    report = PlatformReport(
        schema_version=1,
        platform="win32",
        python="3.12.7",
        capabilities=(Capability("dispatch-backend", "workflow-orca", "available", "resolved", ("g",), "m"),),
        probes=(ProbeResult("dispatch-backend", "unavailable", "no orca", agrees_with_declared=False),),
    )
    payload = platform_payload(report)
    assert list(payload) == ["schema_version", "platform", "python", "capabilities", "probes", "unavailable"]
    assert payload["capabilities"][0]["guarantees"] == ["g"]
    assert payload["probes"][0]["agrees_with_declared"] is False
    assert payload["unavailable"] == list(report.unavailable)


def test_tokens_payload_carries_every_bucket_in_full() -> None:
    update = TokensUpdate(updated=(TokenStamp("a", 3),), unchanged=(TokenStamp("b", 4),), skipped=(), dry_run=True)
    assert tokens_payload(update) == {
        "dry_run": True,
        "updated": [{"page": "a", "tokens": 3}],
        "unchanged": [{"page": "b", "tokens": 4}],
        "skipped": [],
    }


def test_line_endings_payload_lists_findings() -> None:
    report = LineEndingsReport(findings=(LineEndingFinding("notes/a.md", 2),), fixed=True)
    assert line_endings_payload(report) == {"fixed": True, "findings": [{"member": "notes/a.md", "crlf_count": 2}]}


def test_read_index_payload_pins_every_field_and_sorted_counts() -> None:
    from graph_works_wire import util
    from samples_util import READ_INDEX_VERIFY

    payload = util.read_index_payload(READ_INDEX_VERIFY)
    assert payload == {
        "mode": "verify",
        "db_path": str(Path("/ws/.gw/cache/read-index.sqlite")),
        "exists": True,
        "enabled": True,
        "backend": "index",
        "backend_reason": None,
        "fingerprint": {"current": "c", "stored": "s"},
        "versions": {"schema": "1", "projection": "1", "okf_io": "0.2.0"},
        "generation": 3,
        "last_reconcile_ns": 2_000_000_000,
        "tables": {"members": 1, "tags": 2},
        "kinds": {"a": 1, "z": 2},
        "unreadable_files": 4,
        "rebuilt_reason": None,
        "drift": ["a.md"],
        "elapsed_s": None,
        "parsed": 0,
        "error": "inspection error",
    }
    assert list(payload["tables"]) == ["members", "tags"]
    assert list(payload["kinds"]) == ["a", "z"]
