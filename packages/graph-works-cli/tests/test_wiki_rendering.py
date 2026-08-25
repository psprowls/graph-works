"""Public JSON payload contracts for the future ``gw wiki`` commands."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from code_wiki_okf.entities.sync import SyncSummary
from code_wiki_okf.mirror.model import MirrorResult
from code_wiki_okf.sync import MirrorSummary
from graph_works_cli.wiki_cli.rendering import (
    bootstrap_payload,
    bootstrap_plan_payload,
    ingest_payload,
    lint_payload,
    proposal_payload,
    scan_apply_payload,
    scan_emit_payload,
    scan_normal_payload,
    stats_payload,
)
from graph_works_core.ingest.commands import IngestResult
from graph_works_core.lint_drift.lint import LaneReport, LintReport, ProposalBacklog, SemanticFinding
from graph_works_core.scan.commands import ScanResult, StructuralSummary
from graph_works_core.scan.scan_contract import ApplyResult, ScanWorklist
from graph_works_core.wiki_stats.commands import HubEntry, WikiStats
from graph_works_core.workspace.init import WorkspaceInit, plan_init
from graph_works_core.workspace.layout import layout_for
from okf_ext.bundle import ApplyResult as BundleApplyResult
from okf_ext.proposals import Proposal
from okf_io import Finding, Report


def test_bootstrap_payload_has_exact_keys_and_reports_actual_diff_additions(tmp_path: Path) -> None:
    layout = layout_for(tmp_path / "workspace")
    result = WorkspaceInit(
        layout=layout,
        created=(layout.root,),
        written=("workspace.yaml", "workspace.yaml"),
        scaffold=BundleApplyResult(written=("index.md",), failed=(), skipped=()),
        installs=(),
    )

    payload = bootstrap_payload(result)

    assert set(payload) == {"ok", "changed", "workspace", "bundle_dir", "config_dir", "cache_dir", "created", "written"}
    assert payload["written"] == [f"{layout.root}/", "workspace.yaml", "index.md"]


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
    proposal_result = proposal_payload(proposal)

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
        "sources",
        "verified",
        "malformed",
    }
    assert proposal_result["sources"] == [{"resource": "/sources/demo.md"}]
    assert proposal_result["verified"] == [{"by": "reviewer"}]


def test_both_scan_payloads_report_identical_structural_errors(tmp_path: Path) -> None:
    """`entity_errors` is the partial-write signal the scanner agent reports
    verbatim (plugins/graph-works/agents/scanner.md). Emit mode must not
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
