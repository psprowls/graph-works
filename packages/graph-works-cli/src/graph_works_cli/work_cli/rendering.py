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

JSON occupies stdout alone; warnings and errors go to stderr in every mode. A
`--json` refusal additionally emits a single-key `{"error": ...}` envelope on
stdout before exiting non-zero (D-004) -- see `_envelope()` below; the exit
code never becomes zero, and human-mode output is unaffected.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Any, Never, Protocol, cast

import typer
from graph_works_core.archive.commands import ArchiveRun
from graph_works_core.orchestrate.commands import OrchestrateResult
from graph_works_core.orchestrate.stage_advance import StageAdvance
from graph_works_core.work.commands import (
    ChildRollup,
    Decision,
    DecisionCommandResult,
    FilingRun,
    IngestQueueReport,
    NextResult,
    OverturnResult,
    PathMutationResult,
    RegenIndexesResult,
    StatusReport,
    Transition,
)
from graph_works_core.work.reconcile import ReconcileContext

from graph_works_cli import exit_codes

# ---------------------------------------------------------------------------
# Emit / exit policy
# ---------------------------------------------------------------------------

_JSON_MODE: ContextVar[bool | None] = ContextVar("gw_work_json_mode", default=None)

#: Captured alongside `_JSON_MODE`, at the same `--json` parse-time seam --
#: `typer.Context`/`typer.CallbackParam` wrap this codebase's own vendored
#: click fork (`typer._click`), which keeps a context stack the real `click`
#: package's `get_current_context()` cannot see (see this package's AGENTS.md
#: "typer.core vs click" gotcha); capturing the path here, once, at a point
#: where the context is unambiguously available avoids relying on any such
#: lookup later, inside `fail()`.
_COMMAND_NAME: ContextVar[str] = ContextVar("gw_work_command_name", default="")

#: The closed `reason` vocabulary for a `fail()` envelope (D-004 §3.2). A new
#: exit site must pick one of these deliberately -- there is no catch-all.
_REASONS = frozenset(
    {
        "refused",
        "incomplete-apply",
        "conflict",
        "incomplete",
        "usage",
        "workspace",
        "unresolved",
        "not-a-repo",
        "io",
    }
)


def _set_json_mode(ctx: typer.Context, param: typer.CallbackParam, value: bool) -> bool:
    """The shared `--json` option callback: records this invocation's mode
    and command path.

    Click invokes an option's callback even when the option is absent,
    passing the declared default -- so every command built with
    `json_option()` sets this var, to `True` or to `False`. A command that
    hand-rolls its own `--json` leaves it `None`, which `fail()` treats as a
    programming error rather than a silent default.
    """
    _JSON_MODE.set(value)
    _root_name, _, rest = ctx.command_path.partition(" ")
    _COMMAND_NAME.set(rest)
    return value


def reset_json_mode() -> None:
    """Reset the per-invocation JSON-mode var. Called from the root callback,
    which Click runs before the subcommand's own option parsing -- so this
    always executes before that command's `json_option()` callback."""
    _JSON_MODE.set(None)
    _COMMAND_NAME.set("")


def json_option(help: str) -> bool:
    """The one `--json` declaration every `gw work` command must use.

    Replaces a hand-written `typer.Option(False, "--json", help=...)`: same
    surface (default `False`, same help text), plus the mode-tracking
    callback `fail()` depends on. Typed `bool` to match every call site's own
    `json_output: bool = ...` annotation -- `typer.Option()` itself is typed
    `Any` in typer's stubs.
    """
    return cast(bool, typer.Option(False, "--json", help=help, callback=_set_json_mode))


def _envelope(*, reason: str, message: str, code: int, payload: object) -> dict[str, Any]:
    """The single-key, structurally-unmistakable refusal document (D-004 §3.2).

    No success projection in this module carries a top-level `error` key, so
    `"error" in doc` is a sound, collision-free discriminator (§2.5).
    """
    assert reason in _REASONS, f"fail(): {reason!r} is not in the closed reason vocabulary"
    return {
        "error": {
            "command": _COMMAND_NAME.get(),
            "reason": reason,
            "message": message,
            "exit_code": code,
            "payload": payload,
        }
    }


def emit(payload: object) -> None:
    """One JSON document on stdout, and nothing else."""
    typer.echo(json.dumps(payload, indent=2))


