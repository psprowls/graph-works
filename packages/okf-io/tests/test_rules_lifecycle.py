from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from helpers import write
from okf_io import bundle
from okf_io.validate import validate


def report_for(tmp_path: Path, frontmatter: str, *, today: date = date(2026, 8, 3)):
    write(tmp_path / "a.md", f"---\ntype: Metric\ntitle: T\ndescription: D\n{frontmatter}---\n\n# Definition\n")
    return validate(bundle.load(tmp_path), today=today)


@pytest.mark.parametrize("value", ["draft", "stable", "deprecated"])
def test_the_three_known_statuses_are_silent(tmp_path, value):
    assert report_for(tmp_path, f"status: {value}\n").by_code("lifecycle.status-unknown") == ()


def test_an_absent_status_is_silent(tmp_path):
    """§5.4 defaults it to stable; only an authored value can be wrong."""
    assert report_for(tmp_path, "").by_code("lifecycle.status-unknown") == ()


@pytest.mark.parametrize("value", ["archived", '""'])
def test_an_unknown_status_is_an_error(tmp_path, value):
    finding = report_for(tmp_path, f"status: {value}\n").by_code("lifecycle.status-unknown")[0]
    assert finding.severity == "error"


@pytest.mark.parametrize("value", ["soonish", '"2026-12"', '"31/12/2026"'])
def test_a_malformed_stale_after_is_an_error(tmp_path, value):
    report = report_for(tmp_path, f"stale_after: {value}\n")
    assert report.by_code("lifecycle.stale-after-malformed")[0].severity == "error"


def test_a_well_formed_stale_after_is_silent(tmp_path):
    report = report_for(tmp_path, "stale_after: 2026-12-31\n", today=date(2026, 8, 3))
    assert report.by_code("lifecycle.stale-after-malformed") == ()
    assert report.by_code("lifecycle.stale") == ()


def test_staleness_is_a_function_of_the_injected_clock(tmp_path):
    """The boundary, in both directions, with no system clock anywhere."""
    frontmatter = "stale_after: 2026-12-31\n"
    assert report_for(tmp_path, frontmatter, today=date(2026, 12, 30)).by_code("lifecycle.stale") == ()
    on_the_day = report_for(tmp_path, frontmatter, today=date(2026, 12, 31))
    assert on_the_day.by_code("lifecycle.stale")[0].severity == "warn"
    assert report_for(tmp_path, frontmatter, today=date(2027, 1, 1)).by_code("lifecycle.stale") != ()


def test_a_fully_uncoercible_stale_after_never_reports_stale(tmp_path):
    """Value that fully fails coercion produces malformed, no stale."""
    report = report_for(tmp_path, "stale_after: soonish\n", today=date(2030, 1, 1))
    assert report.by_code("lifecycle.stale-after-malformed") != ()
    assert report.by_code("lifecycle.stale") == ()


def test_a_date_prefixed_stale_after_never_also_reports_stale(tmp_path):
    """Value that coerces via truncation but is not YYYY-MM-DD produces malformed, no stale.

    A value like "2026-01-01T00:00:00Z" coerces because _as_date takes [:10], but our
    regex correctly flags it as not YYYY-MM-DD. Both findings must not fire.
    """
    report = report_for(tmp_path, 'stale_after: "2026-01-01T00:00:00Z"\n', today=date(2026, 6, 1))
    assert report.by_code("lifecycle.stale-after-malformed") != ()
    assert report.by_code("lifecycle.stale") == ()
