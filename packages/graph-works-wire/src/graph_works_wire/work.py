"""Plain-data projections for `gw work` results.

`dataclasses.asdict()` is **not** the public contract (spec 2.6): a core
dataclass gaining a field would silently widen every interface's JSON, and a
core field being renamed would silently break it. One projection per result
family, written out key by key, is what freezes the surface independently of
the internal shapes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol, cast

from graph_works_core.archive.commands import ArchiveRun
from graph_works_core.orchestrate.commands import OrchestrateResult
from graph_works_core.orchestrate.placement import PlacementRecord
from graph_works_core.orchestrate.stage_advance import StageAdvance
from graph_works_core.work.commands import (
    ActiveWorkTouch,
    ChildRollup,
    Decision,
    DecisionCommandResult,
    DispatchExplanation,
    FilingRun,
    IngestQueueReport,
    ItemRead,
    NextResult,
    OpenDecision,
    OverturnResult,
    PathMutationResult,
    QueueEntry,
    RegenIndexesResult,
    StatusReport,
    Transition,
    WorkItem,
)
from graph_works_core.work.reconcile import ReconcileContext
from graph_works_core.workspace.dispatch import DispatchResolution
from graph_works_core.workspace.finish import FinishTarget

from graph_works_wire._jsonable import jsonable
from graph_works_wire.config import rule_payload

# ---------------------------------------------------------------------------
# Shared fragments
# ---------------------------------------------------------------------------


def item_payload(result: ItemRead) -> dict[str, Any]:
    """`/v1/work/item`: one work item read whole."""
    return {
        "path": result.path,
        "frontmatter": jsonable(dict(result.frontmatter)),
        "body": result.body,
        "sources": [{"id": source.id, "resource": source.resource, "title": source.title} for source in result.sources],
        "references": list(result.references),
        "parse_error": result.parse_error,
        "coercion_failures": list(result.coercion_failures),
        "refusal": result.refusal,
        "detail": result.detail,
    }


def _transition(transition: Transition | None) -> dict[str, Any] | None:
    """`Transition` -> the three keys the plugin contract reads, plus its two
    unresolved requests. `None` stays `None`: "this stage completes nothing"
    is a distinguishable answer from "it completes with no changes"."""
    if transition is None:
        return None
    return {
        "phase": transition.phase,
        "work_status": transition.work_status,
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
        "open_paths": list(rollup.open_paths),
    }


class _FindingView(Protocol):
    code: str
    severity: str
    message: str
    spec: str
    path: str | None
    line: int | None


class _ReportView(Protocol):
    ok: bool
    findings: tuple[_FindingView, ...]


def _finding(finding: _FindingView) -> dict[str, Any]:
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
        "hold": entry.hold,
        "phase": entry.phase,
        "checkpoint": entry.checkpoint,
        "prose": entry.prose,
    }


class _HoldView(Protocol):
    path: str
    owner_path: str
    ledger_path: str
    decision: Decision


def _hold(value: object) -> dict[str, Any]:
    hold = cast(_HoldView, value)
    return {
        "path": hold.path,
        "owner_path": hold.owner_path,
        "ledger_path": hold.ledger_path,
        "decision": _decision(hold.decision),
    }


class _RefusalView(Protocol):
    path: str
    kind: str
    detail: str


class _ApplicationView(Protocol):
    rolled_back: bool
    failures: tuple[str, ...]


class _WorktreeView(Protocol):
    action: str
    path: str
    branch: str
    base_branch: str | None
    exists: bool | None
    parent_path: str | None


def _refusal(value: object) -> dict[str, str]:
    refusal = cast(_RefusalView, value)
    return {
        "path": str(refusal.path),
        "kind": str(refusal.kind),
        "detail": str(refusal.detail),
    }


def _application(application: object | None) -> dict[str, Any]:
    viewed = None if application is None else cast(_ApplicationView, application)
    return {
        "applied": viewed is not None,
        "rolled_back": False if viewed is None else viewed.rolled_back,
        "failures": [] if viewed is None else list(viewed.failures),
    }


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def normalized_payload(result: NextResult) -> list[dict[str, str]] | None:
    """Persisted managed-source repairs, each identified by canonical path.

    Keyed off `application.normalized` -- what actually persisted -- not off
    `normalizations`, which is the plan. A dry-run preview reports nothing
    normalized because nothing was.
    """
    persisted = set(result.application.normalized)
    if not persisted:
        return None
    payload: list[dict[str, str]] = []
    for change in result.normalizations:
        if change.path not in persisted:
            continue
        payload.append(
            {
                "path": change.path,
                "source_id": str(change.ref.source_id),
                "resource": change.ref.resource,
            }
        )
    return payload or None


def descent_payload(result: NextResult) -> dict[str, Any] | None:
    if result.descent is None:
        return None
    return {
        "from": result.requested_path,
        "path": list(result.descent.path),
        "leaf": result.descent.leaf,
        "blocked_at": result.descent.blocked_at,
        "reason": result.descent.reason,
    }


def dispatch_payload(resolution: DispatchResolution) -> dict[str, Any]:
    profile = resolution.profile
    return {
        "profile": {
            "skill": profile.skill,
            "mode": profile.mode,
            "prompt_tail": profile.prompt_tail,
            "agent": profile.agent,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
        },
        "provenance": {
            field: {
                "rule": {"source": origin.rule.source, "index": origin.rule.index, "name": origin.rule.name},
                "reason": origin.reason,
            }
            for field, origin in resolution.provenance.items()
        },
    }


def next_blockers(result: NextResult) -> list[str]:
    """The route's blockers, plus the descent's own refusal and the dispatch
    preflight refusal when either exists.

    A `--descend` that could not reach a leaf is a fact `route()` never sees:
    routing ran against the ancestor, so its blockers explain the ancestor and
    say nothing about why the walk stopped. The `--descend:` prefix is the
    marker the workflow skill matches on.

    Configuration preflight is the same shape of thing one layer further out:
    `work-tracker-okf` declines the skill mapping by name, so `RouteResult`
    cannot know a configured skill is malformed. It is appended here for the
    same reason the descent refusal is -- a stop condition the routing table
    cannot see, surfaced through the channel the workflow skill already reads.
    """
    blockers = list(result.route.blockers)
    descent = result.descent
    if descent is not None and descent.leaf is None and descent.reason:
        blockers.append(f"--descend: {descent.reason}")
    if result.dispatch_preflight is not None:
        blockers.append(result.dispatch_preflight)
    return blockers


def _usable_resolution(result: NextResult) -> DispatchResolution | None:
    """The resolution `next_payload` and `work_queue_payload` may show.

    `None` when there is no dispatch, or when a preflight refused the profile
    the fold produced -- the route still has a dispatch, but the skill it
    names is unusable. One rule, so both projections agree by construction.
    """
    resolution = result.dispatch_resolution
    return None if resolution is None or result.dispatch_preflight is not None else resolution


def _usable_on_dispatch(result: NextResult) -> Transition | None:
    """The `on_dispatch` transition `next_payload` and `work_queue_payload` may show.

    `None` when a preflight refused the dispatch fold's profile -- the same
    condition `_usable_resolution` gates on, kept as its own helper because
    `on_dispatch` is `None`-able independently of whether a resolution exists.
    """
    return None if result.dispatch_preflight is not None else result.route.on_dispatch


def _finish_target(target: FinishTarget) -> dict[str, Any]:
    return {
        "repo": {
            "name": target.repo.name,
            "path": str(target.repo.path) if target.repo.path else None,
            "source": target.repo.source,
        },
        "worktree": target.worktree,
        "source_branch": target.source_branch,
        "target_branch": target.target_branch,
    }


def next_payload(result: NextResult, *, bundle_root: Path) -> dict[str, Any]:
    """The `gw work next` contract: phase, status, blockers, on_complete,
    action, normalized, child_rollup -- plus the donor-compatible additions
    `gw next` (C6) wraps.

    A non-null *preflight* nulls `action` and `on_dispatch`: the route still
    has a dispatch, but the skill it names is unusable, and a caller reading
    either key must not be handed a name or a transition for a dispatch that
    can never happen.
    """
    resolution = _usable_resolution(result)
    return {
        "finish_targets": [_finish_target(t) for t in result.finish_targets],
        "requested_path": result.requested_path,
        "selected_path": result.selected_path,
        "work_status": result.state.work_status,
        "kind": result.state.type,
        "phase": result.state.phase,
        "effort": result.state.effort,
        "action": None if resolution is None else {"skill": resolution.profile.skill, "reason": result.route.reason},
        "artifact": None if result.artifact is None else {"path": str(result.artifact.path(bundle_root))},
        "on_dispatch": _transition(_usable_on_dispatch(result)),
        "on_complete": _transition(result.route.on_complete),
        "blockers": next_blockers(result),
        "dispatch": None if resolution is None else dispatch_payload(resolution),
        "child_rollup": _rollup(result.child_rollup),
        "descent": descent_payload(result),
        "normalized": normalized_payload(result),
    }


def dispatch_explain_payload(explanation: DispatchExplanation) -> dict[str, Any]:
    """`/v1/dispatch/explain`: the attributes, every rule with whether it
    matched, and the profile -- through `dispatch_payload` and
    `next_blockers`, so profile, provenance and blockers equal `/v1/work/next`'s."""
    resolution = explanation.resolution
    dispatch = None if resolution is None else dispatch_payload(resolution)
    return {
        "path": explanation.path,
        "attributes": None if explanation.attributes is None else dict(explanation.attributes),
        "packaged_rule": None if explanation.packaged_rule is None else rule_payload(explanation.packaged_rule),
        "rules": [{**rule_payload(rule), "matched": matched} for rule, matched in explanation.rules],
        "profile": None if dispatch is None else dispatch["profile"],
        "provenance": None if dispatch is None else dispatch["provenance"],
        "blockers": next_blockers(explanation.next_result),
    }