def fail(
    message: str,
    *,
    reason: str,
    code: int = exit_codes.GENERIC,
    cause: BaseException | None = None,
    payload: object | None = None,
) -> Never:
    """Emit a `--json` refusal envelope (when in JSON mode), write the
    human-facing error to stderr in every mode, and stop the current command.

    `reason` is required, not defaulted: a new exit site must name which of
    the closed vocabulary it is, so it can never silently fall through to a
    catch-all. The stderr line and exit code are unchanged from before D-004;
    the envelope is purely additive, and only appears on stdout.
    """
    mode = _JSON_MODE.get()
    assert mode is not None, "gw work command reached fail() without declaring --json via json_option()"
    if mode:
        emit(_envelope(reason=reason, message=message, code=code, payload=payload))
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
        "prose": entry.prose,
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


def next_blockers(result: NextResult, *, preflight: str | None = None) -> list[str]:
    """The route's blockers, plus the descent's own refusal and the dispatch
    preflight refusal when either exists.

    A `--descend` that could not reach a leaf is a fact `route()` never sees:
    routing ran against the ancestor, so its blockers explain the ancestor and
    say nothing about why the walk stopped. The `--descend:` prefix is the
    marker the workflow skill matches on.

    *preflight* is the same shape of thing one layer further out:
    `work-tracker-okf` declines the skill mapping by name, so `RouteResult`
    cannot know a configured skill is malformed. It is appended here for the
    same reason the descent refusal is -- a stop condition the routing table
    cannot see, surfaced through the channel the workflow skill already reads.
    """
    blockers = list(result.route.blockers)
    descent = result.descent
    if descent is not None and descent.leaf is None and descent.reason:
        blockers.append(f"--descend: {descent.reason}")
    if preflight is not None:
        blockers.append(preflight)
    return blockers


def next_payload(
    result: NextResult, *, bundle_root: Path, skill: str | None, preflight: str | None = None
) -> dict[str, Any]:
    """The `gw work next` contract: phase, status, blockers, on_complete,
    action, normalized, child_rollup -- plus the donor-compatible additions
    `gw next` (C6) wraps.

    A non-null *preflight* nulls `action` and `on_dispatch`: the route still
    has a dispatch, but the skill it names is unusable, and a caller reading
    either key must not be handed a name or a transition for a dispatch that
    can never happen.
    """
    dispatch = result.route.dispatch
    return {
        "requested_path": result.requested_path,
        "selected_path": result.selected_path,
        "work_status": result.state.work_status,
        "kind": result.state.type,
        "phase": result.state.phase,
        "effort": result.state.effort,
        "action": (
            None if dispatch is None or preflight is not None else {"skill": skill, "reason": result.route.reason}
        ),
        "artifact": None if result.artifact is None else {"path": str(result.artifact.path(bundle_root))},
        "on_dispatch": None if preflight is not None else _transition(result.route.on_dispatch),
        "on_complete": _transition(result.route.on_complete),
        "blockers": next_blockers(result, preflight=preflight),
        "child_rollup": _rollup(result.child_rollup),
        "descent": descent_payload(result),
        "normalized": normalized_payload(result),
    }


def render_next(result: NextResult, payload: dict[str, Any]) -> None:
    normalized = payload["normalized"]
    if normalized:
        for source in normalized:
            typer.echo(f"[fix] stamped {source['source_id']} for {source['path']}: {source['resource']}")
    typer.echo(
        f"{payload['selected_path']}: kind={payload['kind']} "
        f"work_status={payload['work_status']} phase={payload['phase']}"
    )
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


def render_advance(payload: dict[str, Any]) -> None:
    typer.echo(f"[ok] {payload['path']}: phase={payload['phase']} work_status={payload['work_status']}")
    for key, change in payload["changes"].items():
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


def render_status(payload: dict[str, Any]) -> None:
    typer.echo(f"{payload['total']} item(s) under work/")
    for label, key in (
        ("work_status", "by_work_status"),
        ("type", "by_type"),
        ("phase", "by_phase"),
    ):
        rendered = ", ".join(f"{name} {count}" for name, count in payload[key].items())
        typer.echo(f"  by {label}: {rendered or '-'}")
    for path, rolled in payload["children"].items():
        typer.echo(f"  children {path}: {rolled['terminal']}/{rolled['total']} terminal")
    resume = payload["resume"]
    if resume is not None:
        typer.echo(f"  resume: {resume['primary']['path']} — {resume['primary']['title']}")
        for alternative in resume["alternatives"]:
            typer.echo(f"    alt: {alternative['path']} — {alternative['title']}")


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


