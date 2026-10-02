"""`wiki_lint_payload`: the mechanical-only lint projection with counts."""

from __future__ import annotations

from graph_works_core.lint_drift.lint import LaneReport, LintReport
from graph_works_wire.wiki import wiki_lint_payload
from okf_io.validate import Finding, Report


def _f(code: str, severity: str) -> Finding:
    return Finding(code=code, severity=severity, message="m", spec="s", path="p.md")  # type: ignore[arg-type]


def test_counts_cover_every_lane() -> None:
    report = LintReport(
        mechanical=(
            LaneReport("wiki", Report(findings=(_f("links.broken", "warn"), _f("schemas.invalid", "error")))),
            LaneReport("work", Report(findings=(_f("links.broken", "warn"),))),
        )
    )
    payload = wiki_lint_payload(report)
    assert payload["semantic"] is None
    assert payload["counts"] == {"errors": 1, "warnings": 2, "by_code": {"links.broken": 2, "schemas.invalid": 1}}
