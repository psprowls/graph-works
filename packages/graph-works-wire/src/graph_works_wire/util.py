"""Plain-data projections for `gw util` results."""

from __future__ import annotations

from typing import Any

from graph_works_core.util.commands import LineEndingsReport, LogAppendResult, LogRead, TokensUpdate
from graph_works_core.util.platform import PlatformReport


def log_payload(result: LogAppendResult) -> dict[str, object]:
    """The appended log entry and whether it landed."""
    return {
        "path": str(result.path),
        "day": result.day.isoformat(),
        "op": result.op,
        "title": result.title,
        "detail": result.detail,
        "entry": result.entry,
        "written": result.written,
    }


def log_read_payload(result: LogRead) -> dict[str, object]:
    """`/v1/log`: filtered log entries, newest first. (`log_payload` is the append.)"""
    return {
        "path": str(result.path),
        "exists": result.exists,
        "entries": [
            {"date": entry.day.isoformat(), "line": entry.line, "end": entry.end, "op": entry.op, "text": entry.text}
            for entry in result.entries
        ],
        "invalid_sections": [{"line": section.line, "heading": section.heading} for section in result.invalid_sections],
    }


def platform_payload(report: PlatformReport) -> dict[str, Any]:
    """The whole platform report; each probe rendered beside its declaration."""
    return {
        "schema_version": report.schema_version,
        "platform": report.platform,
        "python": report.python,
        "capabilities": [
            {
                "name": capability.name,
                "value": capability.value,
                "status": capability.status,
                "detail": capability.detail,
                "guarantees": list(capability.guarantees),
                "provider": capability.provider,
            }
            for capability in report.capabilities
        ],
        "probes": [
            {
                "capability": probe.capability,
                "status": probe.status,
                "detail": probe.detail,
                "agrees_with_declared": probe.agrees_with_declared,
            }
            for probe in report.probes
        ],
        "unavailable": list(report.unavailable),
    }


def tokens_payload(update: TokensUpdate) -> dict[str, Any]:
    """Every bucket in full (the human view caps them; JSON never does)."""
    return {
        "dry_run": update.dry_run,
        "updated": [{"page": stamp.page, "tokens": stamp.tokens} for stamp in update.updated],
        "unchanged": [{"page": stamp.page, "tokens": stamp.tokens} for stamp in update.unchanged],
        "skipped": [{"page": page.page, "reason": page.reason} for page in update.skipped],
    }


def line_endings_payload(report: LineEndingsReport) -> dict[str, Any]:
    """Every CRLF finding, and whether this run fixed them."""
    return {
        "fixed": report.fixed,
        "findings": [{"member": finding.member, "crlf_count": finding.crlf_count} for finding in report.findings],
    }
