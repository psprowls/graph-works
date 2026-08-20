"""`run_log`: the op/title/detail wrapper `gw util log` routes to.

One raise, and it fires before any I/O; everything else is reported as data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core.util.commands import VALID_OPS, LogAppendResult, run_log
from graph_works_core.workspace.layout import layout_for

TODAY = date(2026, 8, 19)

LOG = """# Log

## 2026-08-18

- **note** yesterday
"""


def _layout(tmp_path: Path, log: str | None = LOG):
    layout = layout_for(tmp_path / ".works")
    layout.bundle_dir.mkdir(parents=True)
    (layout.bundle_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    if log is not None:
        (layout.bundle_dir / "log.md").write_text(log, encoding="utf-8")
    return layout


def test_an_entry_lands_as_one_section_nine_bullet(tmp_path):
    layout = _layout(tmp_path)

    result = run_log(layout, "create", "Core backing for the util commands", today=TODAY)

    assert result == LogAppendResult(
        path=layout.bundle_dir / "log.md",
        day=TODAY,
        op="create",
        title="Core backing for the util commands",
        detail=None,
        entry="**create** Core backing for the util commands",
        written=True,
    )
    text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert "## 2026-08-19" in text
    assert "- **create** Core backing for the util commands" in text


def test_a_detail_is_appended_after_an_em_dash(tmp_path):
    layout = _layout(tmp_path)

    result = run_log(layout, "note", "Wave 2 kickoff", "three children filed", today=TODAY)

    assert result.entry == "**note** Wave 2 kickoff — three children filed"


@pytest.mark.parametrize("detail", [None, "", "   "])
def test_a_missing_or_blank_detail_omits_the_tail_entirely(tmp_path, detail):
    layout = _layout(tmp_path)

    result = run_log(layout, "note", "Wave 2 kickoff", detail, today=TODAY)

    assert result.entry == "**note** Wave 2 kickoff"


def test_an_unknown_op_raises_before_any_file_is_opened(tmp_path):
    layout = _layout(tmp_path)
    before = (layout.bundle_dir / "log.md").read_bytes()

    with pytest.raises(ValueError, match="unknown op"):
        run_log(layout, "frobnicate", "nope", today=TODAY)

    assert (layout.bundle_dir / "log.md").read_bytes() == before


def test_every_legacy_op_is_still_accepted(tmp_path):
    assert frozenset({"create", "delete", "ingest", "lint", "note", "query", "scan", "update"}) == VALID_OPS


def test_a_missing_log_reports_a_refusal_rather_than_raising(tmp_path):
    layout = _layout(tmp_path, log=None)

    result = run_log(layout, "note", "nothing to record", today=TODAY)

    assert result.written is False
    assert result.entry == "**note** nothing to record"


def test_an_unparseable_log_reports_a_refusal(tmp_path):
    layout = _layout(tmp_path, log="---\nnot: [closed\n")

    assert run_log(layout, "note", "nope", today=TODAY).written is False


def test_an_out_of_order_log_reports_a_refusal(tmp_path):
    layout = _layout(tmp_path, log="# Log\n\n## 2026-08-10\n\n- old\n\n## 2026-08-20\n\n- newer\n")

    assert run_log(layout, "note", "nope", today=TODAY).written is False


def test_today_is_required(tmp_path):
    """okf-io never reads the clock, and neither does the band above it."""
    layout = _layout(tmp_path)

    with pytest.raises(TypeError):
        run_log(layout, "note", "no date")
