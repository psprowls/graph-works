"""Spec §6's acceptance properties, over the real corpus, end to end."""

from __future__ import annotations

from datetime import UTC, date, datetime

import ext_helpers
import pytest
from okf_ext import proposals
from okf_io import Document, load_bundle, validate

AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
TODAY = date(2026, 8, 10)
#: `process:` rather than `agent:` -- OKF §7's actor convention recognizes
#: `human:`, `process:`, and `<producer>/<version>`, not `agent:`. Using a
#: conformant value here is what lets `test_a_full_cycle_costs_no_finding`
#: assert zero `trust.actor-convention` findings on what this cycle writes.
AGENT = "process:ingest"
HUMAN = "human:psprowls"
#: Cites `src-a` by footnote so a promoted page carrying that source id is
#: never `provenance.source-uncited` -- every fixture proposal this file
#: promotes with `RENDER` carries a `src-a` source.
RENDER = proposals.PageRender(
    type="Concept",
    body="# Fresh\n\nProse.[^src-a]\n\n[^src-a]: /sources/a.md\n",
    frontmatter={"title": "Fresh", "description": "d"},
)


def _bundle(tmp_path):
    return load_bundle(ext_helpers.proposed_copy(tmp_path))


def _by_id(bundle):
    return {p.concept_id: p for p in proposals.list_proposals(bundle)}


def test_the_whole_cycle_round_trips(tmp_path):
    """propose -> merge -> decide -> promote, reloading between each. Every
    step asserts what landed rather than what was planned."""
    root = ext_helpers.proposed_copy(tmp_path)

    bundle = load_bundle(root)
    filed = proposals.plan_propose(
        bundle,
        "pages/cycle.md",
        [{"id": "src-a", "resource": "/sources/a.md", "title": "A"}],
        title="Cycle",
        description="why",
        by=AGENT,
        at=AT,
    )
    assert proposals.apply(bundle, filed).ok
    member = filed.writes[0].member

    bundle = load_bundle(root)
    merged = proposals.plan_propose(
        bundle,
        "pages/cycle.md",
        [{"id": "src-b", "resource": "/sources/b.md", "title": "B"}],
        title="Cycle",
        description="why",
        by=AGENT,
        at=AT,
    )
    assert proposals.apply(bundle, merged).ok
    document = Document.load(root / member)
    assert [s.id for s in document.fm.sources] == ["src-a", "src-b"]
    assert (
        proposals.render_body(description="why", sources=[dict(s) for s in document.fm_data()["sources"]])
        == document.body
    )

    bundle = load_bundle(root)
    proposal = next(p for p in proposals.list_proposals(bundle) if p.member == member)
    body_before = Document.load(root / member).body
    assert proposals.apply(bundle, proposals.plan_decide(bundle, proposal, "approved", by=HUMAN, at=LATER)).ok
    assert Document.load(root / member).body == body_before

    bundle = load_bundle(root)
    proposal = next(p for p in proposals.list_proposals(bundle) if p.member == member)
    assert proposals.apply(bundle, proposals.plan_promote(bundle, proposal, RENDER, by=AGENT, at=LATER)).ok

    page = Document.load(root / "pages/cycle.md")
    assert page.parse_error is None
    assert [s.id for s in page.fm.sources] == ["src-a", "src-b"]
    assert page.fm.verified[0].by is not None and page.fm.verified[0].by.raw == HUMAN
    assert dict(Document.load(root / member).fm.extra)["page_status"] == "created"


def test_identical_input_renders_identical_bytes(tmp_path):
    a = _bundle(tmp_path / "a")
    b = _bundle(tmp_path / "b")
    args = dict(title="Same", description="why", by=AGENT, at=AT)
    sources = [{"id": "src-a", "resource": "/sources/a.md", "title": "A"}]
    assert (
        proposals.plan_propose(a, "pages/same.md", sources, **args).writes[0].text
        == proposals.plan_propose(b, "pages/same.md", sources, **args).writes[0].text
    )


