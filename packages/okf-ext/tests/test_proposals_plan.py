"""The two ledger planners: filing (upsert) and deciding (the flip)."""

from __future__ import annotations

from datetime import UTC, datetime

import ext_helpers
import pytest
from okf_ext.proposals.model import Proposal
from okf_ext.proposals.plan import list_proposals, plan_decide, plan_propose
from okf_ext.proposals.render import render_body
from okf_io import load_bundle

AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
BY = "agent:ingest"
NEW_SOURCE = {"id": "src-c", "resource": "/sources/c.md", "title": "C"}


def _by_id(bundle):
    return {p.concept_id: p for p in list_proposals(bundle)}


def test_a_target_with_no_live_proposal_plans_one_create():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_propose(bundle, "pages/brand-new.md", [NEW_SOURCE], title="Brand new", description="why", by=BY, at=AT)
    assert plan.ok
    (write,) = plan.writes
    assert write.mode == "create"
    assert write.member == "proposals/pages-brand-new.md"
    assert "type: Proposal" in write.text
    assert "page_status: proposed" in write.text
    assert "target: pages/brand-new.md" in write.text
    assert render_body(description="why", sources=[NEW_SOURCE]) in write.text


def test_a_live_proposal_merges_and_re_renders_its_body():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_propose(bundle, "pages/live.md", [NEW_SOURCE], title="Live", description="why", by=BY, at=AT)
    assert plan.ok
    (write,) = plan.writes
    assert write.mode == "update"
    assert write.member == "proposals/live.md"
    assert [s["id"] for s in write.frontmatter["sources"]] == ["src-a", "src-c"]
    assert write.body is not None
    assert write.digest is not None


def test_a_colliding_id_on_a_distinct_resource_is_suffixed():
    """Dedup is by `resource` -- that is what identity means for a source --
    so a second source arriving under a taken `id` keeps its resource and
    gives up its id rather than the other way round."""
    bundle = ext_helpers.proposed_bundle()
    clash = {"id": "src-a", "resource": "/sources/b.md", "title": "B under a taken id"}
    plan = plan_propose(bundle, "pages/live.md", [clash], title="Live", description="why", by=BY, at=AT)
    (write,) = plan.writes
    assert [s["id"] for s in write.frontmatter["sources"]] == ["src-a", "src-a-2"]
    assert [s["resource"] for s in write.frontmatter["sources"]] == ["/sources/a.md", "/sources/b.md"]


def test_a_first_filing_with_colliding_ids_in_its_own_sources_is_deduped_too():
    """The create door gets no special exemption from the dedup the merge
    door already enforces. Two distinct sources sharing one caller-supplied
    `id` would otherwise render two footnote citations pointing at the same
    `[^id]` -- under CommonMark's first-definition-wins rule for a shortcut
    reference link, the second bullet's citation would silently resolve to
    the first source's resource, misattributing the claim it was meant to
    cite."""
    bundle = ext_helpers.proposed_bundle()
    first = {"id": "src-x", "resource": "/sources/a.md", "title": "First"}
    second = {"id": "src-x", "resource": "/sources/b.md", "title": "Second"}
    plan = plan_propose(
        bundle, "pages/brand-new.md", [first, second], title="Brand new", description="why", by=BY, at=AT
    )
    (write,) = plan.writes
    assert write.mode == "create"
    assert "id: src-x\n" in write.text
    assert "id: src-x-2\n" in write.text
    assert "resource: /sources/a.md" in write.text
    assert "resource: /sources/b.md" in write.text
    assert "- First[^src-x]" in write.text
    assert "- Second[^src-x-2]" in write.text
    assert "[^src-x]: /sources/a.md" in write.text
    assert "[^src-x-2]: /sources/b.md" in write.text


def test_re_proposing_what_is_already_there_plans_nothing():
    """Idempotence surfaces as an empty plan, not as a boolean -- the call
    `tags`, `tables`, `sections` and `generators` have all already made."""
    bundle = ext_helpers.proposed_bundle()
    same = {"id": "src-a", "resource": "/sources/a.md", "title": "Claude path skill ingest guidance"}
    plan = plan_propose(
        bundle,
        "pages/live.md",
        [same],
        title="Live",
        description="A proposal still open, whose target does not exist.",
        by=BY,
        at=AT,
    )
    assert plan.ok
    assert plan.is_empty


def test_proposing_the_same_sources_with_a_new_description_still_plans_an_update():
    """`changed` and `metadata_changed` are tracked separately -- unchanged
    sources with a changed description still plan a write, they just carry
    `changed=False, metadata_changed=True` rather than an empty plan."""
    bundle = ext_helpers.proposed_bundle()
    same = {"id": "src-a", "resource": "/sources/a.md", "title": "Claude path skill ingest guidance"}
    plan = plan_propose(
        bundle,
        "pages/live.md",
        [same],
        title="Live",
        description="A new spin on the same argument.",
        by=BY,
        at=AT,
    )
    assert plan.ok
    assert not plan.is_empty
    (write,) = plan.writes
    assert write.frontmatter["description"] == "A new spin on the same argument."
    assert [s["id"] for s in write.frontmatter["sources"]] == ["src-a"]
    assert [s["resource"] for s in write.frontmatter["sources"]] == ["/sources/a.md"]


