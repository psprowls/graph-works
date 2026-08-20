"""Explicit JSON payloads for the public wiki command surface."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.ingest.commands import IngestResult
from graph_works_core.lint_drift.lint import LintReport
from graph_works_core.scan.commands import ScanResult, StructuralSummary
from graph_works_core.scan.scan_contract import ApplyResult
from graph_works_core.wiki_stats.commands import HubEntry, WikiStats
from graph_works_core.workspace.init import WorkspaceInit
from okf_ext.proposals import Proposal


def bootstrap_payload(result: WorkspaceInit) -> dict[str, object]:
    """Render workspace initialization without exposing installer internals."""
    written = list(dict.fromkeys(line[2:] for line in result.diff().splitlines() if line.startswith("+ ")))
    return {
        "ok": result.ok,
        "changed": result.changed,
        "workspace": str(result.layout.root),
        "bundle_dir": str(result.layout.bundle_dir),
        "config_dir": str(result.layout.config_dir),
        "cache_dir": str(result.layout.cache_dir),
        "created": [str(path) for path in result.created],
        "written": written,
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
        "mirror_skipped_repos": list(mirror.skipped_repos),
        "mirror_errors": [f"{repo}: {error}" for repo, error in mirror.failed_repos],
    }


def scan_normal_payload(result: ScanResult) -> dict[str, object]:
    """Render a local, complete scan result."""
    return {
        "ok": result.ok,
        "short_head": result.worklist.short_head,
        "entities_written": list(result.structural.entities.written),
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
        "entities_deleted": list(structural.entities.deleted),
        "entity_errors": [f"{path}: {kind}" for path, kind in structural.entities.catalog_declined],
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


def proposal_payload(proposal: Proposal) -> dict[str, object]:
    """Render one parsed proposal without its internal identity fields."""
    return {
        "member": proposal.member,
        "target": proposal.target,
        "title": proposal.title,
        "description": proposal.description,
        "page_status": proposal.page_status,
        "sources": [dict(source) for source in proposal.sources],
        "verified": [dict(entry) for entry in proposal.verified],
        "malformed": proposal.malformed,
    }