def test_every_member_the_plan_did_not_name_is_untouched(tmp_path):
    """Byte fidelity, the property okf-io makes and every writer above it
    must not undo."""
    root = ext_helpers.proposed_copy(tmp_path)
    before = ext_helpers.snapshot(root)
    bundle = load_bundle(root)
    plan = proposals.plan_decide(bundle, _by_id(bundle)["proposals/live"], "approved", by=HUMAN, at=AT)
    assert proposals.apply(bundle, plan).ok
    after = ext_helpers.snapshot(root)
    changed = {name for name in before if before[name] != after.get(name)}
    assert changed == {"proposals/live.md"}
    assert set(after) - set(before) == set()


def test_after_the_flip_nothing_re_renders_the_body(tmp_path):
    root = ext_helpers.proposed_copy(tmp_path)
    bundle = load_bundle(root)
    assert proposals.apply(
        bundle, proposals.plan_decide(bundle, _by_id(bundle)["proposals/live"], "approved", by=HUMAN, at=AT)
    ).ok

    bundle = load_bundle(root)
    proposal = _by_id(bundle)["proposals/live"]
    reopened = proposals.plan_propose(
        bundle,
        proposal.target,
        [{"id": "src-z", "resource": "/sources/b.md"}],
        title="x",
        description="y",
        by=AGENT,
        at=AT,
    )
    assert [r.kind for r in reopened.refusals] == ["already-decided"]


def test_an_update_promotion_leaves_the_target_body_byte_identical(tmp_path):
    root = ext_helpers.proposed_copy(tmp_path)
    before = (root / "pages/existing.md").read_bytes()
    bundle = load_bundle(root)
    plan = proposals.plan_promote(bundle, _by_id(bundle)["proposals/approved-existing"], by=AGENT, at=AT)
    assert proposals.apply(bundle, plan).ok
    after = Document.load(root / "pages/existing.md")
    assert after.body == Document.parse(before.decode()).body


def test_both_doors_write_the_same_page_but_for_verified(tmp_path):
    """Asserted on the written files, not on the plans -- the invariant is
    about what lands on disk."""
    promoted_root = ext_helpers.proposed_copy(tmp_path / "p")
    direct_root = ext_helpers.proposed_copy(tmp_path / "d")

    bundle = load_bundle(promoted_root)
    proposal = _by_id(bundle)["proposals/approved-new"]
    assert proposals.apply(bundle, proposals.plan_promote(bundle, proposal, RENDER, by=AGENT, at=AT)).ok

    bundle = load_bundle(direct_root)
    assert proposals.apply(
        bundle, proposals.plan_create(bundle, proposal.target, RENDER, by=AGENT, at=AT, sources=proposal.sources)
    ).ok

    promoted = Document.load(promoted_root / proposal.target)
    direct = Document.load(direct_root / proposal.target)
    assert promoted.body == direct.body
    assert promoted.fm_data()["sources"] == direct.fm_data()["sources"]
    assert promoted.fm_data()["generated"] == direct.fm_data()["generated"]
    assert "verified" in promoted.fm_data()
    assert "verified" not in direct.fm_data()