def render_ingest_queue(payload: dict[str, Any]) -> None:
    pending = payload["pending"]
    typer.echo(f"{len(pending)} design spec(s) pending ingest")
    for entry in pending:
        typer.echo(f"  {entry['path']} — {entry['work_status']}")
        typer.echo(f"    {entry['resource']}")
    if pending:
        typer.echo("  Drain with: /gw:ingest <resource>")


def lint_payload(report: object) -> dict[str, Any]:
    view = cast(_ReportView, report)
    return {"ok": view.ok, "findings": [_finding(finding) for finding in view.findings]}


def render_lint(report: object) -> None:
    """Errors to stderr, warnings to stdout -- so a piped lint carries only
    what the reader asked for."""
    view = cast(_ReportView, report)
    for finding in view.findings:
        line = f"{finding.severity:<6} {finding.code}: {finding.message}"
        typer.echo(line, err=finding.severity == "error")


def regen_index_payload(result: RegenIndexesResult) -> dict[str, Any]:
    application = result.application
    return {
        "indexes": [str(plan.path) for plan in result.plans if plan.changed],
        "warnings": [*result.mutation.warnings, *(() if application is None else application.warnings)],
        "refusals": [_refusal(item) for item in result.mutation.refusals],
        "applied": application is not None,
        "rolled_back": False if application is None else application.rolled_back,
        "failures": [] if application is None else list(application.failures),
    }


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


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


def render_decision_write(payload: dict[str, Any], verb: str) -> None:
    resolved = f" (requested {payload['requested_path']})" if payload["requested_path"] != payload["owner_path"] else ""
    typer.echo(f"[ok] ledger: {payload['owner_path']}{resolved}")
    if payload["entry"]:
        typer.echo(f"[ok] {verb} {payload['entry']['id']}  status={payload['entry']['status']}")
    if payload["superseded"]:
        typer.echo(f"[ok] superseded {payload['superseded']}")
    follow_up = payload.get("follow_up")
    if follow_up:
        typer.echo(f"[ok] follow-up {follow_up['path']}: {follow_up['page_path']}")


def render_decision_list(payload: dict[str, Any]) -> None:
    resolved = f" (requested {payload['requested_path']})" if payload["requested_path"] != payload["owner_path"] else ""
    typer.echo(f"[ok] ledger: {payload['owner_path']}{resolved}  {payload['ledger_path']}")
    for entry in payload["entries"]:
        question = f" — {entry['question']}" if entry["question"] else ""
        typer.echo(f"  {entry['id']}  {entry['status'] or '(no status)'}{question}")
    typer.echo("  counts: " + ", ".join(f"{key}={value}" for key, value in payload["counts"].items()))


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
    }


def orchestrate_payload(result: OrchestrateResult) -> dict[str, Any]:
    return {
        "path": result.path,
        "terminal": result.terminal,
        "max_parallel": result.max_parallel,
        "slots_free": result.slots_free,
        "permission_mode": result.permission_mode,
        "supervise_merges": result.supervise_merges,
        "live": list(result.live),
        "dispatches": [
            {
                "key": dispatch.key,
                "path": dispatch.slug,
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
        "warnings": list(result.warnings),
    }


def render_orchestrate(payload: dict[str, Any]) -> None:
    free = payload["slots_free"]
    max_p = payload["max_parallel"]
    header = f"{payload['path']}: terminal={payload['terminal']} slots_free={free}/{max_p}"
    if payload["supervise_merges"]:
        header += " supervise_merges=True"
    typer.echo(header)
    for dispatch in payload["dispatches"]:
        typer.echo(
            f"  dispatch {dispatch['key']}: {dispatch['skill']} mode={dispatch['mode']} "
            f"model={dispatch['model']} worktree={dispatch['worktree']['action']}"
        )
    for advance in payload["advances"]:
        typer.echo(f"  advance {advance['path']} (mode={advance['mode']}): {advance['reason']}")
    for blocked in payload["blocked"]:
        echo_wrapped(f"  blocked {blocked['path']} ({blocked['kind']}): ", blocked["reason"])
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


def render_reconcile(payload: dict[str, Any]) -> None:
    typer.echo(f"{payload['path']}: owner={payload['owner_path'] or '-'}")
    typer.echo(f"  spec: {payload['spec_path']}")
    typer.echo(f"  anchor: {payload['spec_anchor_commit'] or '-'} ({payload['anchor_source']})")
    typer.echo(f"  range: {payload['commit_range'] or '-'}")
    typer.echo(f"  touched: {', '.join(payload['touched_paths']) or '-'}")
    for sibling in payload["landed_siblings"]:
        typer.echo(f"  landed: {sibling['path']} resolved_in {sibling['resolved_in']}")
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
