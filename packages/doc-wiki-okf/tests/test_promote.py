"""Promotion: one plan, three effects, and none of the capability's keys."""

from dataclasses import replace

import pytest
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.pool import ProposalPool
from doc_wiki_okf.proposals.promote import page_render, plan_promotion
from okf_ext.proposals import OWNED_PROVENANCE_KEYS, apply, list_proposals, plan_decide
from okf_ext.schemas import Proposables, ProposableType, ProposalGuidance, ProposalPromotion, schema_rule
from okf_ext.sections import render_skeleton
from okf_ext.shape import load_sections
from okf_io import load_bundle, parse
from okf_io import validate as okf_validate
from proposal_helpers import AT, BY, TODAY, build_bundle, pool, schema_set, section_set, source

IGNORE = ("schema/*", "*/schema/*", "sections/*", "*/sections/*")


def _approved(root, *, type_name="Adr", title="Bulk Write Staging Protocol"):
    """A bundle whose single proposal is `approved` and ready to promote."""
    bundle = build_bundle(root)
    apply(
        bundle,
        plan_file(
            bundle,
            pool(),
            type_name=type_name,
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
    plan = plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert plan.ok
    assert [(write.member, write.mode) for write in plan.writes] == [
        ("adrs/2026-08-12-bulk-write-staging-protocol.md", "create"),
        (proposal.member, "update"),
    ]


def test_the_flip_carries_both_the_status_and_the_dated_target(tmp_path) -> None:
    """One plan, so page write, status flip and retarget commit together or
    not at all -- the property splitting the retarget out would hand back."""
    bundle, proposal = _approved(tmp_path / "b")
    plan = plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert dict(plan.writes[1].frontmatter) == {
        "page_status": "created",
        "target": "adrs/2026-08-12-bulk-write-staging-protocol.md",
    }


def test_the_promoted_page_is_an_adr_carrying_the_skeleton_and_its_decision_date(tmp_path) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root)
    plan = plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert apply(bundle, plan).ok

    page = parse((root / "adrs/2026-08-12-bulk-write-staging-protocol.md").read_text(encoding="utf-8"))
    assert page.parse_error is None
    assert page.fm.type == "Adr"
    assert page.fm.title == "Bulk Write Staging Protocol"
    assert page.fm.description == "Why this page."
    assert page.fm_data()["decision_date"] == TODAY.isoformat()
    assert page.fm_data()["status"] == "stable"
    assert page.body == render_skeleton(section_set().types["Adr"])


def test_the_promoted_page_passes_the_adr_schema(tmp_path) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root)
    apply(bundle, plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY))

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
    render = page_render(pool()["Adr"], proposal, section_set=section_set(), on=TODAY)
    assert not set(render.frontmatter) & set(OWNED_PROVENANCE_KEYS)
    assert set(render.frontmatter) == {"title", "description", "decision_date", "status"}


def test_a_target_in_no_declared_lane_refuses(tmp_path) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root)
    stray = replace(proposal, target="nowhere/x.md", target_type=None)
    plan = plan_promotion(bundle, pool(), stray, section_set=section_set(), by=BY, at=AT, on=TODAY)
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
            pool(),
            type_name="Adr",
            title="Not Yet",
            description="",
            source=source("a", "sources/a.md"),
            by=BY,
            at=AT,
        ),
    )
    bundle = load_bundle(root, ignore=IGNORE)
    plan = plan_promotion(bundle, pool(), list_proposals(bundle)[0], section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert not plan.ok
    assert plan.refusals[0].kind == "not-approved"


def test_an_undated_lane_promotes_in_place(tmp_path) -> None:
    bundle, proposal = _approved(tmp_path / "b", type_name="Reference", title="CLI Flags")
    plan = plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert plan.writes[0].member == "docs/reference/cli-flags.md"
    assert dict(plan.writes[1].frontmatter)["target"] == "docs/reference/cli-flags.md"


def test_undated_promotion_preserves_recorded_filename(tmp_path) -> None:
    bundle, proposal = _approved(tmp_path / "b", type_name="Explanation", title="Byte Fidelity")
    proposal = replace(proposal, target="docs/explanations/recorded.md")
    plan = plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert (plan.writes[0].member, plan.writes[0].mode) == ("docs/explanations/recorded.md", "create")
    assert "decision_date" not in plan.writes[0].frontmatter


@pytest.mark.parametrize(
    "type_name,target", [("Adr", "adrs/old-name.md"), ("Explanation", "docs/explanations/old-name.md")]
)
def test_existing_target_is_updated_even_when_title_differs(tmp_path, type_name, target) -> None:
    root = tmp_path / "b"
    bundle, proposal = _approved(root, type_name=type_name, title="New Title")
    page = root / target
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type_name}\ntitle: Old\ndescription: old\nsources: []\n---\n\nPreserved prose.\n",
        encoding="utf-8",
        newline="",
    )
    bundle = load_bundle(root, ignore=IGNORE)
    proposal = replace(proposal, target=target)
    plan = plan_promotion(bundle, pool(), proposal, section_set=section_set(), by=BY, at=AT, on=TODAY)
    assert (plan.writes[0].member, plan.writes[0].mode) == (target, "update")
    assert plan.writes[1].frontmatter["target"] == target
    assert apply(bundle, plan).ok
    assert "Preserved prose." in page.read_text(encoding="utf-8")


def test_custom_promotion_frontmatter_expands_the_date(tmp_path) -> None:
    entry = ProposableType(
        name="Runbook",
        directory="runbooks/",
        guidance=ProposalGuidance(summary="s", question="q?"),
        promotion=ProposalPromotion(frontmatter={"service": "unassigned", "reviewed": "{on}"}),
    )
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "Runbook.yaml").write_text(
        "type: Runbook\nsections:\n  - heading: Recovery\n    level: 2\n", encoding="utf-8", newline=""
    )
    _, proposal = _approved(tmp_path / "b")
    render = page_render(
        entry, replace(proposal, title="T", description="d"), section_set=load_sections(sections_dir), on=TODAY
    )
    assert dict(render.frontmatter) == {
        "title": "T",
        "description": "d",
        "service": "unassigned",
        "reviewed": "2026-08-12",
    }


@pytest.mark.parametrize("target_type", ["Source", "Missing"])
def test_unavailable_recorded_type_refuses_and_stays_approved(tmp_path, target_type) -> None:
    bundle, proposal = _approved(tmp_path / "b", type_name="Explanation")
    plan = plan_promotion(
        bundle, pool(), replace(proposal, target_type=target_type), section_set=section_set(), by=BY, at=AT, on=TODAY
    )
    assert [r.kind for r in plan.refusals] == ["type-unavailable"]
    assert plan.writes == ()
    assert list_proposals(bundle)[0].page_status == "approved"


def test_work_types_refuse_ordinary_promotion(tmp_path) -> None:
    bundle, proposal = _approved(tmp_path / "b")
    entry = ProposableType(
        name="Task", directory="work/", guidance=ProposalGuidance(summary="s", question="q?"), promotion=None
    )
    work_pool = ProposalPool(Proposables(types=(entry,), locked=(), refused=()))
    plan = plan_promotion(
        bundle,
        work_pool,
        replace(proposal, target="work/p.md", target_type="Task"),
        section_set=section_set(),
        by=BY,
        at=AT,
        on=TODAY,
    )
    assert [r.kind for r in plan.refusals] == ["type-unavailable"]
    assert "gw work file" in plan.refusals[0].detail
    assert plan.writes == ()
    assert list_proposals(bundle)[0].page_status == "approved"
