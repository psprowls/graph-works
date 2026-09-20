"""Plain-data projections for wiki results.

Bootstrap, scan, ingest, query, lint, drift, stats, proposal decide/file, tags.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from doc_wiki_okf.ingest import DocumentBrief
from graph_works_core.ingest.commands import IngestResult
from graph_works_core.lint_drift.lint import LintReport
from graph_works_core.lint_drift.propagate_drift import DriftBrief, PropagateResult
from graph_works_core.proposals import ProposalDecideRun, ProposalFileRun, ProposalListing, ProposalRefusal
from graph_works_core.query.commands import QueryBrief, QueryResult
from graph_works_core.scan.commands import ScanResult, StructuralSummary
from graph_works_core.scan.scan_contract import ApplyResult
from graph_works_core.wiki_page.citations import Citation, WikiCitations
from graph_works_core.wiki_page.commands import PageLink, PageRead, TreeNode, WikiTree
from graph_works_core.wiki_stats.commands import HubEntry, WikiStats
from graph_works_core.workspace.init import WorkspaceInit, WorkspacePlan
from okf_ext.proposals import ApplyResult as ProposalApplyResult
from okf_ext.proposals import Proposal, Write
from okf_ext.tags import TagInventory

from graph_works_wire._jsonable import jsonable


def _page_link(link: PageLink) -> dict[str, object]:
    return {
        "source": link.source,
        "raw": link.raw,
        "target": link.target,
        "external": link.external,
        "line": link.line,
    }


def page_payload(result: PageRead) -> dict[str, object]:
    """`/v1/wiki/page`: one page with its computed link neighbourhood."""
    return {
        "id": result.id,
        "frontmatter": jsonable(dict(result.frontmatter)),
        "body": result.body,
        "outlinks": [_page_link(link) for link in result.outlinks],
        "backlinks": list(result.backlinks),
        "broken": [_page_link(link) for link in result.broken],
        "parse_error": result.parse_error,
        "refusal": result.refusal,
    }


def _citation(citation: Citation) -> dict[str, object]:
    return {
        "raw": citation.raw,
        "line": citation.line,
        "repo": citation.repo,
        "path": citation.path,
        "start": citation.start,
        "end": citation.end,
        "status": citation.status,
        "candidates": [{"repo": c.repo, "path": c.path} for c in citation.candidates],
    }


def citations_payload(result: WikiCitations) -> dict[str, object]:
    """`/v1/wiki/citations`: a page's code citations, body order, body-relative lines."""
    return {
        "id": result.id,
        "citations": [_citation(citation) for citation in result.citations],
        "refusal": result.refusal,
    }


def bootstrap_payload(result: WorkspaceInit) -> dict[str, object]:
    """Render workspace initialization without exposing installer internals.

    `written` and `deleted` are both read off `diff()`'s line vocabulary
    (`+ ` and `- `), so the JSON can never disagree with the text rendering.
    """
    lines = result.diff().splitlines()
    written = list(dict.fromkeys(line[2:] for line in lines if line.startswith("+ ")))
    deleted = list(dict.fromkeys(line[2:] for line in lines if line.startswith("- ")))
    return {
        "ok": result.ok,
        "changed": result.changed,
        "workspace": str(result.layout.root),
        "bundle_dir": str(result.layout.bundle_dir),
        "config_dir": str(result.layout.config_dir),
        "cache_dir": str(result.layout.cache_dir),
        "created": [str(path) for path in result.created],
        "written": written,
        "deleted": deleted,
    }