@pytest.mark.parametrize(
    "kind",
    [
        "already-decided",
        "not-proposed",
        "not-approved",
        "target-exists",
        "missing-render",
        "target-escapes-bundle",
        "malformed-proposal",
        "unreadable-target",
        "unrenderable-body",
    ],
)
def test_every_refusal_kind_is_reachable(kind, tmp_path):
    """A closed vocabulary is only closed if every member can actually fire."""
    bundle = _bundle(tmp_path)
    by_id = _by_id(bundle)
    produced = {
        "already-decided": lambda: proposals.plan_propose(
            bundle, "pages/rejected.md", [], title="t", description="d", by=AGENT, at=AT
        ),
        "not-proposed": lambda: proposals.plan_decide(bundle, by_id["proposals/rejected"], "approved", by=HUMAN, at=AT),
        "not-approved": lambda: proposals.plan_promote(bundle, by_id["proposals/live"], RENDER, by=AGENT, at=AT),
        "target-exists": lambda: proposals.plan_create(bundle, "pages/existing.md", RENDER, by=AGENT, at=AT),
        "missing-render": lambda: proposals.plan_promote(bundle, by_id["proposals/approved-new"], by=AGENT, at=AT),
        "target-escapes-bundle": lambda: proposals.plan_create(bundle, "../out.md", RENDER, by=AGENT, at=AT),
        "malformed-proposal": lambda: proposals.plan_decide(
            bundle, by_id["proposals/bad-status"], "approved", by=HUMAN, at=AT
        ),
        "unreadable-target": lambda: proposals.plan_promote(
            bundle, by_id["proposals/approved-broken-target"], RENDER, by=AGENT, at=AT
        ),
        "unrenderable-body": lambda: proposals.plan_propose(
            bundle, "pages/hand-edited.md", [], title="Hand edited", description="a new spin", by=AGENT, at=AT
        ),
    }[kind]()
    assert [r.kind for r in produced.refusals] == [kind]


def test_a_full_cycle_costs_no_finding(tmp_path):
    """The zero-`validate()`-impact promise, asserted after the capability has
    actually written -- not only over a hand-authored corpus."""
    root = ext_helpers.proposed_copy(tmp_path)
    bundle = load_bundle(root)
    filed = proposals.plan_propose(
        bundle,
        "pages/clean.md",
        [{"id": "src-a", "resource": "/sources/a.md", "title": "A"}],
        title="Clean",
        description="why",
        by=AGENT,
        at=AT,
    )
    assert proposals.apply(bundle, filed).ok
    member = filed.writes[0].member

    bundle = load_bundle(root)
    proposal = next(p for p in proposals.list_proposals(bundle) if p.member == member)
    assert proposals.apply(bundle, proposals.plan_decide(bundle, proposal, "approved", by=HUMAN, at=LATER)).ok

    bundle = load_bundle(root)
    proposal = next(p for p in proposals.list_proposals(bundle) if p.member == member)
    assert proposals.apply(bundle, proposals.plan_promote(bundle, proposal, RENDER, by=AGENT, at=LATER)).ok

    report = validate(load_bundle(root), today=TODAY)
    written = {member, "pages/clean.md"}
    offenders = [f"{f.code} {f.path}" for f in report.findings if f.path in written]
    assert not offenders, offenders


def test_an_unchanged_refile_of_an_unrenderable_body_is_still_a_no_op(tmp_path):
    """The guard sits after the no-op return, so a ledger nobody is changing
    is never told its body is unrenderable. Idempotence outranks the check."""
    bundle = _bundle(tmp_path)
    plan = proposals.plan_propose(
        bundle,
        "pages/hand-edited.md",
        [{"id": "src-b", "resource": "/sources/b.md", "title": "Second source"}],
        title="Hand edited",
        description="A proposed proposal whose body carries prose no ledger field holds.",
        by=AGENT,
        at=AT,
    )
    assert plan.ok
    assert plan.is_empty


def test_a_decided_unrenderable_body_still_refuses_as_already_decided(tmp_path):
    """Decided outranks unrenderable: the body of a decided proposal stopped
    being machine-owned at the flip, so the older refusal is the honest one
    and the guard must never reach a decided document."""
    root = ext_helpers.proposed_copy(tmp_path)
    note = root / "proposals" / "hand-edited.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace("page_status: proposed", "page_status: rejected"),
        encoding="utf-8",
        newline="",
    )
    plan = proposals.plan_propose(
        load_bundle(root), "pages/hand-edited.md", [], title="Hand edited", description="a new spin", by=AGENT, at=AT
    )
    assert [r.kind for r in plan.refusals] == ["already-decided"]
