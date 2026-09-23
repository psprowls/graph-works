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

from datetime import UTC, date, datetime

import typer
from graph_works_core.archive.commands import run_archive, stranded_warnings
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.orchestrate.placement import run_record_placement
from graph_works_core.orchestrate.stage_advance import ExpectedPhase, run_stage_advance
from graph_works_core.work import commands as work
from graph_works_core.workspace.config import WorkspaceConfig, load_workspace_config
from graph_works_core.workspace.errors import WorkspaceConfigError, WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_wire import work as wire_work

from graph_works_cli import exit_codes
from graph_works_cli.provenance import warn_if_stale_routing
from graph_works_cli.work_cli import rendering
from graph_works_cli.work_cli.decision import decision_app
from graph_works_cli.work_cli.reconcile import reconcile_context
from graph_works_cli.workspace_resolution import resolve_workspace

work_app = typer.Typer(name="work", help="Work-item pipeline verbs.", no_args_is_help=True)
work_app.add_typer(decision_app, name="decision")
work_app.command(name="reconcile-context")(reconcile_context)


def _today() -> date:
    """One UTC date per invocation. Core never reads the clock (spec 6)."""
    return datetime.now(UTC).date()


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
    affects: str = typer.Option("", "--affects", help="Comma-separated repo paths or package names."),
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
    if payload["applied"] and (payload["rolled_back"] or failures):
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
    strict: bool = typer.Option(False, "--strict", help="Promote every warning to an error."),
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the report as JSON."),
) -> None:
    """Report work-lane conformance. Never writes.

    The fast, synchronous, LLM-free work-lane check -- a sibling of
    `gw wiki lint`, not a subset of it.
    """
    layout = resolve_workspace(workspace)
    config = _config(layout)
    try:
        report = work.run_lint(layout, config, repo_roots=resolve_repos(layout), strict=strict, today=_today())
    except WorkspaceError as exc:
        rendering.fail(str(exc), reason="workspace", code=exit_codes.SCHEMA_MISMATCH, cause=exc)
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
    workspace: str = typer.Option("", "--workspace", help="Workspace path."),
    json_output: bool = rendering.json_option("Emit the routing decision as JSON."),
) -> None:
    """Compute what to dispatch for PATH, and what advancing would change.

    Its one permitted write is the canonical design-source repair reported as
    `normalized` -- it is applied, not previewed, so the next read agrees with
    this one.
    """
    warn_if_stale_routing()
    layout = resolve_workspace(workspace)
    try:
        result = work.run_next(layout, path, descend=descend, dry_run=False)
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
    layout = resolve_workspace(workspace)
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

    Sweep mode reports no skips -- a sweep's non-candidates were never
    candidates. Targeted mode reports one per named path that did not move,
    and exits non-zero, because there the caller named the path and is owed
    an answer. The wiki lane is never involved.
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
