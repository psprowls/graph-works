"""`gw work` — the work-item pipeline verbs.

Interface band only (ADR 2026-08-13-command-modules): parse arguments, resolve the workspace, call
**one** `graph-works-core` command, project the typed result, choose an exit
code. No business composition lives here, and nothing under
`graph_works_cli` imports `work_tracker_okf` -- `test_work_surface.py`
asserts it.

The clock is read here and nowhere below: every core writer takes `today=`
or `on=` and never looks it up itself.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any, Never, cast

import typer
from graph_works_core.archive.commands import run_archive, stranded_warnings
from graph_works_core.orchestrate.accept_integration import run_accept_integration
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.orchestrate.dispatch import run_dispatch
from graph_works_core.orchestrate.gate import gate_wait_facts
from graph_works_core.orchestrate.integrate import run_integrate
from graph_works_core.orchestrate.merge_workspace import run_merge_workspace
from graph_works_core.orchestrate.placement import (
    ReaderObservation,
    run_record_baseline,
    run_record_placement,
    run_record_reader,
)
from graph_works_core.orchestrate.reroute import run_reroute
from graph_works_core.orchestrate.stage_advance import ExpectedPhase, run_stage_advance
from graph_works_core.orchestrate.wait import WAIT_FLOOR_S, WaitClock, WaitFailed, apply_wait_floor, run_wait
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from graph_works_core.work import commands as work
from graph_works_core.workspace.config import WorkspaceConfig, load_workspace_config
from graph_works_core.workspace.errors import WorkspaceConfigError, WorkspaceError
from graph_works_core.workspace.finish import STRATEGIES, IntegrationStrategy
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_wire import work as wire_work

from graph_works_cli import exit_codes
from graph_works_cli.provenance import warn_if_stale_routing
from graph_works_cli.work_cli import rendering
from graph_works_cli.work_cli.ask import ask, ask_answer
from graph_works_cli.work_cli.decision import decision_app
from graph_works_cli.work_cli.gate import gate_app
from graph_works_cli.work_cli.obligation import obligation_app
from graph_works_cli.work_cli.orca import orca_port
from graph_works_cli.work_cli.reconcile import reconcile_context
from graph_works_cli.workspace_resolution import resolve_workspace

work_app = typer.Typer(name="work", help="Work-item pipeline verbs.", no_args_is_help=True)
work_app.add_typer(decision_app, name="decision")
work_app.add_typer(gate_app, name="gate")
work_app.add_typer(obligation_app, name="obligation")
work_app.command(name="reconcile-context")(reconcile_context)
work_app.command(name="ask")(ask)
work_app.command(name="ask-answer")(ask_answer)


def _today() -> date:
    """One UTC date per invocation. Core never reads the clock (spec 6)."""
    return datetime.now(UTC).date()


def _wait_clock() -> WaitClock:
    """The one clock `gw work wait` reads; core measures sleep as wall minus monotonic."""
    return WaitClock(wall=lambda: datetime.now(UTC), monotonic=time.monotonic)


_VALIDATION_REASONS = frozenset(
    {"plan-invalid", "key-not-in-plan", "override-invalid", "reason-missing", "record-invalid"}
)


def _step_failure(payload: dict[str, Any]) -> Never:
    failure = payload["failure"]
    code = exit_codes.SCHEMA_MISMATCH if failure["reason"] in _VALIDATION_REASONS else exit_codes.GENERIC
    rendering.fail(
        f"{payload['key']}: {failure['step']} failed ({failure['reason']}) — {failure['detail']}",
        reason="refused",
        code=code,
        payload=payload,
    )


def _warn_refusals(refusals: object) -> None:
    """Name every refusal before failing -- the reason is computed, so print it."""
    assert isinstance(refusals, list)
    for refusal in refusals:
        assert isinstance(refusal, dict)
        rendering.warn(f"{refusal['path']}: {refusal['kind']} — {refusal['detail']}")


def _optional_date(raw: str, option: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        rendering.fail(f"{option}: expected YYYY-MM-DD, got {raw!r}", reason="usage", cause=exc)


def _config(layout: WorkspaceLayout) -> WorkspaceConfig:
    """The workspace's own declarations, or a mapped configuration failure."""
    try:
        return load_workspace_config(layout)
    except WorkspaceConfigError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)


_DEP_KEYS = ("path", "blocks", "needs")