def bootstrap_plan_payload(plan: WorkspacePlan) -> dict[str, object]:
    """Render a previewed workspace initialization, keyed like `bootstrap_payload`.

    A sibling rather than a branch inside that function: it takes a
    `WorkspaceInit`, and a plan is a different type with a different vocabulary
    (`is_empty` rather than `changed`). Keeping the key names aligned is what lets
    one caller read either shape — `created` / `written` become `planned`, because
    a plan has created and written nothing.

    `planned` is not shaped like `written`. `written` is a deduped list of bare
    filenames; `planned` is `plan.diff()` split into lines, so it keeps the `+ `,
    `= ` and `! ` markers, is not deduped, and mixes additions, skips and refusals
    in one list. That is deliberate: it makes `--dry-run --json`'s `planned` agree
    line for line with what `--dry-run` prints, and a preview that disagreed with
    its own text rendering would be worse than one that does not match `written`.
    """
    return {
        "ok": plan.ok,
        "changed": not plan.is_empty,
        "workspace": str(plan.layout.root),
        "bundle_dir": str(plan.layout.bundle_dir),
        "config_dir": str(plan.layout.config_dir),
        "cache_dir": str(plan.layout.cache_dir),
        "planned": plan.diff().splitlines(),
    }


def _mirror_keys(structural: StructuralSummary) -> dict[str, object]:
    """The mirror lane's additive block, shared by both scan payloads.

    One function rather than two literal dicts: `scan_normal_payload` and
    `scan_emit_payload` describe the same structural pass, and a key added to
    one and forgotten in the other is the reporting gap D3 closes.
    """
    mirror = structural.mirror
    return {
        "mirror_created": mirror.created,
        "mirror_updated": mirror.regenerated,
        "mirror_moved": mirror.moved,
        "mirror_deleted": mirror.deleted,
        "mirror_declined": mirror.declined,
        "mirror_stranded": mirror.stranded,
        "mirror_skipped_repos": list(mirror.skipped_repos),
        "mirror_errors": [f"{repo}: {error}" for repo, error in mirror.failed_repos],
    }


def scan_normal_payload(result: ScanResult) -> dict[str, object]:
    """Render a local, complete scan result."""
    return {
        "ok": result.ok,
        "short_head": result.worklist.short_head,
        "entities_written": list(result.structural.entities.written),
        "entities_created": list(result.structural.entities.created),
        "entities_updated": list(result.structural.entities.updated),
        "entities_deleted": list(result.structural.entities.deleted),
        "narrated": result.applied.narrated,
        "sections_filled": result.applied.sections_filled,
        "stamped": result.applied.stamped,
        "entity_errors": list(result.errors),
        **_mirror_keys(result.structural),
    }


def scan_emit_payload(
    *,
    worklist_path: Path,
    briefs_dir: Path,
    results_dir: Path,
    short_head: str,
    structural: StructuralSummary,
) -> dict[str, object]:
    """Render the artifact locations produced by an emitted scan."""
    return {
        "worklist_path": str(worklist_path),
        "briefs_dir": str(briefs_dir),
        "results_dir": str(results_dir),
        "short_head": short_head,
        "entities_written": list(structural.entities.written),
        "entities_created": list(structural.entities.created),
        "entities_updated": list(structural.entities.updated),
        "entities_deleted": list(structural.entities.deleted),
        "entity_errors": list(structural.errors),
        **_mirror_keys(structural),
    }


def scan_apply_payload(result: ApplyResult) -> dict[str, object]:
    """Render durable scan application counts, excluding ``dry_run``."""
    return {
        "narrated": result.narrated,
        "sections_filled": result.sections_filled,
        "stamped": result.stamped,
        "entity_errors": list(result.entity_errors),
    }


def ingest_brief_payload(brief: DocumentBrief) -> dict[str, object]:
    """Render a computed ingest brief. Nothing was written for this payload."""
    return brief.as_data()


def ingest_payload(result: IngestResult) -> dict[str, object]:
    """Render ingest output while keeping suggestion execution state private."""
    warnings: list[str] = []
    if not result.frontmatter_parsed:
        warnings.append("ingestor response frontmatter was not parsed; fallback values were used")
    suggestion_error = result.proposal_status.get("error")
    if suggestion_error:
        warnings.append(f"suggestion phase degraded: {suggestion_error}")
    warnings.extend(f"suggestion apply failed: {entry}" for entry in (result.proposal_status.get("failed") or ()))
    warnings.extend(f"suggestion apply errored: {entry}" for entry in (result.proposal_status.get("errored") or ()))
    proposals = [
        {key: row.get(key) for key in ("lane", "title", "target", "proposal", "status")} for row in result.proposals
    ]
    return {
        "ok": result.ok,
        "page": result.page,
        "copy": result.copy,
        "title": result.title,
        "source_kind": result.source_kind,
        "entity_uri": result.entity_uri,
        "entity_page": result.entity_page,
        "frontmatter_parsed": result.frontmatter_parsed,
        "written": list(result.written),
        "indexes_updated": list(result.indexes_updated),
        "proposals": proposals,
        "warnings": warnings,
        "refusals": list(result.refusals),
    }


