"""Plain-data wiki projections: exact keys and explicit nested rows."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace as ns

from code_wiki_okf.entities.sync import SyncSummary
from code_wiki_okf.mirror.model import MirrorResult
from code_wiki_okf.sync import MirrorSummary
from graph_works_core.ingest.commands import IngestResult
from graph_works_core.lint_drift.lint import LaneReport, LintReport, ProposalBacklog, SemanticFinding
from graph_works_core.proposals import ProposalDecideRun, ProposalFileRun, ProposalRefusal
from graph_works_core.scan.commands import ScanResult, StructuralSummary
from graph_works_core.scan.scan_contract import ApplyResult, ScanWorklist
from graph_works_core.wiki_page.commands import TreeNode, TreePage, WikiTree
from graph_works_core.wiki_stats.commands import HubEntry, WikiStats
from graph_works_core.workspace.init import WorkspaceInit, plan_init
from graph_works_core.workspace.layout import layout_for
from graph_works_wire import wiki
from graph_works_wire.wiki import (
    bootstrap_payload,
    bootstrap_plan_payload,
    ingest_payload,
    lint_payload,
    proposal_decide_payload,
    proposal_file_payload,
    proposal_payload,
    scan_apply_payload,
    scan_emit_payload,
    scan_normal_payload,
    stats_payload,
    tag_inventory_payload,
    tags_undeclared_payload,
)
from okf_ext.bundle import ApplyResult as BundleApplyResult
from okf_ext.proposals import ApplyResult as ProposalApplyResult
from okf_ext.proposals import DecisionPlan, Proposal, ProposalPlan, Write, WriteFailure
from okf_io import Finding, Report

AT = "2026-08-24T12:00:00+00:00"
DECIDE_WRITE = Write(
    member="proposals/a.md",
    mode="update",
    frontmatter={"page_status": "approved", "verified": [{"by": "human", "at": AT}]},
)
DECIDE_KEYS = {
    "target",
    "proposal",
    "decision",
    "ok",
    "refusals",
    "writes",
    "written",
    "applied",
    "rolled_back",
    "failures",
}


def _decide(**over: object) -> ProposalDecideRun:
    base: dict[str, object] = {
        "target": "concepts/a.md",
        "decision": "approved",
        "proposal": "proposals/a.md",
        "plan": DecisionPlan(
            root=Path("/ws/okf"),
            proposal="proposals/a.md",
            decision="approved",
            writes=(DECIDE_WRITE,),
            refusals=(),
        ),
        "refusals": (),
        "result": None,
    }
    base.update(over)
    return ProposalDecideRun(**base)  # type: ignore[arg-type]


def test_decide_dry_run_projects_planned_frontmatter_only() -> None:
    payload = proposal_decide_payload(_decide())
    assert set(payload) == DECIDE_KEYS
    assert payload["writes"] == [
        {
            "member": "proposals/a.md",
            "mode": "update",
            "frontmatter": {"page_status": "approved", "verified": [{"by": "human", "at": AT}]},
        }
    ]
    assert payload["applied"] is False and payload["written"] == [] and payload["rolled_back"] is False
    assert payload["ok"] is True and payload["failures"] == []
    assert "/ws/okf" not in json.dumps(payload)


def test_decide_unknown_target_is_a_refusal_with_no_writes() -> None:
    refusal = ProposalRefusal(path="concepts/x.md", kind="no-proposal", detail="no proposal targets 'concepts/x.md'")
    payload = proposal_decide_payload(_decide(proposal=None, plan=None, refusals=(refusal,)))
    assert payload["proposal"] is None and payload["writes"] == [] and payload["ok"] is False
    assert payload["refusals"] == [
        {"path": "concepts/x.md", "kind": "no-proposal", "detail": "no proposal targets 'concepts/x.md'"}
    ]


def test_decide_applied_and_failed_forms() -> None:
    applied = proposal_decide_payload(
        _decide(result=ProposalApplyResult(written=("proposals/a.md",), failed=(), skipped=()))
    )
    assert applied["applied"] is True and applied["written"] == ["proposals/a.md"]
    failed = proposal_decide_payload(
        _decide(
            result=ProposalApplyResult(
                written=(),
                failed=(WriteFailure(path="proposals/a.md", kind="stale", error="changed"),),
                skipped=(),
            )
        )
    )
    assert failed["ok"] is False and failed["failures"] == ["proposals/a.md: stale -- changed"]


def test_file_payload_never_projects_create_text() -> None:
    plan = ProposalPlan(
        root=Path("/ws/okf"),
        target="docs/explanations/t.md",
        proposal="proposals/explanations-t.md",
        writes=(Write(member="proposals/explanations-t.md", mode="create", text="---\nsecret body\n"),),
        refusals=(),
    )
    run = ProposalFileRun(
        lane="explanation", target=plan.target, proposal=plan.proposal, plan=plan, refusals=(), result=None
    )
    payload = proposal_file_payload(run)
    assert set(payload) == {
        "lane",
        "target",
        "proposal",
        "ok",
        "refusals",
        "writes",
        "written",
        "applied",
        "rolled_back",
        "failures",
    }
    assert payload["writes"] == [{"member": "proposals/explanations-t.md", "mode": "create", "frontmatter": {}}]
    assert "secret body" not in json.dumps(payload)


def test_bootstrap_payload_has_exact_keys_and_reports_additions_and_deletions(tmp_path: Path) -> None:
    layout = layout_for(tmp_path / "workspace")
    result = WorkspaceInit(
        layout=layout,
        created=(layout.root,),
        written=("workspace.yaml", "workspace.yaml"),
        deleted=("okf/AGENTS.md", "okf/CLAUDE.md"),
        scaffold=BundleApplyResult(written=("index.md",), failed=(), skipped=()),
        installs=(),
    )

    payload = bootstrap_payload(result)

    assert set(payload) == {
        "ok",
        "changed",
        "workspace",
        "bundle_dir",
        "config_dir",
        "cache_dir",
        "created",
        "written",
        "deleted",
    }
    assert payload["written"] == [f"{layout.root}/", "workspace.yaml", "index.md"]
    assert payload["deleted"] == ["okf/AGENTS.md", "okf/CLAUDE.md"]


def test_bootstrap_plan_payload_has_exact_keys_and_reports_the_planned_lines(tmp_path: Path) -> None:
    plan = plan_init(tmp_path / "workspace", today=date(2026, 8, 20), topic="Demo")

    payload = bootstrap_plan_payload(plan)

    assert set(payload) == {"ok", "changed", "workspace", "bundle_dir", "config_dir", "cache_dir", "planned"}
    assert payload["changed"] is True
    assert "+ workspace.yaml" in payload["planned"]


_MIRROR_KEYS = {
    "mirror_created",
    "mirror_updated",
    "mirror_moved",
    "mirror_deleted",
    "mirror_declined",
    "mirror_stranded",
    "mirror_skipped_repos",
    "mirror_errors",
}


def test_scan_payloads_have_exact_keys_and_exclude_runtime_only_values() -> None:
    sync = SyncSummary(
        created=("packages/created.md",),
        updated=("packages/updated.md",),
        written=("packages/demo.md",),
        deleted=("apps/old.md",),
        catalog_declined=(("index.md", "locked"),),
    )
    structural = StructuralSummary(entities=sync, mirror=MirrorSummary())
    result = ScanResult(
        structural=structural,
        worklist=ScanWorklist(short_head="abc123"),
        applied=ApplyResult(narrated=2, sections_filled=3, stamped=4, entity_errors=("page: declined",), dry_run=True),
        errors=("graph unavailable",),
    )

    normal = scan_normal_payload(result)
    emitted = scan_emit_payload(
        worklist_path=Path("worklist.json"),
        briefs_dir=Path("briefs"),
        results_dir=Path("results"),
        short_head="abc123",
        structural=structural,
    )
    applied = scan_apply_payload(result.applied)

    assert (
        set(normal)
        == {
            "ok",
            "short_head",
            "entities_written",
            "entities_created",
            "entities_updated",
            "entities_deleted",
            "narrated",
            "sections_filled",
            "stamped",
            "entity_errors",
        }
        | _MIRROR_KEYS
    )
    assert (
        set(emitted)
        == {
            "worklist_path",
            "briefs_dir",
            "results_dir",
            "short_head",
            "entities_written",
            "entities_created",
            "entities_updated",
            "entities_deleted",
            "entity_errors",
        }
        | _MIRROR_KEYS
    )
    assert set(applied) == {"narrated", "sections_filled", "stamped", "entity_errors"}
    assert "dry_run" not in applied

    assert normal["entities_created"] == ["packages/created.md"]
    assert normal["entities_updated"] == ["packages/updated.md"]
    assert emitted["entities_created"] == ["packages/created.md"]
    assert emitted["entities_updated"] == ["packages/updated.md"]


def test_scan_normal_payload_reports_both_lanes() -> None:
    """D3: a scan that writes 755 mirror pages and reports none of them is the
    defect class this epic exists to close. Additive keys only -- the existing
    `entities_*` keys keep their names.
    """
    result = ScanResult(
        structural=StructuralSummary(
            entities=SyncSummary(written=("packages/widgets",)),
            mirror=MirrorSummary(
                results=(
                    MirrorResult(
                        repo="demo",
                        moved=(),
                        created=("src/a.py",),
                        regenerated=(),
                        deleted=(),
                        declined_deletions=(),
                        index_updates=(),
                    ),
                ),
                skipped_repos=("no-git",),
                failed_repos=(("broken", "disk full"),),
            ),
        ),
        worklist=ScanWorklist(head_commit="abc123", short_head="abc123"),
    )

    payload = scan_normal_payload(result)

    assert payload["entities_written"] == ["packages/widgets"]  # unchanged
    assert payload["mirror_created"] == 1
    assert payload["mirror_updated"] == 0
    assert payload["mirror_moved"] == 0
    assert payload["mirror_deleted"] == 0
    assert payload["mirror_declined"] == 0
    assert payload["mirror_skipped_repos"] == ["no-git"]
    assert payload["mirror_errors"] == ["broken: disk full"]


def test_ingest_payload_has_exact_keys_and_omits_internal_proposal_status() -> None:
    result = IngestResult(
        ok=True,
        page="sources/demo.md",
        copy="sources/references/demo.md",
        title="Demo",
        source_kind="reference",
        frontmatter_parsed=False,
        proposals=(
            {
                "lane": "adrs",
                "title": "Keep",
                "target": "adrs/demo.md",
                "proposal": "text",
                "status": "filed",
                "internal": "no",
            },
        ),
        proposal_status={
            "error": "model unavailable",
            "failed": ["Keep: mkdir-error", "Merge: write-error"],
            "errored": ["Explain: RuntimeError"],
        },
    )

    payload = ingest_payload(result)

    assert set(payload) == {
        "ok",
        "page",
        "copy",
        "title",
        "source_kind",
        "entity_uri",
        "entity_page",
        "frontmatter_parsed",
        "written",
        "indexes_updated",
        "proposals",
        "warnings",
        "refusals",
    }
    assert "proposal_status" not in payload
    assert payload["proposals"] == [
        {"lane": "adrs", "title": "Keep", "target": "adrs/demo.md", "proposal": "text", "status": "filed"}
    ]
    assert payload["warnings"] == [
        "ingestor response frontmatter was not parsed; fallback values were used",
        "suggestion phase degraded: model unavailable",
        "suggestion apply failed: Keep: mkdir-error",
        "suggestion apply failed: Merge: write-error",
        "suggestion apply errored: Explain: RuntimeError",
    ]


def test_lint_payload_has_exact_keys_and_explicit_nested_rows() -> None:
    report = LintReport(
        mechanical=(
            LaneReport(
                name="wiki",
                report=Report(findings=(Finding("links.broken", "error", "Missing", "§5", "demo.md", 12),)),
            ),
        ),
        semantic=(SemanticFinding("quality", "Clarify", "demo", "test-model"),),
        open_proposals=ProposalBacklog(count=1, oldest=date(2026, 8, 1), malformed=2, ages={"7-30d": 1}),
        errors=("lane failed",),
    )

    payload = lint_payload(report)

    assert set(payload) == {"ok", "mechanical", "semantic", "open_proposals", "errors"}
    assert payload["mechanical"] == [
        {
            "lane": "wiki",
            "findings": [
                {
                    "code": "links.broken",
                    "severity": "error",
                    "message": "Missing",
                    "spec": "§5",
                    "path": "demo.md",
                    "line": 12,
                }
            ],
        }
    ]
    assert payload["semantic"] == [{"group": "quality", "message": "Clarify", "page": "demo", "model": "test-model"}]
    assert payload["open_proposals"] == {"count": 1, "oldest": "2026-08-01", "malformed": 2, "ages": {"7-30d": 1}}


def test_stats_and_proposal_payloads_have_exact_keys() -> None:
    stats = WikiStats(4, 5, 2, (HubEntry("home", 3),), (HubEntry("api", 2),), ("orphan",), ("sink",))
    proposal = Proposal(
        member="adrs/demo.md",
        concept_id="demo",
        target="adrs/target.md",
        title="Demo",
        description="Description",
        page_status="proposed",
        raw_page_status="proposed",
        sources=({"resource": "/sources/demo.md"},),
        verified=({"by": "reviewer"},),
    )

    stats_result = stats_payload(stats)
    proposal_result = proposal_payload(proposal, mode="create")

    assert set(stats_result) == {
        "total_pages",
        "total_edges",
        "component_count",
        "top_outbound_hubs",
        "top_inbound_hubs",
        "orphans",
        "sinks",
    }
    assert stats_result["top_outbound_hubs"] == [{"page": "home", "degree": 3}]
    assert stats_result["top_inbound_hubs"] == [{"page": "api", "degree": 2}]
    assert set(proposal_result) == {
        "member",
        "target",
        "title",
        "description",
        "page_status",
        "mode",
        "sources",
        "verified",
        "malformed",
    }
    assert proposal_result["sources"] == [{"resource": "/sources/demo.md"}]
    assert proposal_result["verified"] == [{"by": "reviewer"}]


def test_both_scan_payloads_report_identical_structural_errors(tmp_path: Path) -> None:
    """`entity_errors` is the partial-write signal the scanner agent reports
    verbatim (plugins/gw/skills/scan/SKILL.md). Emit mode must not
    render a narrower set than normal mode, or a failed mirror repo becomes
    invisible to the agent driving the scan.
    """
    structural = StructuralSummary(
        entities=SyncSummary(
            skipped=("packages/a.md: generator: boom",),
            catalog_declined=(("packages/index.md", "stale"),),
        ),
        mirror=MirrorSummary(failed_repos=(("demo", "disk full"),)),
    )

    emit = scan_emit_payload(
        worklist_path=tmp_path / "worklist.json",
        briefs_dir=tmp_path / "briefs",
        results_dir=tmp_path / "results",
        short_head="abc1234",
        structural=structural,
    )

    assert emit["entity_errors"] == list(structural.errors)
    assert "demo: mirror sync failed: disk full" in emit["entity_errors"]


def test_tag_inventory_payload_counts_distinct_tagged_pages_and_sorts_counts() -> None:
    result = SimpleNamespace(
        counts={"zeta": 1, "alpha": 2},
        concepts={"alpha": ("c1", "c2"), "zeta": ("c1",)},
        untagged=("c3",),
        skipped=(SimpleNamespace(path="x.md", reason="parse", detail="bad"),),
    )
    assert tag_inventory_payload(result) == {
        "total_tags": 2,
        "tagged_pages": 2,
        "untagged_pages": 1,
        "counts": {"alpha": 2, "zeta": 1},
        "skipped": [{"path": "x.md", "reason": "parse", "detail": "bad"}],
    }
    assert list(tag_inventory_payload(result)["counts"]) == ["alpha", "zeta"]


def test_tags_undeclared_payload_is_a_single_list() -> None:
    assert tags_undeclared_payload(("b", "a")) == {"undeclared": ["b", "a"]}


def test_proposal_payload_places_mode_after_page_status() -> None:
    proposal = ns(
        member="proposals/a.md",
        target="adrs/a.md",
        title="A",
        description="",
        page_status="approved",
        sources=(),
        verified=(),
        malformed=None,
    )

    payload = wiki.proposal_payload(proposal, mode="update")

    assert list(payload) == [
        "member",
        "target",
        "title",
        "description",
        "page_status",
        "mode",
        "sources",
        "verified",
        "malformed",
    ]
    assert payload["mode"] == "update"


def test_proposals_payload_projects_each_listing() -> None:
    proposal = ns(
        member="proposals/a.md",
        target="concepts/a.md",
        title="A",
        description="",
        page_status="proposed",
        sources=(),
        verified=(),
        malformed=None,
    )
    listing = ns(proposal=proposal, mode="create")

    assert wiki.proposals_payload([listing]) == [wiki.proposal_payload(proposal, mode="create")]
    assert wiki.proposals_payload([]) == []


def test_wiki_tree_payload_nests_sections() -> None:
    tree = WikiTree(
        (
            TreeNode(
                "Concepts",
                2,
                False,
                (),
                (TreeNode("Architecture", 3, False, (TreePage("concepts/a", "A", None),), ()),),
            ),
        )
    )

    assert wiki.wiki_tree_payload(tree) == {
        "sections": [
            {
                "heading": "Concepts",
                "level": 2,
                "generated": False,
                "pages": [],
                "sections": [
                    {
                        "heading": "Architecture",
                        "level": 3,
                        "generated": False,
                        "pages": [{"id": "concepts/a", "title": "A", "type": None}],
                        "sections": [],
                    }
                ],
            }
        ]
    }
