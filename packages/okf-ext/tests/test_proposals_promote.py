"""The one writer, through both of its doors."""

from __future__ import annotations

from datetime import UTC, datetime

import ext_helpers
import pytest
from okf_ext.proposals.model import PageRender
from okf_ext.proposals.plan import list_proposals, plan_create, plan_promote

AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
BY = "agent:ingest"
RENDER = PageRender(type="Concept", body="# Fresh\n\nProse.\n", frontmatter={"title": "Fresh", "description": "d"})


def _by_id(bundle):
    return {p.concept_id: p for p in list_proposals(bundle)}


def test_the_direct_door_writes_a_page_with_no_verified():
    """A directly requested page is human-*requested*, not human-*reviewed*.
    That distinction is what makes `trust_tier()` able to tell three states
    apart rather than two."""
    bundle = ext_helpers.proposed_bundle()
    plan = plan_create(
        bundle, "pages/direct.md", RENDER, by=BY, at=AT, sources=[{"id": "src-a", "resource": "/sources/a.md"}]
    )
    assert plan.ok
    (write,) = plan.writes
    assert write.mode == "create"
    assert write.member == "pages/direct.md"
    assert "type: Concept" in write.text
    assert "generated:" in write.text
    assert "verified:" not in write.text
    assert "# Fresh" in write.text


def test_the_direct_door_refuses_an_occupied_target():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_create(bundle, "pages/existing.md", RENDER, by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["target-exists"]


def test_the_direct_door_refuses_an_escaping_target():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_create(bundle, "../outside.md", RENDER, by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["target-escapes-bundle"]


@pytest.mark.parametrize("key", ["generated", "sources", "verified"])
def test_a_render_supplying_provenance_raises(key):
    """The capability owns the merge, never the render -- and provenance is
    the part of the merge it owns. A caller supplying it would make the
    one-writer invariant a hand-maintained convention."""
    bundle = ext_helpers.proposed_bundle()
    bad = PageRender(type="Concept", body="b", frontmatter={key: "anything"})
    with pytest.raises(ValueError, match=key):
        plan_create(bundle, "pages/direct.md", bad, by=BY, at=AT)


def test_promoting_into_a_missing_target_needs_a_render():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-new"], by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["missing-render"]


def test_a_create_mode_promotion_writes_the_page_then_flips_the_ledger():
    """One plan, and the page is ordered first: `write_all` commits in the
    order given, so a partial failure leaves the ledger honest rather than
    claiming `created` for a page that never landed."""
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-new"], RENDER, by=BY, at=AT)
    assert plan.ok
    page, ledger = plan.writes
    assert page.member == "pages/fresh.md"
    assert page.mode == "create"
    assert ledger.member == "proposals/approved-new.md"
    assert ledger.frontmatter == {"page_status": "created"}


def test_a_promoted_page_is_born_human_reviewed():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-new"], RENDER, by=BY, at=AT)
    assert "verified:" in plan.writes[0].text
    assert "human:psprowls" in plan.writes[0].text


def test_both_doors_produce_the_same_page_but_for_verified():
    """The one-writer invariant, pinned as a test rather than left as a
    convention: two entrypoints, one merge."""
    bundle = ext_helpers.proposed_bundle()
    proposal = _by_id(bundle)["proposals/approved-new"]
    promoted = plan_promote(bundle, proposal, RENDER, by=BY, at=AT).writes[0].text
    direct = plan_create(bundle, proposal.target, RENDER, by=BY, at=AT, sources=proposal.sources).writes[0].text

    def strip_verified(text: str) -> list[str]:
        lines = text.splitlines()
        kept, skipping = [], False
        for line in lines:
            if line.startswith("verified:"):
                skipping = True
                continue
            if skipping and line.startswith(("  ", "- ")):
                continue
            skipping = False
            kept.append(line)
        return kept

    assert strip_verified(promoted) == strip_verified(direct)


def test_an_update_mode_promotion_is_frontmatter_only():
    """The body change an update proposal argues for is the caller's job via
    `generators` at tier 3. This capability merges provenance and stops."""
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-existing"], RENDER, by=BY, at=AT)
    assert plan.ok
    page, ledger = plan.writes
    assert page.member == "pages/existing.md"
    assert page.mode == "update"
    assert page.body is None
    assert [s["id"] for s in page.frontmatter["sources"]] == ["src-a", "src-b"]
    assert page.frontmatter["verified"][-1]["by"] == "human:psprowls"
    assert ledger.frontmatter == {"page_status": "created"}


def test_an_unparseable_target_refuses_rather_than_overwriting_what_it_cannot_read():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-broken-target"], RENDER, by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["unreadable-target"]


@pytest.mark.parametrize("concept_id", ["proposals/live", "proposals/rejected", "proposals/created"])
def test_promoting_anything_but_an_approved_one_refuses(concept_id):
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)[concept_id], RENDER, by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["not-approved"]


def test_promoting_a_malformed_proposal_refuses():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/no-target"], RENDER, by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["malformed-proposal"]


def test_an_early_refusal_reports_the_true_target_mode():
    """`created`'s target genuinely exists, so even the `not-approved` refusal
    -- which fires before the create/update branches -- must report `update`,
    not the stale `create` default."""
    bundle = ext_helpers.proposed_bundle()
    plan = plan_promote(bundle, _by_id(bundle)["proposals/created"], RENDER, by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["not-approved"]
    assert plan.mode == "update"


def test_update_mode_promotion_genuinely_ignores_a_bad_render():
    """The docstring says *render* is ignored once the target exists -- so a
    render that would fail `_check_render` must not even be validated."""
    bundle = ext_helpers.proposed_bundle()
    bad_render = PageRender(type="Concept", body="b", frontmatter={"sources": []})
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-existing"], bad_render, by=BY, at=AT)
    assert plan.ok
