"""`run_log_read`: the `/gw:log` recipe's semantics as a typed core read."""

from __future__ import annotations

from datetime import date

import pytest
from graph_works_core.util.commands import InvalidLogSection, run_log_read
from graph_works_core.workspace.layout import layout_for

LOG = """# Log

## 2026-09-16

- **scan** first
- **ingest** spec — detail
  continuation paragraph
  - nested bullet

## not-a-date

- **note** orphaned

## 2026-09-17

- plain unlabelled
- query | legacy entry
"""


def _layout(tmp_path, log=LOG):
    layout = layout_for(tmp_path / ".works")
    layout.bundle_dir.mkdir(parents=True)
    if log is not None:
        (layout.bundle_dir / "log.md").write_text(log, encoding="utf-8")
    return layout


def test_entries_are_newest_first_and_outermost_only(tmp_path):
    result = run_log_read(_layout(tmp_path))
    assert result.exists is True
    assert [(e.day.isoformat(), e.op) for e in result.entries] == [
        ("2026-09-17", "query"),
        ("2026-09-17", None),
        ("2026-09-16", "ingest"),
        ("2026-09-16", "scan"),
    ]
    ingest = result.entries[2]
    assert ingest.text.splitlines() == [
        "- **ingest** spec — detail",
        "  continuation paragraph",
        "  - nested bullet",
    ]


def test_invalid_sections_are_reported_and_excluded(tmp_path):
    result = run_log_read(_layout(tmp_path))
    assert [s.heading for s in result.invalid_sections] == ["not-a-date"]
    assert isinstance(result.invalid_sections[0], InvalidLogSection)
    assert all(e.op != "note" for e in result.entries)


def test_op_filter_is_case_insensitive_and_hides_unlabelled(tmp_path):
    result = run_log_read(_layout(tmp_path), op="SCAN")
    assert [e.op for e in result.entries] == ["scan"]


def test_since_is_inclusive_and_filters_before_last(tmp_path):
    layout = _layout(tmp_path)
    assert [e.day for e in run_log_read(layout, since=date(2026, 9, 17)).entries] == [date(2026, 9, 17)] * 2
    assert [e.op for e in run_log_read(layout, op="scan", last=1).entries] == ["scan"]
    assert run_log_read(layout, last=0).entries == ()


def test_negative_last_raises_before_io(tmp_path):
    with pytest.raises(ValueError, match="last"):
        run_log_read(layout_for(tmp_path / "absent"), last=-1)


def test_a_missing_log_reads_as_empty(tmp_path):
    result = run_log_read(_layout(tmp_path, log=None))
    assert result.exists is False and result.entries == () and result.invalid_sections == ()
