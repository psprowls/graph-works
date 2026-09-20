"""`run_proposals_read`: the proposal listing `gw wiki proposals` and serve share."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.proposals import run_proposal_decide, run_proposals_read
from test_proposals_commands import AT, MEMBER, TARGET, _file, _layout


def test_a_fresh_workspace_has_no_open_proposals(tmp_path: Path) -> None:
    assert run_proposals_read(_layout(tmp_path)) == ()


def test_the_default_is_the_open_proposals_with_create_mode(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _file(layout)

    [listing] = run_proposals_read(layout)

    assert listing.proposal.member == MEMBER
    assert listing.proposal.page_status == "proposed"
    assert listing.mode == "create"


def test_mode_is_update_when_the_target_exists(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _file(layout)
    target = layout.bundle_dir / TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\ntype: Explanation\ntitle: Typed CLI\n---\n\nBody.\n", encoding="utf-8")

    [listing] = run_proposals_read(layout)

    assert listing.mode == "update"


def test_page_status_selects_other_dispositions(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _file(layout)
    decided = run_proposal_decide(layout, TARGET, "approved", by="human", at=AT, dry_run=False)
    assert decided.ok

    assert run_proposals_read(layout) == ()
    assert [listing.proposal.page_status for listing in run_proposals_read(layout, "approved")] == ["approved"]
    assert run_proposals_read(layout, "rejected") == ()


def test_an_unknown_page_status_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="page_status 'maybe'"):
        run_proposals_read(_layout(tmp_path), "maybe")