def work_queue_payload(entries: Sequence[QueueEntry]) -> dict[str, Any]:
    """`/v1/work/queue`: one row per active item, by `next_payload`'s rules.

    `skill`/`mode`/`reason` come from `_usable_resolution` and `requires` from
    `_usable_on_dispatch`, both nulled by a preflight exactly as `next_payload`
    nulls `action` and `on_dispatch` -- so the queue and `/v1/work/next` agree
    by construction. `blockers` is `next_blockers`.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries:
        result = entry.result
        resolution = _usable_resolution(result)
        on_dispatch = _usable_on_dispatch(result)
        rows.append(
            {
                "path": result.selected_path,
                "type": result.state.type,
                "title": entry.item.title,
                "phase": result.state.phase,
                "work_status": result.state.work_status,
                "skill": None if resolution is None else resolution.profile.skill,
                "mode": None if resolution is None else resolution.profile.mode,
                "reason": None if resolution is None else result.route.reason,
                "blockers": next_blockers(result),
                "requires": [] if on_dispatch is None else list(on_dispatch.requires),
            }
        )
    return {"items": rows}


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------


def _json_safe(value: object) -> object:
    """`FieldChange.before`/`after` mirror whatever type the field is written
    as -- `updated` is a raw `date` (advance.py writes it bare so ruamel does
    not requote it); every other field is already a JSON-native string."""
    return value.isoformat() if isinstance(value, date) else value


def advance_payload(result: StageAdvance, path: str) -> dict[str, Any]:
    """The `gw work advance` contract: phase, status, blockers, on_complete."""
    plan = result.outcome.plan
    applied = {change.key: [_json_safe(change.before), _json_safe(change.after)] for change in plan.changes}
    stamped: dict[str, str] = {}
    if result.outcome.stamped is not None:
        assert result.outcome.stamped.source_id is not None
        stamped[result.outcome.stamped.source_id] = result.outcome.stamped.resource
    phase = plan.transition.phase if plan.transition is not None else None
    status = plan.transition.work_status if plan.transition is not None else None
    application = result.application
    return {
        "path": path,
        "phase": phase,
        "work_status": status,
        "blockers": list(plan.route.blockers),
        "on_complete": _transition(plan.route.on_complete),
        "changes": applied,
        "stamped": stamped,
        "plan_row": result.outcome.plan_row,
        "changed": result.outcome.changed,
        "applied": application is not None,
        "rolled_back": False if application is None else application.rolled_back,
        "failures": [] if application is None else list(application.failures),
        "warnings": list(result.warnings) + ([] if application is None else list(application.warnings)),
        "refusal": None if plan.refusal is None else {"reason": plan.refusal, "detail": plan.detail},
        "results_path": None if result.results_path is None else str(result.results_path),
        "pointer_path": None if result.pointer_path is None else str(result.pointer_path),
        "repo_note": result.repo_note,
    }


# ---------------------------------------------------------------------------
# record-placement
# ---------------------------------------------------------------------------


def placement_payload(result: PlacementRecord) -> dict[str, Any]:
    """The `gw work record-placement` contract: the observed pair, and whether it landed."""
    plan = result.plan
    application = result.application
    return {
        "path": plan.path,
        "root": plan.root,
        "expected_phase": plan.expected_phase,
        "current_phase": plan.current_phase,
        "before": {"worktree": plan.before[0], "branch": plan.before[1]},
        "after": {"worktree": plan.after[0], "branch": plan.after[1]},
        "repo": plan.repo,
        "changed": plan.changed,
        "applied": application is not None,
        "written": result.written,
        "rolled_back": False if application is None else application.rolled_back,
        "failures": [] if application is None else list(application.failures),
        "warnings": [] if application is None else list(application.warnings),
        "refusal": None if plan.refusal is None else {"reason": plan.refusal, "detail": plan.detail},
        "repo_note": result.repo_note,
    }


# ---------------------------------------------------------------------------
# touch-active-work
# ---------------------------------------------------------------------------


def touch_active_work_payload(result: ActiveWorkTouch) -> dict[str, Any]:
    return {
        "path": result.path,
        "phase": result.phase,
        "pointer_path": None if result.pointer_path is None else str(result.pointer_path),
        "refusal": None if result.refusal is None else {"reason": result.refusal, "detail": result.detail},
    }


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


def file_payload(outcome: FilingRun) -> dict[str, Any]:
    plan = outcome.plan
    application = outcome.application
    return {
        "path": plan.filing.path,
        "page_path": str(plan.filing.target),
        "refusal": plan.refusal,
        "detail": plan.filing.detail,
        "indexes": [str(update.path) for update in plan.indexes if update.changed],
        "logged": None if plan.log is None else plan.log.entry,
        "warnings": [*plan.warnings, *(() if application is None else application.warnings)],
        "applied": application is not None,
        "rolled_back": False if application is None else application.rolled_back,
        "failures": [] if application is None else list(application.failures),
    }


# ---------------------------------------------------------------------------
# status / lint / regen-index
# ---------------------------------------------------------------------------


def status_payload(report: StatusReport) -> dict[str, Any]:
    resume = report.resume
    return {
        "total": report.rollup.total,
        "by_work_status": dict(report.rollup.by_work_status),
        "by_type": dict(report.rollup.by_type),
        "by_phase": dict(report.rollup.by_phase),
        "children": {path: _rollup(rolled) for path, rolled in report.rollup.children.items()},
        "resume": None
        if resume is None
        else {
            "primary": {"path": resume.primary.path, "title": resume.primary.title},
            "alternatives": [{"path": item.path, "title": item.title} for item in resume.alternatives],
        },
    }


def work_list_payload(items: Sequence[WorkItem]) -> dict[str, Any]:
    """`/v1/work/list`: the board's rows. `parent` is the parent item's canonical path or null."""
    return {
        "items": [
            {
                "path": item.path,
                "type": item.type,
                "title": item.title,
                "work_status": item.work_status,
                "phase": item.phase,
                "effort": item.effort,
                "owner": item.owner,
                "parent": item.parent_path,
                "updated": item.updated,
            }
            for item in items
        ]
    }


def ingest_queue_payload(report: IngestQueueReport) -> dict[str, Any]:
    return {
        "pending": [
            {
                "path": entry.path,
                "work_status": entry.work_status,
                "resource": entry.resource,
                "origin": entry.origin,
            }
            for entry in report.pending
        ]
    }


def lint_payload(report: object) -> dict[str, Any]:
    view = cast(_ReportView, report)
    return {"ok": view.ok, "findings": [_finding(finding) for finding in view.findings]}


def regen_index_payload(result: RegenIndexesResult) -> dict[str, Any]:
    application = result.application
    return {
        "indexes": [str(plan.path) for plan in (*result.plans, *result.marker_strips) if plan.changed],
        "warnings": [*result.mutation.warnings, *(() if application is None else application.warnings)],
        "refusals": [_refusal(item) for item in result.mutation.refusals],
        "applied": application is not None,
        "rolled_back": False if application is None else application.rolled_back,
        "failures": [] if application is None else list(application.failures),
    }


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


def _wiki_archive(run: ArchiveRun) -> dict[str, Any]:
    plan = run.wiki_plan
    wiki = run.wiki
    return {
        "tokens": list(plan.tokens),
        "path_mapping": {move.source: move.dest for move in plan.moves.moves},
        "skipped": [
            {"token": skipped.token, "reason": skipped.reason, "detail": skipped.detail} for skipped in plan.skipped
        ],
        "refusals": [_refusal(refusal) for refusal in (*plan.moves.refusals, *(() if wiki is None else wiki.refusals))],
        "archived": [] if wiki is None else list(wiki.archived),
        "indexes": [] if wiki is None else [str(update.path) for update in wiki.indexes if update.changed],
        "failures": []
        if wiki is None
        else [f"{failure.path}: {failure.kind} -- {failure.error}" for failure in wiki.move.failed],
    }


def archive_payload(run: ArchiveRun, *, dry_run: bool) -> dict[str, Any]:
    result = run.result
    return {
        "dry_run": dry_run,
        "ok": run.ok,
        "conflict": list(run.conflict),
        "path_mapping": dict(run.plan.path_mapping),
        "indexes": [] if result is None else [path for path in result.written if path.endswith("index.md")],
        "warnings": [*run.plan.warnings, *(() if result is None else result.warnings)],
        "refusals": [_refusal(refusal) for refusal in run.plan.refusals],
        **_application(result),
        "pointer_cleared": run.pointer_cleared,
        "logged": run.logged,
        "wiki": _wiki_archive(run),
    }


def path_mutation_payload(result: PathMutationResult) -> dict[str, Any]:
    return {
        "path_mapping": dict(result.plan.path_mapping),
        "indexes": [write.member for write in result.plan.writes if write.member.endswith("index.md")],
        "warnings": [*result.plan.warnings, *(() if result.application is None else result.application.warnings)],
        "refusals": [_refusal(refusal) for refusal in result.plan.refusals],
        **_application(result.application),
    }


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def decision_payload(result: DecisionCommandResult) -> dict[str, Any]:
    plan = result.plan
    application = result.application
    return {
        "owner_path": result.owner.owner_path,
        "requested_path": result.owner.redirected_from or result.owner.owner_path,
        "ledger_path": str(result.owner.ledger),
        "entry": None if plan is None or plan.primary is None else _decision(plan.primary),
        "superseded": None if plan is None or plan.superseded is None else plan.superseded.id,
        "refusal": None if plan is None else plan.refusal,
        **_application(application),
        "entries": [_decision(entry) for entry in result.entries],
        "counts": dict(result.counts),
        "warnings": list(result.warnings),
    }


def open_decisions_payload(records: Sequence[OpenDecision]) -> dict[str, Any]:
    """`/v1/work/decisions/open`: every open entry across active owners, with the items it holds."""
    return {
        "decisions": [
            {
                "owner_path": record.owner_path,
                "ledger_path": str(record.ledger),
                "held": list(record.held),
                "entry": _decision(record.decision),
            }
            for record in records
        ]
    }


def overturn_payload(result: OverturnResult) -> dict[str, Any]:
    decision = result.plan.decision
    filing = result.plan.filing
    application = None if result.application is None else result.application.mutation
    return {
        "owner_path": result.owner.owner_path,
        "requested_path": result.owner.redirected_from or result.owner.owner_path,
        "ledger_path": str(result.owner.ledger),
        "entry": None if decision.primary is None else _decision(decision.primary),
        "superseded": None if decision.superseded is None else decision.superseded.id,
        "refusal": result.plan.refusal,
        **_application(application),
        "follow_up": {"path": filing.filing.path, "page_path": str(filing.filing.target)},
        "follow_up_filed": application is not None and application.ok,
        "warnings": list(result.warnings),
    }


# ---------------------------------------------------------------------------
# orchestrate
# ---------------------------------------------------------------------------


def _worktree(value: object) -> dict[str, Any]:
    action = cast(_WorktreeView, value)
    return {
        "action": action.action,
        "path": action.path,
        "branch": action.branch,
        "base_branch": action.base_branch,
        "exists": action.exists,
        "parent_path": action.parent_path,
    }


def orchestrate_payload(result: OrchestrateResult) -> dict[str, Any]:
    return {
        "path": result.path,
        "terminal": result.terminal,
        "max_parallel": result.max_parallel,
        "slots_free": result.slots_free,
        "supervise_merges": result.supervise_merges,
        "live": list(result.live),
        "repo": None
        if result.code_repo is None
        else {"name": result.code_repo_name, "path": result.code_repo, "source": result.code_repo_source},
        "dispatches": [
            {
                "repo": {
                    "name": result.dispatch_repos[dispatch.key].name,
                    "path": str(result.dispatch_repos[dispatch.key].path)
                    if result.dispatch_repos[dispatch.key].path is not None
                    else None,
                    "source": result.dispatch_repos[dispatch.key].source,
                },
                "finish_targets": [_finish_target(t) for t in result.plan.finish_targets.get(dispatch.key, ())],
                "key": dispatch.key,
                "path": dispatch.slug,
                "phase": dispatch.phase,
                "kind": dispatch.kind,
                "effort": dispatch.effort,
                "skill": dispatch.skill,
                "mode": dispatch.mode,
                "agent": dispatch.agent,
                "model": dispatch.model,
                "provenance": dispatch_payload(result.plan.dispatch_resolutions[dispatch.key])["provenance"],
                "reasoning_effort": dispatch.reasoning_effort,
                "worktree": _worktree(dispatch.worktree),
                "merge_target": dispatch.merge_target,
                "prompt": dispatch.prompt,
            }
            for dispatch in result.dispatches
        ],
        "preparations": [
            {
                "owner_path": preparation.owner_path,
                "owner_phase": preparation.owner_phase,
                "repo": {
                    "name": preparation.repo.name,
                    "path": str(preparation.repo.path),
                    "source": preparation.repo.source,
                },
                "branch": preparation.branch,
                "base_branch": preparation.base_branch,
                "worktree": _worktree(preparation.worktree),
            }
            for preparation in result.preparations
        ],
        "advances": [
            {
                "path": advance.path,
                "reason": advance.reason,
                "worktree": advance.worktree,
                "branch": advance.branch,
                "mode": advance.mode,
            }
            for advance in result.advances
        ],
        "blocked": [{"path": item.path, "kind": item.kind, "reason": item.reason} for item in result.blocked],
        "decisions": {
            "owner_path": result.decisions_owner_path,
            "ledger_path": result.decisions_ledger_path,
            "open": [_decision(entry) for entry in result.open_decisions],
            "assumed": [_decision(entry) for entry in result.assumed_decisions],
            "counts": dict(result.decision_counts),
        },
        "holds": [_hold(hold) for hold in result.holds],
        "warnings": list(result.warnings),
    }


# ---------------------------------------------------------------------------
# reconcile-context
# ---------------------------------------------------------------------------


def reconcile_payload(context: ReconcileContext) -> dict[str, Any]:
    """All fourteen donor fields, at their donor names and nesting."""
    return {
        "owner_path": context.owner_path,
        "path": context.path,
        "spec_path": context.spec_path,
        "spec_anchor_commit": context.spec_anchor_commit,
        "anchor_source": context.anchor_source,
        "commit_range": context.commit_range,
        "landed_siblings": [
            {"path": sibling.path, "resolved_in": sibling.resolved_in, "affects": list(sibling.affects)}
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