def _parse_dep_spec(raw: str) -> dict[str, str]:
    """One complete `--dep path=X,blocks=P,needs=N` mapping.

    Key-value pairs rather than invented punctuation: `planning-epics` is the
    main machine author of these, so verbosity is cheap and self-documentation
    is not. Keys are case-sensitive by design -- `SLUG=a` is an unknown key,
    not a normalized spelling. Syntax only; the edge **vocabulary** is core's
    to validate, through `parse_dependencies` below.
    """
    pairs: dict[str, str] = {}
    for fragment in raw.split(","):
        if "=" not in fragment:
            rendering.fail(f"--dep {raw!r}: expected key=value pairs", reason="usage")
        key, _, value = fragment.partition("=")
        key, value = key.strip(), value.strip()
        if key not in _DEP_KEYS:
            rendering.fail(f"--dep {raw!r}: unknown key {key!r}; expected path|blocks|needs", reason="usage")
        if key in pairs:
            rendering.fail(f"--dep {raw!r}: duplicate key {key!r}", reason="usage")
        if not value:
            rendering.fail(f"--dep {raw!r}: {key}= must not be empty", reason="usage")
        pairs[key] = value
    missing = [key for key in _DEP_KEYS if key not in pairs]
    if missing:
        rendering.fail(f"--dep {raw!r}: missing {', '.join(missing)}", reason="usage")
    return pairs


def _dependency_edges(dep_specs: list[str]) -> tuple[work.DependencyEdge, ...]:
    """Parse repeatable complete mappings; core owns path and vocabulary validation."""
    raw: list[object] = []
    origins: list[str] = []
    for spec in dep_specs:
        raw.append(_parse_dep_spec(spec))
        origins.append(spec)

    parsed = work.parse_dependencies(raw)
    if parsed.issues:
        detail = "; ".join(f"{issue.code}: {issue.detail} ({origins[issue.index]!r})" for issue in parsed.issues)
        rendering.fail(f"--dep: {detail}", reason="usage")
    return parsed.edges


