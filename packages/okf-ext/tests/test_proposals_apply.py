"""Writing every plan type through one apply."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import ext_helpers
import pytest
from okf_ext.proposals.apply import apply
from okf_ext.proposals.model import PageRender, ProposalPlan, Write
from okf_ext.proposals.plan import list_proposals, plan_create, plan_decide, plan_promote, plan_propose
from okf_io import Document, load_bundle

AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
BY = "agent:ingest"
RENDER = PageRender(type="Concept", body="# Fresh\n\nProse.\n", frontmatter={"title": "Fresh", "description": "d"})


def _live(tmp_path):
    return load_bundle(ext_helpers.proposed_copy(tmp_path))


def _by_id(bundle):
    return {p.concept_id: p for p in list_proposals(bundle)}


def test_a_filed_proposal_lands_and_re_parses(tmp_path):
    bundle = _live(tmp_path)
    plan = plan_propose(
        bundle,
        "pages/brand-new.md",
        [{"id": "src-c", "resource": "/sources/b.md", "title": "B"}],
        title="Brand new",
        description="why",
        by=BY,
        at=AT,
    )
    result = apply(bundle, plan)
    assert result.ok, result.failed
    landed = Document.load(bundle.root / plan.writes[0].member)
    assert landed.parse_error is None
    assert landed.fm.type == "Proposal"
    assert dict(landed.fm.extra)["page_status"] == "proposed"


def test_a_merged_proposal_lands_with_its_re_rendered_body(tmp_path):
    bundle = _live(tmp_path)
    plan = plan_propose(
        bundle,
        "pages/live.md",
        [{"id": "src-c", "resource": "/sources/c.md", "title": "C"}],
        title="Live",
        description="why",
        by=BY,
        at=AT,
    )
    assert plan.ok
    assert len(plan.writes) == 1
    write = plan.writes[0]
    assert write.mode == "update"
    assert write.body is not None

    result = apply(bundle, plan)
    assert result.ok, result.failed

    landed = Document.load(bundle.root / "proposals/live.md")
    assert landed.parse_error is None
    assert "[^src-c]" in landed.body
    assert "/sources/c.md" in landed.body

    assert {source.id for source in landed.fm.sources} == {"src-a", "src-c"}


def test_a_decision_lands_without_touching_the_body(tmp_path):
    bundle = _live(tmp_path)
    before = (bundle.root / "proposals/live.md").read_bytes()
    proposal = _by_id(bundle)["proposals/live"]
    result = apply(bundle, plan_decide(bundle, proposal, "approved", by="human:psprowls", at=AT))
    assert result.ok, result.failed
    after = Document.load(bundle.root / "proposals/live.md")
    assert dict(after.fm.extra)["page_status"] == "approved"
    assert after.body == Document.parse(before.decode()).body


def test_a_promotion_writes_the_page_and_flips_the_ledger_in_one_apply(tmp_path):
    bundle = _live(tmp_path)
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-new"], RENDER, by=BY, at=AT)
    result = apply(bundle, plan)
    assert result.ok, result.failed
    assert result.written == ("pages/fresh.md", "proposals/approved-new.md")
    page = Document.load(bundle.root / "pages/fresh.md")
    assert page.parse_error is None
    assert page.fm.verified
    ledger = Document.load(bundle.root / "proposals/approved-new.md")
    assert dict(ledger.fm.extra)["page_status"] == "created"


def test_an_update_promotion_leaves_the_pages_prose_untouched(tmp_path):
    bundle = _live(tmp_path)
    before = Document.load(bundle.root / "pages/existing.md").body
    plan = plan_promote(bundle, _by_id(bundle)["proposals/approved-existing"], by=BY, at=AT)
    assert apply(bundle, plan).ok
    assert Document.load(bundle.root / "pages/existing.md").body == before


def test_the_direct_door_lands(tmp_path):
    bundle = _live(tmp_path)
    plan = plan_create(bundle, "pages/direct.md", RENDER, by=BY, at=AT)
    assert apply(bundle, plan).ok
    assert Document.load(bundle.root / "pages/direct.md").fm.verified == ()


def test_a_stale_body_is_refused(tmp_path):
    bundle = _live(tmp_path)
    plan = plan_propose(
        bundle,
        "pages/live.md",
        [{"id": "src-c", "resource": "/sources/b.md"}],
        title="Live",
        description="why",
        by=BY,
        at=AT,
    )
    ext_helpers.write(
        bundle.root / "proposals/live.md",
        (bundle.root / "proposals/live.md").read_text(encoding="utf-8") + "\ndrift\n",
    )
    result = apply(load_bundle(bundle.root), plan)
    assert [(f.path, f.kind) for f in result.failed] == [("proposals/live.md", "stale")]


def test_two_writes_for_one_member_are_refused(tmp_path):
    """Both were computed against the same document; applying them in sequence
    would silently discard the first."""
    bundle = _live(tmp_path)
    plan = ProposalPlan(
        root=bundle.root,
        target="pages/live.md",
        proposal="proposals/live.md",
        writes=(
            Write(member="proposals/live.md", mode="update", frontmatter={"title": "a"}),
            Write(member="proposals/live.md", mode="update", frontmatter={"title": "b"}),
        ),
        refusals=(),
    )
    result = apply(bundle, plan)
    assert [(f.path, f.kind) for f in result.failed] == [("proposals/live.md", "duplicate-edit")]


def test_a_non_member_and_an_unparseable_member_are_refused(tmp_path):
    bundle = _live(tmp_path)
    plan = ProposalPlan(
        root=bundle.root,
        target="pages/x.md",
        proposal="proposals/ghost.md",
        writes=(
            Write(member="proposals/ghost.md", mode="update", frontmatter={"title": "a"}),
            Write(member="pages/unparseable.md", mode="update", frontmatter={"title": "b"}),
        ),
        refusals=(),
    )
    result = apply(bundle, plan)
    assert {(f.path, f.kind) for f in result.failed} == {
        ("proposals/ghost.md", "not-a-member"),
        ("pages/unparseable.md", "parse-error"),
    }


def test_a_serialize_error_is_refused_for_that_document_alone_and_a_sibling_still_writes(tmp_path, monkeypatch):
    """Criterion 5's "siblings still write" half, which a single-document plan
    cannot demonstrate: with only one member in the batch, there is no sibling
    to observe succeeding alongside a failure. This hand-builds a plan naming
    **two** real members -- `proposals/live.md`, forced to blow up in
    `Document.serialize`, and `proposals/rejected.md`, a real frontmatter edit
    against a real fixture -- and checks both `result.failed` (naming the one
    that broke) and `result.written` (naming the one that did not), then reads
    `proposals/rejected.md` back off disk to confirm the write actually landed
    rather than merely being reported. Mirrors
    `test_generators_apply.test_a_serialize_error_is_refused_for_that_document_alone_and_a_sibling_still_writes`."""
    bundle = _live(tmp_path)
    plan = ProposalPlan(
        root=bundle.root,
        target="pages/x.md",
        proposal="proposals/live.md",
        writes=(
            Write(member="proposals/live.md", mode="update", frontmatter={"title": "a"}),
            Write(member="proposals/rejected.md", mode="update", frontmatter={"title": "b"}),
        ),
        refusals=(),
    )

    from okf_io import document as document_module

    original_serialize = document_module.Document.serialize

    def _boom(self):
        if self.path is not None and self.path.name == "live.md":
            raise ValueError("synthetic serialize failure")
        return original_serialize(self)

    monkeypatch.setattr(document_module.Document, "serialize", _boom)

    result = apply(bundle, plan)

    assert [failure.path for failure in result.failed] == ["proposals/live.md"]
    assert [failure.kind for failure in result.failed] == ["serialize-error"]
    assert result.written == ("proposals/rejected.md",)
    landed = Document.load(bundle.root / "proposals/rejected.md")
    assert landed.fm_raw["title"] == "b"


def test_a_serialize_error_leaves_the_live_document_untouched(tmp_path, monkeypatch):
    """Criterion 3's real negative case.

    `serialize-error` is the one failure kind that is only ever detected
    *after* `_rendered` has already run `copy.deepcopy` and mutated the
    scratch's frontmatter via `Document.set` -- `scratch.serialize()` is the
    very last thing `_rendered` does before handing back to `apply`. If
    `_rendered` aliased `document.fm_raw` instead of copying it, those
    mutations would land on the *live* document even though the write never
    reaches disk. Mirrors
    `test_generators_apply.test_a_serialize_error_leaves_the_live_document_untouched`.

    The snapshot is a `copy.deepcopy`, not a shallow `dict(...)`: `sources`
    is a nested `CommentedSeq`, and a shallow copy would still alias that
    inner sequence, silently agreeing with a mutated live document."""
    bundle = _live(tmp_path)
    document = bundle.concepts["proposals/live"]
    before_on_disk = (bundle.root / "proposals/live.md").read_bytes()
    before_fm = copy.deepcopy(document.fm_raw)
    before_body = document.body

    plan = ProposalPlan(
        root=bundle.root,
        target="pages/x.md",
        proposal="proposals/live.md",
        writes=(Write(member="proposals/live.md", mode="update", frontmatter={"title": "a"}),),
        refusals=(),
    )

    from okf_io import document as document_module

    original_serialize = document_module.Document.serialize

    def _boom(self):
        if self.path is not None and self.path.name == "live.md":
            raise ValueError("synthetic serialize failure")
        return original_serialize(self)

    monkeypatch.setattr(document_module.Document, "serialize", _boom)

    result = apply(bundle, plan)

    assert [failure.kind for failure in result.failed] == ["serialize-error"]
    assert document.fm_raw == before_fm
    assert document.body == before_body
    assert (bundle.root / "proposals/live.md").read_bytes() == before_on_disk


def test_a_plan_from_another_bundle_raises(tmp_path):
    bundle = _live(tmp_path)
    other = ext_helpers.proposed_bundle()
    plan = plan_create(other, "pages/direct.md", RENDER, by=BY, at=AT)
    with pytest.raises(ValueError, match="different bundle"):
        apply(bundle, plan)


def test_a_refused_plan_raises(tmp_path):
    bundle = _live(tmp_path)
    plan = plan_create(bundle, "pages/existing.md", RENDER, by=BY, at=AT)
    with pytest.raises(ValueError, match="refusal"):
        apply(bundle, plan)


def test_re_applying_an_applied_plan_is_a_no_op_or_a_clean_refusal(tmp_path):
    """Determinism, stated as a property: nothing lands twice by accident."""
    bundle = _live(tmp_path)
    plan = plan_create(bundle, "pages/direct.md", RENDER, by=BY, at=AT)
    assert apply(bundle, plan).ok
    again = apply(load_bundle(bundle.root), plan)
    assert not again.ok
    assert [f.kind for f in again.failed] == ["stale"]


PDF = b"%PDF-1.4\n\xff\xfe\x00binary\n"


def test_a_create_write_carrying_bytes_lands_byte_identical(tmp_path):
    """B-B: `apply` hands a create write to `write_all` without parsing it, so
    a binary payload is the same path with a different payload type."""
    bundle = _live(tmp_path)
    plan = ProposalPlan(
        root=bundle.root,
        target="pages/live.md",
        proposal="proposals/live.md",
        writes=(Write(member="sources/references/scan.pdf", mode="create", text=PDF),),
        refusals=(),
    )

    result = apply(bundle, plan)

    assert result.ok, result.failed
    assert result.written == ("sources/references/scan.pdf",)
    assert (bundle.root / "sources/references/scan.pdf").read_bytes() == PDF


def test_an_update_write_ignores_its_text_payload_whether_str_or_bytes(tmp_path):
    """The update path renders from `frontmatter` and `body` and never reads
    `text`, so widening the field changes nothing there."""
    bundle = _live(tmp_path)
    plan = ProposalPlan(
        root=bundle.root,
        target="pages/live.md",
        proposal="proposals/live.md",
        writes=(Write(member="proposals/live.md", mode="update", text=PDF, frontmatter={"title": "Retitled"}),),
        refusals=(),
    )

    result = apply(bundle, plan)

    assert result.ok, result.failed
    landed = Document.load(bundle.root / "proposals/live.md")
    assert landed.parse_error is None
    assert landed.fm.title == "Retitled"
    assert PDF not in (bundle.root / "proposals/live.md").read_bytes()