def test_proposing_against_a_decided_proposal_refuses_and_names_it():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_propose(bundle, "pages/rejected.md", [NEW_SOURCE], title="R", description="d", by=BY, at=AT)
    assert not plan.ok
    (refusal,) = plan.refusals
    assert refusal.kind == "already-decided"
    assert "proposals/rejected.md" in refusal.detail


def test_proposing_against_a_malformed_proposal_refuses():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_propose(bundle, "pages/bad.md", [NEW_SOURCE], title="B", description="d", by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["malformed-proposal"]


def test_an_escaping_target_refuses():
    """A proposal may freely *cite* an out-of-bundle resource; it may only ever
    *create* a bundle member."""
    bundle = ext_helpers.proposed_bundle()
    plan = plan_propose(bundle, "../outside.md", [NEW_SOURCE], title="O", description="d", by=BY, at=AT)
    assert [r.kind for r in plan.refusals] == ["target-escapes-bundle"]


def test_a_taken_default_placement_is_disambiguated(tmp_path):
    """Placement is cosmetic -- identity is the target -- so a slug collision
    is worked around rather than refused."""
    copy = ext_helpers.proposed_copy(tmp_path)
    occupied = copy / "proposals" / "pages-live-x.md"
    occupied.write_text("---\ntype: Concept\ntitle: Occupies the default placement\n---\nbody\n", encoding="utf-8")
    bundle = load_bundle(copy)

    plan = plan_propose(bundle, "pages/live-x.md", [NEW_SOURCE], title="X", description="d", by=BY, at=AT)
    assert plan.ok
    assert plan.writes[0].member == "proposals/pages-live-x-2.md"


def test_deciding_flips_the_status_and_appends_verified_without_touching_the_body():
    """After this transition no code path re-renders the body. The ownership
    flip is structural, not a flag."""
    bundle = ext_helpers.proposed_bundle()
    plan = plan_decide(bundle, _by_id(bundle)["proposals/live"], "approved", by="human:psprowls", at=AT)
    assert plan.ok
    (write,) = plan.writes
    assert write.mode == "update"
    assert write.member == "proposals/live.md"
    assert write.frontmatter["page_status"] == "approved"
    assert write.frontmatter["verified"] == [{"by": "human:psprowls", "at": AT.isoformat()}]
    assert write.body is None


def test_deciding_appends_rather_than_replaces_an_existing_verified():
    """`plan_decide` only reads `bundle.root`, so a hand-built `Proposal` with
    a non-empty `verified` can be decided against any bundle -- the fixture's
    only `proposed` proposal (`proposals/live`) has no prior `verified`
    entries, which would make a bare length check pass whether the append is
    additive or a replace."""
    bundle = ext_helpers.proposed_bundle()
    prior = ({"by": "agent:ingest", "at": "2026-08-01T00:00:00+00:00"},)
    proposal = Proposal(
        member="proposals/live.md",
        concept_id="proposals/live",
        target="pages/live.md",
        title="Live",
        description="A proposal still open, whose target does not exist.",
        page_status="proposed",
        raw_page_status="proposed",
        sources=(),
        verified=prior,
    )
    plan = plan_decide(bundle, proposal, "rejected", by="human:psprowls", at=AT)
    (write,) = plan.writes
    assert len(write.frontmatter["verified"]) == len(proposal.verified) + 1
    assert write.frontmatter["verified"][:-1] == [dict(v) for v in proposal.verified]


@pytest.mark.parametrize("concept_id", ["proposals/approved-new", "proposals/rejected", "proposals/created"])
def test_deciding_anything_but_a_proposed_one_refuses(concept_id):
    bundle = ext_helpers.proposed_bundle()
    plan = plan_decide(bundle, _by_id(bundle)[concept_id], "approved", by="human:psprowls", at=AT)
    assert [r.kind for r in plan.refusals] == ["not-proposed"]


def test_deciding_a_malformed_proposal_refuses():
    bundle = ext_helpers.proposed_bundle()
    plan = plan_decide(bundle, _by_id(bundle)["proposals/bad-status"], "approved", by="human:psprowls", at=AT)
    assert [r.kind for r in plan.refusals] == ["malformed-proposal"]


@pytest.mark.parametrize("call", ["propose", "decide"])
def test_a_naive_instant_raises(call):
    bundle = ext_helpers.proposed_bundle()
    naive = datetime(2026, 8, 8, 12, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        if call == "propose":
            plan_propose(bundle, "pages/x.md", [NEW_SOURCE], title="X", description="d", by=BY, at=naive)
        else:
            plan_decide(bundle, _by_id(bundle)["proposals/live"], "approved", by="h:x", at=naive)
