"""Promotion: one plan, three effects, and none of the capability's keys."""

from dataclasses import replace

from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.promote import plan_promotion
from okf_ext.proposals import OWNED_PROVENANCE_KEYS, apply, list_proposals, plan_decide
from okf_ext.schemas import schema_rule
from okf_ext.sections import render_skeleton
from okf_io import load_bundle, parse
from okf_io import validate as okf_validate
from proposal_helpers import AT, BY, TODAY, build_bundle, lanes, schema_set, section_set, source

IGNORE = ("schema/*", "*/schema/*", "sections/*", "*/sections/*")


def _approved(root, *, lane="adr", title="Bulk Write Staging Protocol"):
    """A bundle whose single proposal is `approved` and ready to promote."""
    bundle = build_bundle(root)
    apply(
        bundle,
        plan_file(
            bundle,
            lanes(),
            lane=lane,
            title=title,
            description="Why this page.",
            source=source("src-a", "sources/2026-08-spec.md", rationale="It settles it."),
            by=BY,
            at=AT,
        ),
    )
    bundle = load_bundle(root, ignore=IGNORE)
    proposal = list_proposals(bundle)[0]
    apply(bundle, plan_decide(bundle, proposal, "approved", by=BY, at=AT))
    bundle = load_bundle(root, ignore=IGNORE)
    return bundle, list_proposals(bundle)[0]


def test_the_plan_carries_the_page_then_the_flip(tmp_path) -> None:
    bundle, proposal = _approved(tmp_path / "b")
    plan = plan_promotion(bundle, lanes(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert plan.ok
    assert [(write.member, write.mode) for write in plan.writes] == [
        ("adrs/2026-08-12-bulk-write-staging-protocol.md", "create"),
        (proposal.member, "update"),
    ]


def test_the_flip_carries_both_the_status_and_the_dated_target(tmp_path) -> None:
    """One plan, so page write, status flip and retarget commit together or
    not at all -- the property splitting the retarget out would hand back."""
    bundle, proposal = _approved(tmp_path / "b")
    plan = plan_promotion(bundle, lanes(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert dict(plan.writes[1].frontmatter) == {
        "page_status": "created",
        "target": "adrs/2026-08-12-bulk-write-staging-protocol.md",
    }


def test_the_promoted_page_is_an_explanation_carrying_the_skeleton(tmp_path) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root)
    plan = plan_promotion(bundle, lanes(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert apply(bundle, plan).ok

    page = parse((root / "adrs/2026-08-12-bulk-write-staging-protocol.md").read_text(encoding="utf-8"))
    assert page.parse_error is None
    assert page.fm.type == "Explanation"
    assert page.fm.title == "Bulk Write Staging Protocol"
    assert page.fm.description == "Why this page."
    assert page.body == render_skeleton(section_set().types["Explanation"])


def test_the_promoted_page_passes_the_explanation_schema(tmp_path) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root)
    apply(bundle, plan_promotion(bundle, lanes(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY))

    # The ledger itself (`type: Proposal`) is not a page this schema set governs --
    # excluded alongside the declaration directories so the assertion is about the
    # promoted page, which is the acceptance criterion under test here.
    report = okf_validate(
        load_bundle(root, ignore=(*IGNORE, "proposals/*")), today=TODAY, extra_rules=[schema_rule(schema_set())]
    )
    assert [finding.code for finding in report.findings if finding.code.startswith("schemas.")] == []


def test_this_layer_supplies_none_of_the_owned_provenance_keys(tmp_path) -> None:
    """`plan_create` raises if a caller supplies one; this proves we do not,
    rather than relying on the absence of an exception."""
    from doc_wiki_okf.proposals.promote import page_render

    _bundle, proposal = _approved(tmp_path / "b")
    render = page_render(lanes()["adr"], proposal, section_set=section_set())
    assert not set(render.frontmatter) & set(OWNED_PROVENANCE_KEYS)
    assert set(render.frontmatter) == {"title", "description"}


def test_a_target_in_no_declared_lane_refuses(tmp_path) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root)
    stray = replace(proposal, target="nowhere/x.md")
    plan = plan_promotion(bundle, lanes(), stray, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert not plan.ok
    assert plan.writes == ()
    assert plan.refusals[0].kind == "malformed-proposal"
    assert "nowhere/x.md" in plan.refusals[0].detail


def test_an_unapproved_proposal_still_refuses(tmp_path) -> None:
    """The capability's own guard, inherited rather than restated."""
    root = tmp_path / "b"
    bundle = build_bundle(root)
    apply(
        bundle,
        plan_file(
            bundle,
            lanes(),
            lane="adr",
            title="Not Yet",
            description="",
            source=source("a", "sources/a.md"),
            by=BY,
            at=AT,
        ),
    )
    bundle = load_bundle(root, ignore=IGNORE)
    plan = plan_promotion(bundle, lanes(), list_proposals(bundle)[0], section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert not plan.ok
    assert plan.refusals[0].kind == "not-approved"


def test_an_undated_lane_promotes_in_place(tmp_path) -> None:
    bundle, proposal = _approved(tmp_path / "b", lane="reference", title="CLI Flags")
    plan = plan_promotion(bundle, lanes(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert plan.writes[0].member == "references/cli-flags.md"
    assert dict(plan.writes[1].frontmatter)["target"] == "references/cli-flags.md"