def query_brief_payload(brief: QueryBrief) -> dict[str, object]:
    """Render a claude_code-backend query brief: retrieval only, no answer."""
    return {
        "query": brief.query,
        "top_pages": [
            {"path": page.path, "excerpt": page.excerpt, "search_scores": dict(page.search_scores)}
            for page in brief.top_pages
        ],
    }


def query_payload(result: QueryResult) -> dict[str, object]:
    """Render a completed bedrock/vercel query result."""
    return {
        "answer": result.answer,
        "citations": list(result.citations),
        "pages_drilled": result.pages_drilled,
        "search_scores": {page: dict(scores) for page, scores in result.search_scores.items()},
        "path": result.path,
        "fallback_error": result.fallback_error,
    }


def lint_payload(report: LintReport) -> dict[str, object]:
    """Render both lint streams with their field-level public contracts."""
    mechanical = [
        {
            "lane": lane.name,
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                    "spec": finding.spec,
                    "path": finding.path,
                    "line": finding.line,
                }
                for finding in lane.report.findings
            ],
        }
        for lane in report.mechanical
    ]
    semantic = [
        {"group": item.group, "message": item.message, "page": item.page, "model": item.model}
        for item in report.semantic
    ]
    backlog = report.open_proposals
    return {
        "ok": report.ok,
        "mechanical": mechanical,
        "semantic": semantic,
        "open_proposals": {
            "count": backlog.count,
            "oldest": None if backlog.oldest is None else backlog.oldest.isoformat(),
            "malformed": backlog.malformed,
            "ages": dict(backlog.ages),
        },
        "errors": list(report.errors),
    }


def drift_brief_payload(brief: DriftBrief) -> dict[str, object]:
    """Render a claude_code-backend drift brief: candidates and targets, no verdicts."""
    return {
        "targets": [
            {
                "concept_id": target.concept_id,
                "title": target.title,
                "kind": target.kind,
                "candidates": [
                    {
                        "concept_id": c.concept_id,
                        "resource": c.resource,
                        "title": c.title,
                        "narrative": c.narrative,
                        "last_updated_commit": c.last_updated_commit,
                        "changed_files": list(c.changed_files),
                    }
                    for c in target.candidates
                ],
            }
            for target in brief.targets
        ],
    }


def drift_payload(result: PropagateResult) -> dict[str, object]:
    """Render a completed bedrock/vercel drift propagation run."""
    return {
        "entities_considered": result.entities_considered,
        "pages_judged": result.pages_judged,
        "pages_stale": result.pages_stale,
        "pages_skipped_settled": result.pages_skipped_settled,
        "findings": [
            {
                "target": f.target,
                "target_title": f.target_title,
                "entity_id": f.entity_id,
                "entity_title": f.entity_title,
                "detected_commit": f.detected_commit,
                "rationale": f.rationale,
            }
            for f in result.findings
        ],
        "plans": [
            {
                "target": plan.target,
                "proposal": plan.proposal,
                "ok": plan.ok,
                "is_empty": plan.is_empty,
                "refusals": [{"path": r.path, "kind": r.kind, "detail": r.detail} for r in plan.refusals],
            }
            for plan in result.plans
        ],
        "errors": list(result.errors),
        "dry_run": result.dry_run,
    }


def stats_payload(stats: WikiStats) -> dict[str, object]:
    """Render structural graph statistics with explicit hub rows."""

    def hub(entry: HubEntry) -> dict[str, object]:
        return {"page": entry.page, "degree": entry.degree}

    return {
        "total_pages": stats.total_pages,
        "total_edges": stats.total_edges,
        "component_count": stats.component_count,
        "top_outbound_hubs": [hub(entry) for entry in stats.top_outbound_hubs],
        "top_inbound_hubs": [hub(entry) for entry in stats.top_inbound_hubs],
        "orphans": list(stats.orphans),
        "sinks": list(stats.sinks),
    }


