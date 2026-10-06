"""`run_proposals_read`: the proposal listing `gw wiki proposals` and serve share."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_core.proposals import run_proposal_decide, run_proposals_read
from graph_works_core.workspace.config import load_workspace_config
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


@pytest.mark.parametrize(
    "recorded, second_type, want, refusal_kind",
    [
        (None, False, "Runbook", None),
        (None, True, None, "malformed-proposal"),
        ("Runbook", True, "Runbook", None),
        ("Source", False, None, "type-unavailable"),
        ("Broken", False, None, "type-unavailable"),
        ("Unknown", False, None, "type-unavailable"),
    ],
)
def test_listing_resolves_schema_types_and_preserves_raw_metadata(
    tmp_path, recorded, second_type, want, refusal_kind
) -> None:
    layout = _layout(tmp_path)
    _file(layout)
    schema_dir = load_workspace_config(layout).declarations_dir / "schema"
    schema = {
        "type": "object",
        "x-okf-directory": "runbooks/",
        "x-okf-accept-proposals": True,
        "x-okf-proposal-guidance": {"summary": "s", "question": "q?"},
    }
    (schema_dir / "Runbook.schema.json").write_text(json.dumps(schema), encoding="utf-8", newline="")
    if second_type:
        (schema_dir / "Recovery.schema.json").write_text(json.dumps(schema), encoding="utf-8", newline="")
    (schema_dir / "Broken.schema.json").write_text(
        json.dumps({**schema, "x-okf-proposal-guidance": {}}), encoding="utf-8", newline=""
    )
    path = layout.bundle_dir / MEMBER
    text = path.read_text(encoding="utf-8").replace(f"target: {TARGET}", "target: runbooks/recorded.md")
    text = text.replace("target_type: Explanation\n", "" if recorded is None else f"target_type: {recorded}\n")
    path.write_text(text, encoding="utf-8", newline="")
    [listing] = run_proposals_read(layout)
    assert listing.proposal.target == "runbooks/recorded.md"
    assert listing.proposal.target_type == recorded
    assert listing.type_name == want
    assert (None if listing.type_refusal is None else listing.type_refusal.kind) == refusal_kind
    if listing.type_refusal is not None:
        assert listing.type_refusal.path == MEMBER
        assert listing.type_refusal.detail