@work_app.command()
def file(
    title: str = typer.Option(..., "--title", help="Work item title."),
    kind: str = typer.Option(..., "--kind", help="Release | Epic | Feature | Bug | TechDebt | TestGap | Spike."),
    summary: str = typer.Option(..., "--summary", help="One-line summary for the index entry."),
    affects: str = typer.Option(
        "", "--affects", help="Comma-separated repo paths or package names; gw:workspace for a workspace-only item."
    ),
    effort: str = typer.Option("", "--effort", help="xtra-small|small|medium|large|xtra-large."),
    name: str = typer.Option("", "--name", help="Stable basename words. Defaults to the title."),
    parent_path: str = typer.Option("", "--parent-path", help="Canonical parent item path."),
    dep: list[str] = typer.Option(  # noqa: B008 -- Typer declares CLI options in defaults
        [],
        "--dep",
        help="Repeatable edge: path=<canonical>,blocks=<phase>,needs=<phase|resolved>.",
    ),
    blast_radius: str = typer.Option("", "--blast-radius", help="file|package|domain|system."),
    version: str = typer.Option("", "--version", help="Version or release train identifier."),
    target_date: str = typer.Option("", "--target-date", help="Target date (YYYY-MM-DD)."),
    owner: str = typer.Option("", "--owner", help="Owner handle."),
    repo: str = typer.Option(
        "", "--repo", help="Declared repository this item's code lives in; descendants inherit it."
    ),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the filing as JSON."),
) -> None:
    """File one work item, reconcile `work/index.md`, and log its arrival.

    Paths are extensionless and bundle-relative. Dependencies are complete,
    path-keyed mappings; no legacy slug shorthand is accepted.
    """
    layout = resolve_workspace(workspace)
    config = _config(layout)
    edges = _dependency_edges(dep)
    try:
        outcome = work.run_file(
            layout,
            config,
            type=kind,
            title=title,
            description=summary,
            on=_today(),
            name=name or None,
            effort=effort or None,
            blast_radius=blast_radius or None,
            version=version or None,
            target_date=_optional_date(target_date, "--target-date"),
            owner=owner or None,
            repo=repo or None,
            parent_path=parent_path or None,
            depends_on=edges,
            affects=rendering.split_csv(affects),
            tags=rendering.split_csv(tags),
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.file_payload(outcome)
    if outcome.plan.refusal is not None:
        for warning in payload["warnings"]:
            rendering.warn(warning)
        rendering.fail(
            f"{payload['path']}: refused ({payload['refusal']}) — {payload['detail']}",
            reason="refused",
            payload=payload,
        )
    failures = payload["failures"]
    assert isinstance(failures, list)
    if payload["rolled_back"] or failures:
        for failure in failures:
            rendering.warn(failure)
        blocking = failures[0] if failures else f"{payload['path']}: filing apply was incomplete"
        rendering.fail(blocking, reason="incomplete-apply", payload=payload)
    for warning in payload["warnings"]:
        rendering.warn(warning)

    if json_output:
        rendering.emit(payload)
        return
    if dry_run:
        typer.echo(outcome.plan.filing.diff())
        return
    typer.echo(f"[ok] filed {payload['path']}: {payload['page_path']}")
    for path in payload["indexes"]:
        typer.echo(f"  reconciled {path}")
    if payload["logged"]:
        typer.echo(f"  log.md: {payload['logged'].removeprefix('- ')}")
    rendering.render_commit(payload["commit"])


@work_app.command()
def status(
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the rollup as JSON."),
) -> None:
    """Count the active work items and name the one worth resuming."""
    layout = resolve_workspace(workspace)
    try:
        report = work.run_status(layout)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.status_payload(report)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_status(payload)


@work_app.command(name="ingest-queue")
def ingest_queue(
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the queue as JSON."),
) -> None:
    """List terminal work items whose design spec has not been ingested.

    Derived from `sources[]` and the ingested `Source` pages' `origin` — never
    stored, and never written. Drain it with `/gw:ingest <resource>`;
    the ingest itself is what clears an entry.
    """
    layout = resolve_workspace(workspace)
    try:
        report = work.run_ingest_queue(layout)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.ingest_queue_payload(report)
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_ingest_queue(payload)


@work_app.command()
def lint(
    path: str = typer.Argument("", help="Extensionless canonical item path; omit to lint the whole work lane."),
    strict: bool = typer.Option(False, "--strict", help="Promote every warning to an error."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the report as JSON."),
) -> None:
    """Report work-lane conformance. Never writes.

    The fast, synchronous, LLM-free work-lane check -- a sibling of
    `gw wiki lint`, not a subset of it. With PATH, report only that item's
    findings (its page and owned directory).
    """
    layout = resolve_workspace(workspace)
    config = _config(layout)
    try:
        report = work.run_lint(
            layout, config, repo_roots=resolve_repos(layout), strict=strict, today=_today(), path=path or None
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except LookupError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    if json_output:
        rendering.emit(wire_work.lint_payload(report))
    else:
        rendering.render_lint(report)
    if not report.ok:
        raise typer.Exit(code=exit_codes.GENERIC)


@work_app.command(name="next")
def next_stage(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    descend: bool = typer.Option(
        False, "--descend", help="When the item waits on children, switch to the next actionable child."
    ),
    file: str = typer.Option(
        "auto",
        "--file",
        help="Where to write the assembled guidance: 'auto' (the item's references/), a path, or '' to skip writing.",
    ),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the routing decision as JSON."),
) -> None:
    """Compute what to dispatch for PATH, and what advancing would change.

    Writes: the canonical design-source repair reported as `normalized` -- it
    is applied, not previewed, so the next read agrees with this one -- and,
    because `--file` defaults to `auto`, the assembled guidance file (when it
    admitted anything and has a target) plus the claims cache under
    `<cache_dir>/claims/`. `--file ""` skips the guidance file, not the
    assembly.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace)
    # An explicit path resolves against the cwd here, so `guidance_file` is always absolute.
    request = work.GuidanceRequest("auto" if file == "auto" else None if file == "" else Path(file).resolve())
    try:
        result = work.run_next(layout, path, descend=descend, dry_run=False, guidance=request)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.next_payload(result, bundle_root=layout.bundle_dir)
    if json_output:
        rendering.emit(payload)
        for warning in result.warnings:
            rendering.warn(warning)
    else:
        rendering.render_next(result, payload)
    if payload["blockers"]:
        raise typer.Exit(code=exit_codes.GENERIC)


@work_app.command()
def advance(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    effort: str = typer.Option("", "--effort", help="xtra-small|small|medium|large|xtra-large."),
    owner: str = typer.Option("", "--owner", help="Handle to record when execution starts."),
    resolved_in: str = typer.Option("", "--resolved-in", help="PR or commit ref."),
    released_at: str = typer.Option("", "--released-at", help="Release date (YYYY-MM-DD)."),
    worktree: str = typer.Option("", "--worktree", help="The item's worktree path, when the caller knows it."),
    branch: str = typer.Option("", "--branch", help="The item's branch, paired with --worktree."),
    no_infer_worktree: bool = typer.Option(
        False,
        "--no-infer-worktree",
        help="Never infer the worktree/branch from the current directory (every supervised worker passes this).",
    ),
    start_sha: str = typer.Option(
        "", "--start-sha", help="Where this phase started; required for a finish-stage results stub."
    ),
    return_: bool = typer.Option(False, "--return", help="Send an item at `finish` back to `execute`."),
    return_scope: list[str] = typer.Option(  # noqa: B008 - Typer declares options in defaults
        [],
        "--return-scope",
        help="With --return: one line of work execute must do (repeatable). "
        "Omit to return the item's coverage obligations.",
    ),
    skip_gate: str = typer.Option(
        "", "--skip-gate", help="Bypass exactly this execute -> finish gate refusal code (humans only)."
    ),
    reason: str = typer.Option("", "--reason", help="Why the gate is bypassed; recorded in the decision ledger."),
    actor: str = typer.Option("", "--actor", help="Who bypasses the gate; recorded in the decision ledger."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the advance as JSON."),
    expected_phase: ExpectedPhase | None = typer.Option(  # noqa: B008 - Typer declares options in defaults
        None, "--from", help="Expected current phase; none means no phase. Omit to leave unchecked."
    ),
) -> None:
    """Apply the routing table's next transition for PATH.

    The pipeline's single mutation point. An explicit `--worktree`/`--branch`
    pair is applied unconditionally -- it is the caller's own resolved
    decision, which is what lets an item be evicted out of a shared main
    checkout; without the pair, a top-level item falls back to cwd inference
    unless `--no-infer-worktree` is given; a descendant never infers — its
    coordinator records placement with `gw work record-placement`.

    `--start-sha` names where the phase being completed began. Out of `execute`
    it is optional — core derives one from the worktree's merge base against the
    default branch, then from the spec's own anchors. Out of `finish` there is no
    derivation: a guessed range there would sweep in the whole execute range, so
    the stub is written only when this flag is given.

    `--return` is the way home: it walks the routing table's one backwards
    transition, `finish` -> `execute`, for an item that reached `finish`
    before the commit gate existed, that the relay put on hold, or that a
    later stage-gate sends back. It is refused from any other phase, and is
    mutually exclusive with `--resolved-in`.

    `--return-scope TEXT` (repeatable) names the work the returned execute must
    do; without it the item's `origin: coverage` finish obligations are
    returned, and a return with neither is refused.

    `--skip-gate CODE --reason TEXT --actor HANDLE` bypasses exactly the refusal
    this advance's execute -> finish gate returns, and records an answered
    decision in the item's decision ledger in the same write. Supervised
    workers never pass it; a human does.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace)
    try:
        result = run_stage_advance(
            layout,
            path,
            today=_today(),
            expected_phase=expected_phase,
            effort=effort or None,
            owner=owner or None,
            resolved_in=resolved_in or None,
            released_at=_optional_date(released_at, "--released-at"),
            worktree=worktree or None,
            branch=branch or None,
            infer_worktree=not no_infer_worktree,
            start_sha=start_sha or None,
            return_=return_,
            return_scope=tuple(return_scope),
            skip_gate=skip_gate or None,
            skip_reason=reason or None,
            actor=actor or None,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.advance_payload(result, path)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        for failure in payload["failures"]:
            rendering.warn(failure)
        rendering.fail(f"{path}: apply was incomplete", reason="incomplete-apply", payload=payload)
    for warning in payload["warnings"]:
        rendering.warn(warning)

    if json_output:
        rendering.emit(payload)
        if payload["repo_note"]:
            rendering.warn(payload["repo_note"])
        return
    if dry_run:
        typer.echo(result.outcome.plan.diff())
        return
    rendering.render_advance(payload)
    rendering.render_commit(payload["commit"])


@work_app.command(name="record-placement")
def record_placement(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    root: str = typer.Option(..., "--root", help="The orchestration subtree root this dispatch belongs to."),
    phase: str = typer.Option(..., "--phase", help="The phase of the dispatch being recorded."),
    worktree: str = typer.Option(..., "--worktree", help="The observed absolute worktree path."),
    branch: str = typer.Option(..., "--branch", help="The observed branch name, without `refs/heads/`."),
    repo_name: str = typer.Option("", "--repo-name", help="Select among several declared repositories."),
    repo: str = typer.Option(
        "",
        "--repo",
        help=(
            "Declared repository the observed pair lives in; one other than the item's own is "
            "recorded under repo_stamps."
        ),
    ),
    start_sha: str = typer.Option("", "--start-sha", help="The observed commit this placement's work starts from."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the placement record as JSON."),
) -> None:
    """Record where a dispatched stage runs. Never advances PATH.

    Writes `worktree`, `branch` and `updated` -- or, with `--repo` naming a
    repository other than PATH's own, `repo_stamps[NAME]` and `updated`
    instead. Refuses -- writing nothing -- when PATH is not ROOT or its
    descendant, when a descendant is recorded at `design` or `plan`, when
    PATH's phase is no longer `--phase`, and for an invalid or terminal item
    or an invalid observation. An identical pair is a no-op. The values must
    be observed, never the planner's requested names.

    `--repo-name` selects the code repository when `workspace.yaml` declares
    several and PATH's chain sets no `repo:`; an item whose own or an
    ancestor's `repo:` already resolves one needs neither flag, and a
    conflicting `--repo-name` refuses.

    `--repo` names the repository the pair was observed in. A repository
    other than PATH's own records under `repo_stamps`; its own records the
    scalar pair.
    """
    layout = resolve_workspace(workspace)
    try:
        result = run_record_placement(
            layout,
            path,
            root=root,
            phase=phase,
            worktree=worktree,
            branch=branch,
            today=_today(),
            repo_name=repo_name or None,
            repo=repo or None,
            dry_run=dry_run,
            start_sha=start_sha or None,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.placement_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        for failure in payload["failures"]:
            rendering.warn(failure)
        rendering.fail(f"{path}: apply was incomplete", reason="incomplete-apply", payload=payload)
    for warning in payload["warnings"]:
        rendering.warn(warning)
    if json_output:
        rendering.emit(payload)
        if payload["repo_note"]:
            rendering.warn(payload["repo_note"])
        return
    rendering.render_placement(payload)
    rendering.render_commit(payload["commit"])


@work_app.command(name="record-baseline")
def record_baseline(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    cwd: str = typer.Option("", "--cwd", help="Checkout whose HEAD is recorded; default: the current directory."),
    repo_name: str = typer.Option("", "--repo-name", help="Select among several declared repositories."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the baseline record as JSON."),
) -> None:
    """Record where this item's execute stage starts.

    Records HEAD of --cwd (default: the current directory), which must be in the
    item's code repository. Run by the workflow skill before an execute stage's
    code work starts. An already-recorded baseline HEAD descends from is kept; an
    unrelated one refuses.
    """
    layout = resolve_workspace(workspace)
    try:
        result = run_record_baseline(
            layout,
            path,
            cwd=Path(cwd) if cwd else Path.cwd(),
            today=_today(),
            repo_name=repo_name or None,
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.baseline_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        for failure in payload["failures"]:
            rendering.warn(failure)
        rendering.fail(f"{path}: apply was incomplete", reason="incomplete-apply", payload=payload)
    for warning in payload["warnings"]:
        rendering.warn(warning)
    if json_output:
        rendering.emit(payload)
        if payload["repo_note"]:
            rendering.warn(payload["repo_note"])
        return
    typer.echo(f"{path}: start_sha {payload['after']} ({'recorded' if payload['written'] else 'unchanged'})")
    rendering.render_commit(payload["commit"])


@work_app.command(name="record-reader")
def record_reader(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    root: str = typer.Option(..., "--root", help="The orchestration subtree root this dispatch belongs to."),
    phase: str = typer.Option(..., "--phase", help="The phase of the dispatch being recorded."),
    task_id: str = typer.Option(..., "--task-id", help="The dispatched task identifier."),
    dispatch_id: str = typer.Option(..., "--dispatch-id", help="The dispatch attempt identifier."),
    dispatch_key: str = typer.Option(..., "--dispatch-key", help="The planner's dispatch key."),
    repo: str = typer.Option(..., "--repo", help="The declared repository the reader observed."),
    worktree: str = typer.Option(..., "--worktree", help="The observed absolute worktree path."),
    start_sha: str = typer.Option(..., "--start-sha", help="The observed full detached commit OID."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the reader receipt as JSON."),
) -> None:
    """Record the detached commit a design/plan dispatch actually launched on.

    Writes one receipt under the workspace cache; never the item page, never a stamp.
    """
    layout = resolve_workspace(workspace)
    try:
        result = run_record_reader(
            layout,
            path,
            root=root,
            phase=phase,
            observation=ReaderObservation(task_id, dispatch_id, dispatch_key, repo, worktree, start_sha),
            dry_run=dry_run,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.reader_receipt_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if payload["conflict"] is not None:
        rendering.fail(f"{path}: attempt-mismatch", reason="attempt-mismatch", payload=payload)
    if json_output:
        rendering.emit(payload)
        return
    rendering.render_reader_receipt(payload)


@work_app.command(name="touch-active-work")
def touch_active_work(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the stamped pointer as JSON."),
) -> None:
    """Point transcript capture at PATH's current phase. Never advances PATH.

    `/gw:workflow` runs this before every stage skill, so the session's
    `SessionEnd` capture is labelled with the phase it ran. Refuses, writing
    nothing, for an unknown, unreadable, terminal, or unphased item. A failed
    pointer write only warns: capture is provenance, never a gate.
    """
    layout = resolve_workspace(workspace)
    try:
        result = work.run_touch_active_work(layout, path, today=_today())
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.touch_active_work_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if payload["pointer_path"] is None:
        rendering.warn(f"{path}: active-work pointer was not written (cache directory unwritable)")
        if json_output:
            rendering.emit(payload)
        return
    if json_output:
        rendering.emit(payload)
        return
    typer.echo(f"{path}: active-work pointer -> {payload['phase']}")


@work_app.command()
def orchestrate(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical root concept path."),
    live: str = typer.Option(
        "", "--live", help="Comma-separated running dispatch keys (session names, gw-<phase>-<slug>)."
    ),
    repo_name: str = typer.Option("", "--repo-name", help="Select among several declared repositories."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the dispatch plan as JSON."),
) -> None:
    """Compute the auto-drive dispatch plan for PATH's subtree. Read-only.

    A planner, not an executor: it never launches a dispatch, mutates an item,
    provisions a worktree, or edits the manifest.

    `--repo-name` selects the code repository when `workspace.yaml` declares
    several; without it such a workspace refuses rather than guess.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace, json_mode=json_output, command="work orchestrate")
    try:
        result = run_orchestrate(layout, path, live=tuple(rendering.split_csv(live)), repo_name=repo_name or None)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except ValueError as exc:
        rendering.fail(str(exc), reason="unresolved", code=exit_codes.AMBIGUOUS, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.orchestrate_payload(result)
    if json_output:
        rendering.emit(payload)
        for warning in payload["warnings"]:
            rendering.warn(warning)
    else:
        rendering.render_orchestrate(payload)


@work_app.command()
def dispatch(
    key: str = typer.Argument(..., help="The dispatch key from this cycle's plan (dispatches[].key)."),
    plan: str = typer.Option(..., "--plan", help="Saved `gw work orchestrate --json` output, or - for stdin."),
    run: str = typer.Option(..., "--run", help="The Orca Run id."),
    no_probe: bool = typer.Option(False, "--no-probe", help="Skip the submission probe."),
    settle_seconds: float = typer.Option(10.0, "--settle-seconds", help="Probe settle interval."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the dispatch envelope as JSON."),
) -> None:
    """Dispatch one saved planned stage, resuming a journaled attempt by key."""
    layout = resolve_workspace(workspace, json_mode=json_output, command="work dispatch")
    try:
        document = json.loads(sys.stdin.read() if plan == "-" else Path(plan).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        rendering.fail(f"--plan {plan!r}: {exc}", reason="usage", cause=exc)
    try:
        result = run_dispatch(
            layout,
            key,
            plan=document,
            run_id=run,
            port=orca_port(),
            today=_today(),
            clock=lambda: datetime.now(UTC),
            probe=not no_probe,
            settle_seconds=settle_seconds,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.dispatch_payload(result)
    if not payload["ok"]:
        _step_failure(payload)
    if json_output:
        rendering.emit(payload)
        return
    if payload["placement"] is None:
        typer.echo(f"{payload['status']} {payload['task_id']} for {key}; placement was not read back")
        return
    placement = payload["placement"]
    if placement.get("branch") is None and placement.get("start_sha"):
        typer.echo(
            f"dispatched {key} -> {placement.get('path')} detached at {placement['start_sha']} ({payload['status']})"
        )
    else:
        typer.echo(f"dispatched {key} -> {placement.get('path')} on {placement.get('branch')} ({payload['status']})")
    for note in placement.get("notes", []):
        typer.echo(f"note {key}: {note}")


@work_app.command()
def reroute(
    key: str = typer.Argument(..., help="The dispatch key whose current Task has settled."),
    run: str = typer.Option(..., "--run", help="The Orca Run id."),
    reason: str = typer.Option(..., "--reason", help="Why the current Task is superseded."),
    agent: str = typer.Option("", "--agent", help="Replacement agent."),
    model: str = typer.Option("", "--model", help="Replacement model."),
    effort: str = typer.Option("", "--effort", help="Replacement reasoning effort."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the reroute envelope as JSON."),
) -> None:
    """Supersede a settled Task and journal overrides for its next dispatch."""
    layout = resolve_workspace(workspace, json_mode=json_output, command="work reroute")
    try:
        result = run_reroute(
            layout,
            key,
            run_id=run,
            reason=reason,
            port=orca_port(),
            clock=lambda: datetime.now(UTC),
            agent=agent or None,
            model=model or None,
            effort=effort or None,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.reroute_payload(result)
    if not payload["ok"]:
        _step_failure(payload)
    if json_output:
        rendering.emit(payload)
        return
    typer.echo(f"rerouted {key}: superseded {payload['superseded_task_id']} ({payload['status']})")


@work_app.command()
def wait(
    run: str = typer.Option(..., "--run", help="The Orca Run id."),
    ack: str = typer.Option(
        "", "--ack", help="The previous event's delivery_id, acked before waiting; ignored when --timeout-s is 0."
    ),
    timeout_s: float = typer.Option(
        600.0,
        "--timeout-s",
        min=0,
        help=(
            f"Seconds before timeout; values between 0 and {WAIT_FLOOR_S} are raised to {WAIT_FLOOR_S}; "
            "0 probes binding and returns any delivery without acknowledging it."
        ),
    ),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the wait result as JSON."),
) -> None:
    """Wait for a real coordinator event; heartbeats and duplicate completions never wake it."""
    layout = resolve_workspace(workspace, json_mode=json_output, command="work wait")
    timeout_s, floor_warning = apply_wait_floor(timeout_s, exempt_zero=True, option="--timeout-s", default=600.0)
    try:
        result = run_wait(
            orca_port(),
            run,
            ack=ack or None,
            timeout_s=timeout_s,
            clock=_wait_clock(),
            gate_waits=partial(gate_wait_facts, layout),
        )
    except WaitFailed as exc:
        rendering.fail(
            f"work wait --run {run}: {exc}",
            reason="refused",
            payload={"run_id": exc.run_id, "code": exc.code},
            cause=exc,
        )
    if floor_warning is not None:
        result = dataclasses.replace(result, warnings=(floor_warning, *result.warnings))
    payload = wire_work.wait_payload(result)
    if json_output:
        rendering.emit(payload)
        return
    if payload["status"] == "event":
        line = f"event: {len(payload['messages'])} message(s) in {payload['delivery_id']}"
    else:
        line = f"timeout after {payload['waited_s']}s"
    if payload["sleep_gap"] is not None:
        line += f"; sleep gap {payload['sleep_gap']['seconds']}s"
    if payload["rebound"]:
        line += "; rebound"
    typer.echo(line)
    for warning in result.warnings:
        typer.echo(f"warning: {warning}")


@work_app.command(name="regen-index")
def regen_index(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the reconciliation as JSON."),
) -> None:
    """Reconcile `work/index.md` against what is on disk.

    Reconciles the Markdown index; it does not rebuild a JSON sidecar. That
    migration is deliberate (spec 1) and `work-index.json` is not coming back.
    """
    layout = resolve_workspace(workspace)
    try:
        update = work.run_regen_indexes(layout, dry_run=dry_run)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.regen_index_payload(update)
    for warning in payload["warnings"]:
        rendering.warn(warning)
    if payload["refusals"]:
        _warn_refusals(payload["refusals"])
        rendering.fail("index reconciliation refused; nothing was applied", reason="refused", payload=payload)
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        for failure in payload["failures"]:
            rendering.warn(failure)
        rendering.fail("index reconciliation apply was incomplete", reason="incomplete-apply", payload=payload)
    if json_output:
        rendering.emit(payload)
    elif payload["indexes"]:
        for index in payload["indexes"]:
            typer.echo(f"{'would reconcile' if dry_run else 'reconciled'} {index}")
    else:
        typer.echo("nothing to do")
    if not json_output:
        rendering.render_commit(payload["commit"])


@work_app.command()
def archive(
    path: list[str] = typer.Argument(  # noqa: B008
        None, help="Canonical paths to archive. Omit to sweep every eligible item."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of moving."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the archive run as JSON."),
) -> None:
    """Relocate terminal work items to `work/_archive/`, repairing the OKF
    markdown references that pointed at them.

    `[[wikilink]]` forms are not an OKF link form, are never rewritten, and
    are reported to stderr as stranded instead -- per lane, never merged.

    Only top-level items archive; a child moves with its root. Sweep mode
    selects every top-level item whose whole subtree is terminal and skips
    the rest silently. Targeted mode prints each refused path with its
    refusal kind to stderr -- `not-top-level` names the root to archive
    instead -- and exits non-zero with nothing applied. The wiki lane is
    never involved.
    """
    layout = resolve_workspace(workspace)
    targeted = list(path or ())
    try:
        run = run_archive(layout, targeted or None, today=_today(), dry_run=dry_run)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)

    payload = wire_work.archive_payload(run, dry_run=dry_run)
    for warning in payload["warnings"]:
        rendering.warn(warning)
    # Two counts, never a merged total: `run_archive` keeps `plan` and
    # `wiki_plan` separate for the same reason -- a caller acting on the
    # number needs to know which lane stranded what.
    for warning in stranded_warnings(run):
        rendering.warn(warning)
    if payload["conflict"]:
        rendering.fail(
            f"cross-lane conflict on {', '.join(payload['conflict'])}; nothing was applied",
            reason="conflict",
            payload=payload,
        )
    if payload["refusals"]:
        _warn_refusals(payload["refusals"])
        rendering.fail("archive refused; nothing was applied", reason="refused", payload=payload)
    if payload["applied"] and (payload["rolled_back"] or payload["failures"]):
        for failure in payload["failures"]:
            rendering.warn(failure)
        rendering.fail("archive apply was incomplete", reason="incomplete-apply", payload=payload)
    if not run.ok or (targeted and any(path not in payload["path_mapping"] for path in targeted)):
        rendering.fail("archive did not complete for every requested path", reason="incomplete", payload=payload)

    if json_output:
        rendering.emit(payload)
    else:
        typer.echo(
            "\n".join(f"{source} -> {destination}" for source, destination in payload["path_mapping"].items())
            or "nothing to archive"
        )
        for source, destination in payload["path_mapping"].items():
            typer.echo(f"archived {source} -> {destination}")
        for path in payload["indexes"]:
            typer.echo(f"reconciled {path}")
        if payload["logged"]:
            typer.echo(f"log.md: {payload['logged']}")
        rendering.render_commit(payload["commit"])
        rendering.render_commit(payload["wiki_commit"])


def _finish_path_mutation(payload: dict[str, object], *, dry_run: bool, json_output: bool) -> None:
    warnings = payload["warnings"]
    assert isinstance(warnings, list)
    for warning in warnings:
        rendering.warn(str(warning))
    refusals = payload["refusals"]
    assert isinstance(refusals, list)
    if refusals:
        _warn_refusals(refusals)
        rendering.fail("work mutation refused; nothing was applied", reason="refused", payload=payload)
    failures = payload["failures"]
    assert isinstance(failures, list)
    if payload["applied"] and (payload["rolled_back"] or failures):
        for failure in failures:
            rendering.warn(str(failure))
        rendering.fail("work mutation apply was incomplete", reason="incomplete-apply", payload=payload)
    if json_output:
        rendering.emit(payload)
    elif dry_run:
        mapping = payload["path_mapping"]
        assert isinstance(mapping, dict)
        typer.echo(
            "\n".join(f"{source} -> {destination}" for source, destination in mapping.items()) or "nothing to do"
        )
    else:
        typer.echo("[ok] work mutation applied")
        rendering.render_commit(cast(dict[str, Any] | None, payload["commit"]))


@work_app.command()
def reparent(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    parent_path: str = typer.Option(..., "--parent", help="Canonical destination parent path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the mutation as JSON."),
) -> None:
    """Move a complete work subtree beneath PARENT_PATH."""
    layout = resolve_workspace(workspace)
    try:
        result = work.run_reparent(layout, path, parent_path, dry_run=dry_run)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    _finish_path_mutation(wire_work.path_mutation_payload(result), dry_run=dry_run, json_output=json_output)


@work_app.command()
def adopt(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    release_path: str = typer.Option(..., "--release", help="Canonical destination Release path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the mutation as JSON."),
) -> None:
    """Adopt an active root subtree beneath a Release."""
    layout = resolve_workspace(workspace)
    try:
        result = work.run_release_adoption(layout, path, release_path, dry_run=dry_run)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except (OSError, ValueError) as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    _finish_path_mutation(wire_work.path_mutation_payload(result), dry_run=dry_run, json_output=json_output)


@work_app.command(name="prepare-workspace")
def prepare_workspace(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    apply: bool = typer.Option(False, "--apply", help="Create and record; without it, print the plan."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the preparation as JSON."),
) -> None:
    """Create PATH's gw-owned workspace worktree and branch, outermost owner first.

    Plans by default. With `--apply`, runs `git worktree add` under
    `<worktrees_dir>/workspace/<stem>` and records `repo_stamps[_workspace]`.
    Idempotent. A workspace that is not its own git toplevel, or has
    `workflow.workspace_commits: off`, prints a note and does nothing.
    """
    layout = resolve_workspace(workspace)
    try:
        result = run_prepare_workspace(layout, path, today=_today(), apply=apply)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.prepare_workspace_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_prepare_workspace(payload)
    if payload["note"]:
        rendering.warn(payload["note"])


@work_app.command(name="integrate")
def integrate(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    repo: str = typer.Option(..., "--repo", help="Declared repository whose finish target to integrate."),
    strategy: str = typer.Option(
        "",
        "--strategy",
        help="squash, merge (--no-ff) or ff. Default: repositories.<name>.finish.strategy, else squash.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Merge and record; without it, print the plan."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the integration result as JSON."),
) -> None:
    """Integrate PATH's finish source for one code repository and record its receipt.

    gw chooses the merge flags for the strategy, runs the merge in the target
    worktree and records v2 evidence under the owner lock. A conflict or a
    rejected commit restores the target and refuses. Plans by default.
    """
    chosen: IntegrationStrategy | None = None
    if strategy:
        if strategy not in STRATEGIES:
            rendering.fail(f"--strategy {strategy!r}: expected squash, merge or ff", reason="usage")
        chosen = strategy
    layout = resolve_workspace(workspace)
    try:
        result = run_integrate(
            layout,
            path,
            repo_name=repo,
            strategy=chosen,
            today=_today(),
            apply=apply,
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.integrate_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_integrate(payload)


@work_app.command(name="accept-integration")
def accept_integration(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    repo: str = typer.Option(
        ..., "--repo", help="Declared repository (or _workspace) whose finish target this settles."
    ),
    evidence: str = typer.Option(..., "--evidence", help="Commit on the target branch that holds the integration."),
    reason: str = typer.Option("", "--reason", help="Why this evidence stands (e.g. squashed in PR 12)."),
    by: str = typer.Option("", "--by", help="Who attests: the human's handle, never a worker."),
    apply: bool = typer.Option(False, "--apply", help="Record; without it, print the plan."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the acceptance as JSON."),
) -> None:
    """Record human-attested integration evidence gw cannot verify, with a ledger entry.

    For a rebase, an external squash or a PR merged upstream: the receipt entry
    is marked accepted, not verified. It never advances the item. Plans by default.
    """
    layout = resolve_workspace(workspace)
    try:
        result = run_accept_integration(
            layout, path, repo_name=repo, evidence=evidence, reason=reason, by=by, today=_today(), apply=apply
        )
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.accept_integration_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_accept_integration(payload)


@work_app.command(name="merge-workspace")
def merge_workspace(
    path: str = typer.Argument(..., help="Extensionless bundle-relative canonical concept path."),
    apply: bool = typer.Option(False, "--apply", help="Merge and record; without it, print the plan."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the merge result as JSON."),
) -> None:
    """Merge PATH's workspace branch into workspace main and record its receipt.

    The one sanctioned non-verb commit on workspace main; a conflict aborts and
    refuses, holding the finish. Plans by default; only --apply changes Git.
    """
    layout = resolve_workspace(workspace)
    try:
        result = run_merge_workspace(layout, path, today=_today(), apply=apply)
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
    except OSError as exc:
        rendering.fail(str(exc), reason="io", cause=exc)
    payload = wire_work.merge_workspace_payload(result)
    if payload["refusal"] is not None:
        rendering.fail(
            f"{path}: refused ({payload['refusal']['reason']}) — {payload['refusal']['detail']}",
            reason="refused",
            payload=payload,
        )
    if json_output:
        rendering.emit(payload)
    else:
        rendering.render_merge_workspace(payload)