def proposal_payload(proposal: Proposal, *, mode: str) -> dict[str, object]:
    """Render one parsed proposal without its internal identity fields.

    *mode* (`create` or `update`) is derived by core from the bundle, which
    wire never reads.
    """
    return {
        "member": proposal.member,
        "target": proposal.target,
        "title": proposal.title,
        "description": proposal.description,
        "page_status": proposal.page_status,
        "mode": mode,
        "sources": [dict(source) for source in proposal.sources],
        "verified": [dict(entry) for entry in proposal.verified],
        "malformed": proposal.malformed,
    }


def _proposal_refusal(refusal: ProposalRefusal) -> dict[str, object]:
    return {"path": refusal.path, "kind": refusal.kind, "detail": refusal.detail}


def _proposal_write(write: Write) -> dict[str, object]:
    """Planned key assignments only: body text and a create's whole document are never projected."""
    return {"member": write.member, "mode": write.mode, "frontmatter": jsonable(dict(write.frontmatter))}


def _proposal_application(result: ProposalApplyResult | None) -> dict[str, object]:
    """The mutation keys shared by proposal decision and filing projections."""
    return {
        "written": [] if result is None else list(result.written),
        "applied": result is not None,
        "rolled_back": False,
        "failures": []
        if result is None
        else [f"{failure.path}: {failure.kind} -- {failure.error}" for failure in result.failed],
    }


def proposal_decide_payload(run: ProposalDecideRun) -> dict[str, object]:
    """`gw wiki proposal approve|reject --json`, and serve's decide route."""
    return {
        "target": run.target,
        "proposal": run.proposal,
        "decision": run.decision,
        "ok": run.ok,
        "refusals": [_proposal_refusal(refusal) for refusal in run.refusals],
        "writes": [] if run.plan is None or run.refusals else [_proposal_write(write) for write in run.plan.writes],
        **_proposal_application(run.result),
    }


def proposal_file_payload(run: ProposalFileRun) -> dict[str, object]:
    """`gw wiki proposal file --json`."""
    return {
        "lane": run.lane,
        "target": run.target,
        "proposal": run.proposal,
        "ok": run.ok,
        "refusals": [_proposal_refusal(refusal) for refusal in run.refusals],
        "writes": [] if run.refusals else [_proposal_write(write) for write in run.plan.writes],
        **_proposal_application(run.result),
    }


def tag_inventory_payload(result: TagInventory) -> dict[str, object]:
    """Every tag the vault carries, with page counts sorted by tag."""
    return {
        "total_tags": len(result.counts),
        "tagged_pages": len({concept for ids in result.concepts.values() for concept in ids}),
        "untagged_pages": len(result.untagged),
        "counts": dict(sorted(result.counts.items())),
        "skipped": [{"path": s.path, "reason": s.reason, "detail": s.detail} for s in result.skipped],
    }


def tags_undeclared_payload(missing: Sequence[str]) -> dict[str, object]:
    """The gate's answer: every tag the vocabulary does not know."""
    return {"undeclared": list(missing)}


def proposals_payload(listings: Sequence[ProposalListing]) -> list[dict[str, object]]:
    """`gw wiki proposals --json` and `/v1/wiki/proposals`."""
    return [proposal_payload(listing.proposal, mode=listing.mode) for listing in listings]


def _tree_node(node: TreeNode) -> dict[str, object]:
    return {
        "heading": node.heading,
        "level": node.level,
        "generated": node.generated,
        "pages": [{"id": page.id, "title": page.title, "type": page.type} for page in node.pages],
        "sections": [_tree_node(child) for child in node.children],
    }


def wiki_tree_payload(tree: WikiTree) -> dict[str, object]:
    """`/v1/wiki/tree`: the root index's sections, `###` nested under `##`."""
    return {"sections": [_tree_node(node) for node in tree.sections]}
