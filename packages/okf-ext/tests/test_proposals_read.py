"""Reading the ledger: enumeration, identity, malformation, derived mode."""

from __future__ import annotations

import json

import ext_helpers
from okf_ext.proposals.plan import _normalize_target, list_proposals, mode


def test_every_proposal_is_listed_sorted_by_member():
    proposals = list_proposals(ext_helpers.proposed_bundle())
    assert [p.member for p in proposals] == sorted(p.member for p in proposals)
    assert {p.concept_id for p in proposals} == set(ext_helpers.PROPOSED_EXPECTED)


def test_a_filter_narrows_to_one_status_and_never_returns_a_malformed_one():
    bundle = ext_helpers.proposed_bundle()
    approved = list_proposals(bundle, page_status="approved")
    assert {p.concept_id for p in approved} == {
        "proposals/approved-new",
        "proposals/approved-existing",
        "proposals/approved-broken-target",
    }
    assert all(p.malformed is None for p in approved)


def test_a_missing_target_is_malformed_and_never_joins_target_lookup():
    bundle = ext_helpers.proposed_bundle()
    (found,) = [p for p in list_proposals(bundle) if p.concept_id == "proposals/no-target"]
    assert found.target == ""
    assert found.malformed is not None
    assert "target" in found.malformed


def test_an_unknown_page_status_is_malformed_and_keeps_what_was_on_disk():
    bundle = ext_helpers.proposed_bundle()
    (found,) = [p for p in list_proposals(bundle) if p.concept_id == "proposals/bad-status"]
    assert found.page_status is None
    assert found.raw_page_status == "pending"
    assert found.malformed is not None


def test_a_target_that_escapes_the_bundle_is_malformed():
    bundle = ext_helpers.proposed_bundle()
    (found,) = [p for p in list_proposals(bundle) if p.concept_id == "proposals/escaping"]
    assert found.target == ""
    assert found.malformed is not None


def test_mode_derives_from_the_world_and_is_never_stored():
    """Nothing that is not written down can drift. A stored `mode` would be
    the one field in the document capable of contradicting reality."""
    bundle = ext_helpers.proposed_bundle()
    by_id = {p.concept_id: p for p in list_proposals(bundle)}
    assert mode(bundle, by_id["proposals/live"]) == "create"
    assert mode(bundle, by_id["proposals/approved-existing"]) == "update"


def test_provenance_is_plain_data():
    bundle = ext_helpers.proposed_bundle()
    by_id = {p.concept_id: p for p in list_proposals(bundle)}
    live = by_id["proposals/live"]
    assert json.loads(json.dumps(list(live.sources)))[0]["id"] == "src-a"
    approved = by_id["proposals/approved-new"]
    assert json.loads(json.dumps(list(approved.verified)))[0]["by"] == "human:psprowls"


def test_normalize_target_pops_real_segments_and_still_refuses_a_deep_escape():
    """Coverage for ..-pop branches: real segments, dot skip, and over-climb."""
    assert _normalize_target("a/../b") == "b"
    assert _normalize_target("./a/b") == "a/b"
    assert _normalize_target("a/b/../../../x") == ""
