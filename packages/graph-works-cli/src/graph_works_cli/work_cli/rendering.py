"""Explicit JSON projections, dense human renderers, and the emit/exit policy
for `gw work`.

`dataclasses.asdict()` is **not** the public contract (spec 2.6): a core
dataclass gaining a field would silently widen the CLI's JSON, and a core
field being renamed would silently break it. One projection per result family,
written out key by key, is what freezes the CLI surface independently of the
internal shapes.

Human output is bespoke here rather than routed through
`code_graph_io.render`: that module's spine is a graph entity, and a routing
decision, a rollup and a reconciliation context are none of those.

JSON occupies stdout alone; warnings and errors go to stderr in every mode,
and a failed command prints no partial JSON document.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Never

import typer
from graph_works_core.archive.commands import ArchiveRun
from graph_works_core.orchestrate.commands import OrchestrateResult, StageAdvance
from graph_works_core.work.commands import (
    AdoptChildSpecsResult,
    ChildRollup,
    Decision,
    DecisionCommandResult,
    FilingOutcome,
    NextResult,
    OverturnResult,
    StatusReport,
    Transition,
)
from graph_works_core.work.reconcile import ReconcileContext
from okf_io import IndexUpdate
from okf_io.validate import Finding, Report
from subagents_io.dispatch import WorktreeAction

from graph_works_cli import exit_codes

# ---------------------------------------------------------------------------
# Emit / exit policy
# ---------------------------------------------------------------------------


def emit(payload: object) -> None:
    """One JSON document on stdout, and nothing else."""
    typer.echo(json.dumps(payload, indent=2))


def fail(message: str, *, code: int = exit_codes.GENERIC, cause: BaseException | None = None) -> Never:
    """Write one user-facing error to stderr and stop the current command."""
    typer.echo(f"Error: {message}", err=True)
    if cause is None:
        raise typer.Exit(code=code)
    raise typer.Exit(code=code) from cause


def warn(message: str) -> None:
    """One diagnostic line on stderr, in both human and JSON mode."""
    typer.echo(f"[warn] {message}", err=True)


def split_csv(value: str) -> list[str]:
    """Split a comma-separated option value into a trimmed, non-empty list."""
    return [part.strip() for part in value.split(",") if part.strip()]


def echo_wrapped(prefix: str, text: str) -> None:
    """Echo `prefix + text`, hanging-indenting continuation lines under it.

    A multi-edge dependency blocker arrives as one string with its fragments on
    their own lines. Raw, every fragment sits at the string's own indent, well
    left of the first line, and reads as if it belonged to the next item.
    Human rendering only -- JSON is unaffected.
    """
    first, *rest = text.split("\n")
    typer.echo(f"{prefix}{first}")
    pad = " " * len(prefix)
    for line in rest:
        typer.echo(f"{pad}{line.strip()}")


# ---------------------------------------------------------------------------
# Shared fragments
# ---------------------------------------------------------------------------


def _transition(transition: Transition | None) -> dict[str, Any] | None:
    """`Transition` -> the three keys the plugin contract reads, plus its two
    unresolved requests. `None` stays `None`: "this stage completes nothing"
    is a distinguishable answer from "it completes with no changes"."""
    if transition is None:
        return None
    return {
        "phase": transition.phase,
        "status": transition.workflow_status,
        "document_status": transition.document_status,
        "requires": list(transition.requires),
        "sync_plan_table": transition.sync_plan_table,
        "stamp_source": transition.stamp_source,
    }


def _rollup(rollup: ChildRollup | None) -> dict[str, Any] | None:
    if rollup is None:
        return None
    return {
        "total": rollup.total,
        "terminal": rollup.terminal,
        "open_slugs": list(rollup.open_slugs),
    }


def _finding(finding: Finding) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "message": finding.message,
        "spec": finding.spec,
        "path": finding.path,
        "line": finding.line,
    }


def _decision(entry: Decision) -> dict[str, Any]:
    return {
        "id": entry.id,
        "number": entry.number,
        "question": entry.question,
        "status": entry.status,
        "affects": list(entry.affects),
        "decided": entry.decided,
        "supersedes": entry.supersedes,
        "prose": entry.prose,
    }


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def normalized_payload(result: NextResult) -> dict[str, str] | None:
    """`{"spec_doc": <rel>}` for the selected item, plus `ancestor_spec_doc`
    when a `--descend` walk healed the requested item's pointer too.

    Keyed off `application.normalized` -- what actually persisted -- not off
    `normalizations`, which is the plan. A dry-run preview reports nothing
    normalized because nothing was.
    """
    persisted = set(result.application.normalized)
    if not persisted:
        return None
    payload: dict[str, str] = {}
    for change in result.normalizations:
        if change.slug not in persisted:
            continue
        key = "spec_doc" if change.slug == result.selected_slug else "ancestor_spec_doc"
        payload[key] = change.ref.rel
    return payload or None


def descent_payload(result: NextResult) -> dict[str, Any] | None:
    if result.descent is None:
        return None
    return {
        "from": result.requested_slug,
        "path": list(result.descent.path),
        "leaf": result.descent.leaf,
        "blocked_at": result.descent.blocked_at,
        "reason": result.descent.reason,
    }


def next_blockers(result: NextResult) -> list[str]:
    """The route's blockers, plus the descent's own refusal when one exists.

    A `--descend` that could not reach a leaf is a fact `route()` never sees:
    routing ran against the ancestor, so its blockers explain the ancestor and
    say nothing about why the walk stopped. The `--descend:` prefix is the
    marker the workflow skill matches on.
    """
    blockers = list(result.route.blockers)
    descent = result.descent
    if descent is not None and descent.leaf is None and descent.reason:
        blockers.append(f"--descend: {descent.reason}")
    return blockers


def next_payload(result: NextResult, *, bundle_root: Path, skill: str | None) -> dict[str, Any]:
    """The `gw work next` contract: phase, status, blockers, on_complete,
    action, normalized, child_rollup -- plus the donor-compatible additions
    `gw next` (C6) wraps."""
    dispatch = result.route.dispatch
    return {
        "slug": result.selected_slug,
        "status": result.state.workflow_status,
        "kind": result.state.type,
        "phase": result.state.phase,
        "effort": result.state.effort,
        "action": None if dispatch is None else {"skill": skill, "reason": result.route.reason},
        "artifact": None if result.artifact is None else {"path": str(result.artifact.path(bundle_root))},
        "on_dispatch": _transition(result.route.on_dispatch),
        "on_complete": _transition(result.route.on_complete),
        "blockers": next_blockers(result),
        "child_rollup": _rollup(result.child_rollup),
        "descent": descent_payload(result),
        "normalized": normalized_payload(result),
    }


def render_next(result: NextResult, payload: dict[str, Any]) -> None:
    normalized = payload["normalized"]
    if normalized:
        for key, value in normalized.items():
            typer.echo(f"[fix] stamped {key}: {value}")
    typer.echo(f"{payload['slug']}: kind={payload['kind']} status={payload['status']} phase={payload['phase']}")
    if payload["descent"]:
        typer.echo(f"  descent: {' -> '.join(payload['descent']['path'])}")
    if payload["action"]:
        typer.echo(f"  dispatch: {payload['action']['skill']} — {payload['action']['reason']}")
    if payload["artifact"]:
        typer.echo(f"  artifact: {payload['artifact']['path']}")
    for blocker in payload["blockers"]:
        echo_wrapped("  blocked: ", blocker)
    for warning in result.warnings:
        warn(warning)


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------


def _json_safe(value: object) -> object:
    """`FieldChange.before`/`after` mirror whatever type the field is written
    as -- `updated` is a raw `date` (advance.py writes it bare so ruamel does
    not requote it); every other field is already a JSON-native string."""
    return value.isoformat() if isinstance(value, date) else value


def advance_payload(result: StageAdvance, slug: str) -> dict[str, Any]:
    """The `gw work advance` contract: phase, status, blockers, on_complete."""
    plan = result.outcome.plan
    applied = {change.key: [_json_safe(change.before), _json_safe(change.after)] for change in plan.changes}
    stamped: dict[str, str] = {}
    if result.outcome.stamped is not None:
        assert result.outcome.stamped.source_id is not None
        stamped[result.outcome.stamped.source_id] = result.outcome.stamped.resource
    phase = plan.transition.phase if plan.transition is not None else None
    status = plan.transition.workflow_status if plan.transition is not None else None
    return {
        "slug": slug,
        "phase": phase,
        "status": status,
        "blockers": list(plan.route.blockers),
        "on_complete": _transition(plan.route.on_complete),
        "applied": applied,
        "stamped": stamped,
        "plan_row": result.outcome.plan_row,
        "changed": result.outcome.changed,
        "refusal": None if plan.refusal is None else {"reason": plan.refusal, "detail": plan.detail},
        "results_path": None if result.results_path is None else str(result.results_path),
        "pointer_path": None if result.pointer_path is None else str(result.pointer_path),
        "repo_note": result.repo_note,
    }


def render_advance(payload: dict[str, Any]) -> None:
    typer.echo(f"[ok] {payload['slug']}: phase={payload['phase']} status={payload['status']}")
    for key, change in payload["applied"].items():
        typer.echo(f"  {key}: {change[0]!r} -> {change[1]!r}")
    for key, value in payload["stamped"].items():
        typer.echo(f"  stamped {key}: {value}")
    if payload["results_path"]:
        typer.echo(f"  results: {payload['results_path']}")
    for blocker in payload["blockers"]:
        echo_wrapped("  blocked: ", blocker)
    if payload["repo_note"]:
        warn(payload["repo_note"])


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


def file_payload(outcome: FilingOutcome) -> dict[str, Any]:
    plan = outcome.plan
    return {
        "slug": plan.filing.slug,
        "page_path": str(plan.filing.target),
        "work_directory": str(plan.filing.work_directory),
        "refusal": plan.refusal,
        "detail": plan.filing.detail,
        "indexes": [update.path for update in outcome.application.indexes if update.changed],
        "logged": None if outcome.application.log is None else outcome.application.log.entry,
        "warnings": list(plan.warnings),
    }


# ---------------------------------------------------------------------------
# status / lint / regen-index
# ---------------------------------------------------------------------------


def status_payload(report: StatusReport) -> dict[str, Any]:
    resume = report.resume
    return {
        "total": report.rollup.total,
        "by_workflow_status": dict(report.rollup.by_workflow_status),
        "by_type": dict(report.rollup.by_type),
        "by_phase": dict(report.rollup.by_phase),
        "children": {slug: _rollup(rolled) for slug, rolled in report.rollup.children.items()},
        "resume": None
        if resume is None
        else {
            "primary": {"slug": resume.primary.slug, "title": resume.primary.title},
            "alternatives": [{"slug": item.slug, "title": item.title} for item in resume.alternatives],
        },
    }


def render_status(payload: dict[str, Any]) -> None:
    typer.echo(f"{payload['total']} item(s) under work/")
    for label, key in (
        ("workflow_status", "by_workflow_status"),
        ("type", "by_type"),
        ("phase", "by_phase"),
    ):
        rendered = ", ".join(f"{name} {count}" for name, count in payload[key].items())
        typer.echo(f"  by {label}: {rendered or '-'}")
    for slug, rolled in payload["children"].items():
        typer.echo(f"  children {slug}: {rolled['terminal']}/{rolled['total']} terminal")
    resume = payload["resume"]
    if resume is not None:
        typer.echo(f"  resume: {resume['primary']['slug']} — {resume['primary']['title']}")
        for alternative in resume["alternatives"]:
            typer.echo(f"    alt: {alternative['slug']} — {alternative['title']}")


def lint_payload(report: Report) -> dict[str, Any]:
    return {"ok": report.ok, "findings": [_finding(finding) for finding in report.findings]}


def render_lint(report: Report) -> None:
    """Errors to stderr, warnings to stdout -- so a piped lint carries only
    what the reader asked for."""
    for finding in report.findings:
        line = f"{finding.severity:<6} {finding.code}: {finding.message}"
        typer.echo(line, err=finding.severity == "error")


def regen_index_payload(update: IndexUpdate) -> dict[str, Any]:
    return {
        "path": update.path,
        "changed": update.changed,
        "created": update.created,
        "added": [change.target for change in update.changes if change.kind == "add"],
        "removed": [change.target for change in update.changes if change.kind == "remove"],
        "drift": [drift.target for drift in update.drift],
    }


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


def archive_payload(run: ArchiveRun, *, dry_run: bool) -> dict[str, Any]:
    result = run.result
    return {
        "dry_run": dry_run,
        "ok": run.ok,
        "planned": list(run.plan.slugs),
        "conflict": list(run.conflict),
        "archived": [] if result is None else list(result.archived),
        "pruned": [] if result is None else list(result.pruned),
        "skipped": [
            {"slug": skip.slug, "reason": skip.reason, "detail": skip.detail}
            for skip in (run.plan.skipped if result is None else result.skipped)
        ],
        "indexes": [] if result is None else [update.path for update in result.indexes if update.changed],
        "pointer_cleared": run.pointer_cleared,
        "logged": run.logged,
    }


# ---------------------------------------------------------------------------
# adopt-child-specs
# ---------------------------------------------------------------------------


def adopt_payload(result: AdoptChildSpecsResult) -> dict[str, Any]:
    plan = result.plan
    return {
        "epic_slug": plan.epic_slug,
        "refusal": plan.refusal,
        "adopted": [
            {
                "child": move.child_slug,
                "from": None if move.draft is None else str(move.draft),
                "spec_doc": move.source_ref.rel,
            }
            for move in plan.adopted
        ],
        "orphaned_drafts": [str(path) for path in plan.orphaned],
        "unseeded_children": list(plan.unseeded),
        "ambiguous": [
            {"stem": str(entry.draft), "candidates": list(entry.candidate_slugs)} for entry in plan.ambiguous
        ],
        "moved": [str(path) for path in result.application.moved],
        "registered": list(result.application.registered),
        "warnings": [*plan.warnings, *result.application.warnings],
    }


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def decision_payload(result: DecisionCommandResult) -> dict[str, Any]:
    plan = result.plan
    return {
        "epic_slug": result.owner.epic_slug,
        "resolved_from": result.owner.redirected_from,
        "ledger_path": str(result.owner.ledger),
        "entry": None if plan is None or plan.primary is None else _decision(plan.primary),
        "superseded": None if plan is None or plan.superseded is None else plan.superseded.id,
        "refusal": None if plan is None else plan.refusal,
        "written": result.application.written,
        "stale": result.application.stale,
        "entries": [_decision(entry) for entry in result.entries],
        "counts": dict(result.counts),
        "warnings": list(result.warnings),
    }


def overturn_payload(result: OverturnResult) -> dict[str, Any]:
    decision = result.plan.decision
    filing = result.plan.filing
    return {
        "epic_slug": result.owner.epic_slug,
        "resolved_from": result.owner.redirected_from,
        "ledger_path": str(result.owner.ledger),
        "entry": None if decision.primary is None else _decision(decision.primary),
        "superseded": None if decision.superseded is None else decision.superseded.id,
        "refusal": result.plan.refusal,
        "written": result.application.decision.written,
        "stale": result.application.decision.stale,
        "follow_up": {"slug": filing.filing.slug, "page_path": str(filing.filing.target)},
        "follow_up_filed": bool(result.application.filing.indexes) or result.application.decision.written,
        "warnings": list(result.warnings),
    }


def render_decision_write(payload: dict[str, Any], verb: str) -> None:
    resolved = f" (resolved from {payload['resolved_from']})" if payload["resolved_from"] else ""
    typer.echo(f"[ok] ledger: {payload['epic_slug']}{resolved}")
    if payload["entry"]:
        typer.echo(f"[ok] {verb} {payload['entry']['id']}  status={payload['entry']['status']}")
    if payload["superseded"]:
        typer.echo(f"[ok] superseded {payload['superseded']}")
    follow_up = payload.get("follow_up")
    if follow_up:
        typer.echo(f"[ok] follow-up {follow_up['slug']}: {follow_up['page_path']}")
    for warning in payload["warnings"]:
        warn(warning)


def render_decision_list(payload: dict[str, Any]) -> None:
    resolved = f" (resolved from {payload['resolved_from']})" if payload["resolved_from"] else ""
    typer.echo(f"[ok] ledger: {payload['epic_slug']}{resolved}  {payload['ledger_path']}")
    for entry in payload["entries"]:
        question = f" — {entry['question']}" if entry["question"] else ""
        typer.echo(f"  {entry['id']}  {entry['status'] or '(no status)'}{question}")
    typer.echo("  counts: " + ", ".join(f"{key}={value}" for key, value in payload["counts"].items()))
    for warning in payload["warnings"]:
        warn(warning)


# ---------------------------------------------------------------------------
# orchestrate
# ---------------------------------------------------------------------------


def _worktree(action: WorktreeAction) -> dict[str, Any]:
    return {
        "action": action.action,
        "path": action.path,
        "branch": action.branch,
        "base_branch": action.base_branch,
        "exists": action.exists,
    }


def orchestrate_payload(result: OrchestrateResult) -> dict[str, Any]:
    return {
        "slug": result.slug,
        "terminal": result.terminal,
        "max_parallel": result.max_parallel,
        "slots_free": result.slots_free,
        "permission_mode": result.permission_mode,
        "live": list(result.live),
        "dispatches": [
            {
                "key": dispatch.key,
                "slug": dispatch.slug,
                "phase": dispatch.phase,
                "kind": dispatch.kind,
                "effort": dispatch.effort,
                "skill": dispatch.skill,
                "mode": dispatch.mode,
                "model": dispatch.model,
                "reasoning_effort": dispatch.reasoning_effort,
                "worktree": _worktree(dispatch.worktree),
                "merge_target": dispatch.merge_target,
                "prompt": dispatch.prompt,
            }
            for dispatch in result.dispatches
        ],
        "advances": [
            {"slug": advance.slug, "reason": advance.reason, "worktree": advance.worktree, "branch": advance.branch}
            for advance in result.advances
        ],
        "blocked": [{"slug": item.slug, "kind": item.kind, "reason": item.reason} for item in result.blocked],
        "decisions": {
            "epic_slug": result.decisions_epic_slug,
            "ledger_path": result.decisions_ledger_path,
            "open": [_decision(entry) for entry in result.open_decisions],
            "assumed": [_decision(entry) for entry in result.assumed_decisions],
            "counts": dict(result.decision_counts),
        },
        "warnings": list(result.warnings),
    }


def render_orchestrate(payload: dict[str, Any]) -> None:
    free = payload["slots_free"]
    max_p = payload["max_parallel"]
    typer.echo(f"{payload['slug']}: terminal={payload['terminal']} slots_free={free}/{max_p}")
    for dispatch in payload["dispatches"]:
        typer.echo(
            f"  dispatch {dispatch['key']}: {dispatch['skill']} mode={dispatch['mode']} "
            f"model={dispatch['model']} worktree={dispatch['worktree']['action']}"
        )
    for advance in payload["advances"]:
        typer.echo(f"  advance {advance['slug']}: {advance['reason']}")
    for blocked in payload["blocked"]:
        echo_wrapped(f"  blocked {blocked['slug']} ({blocked['kind']}): ", blocked["reason"])
    for entry in payload["decisions"]["open"]:
        typer.echo(f"  open decision {entry['id']}: {entry['question']}")
    for warning in payload["warnings"]:
        warn(warning)


# ---------------------------------------------------------------------------
# reconcile-context
# ---------------------------------------------------------------------------


def reconcile_payload(context: ReconcileContext) -> dict[str, Any]:
    """All fourteen donor fields, at their donor names and nesting."""
    return {
        "epic_slug": context.epic_slug,
        "slug": context.slug,
        "spec_path": context.spec_path,
        "spec_anchor_commit": context.spec_anchor_commit,
        "anchor_source": context.anchor_source,
        "commit_range": context.commit_range,
        "landed_siblings": [
            {"slug": sibling.slug, "resolved_in": sibling.resolved_in, "affects": list(sibling.affects)}
            for sibling in context.landed_siblings
        ],
        "touched_paths": list(context.touched_paths),
        "commits_since": [{"sha": commit.sha, "subject": commit.subject} for commit in context.commits_since],
        "cited_decisions": [
            {"id": cited.id, "status": cited.status, "question": cited.question} for cited in context.cited_decisions
        ],
        "contradictions": [
            {"id": cited.id, "status": cited.status, "question": cited.question} for cited in context.contradictions
        ],
        "has_open_decision": context.has_open_decision,
        "diff_command": context.diff_command,
        "warnings": list(context.warnings),
    }


def render_reconcile(payload: dict[str, Any]) -> None:
    typer.echo(f"{payload['slug']}: epic={payload['epic_slug'] or '-'}")
    typer.echo(f"  spec: {payload['spec_path']}")
    typer.echo(f"  anchor: {payload['spec_anchor_commit'] or '-'} ({payload['anchor_source']})")
    typer.echo(f"  range: {payload['commit_range'] or '-'}")
    typer.echo(f"  touched: {', '.join(payload['touched_paths']) or '-'}")
    for sibling in payload["landed_siblings"]:
        typer.echo(f"  landed: {sibling['slug']} resolved_in {sibling['resolved_in']}")
    for commit in payload["commits_since"]:
        typer.echo(f"  commit: {commit['sha'][:8]} {commit['subject']}")
    for cited in payload["cited_decisions"]:
        typer.echo(f"  cites: {cited['id']} status={cited['status']}")
    for conflict in payload["contradictions"]:
        typer.echo(f"  CONTRADICTION: {conflict['id']} is superseded — hold, do not advance")
    if payload["has_open_decision"]:
        typer.echo("  held: an open decision already names this item")
    if payload["diff_command"]:
        typer.echo(f"  diff: {payload['diff_command']}")
    for warning in payload["warnings"]:
        warn(warning)
