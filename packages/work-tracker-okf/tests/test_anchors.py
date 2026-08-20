"""Spec-text anchors: the git shas a spec carries about its own reconciliation.

Pure scans. Neither function knows what a repository is, which is what makes
them testable without a temp repo.
"""

from __future__ import annotations

from work_tracker_okf import anchors

_TWO_HEADINGS = """# A spec

**Baseline commit:** `731526ae9dc6151d3849767a023713791594427b`

## Reconciled 2026-08-01 (aaaaaaa..bbbbbbb)

first pass

## Reconciled 2026-08-12 (ccccccc..ddddddd)

second pass
"""


def test_the_last_reconciled_heading_wins_over_earlier_ones():
    assert anchors.last_reconciled_head(_TWO_HEADINGS) == "ddddddd"


def test_the_head_not_the_tail_of_the_range_is_the_anchor():
    text = "## Reconciled 2026-08-12 (de2dd11f..f5279f78)\n"
    assert anchors.last_reconciled_head(text) == "f5279f78"


def test_a_spec_never_reconciled_has_no_head():
    assert anchors.last_reconciled_head("# A spec\n\nno headings here\n") is None


def test_a_malformed_range_is_not_a_reconciled_heading():
    assert anchors.last_reconciled_head("## Reconciled 2026-08-12 (not-a-sha..also-not)\n") is None


def test_the_backticked_baseline_line_is_read():
    assert anchors.baseline_commit(_TWO_HEADINGS) == "731526ae9dc6151d3849767a023713791594427b"


def test_a_narrative_baseline_line_is_deliberately_not_recognized():
    # Not backticked after the bold prefix: the scan declines rather than
    # guessing where the sha ends.
    assert anchors.baseline_commit("**Baseline commit:** 731526ae, roughly\n") is None


def test_a_spec_with_no_baseline_line_has_no_baseline():
    assert anchors.baseline_commit("# A spec\n") is None


def test_both_scans_are_exported():
    assert set(anchors.__all__) == {"baseline_commit", "last_reconciled_head"}
