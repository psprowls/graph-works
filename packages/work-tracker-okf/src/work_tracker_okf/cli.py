"""Typer console app: `work-tracker-okf init <bundle-root> [--dry-run]`.

The one module in this package that may read the clock -- everything
downstream takes `today` as an argument and never reads it itself.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import typer
from okf_ext.bundle import WriteFailure
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle, Finding, Rule, load_bundle
from okf_io import validate as okf_validate

from work_tracker_okf.archive import apply_archive, plan_archive
from work_tracker_okf.children import apply_children_sync, plan_children_sync
from work_tracker_okf.compose import (
    FilingOutcome,
    advance_and_stamp,
    append_lane_log,
    apply_file_and_reconcile,
    plan_file_and_reconcile,
    rule_set,
)
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.filing import FilingSeed
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.init import InitError, install_bundle
from work_tracker_okf.items import ARCHIVE_IGNORE, IGNORE, load_items
from work_tracker_okf.paths import item_page
from work_tracker_okf.projection import rollup, select_resume
from work_tracker_okf.workflow import RouteResult, Transition, route, state_for

app = typer.Typer(add_completion=False, no_args_is_help=True)


# Not dead code: with a single registered command, Typer otherwise collapses
# the app so its name is skipped on the command line (`init` would be swallowed
# as `bundle_root`) -- this callback keeps `init` an explicit subcommand, so
# child 6 can add the rest of the CLI without a breaking change.
@app.callback()
def _callback() -> None:
    """Track work items as an OKF v0.2 lane."""


def _echo_failure(failure: WriteFailure) -> None:
    """One refusal line to stderr -- shared by the per-file loop and the
    singleton `log_failure`, so the two channels cannot drift in wording."""
    typer.echo(f"refused {failure.path} ({failure.kind}): {failure.error}", err=True)


def _today(value: str | None) -> date:
    """`--today`, or the clock.

    Every clock-reading command takes the flag, `init` included (C6-J) -- one
    rule with no exception to explain. Visible rather than hidden because
    linting a vault *as of* a date is a real thing to want, and because it is
    what lets a CLI test run against the conformant vault, whose properties are
    pinned at `CONFORMANT_TODAY`.
    """
    if value is None:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        typer.echo(f"--today {value!r}: not an ISO 8601 date (YYYY-MM-DD)", err=True)
        raise typer.Exit(code=1) from exc


def _bundle(root: Path, *, ignore: tuple[str, ...] = IGNORE) -> Bundle:
    """Load *root* through a lane recipe. A root that is not a directory is
    caller configuration, so it exits 1 with a message rather than letting
    `load_bundle` raise at a human."""
    if not root.is_dir():
        typer.echo(f"{root}: not a directory", err=True)
        raise typer.Exit(code=1)
    return load_bundle(root, ignore=ignore)


def _rules(root: Path, *, repo_root: Path | None, declarations_dir: Path | None) -> tuple[Rule, ...]:
    """`compose.rule_set`, with its two configuration failures turned into an
    exit code. `code_wiki_okf.cli.validate` guards the identical load the
    identical way."""
    try:
        return rule_set(root, repo_root=repo_root, declarations_dir=declarations_dir)
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _emit(payload: object) -> None:
    """One JSON document on stdout. `--json` is for humans scripting a vault;
    tier 4 imports the API, because JSON erases the `Literal` types E-C exists
    to preserve (C6-K)."""
    typer.echo(json.dumps(payload, indent=2))


def _transition_json(transition: Transition | None) -> dict[str, Any] | None:
    if transition is None:
        return None
    return {
        "phase": transition.phase,
        "workflow_status": transition.workflow_status,
        "document_status": transition.document_status,
        "requires": list(transition.requires),
        "sync_plan_table": transition.sync_plan_table,
        "stamp_source": transition.stamp_source,
    }


def _rollup_json(child_rollup: ChildRollup | None) -> dict[str, Any] | None:
    if child_rollup is None:
        return None
    return {
        "total": child_rollup.total,
        "terminal": child_rollup.terminal,
        "open_slugs": list(child_rollup.open_slugs),
    }


def _finding_json(finding: Finding) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "message": finding.message,
        "spec": finding.spec,
        "path": finding.path,
        "line": finding.line,
    }


def _echo_findings(findings: tuple[Finding, ...], errors: tuple[Finding, ...]) -> None:
    """Errors to stderr, warnings to stdout -- `code-wiki-okf`'s convention, so
    a piped `lint` carries only what a reader asked for."""
    failures = {id(finding) for finding in errors}
    for finding in findings:
        line = f"{finding.severity:<6} {finding.code}: {finding.message}"
        typer.echo(line, err=id(finding) in failures)


def _sections(root: Path, declarations_dir: Path | None) -> SectionSet:
    """The bundle's own `_sections/`, not this package's assets.

    Normally identical -- `init` seeds byte-for-byte copies -- but they diverge
    the moment a human edits the bundle's `_sections/Bug.yaml`, and then `file`
    writes a body from one shape while `lint` checks it against another. One
    bundle, one answer about what a `Bug` page looks like. `code-wiki-okf`'s
    `sync` makes the identical call for the identical reason.
    """
    declarations = root if declarations_dir is None else declarations_dir
    try:
        return load_sections(declarations / "_sections")
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def init(
    bundle_root: Path = typer.Argument(..., help="Directory to install this package into."),  # noqa: B008
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--declarations-dir",
        help="Where `_schema/` and `_sections/` live. Defaults to BUNDLE_ROOT. Not persisted: "
        "this package writes no configuration file, so pass it again if a later command needs it.",
    ),
    today_option: str | None = typer.Option(
        None, "--today", help="Stamp the `log.md` entry with YYYY-MM-DD instead of now."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
) -> None:
    """Install work-tracker-okf into BUNDLE_ROOT, scaffolding it if it is new.

    Additive and idempotent: a bundle another package already created is
    installed into rather than refused, and a re-run writes nothing. The only
    refusal is a file this package owns that exists with content it did not
    write, reported per file -- its neighbours still land.
    """
    today = _today(today_option)
    if declarations_dir is not None and not declarations_dir.is_dir():
        typer.echo(f"--declarations-dir {declarations_dir}: not a directory", err=True)
        raise typer.Exit(code=1)
    try:
        result = install_bundle(
            bundle_root,
            today=today,
            declarations_dir=declarations_dir,
            dry_run=dry_run,
        )
    except InitError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    verb = "would write" if dry_run else "wrote"
    for member in (*result.scaffold.written, *result.install.written):
        typer.echo(f"{verb} {member}")
    for item in (*result.scaffold.skipped, *result.install.skipped):
        typer.echo(f"skipped {item.path}")
    for failure in (*result.scaffold.failed, *result.install.failed):
        _echo_failure(failure)
    if result.log_failure is not None:
        _echo_failure(result.log_failure)
    if result.vocabulary is not None:
        for name in result.vocabulary.added:
            typer.echo(f"{verb} _tags.yaml: {name}")
        for drifted in result.vocabulary.drift:
            typer.echo(f"kept your description of `{drifted.name}` in _tags.yaml; ours differs", err=True)
    for failure in result.vocabulary_problems:
        _echo_failure(failure)
    if result.logged is not None:
        typer.echo(f"{verb} log.md: {result.logged}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("next")
def next_stage(
    root: Path = typer.Argument(..., help="Bundle root to read."),  # noqa: B008
    slug: str = typer.Argument(..., help="The work item to route."),
    json_output: bool = typer.Option(False, "--json", help="Emit the routing decision as JSON."),
) -> None:
    """Report what to dispatch for SLUG, and what advancing would change.

    Never writes and never reads the clock -- routing is a pure function of the
    item graph, so this command takes no `--today`.
    """
    items = load_items(_bundle(root))
    state = state_for(items, slug)
    if state is None:
        typer.echo(f"unknown slug {slug!r}: no item page under either lane", err=True)
        raise typer.Exit(code=1)
    result: RouteResult = route(state)
    if json_output:
        _emit(
            {
                "slug": slug,
                "type": state.type,
                "workflow_status": state.workflow_status,
                "phase": state.phase,
                "effort": state.effort,
                "dispatch": None
                if result.dispatch is None
                else {"stage": result.dispatch.stage, "variant": result.dispatch.variant},
                "reason": result.reason,
                "on_dispatch": _transition_json(result.on_dispatch),
                "on_complete": _transition_json(result.on_complete),
                "blockers": list(result.blockers),
                "child_rollup": _rollup_json(state.child_rollup),
            }
        )
        return
    if result.dispatch is None:
        typer.echo(f"{slug}: nothing to dispatch -- {result.reason}")
    else:
        typer.echo(f"{slug}: {result.dispatch.stage}/{result.dispatch.variant} -- {result.reason}")
    for blocker in result.blockers:
        typer.echo(f"  blocked: {blocker}")


@app.command()
def status(
    root: Path = typer.Argument(..., help="Bundle root to read."),  # noqa: B008
    json_output: bool = typer.Option(False, "--json", help="Emit the rollup as JSON."),
) -> None:
    """Count the active items and name the one worth resuming. Never writes."""
    items = load_items(_bundle(root))
    counts = rollup(items)
    resume = select_resume(items)
    if json_output:
        _emit(
            {
                "total": counts.total,
                "by_workflow_status": dict(counts.by_workflow_status),
                "by_type": dict(counts.by_type),
                "by_phase": dict(counts.by_phase),
                "children": {slug: _rollup_json(rolled) for slug, rolled in counts.children.items()},
                "resume": None
                if resume is None
                else {
                    "primary": {"slug": resume.primary.slug, "title": resume.primary.title},
                    "alternatives": [{"slug": item.slug, "title": item.title} for item in resume.alternatives],
                },
            }
        )
        return
    typer.echo(f"{counts.total} item(s) under work/")
    for label, mapping in (
        ("workflow_status", counts.by_workflow_status),
        ("type", counts.by_type),
        ("phase", counts.by_phase),
    ):
        rendered = ", ".join(f"{key} {value}" for key, value in mapping.items())
        typer.echo(f"  by {label}: {rendered or '-'}")
    if resume is not None:
        typer.echo(f"  resume: {resume.primary.slug} -- {resume.primary.title}")


@app.command()
def lint(
    root: Path = typer.Argument(..., help="Bundle root to validate."),  # noqa: B008
    repo_root: Path | None = typer.Option(  # noqa: B008
        None,
        "--repo-root",
        help="Where `affects` entries and plan-action tokens resolve. Omitting it SKIPS "
        "`targets.affects-missing` and `plan.action-target-missing` rather than reporting them.",
    ),
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--declarations-dir",
        help="Where `_schema/` and `_sections/` live. Defaults to ROOT. Not persisted.",
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as failures."),
    today: str | None = typer.Option(None, "--today", help="Validate as of YYYY-MM-DD instead of now."),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Report conformance and lane findings for ROOT. Never writes.

    Exits 1 when `report.ok` is false. `--strict` promotes every warning to an
    error first, which is why it fails even a conformant vault (C6-L) and must
    not be wired into an acceptance gate.
    """
    day = _today(today)
    bundle = _bundle(root)
    report = okf_validate(
        bundle,
        today=day,
        extra_rules=_rules(root, repo_root=repo_root, declarations_dir=declarations_dir),
        strict=strict,
    )
    if json_output:
        _emit({"ok": report.ok, "findings": [_finding_json(finding) for finding in report.findings]})
    else:
        _echo_findings(report.findings, report.errors)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def file(
    root: Path = typer.Argument(..., help="Bundle root to file into."),  # noqa: B008
    type_: str = typer.Option(..., "--type", help="Epic | Feature | Bug | TechDebt | TestGap | Spike."),
    title: str = typer.Option(..., "--title", help="The item's title."),
    description: str = typer.Option(..., "--description", help="One line, for the index entry."),
    words: str | None = typer.Option(None, "--words", help="Slug words. Defaults to the title."),
    parent: str | None = typer.Option(None, "--parent", help="The parent item's slug."),
    depends_on: list[str] = typer.Option([], "--depends-on", help="Repeatable."),  # noqa: B008
    affects: list[str] = typer.Option([], "--affects", help="Repeatable repo path."),  # noqa: B008
    tags: list[str] = typer.Option([], "--tags", help="Repeatable."),  # noqa: B008
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None, "--declarations-dir", help="Where `_sections/` lives. Defaults to ROOT. Not persisted."
    ),
    today: str | None = typer.Option(None, "--today", help="File as of YYYY-MM-DD instead of now."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
) -> None:
    """File one work item, then reconcile `work/index.md` and log the arrival.

    Filing writes one page; the index reconcile and the `log.md` line are this
    command's, out of the graph-aware composition plan (C6-I: a command that
    changes what the vault contains logs one line).

    `--dry-run` prints all three preflighted effects. The standalone command
    otherwise remains mutating by default.
    """
    bundle = _bundle(root)
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        FilingSeed(
            type=type_,
            title=title,
            description=description,
            on=_today(today),
            words=words,
            parent=parent,
            depends_on=tuple(DependencyEdge(slug) for slug in depends_on),
            affects=tuple(affects),
            tags=tuple(tags),
        ),
        _sections(root, declarations_dir),
    )
    for warning in outcome.plan.warnings:
        typer.echo(warning, err=True)
    if outcome.plan.refusal is not None:
        typer.echo(outcome.plan.filing.diff(), err=True)
        typer.echo(f"{outcome.plan.filing.slug}: refused ({outcome.plan.refusal})", err=True)
        raise typer.Exit(code=1)
    if dry_run:
        typer.echo(outcome.plan.filing.diff())
        if outcome.plan.index.changed:
            typer.echo(outcome.plan.index.diff())
        if outcome.plan.log is not None and outcome.plan.log.changed:
            typer.echo(outcome.plan.log.diff())
        return
    outcome = FilingOutcome(plan=outcome.plan, application=apply_file_and_reconcile(outcome.plan))
    typer.echo(f"wrote {item_page(outcome.plan.filing.slug).rel}")
    for update in outcome.application.indexes:
        if update.changed:
            typer.echo(f"reconciled {update.path}")
    if outcome.application.log is not None:
        typer.echo(f"wrote log.md: {outcome.application.log.entry.removeprefix('- ')}")


@app.command()
def advance(
    root: Path = typer.Argument(..., help="Bundle root to write into."),  # noqa: B008
    slug: str = typer.Argument(..., help="The work item to advance."),
    effort: str | None = typer.Option(None, "--effort", help="xtra-small | small | medium | large | xtra-large."),
    owner: str | None = typer.Option(None, "--owner", help="Handle to record when execution starts."),
    resolved_in: str | None = typer.Option(None, "--resolved-in", help="PR or commit ref."),
    repo_root: Path | None = typer.Option(  # noqa: B008
        None, "--repo-root", help="Where `affects` and plan-action tokens resolve, for the post-write check."
    ),
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None, "--declarations-dir", help="Where `_schema/` and `_sections/` live. Defaults to ROOT."
    ),
    today: str | None = typer.Option(None, "--today", help="Stamp `updated` with YYYY-MM-DD instead of now."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
) -> None:
    """Apply SLUG's next transition, stamp its artifact, ensure its plan row.

    One save (C6-E). The stamp is unconditional (C6-G), and the check below is
    what makes that safe: a pointer written at a nonexistent artifact reports
    itself immediately rather than waiting for someone to run `lint`.

    `--dry-run` stops after the plan and prints the library's own renderer. It
    cannot honestly show post-write findings, so it says the check was skipped
    -- the one place in this CLI where a dry run is not simply the real run
    minus its last line.
    """
    day = _today(today)
    # The rule set is composed *first*: a malformed `_schema/` is caller
    # configuration, and discovering it after the write would leave the page
    # advanced and the check unreportable.
    rules = _rules(root, repo_root=repo_root, declarations_dir=declarations_dir)
    outcome = advance_and_stamp(
        _bundle(root),
        slug,
        today=day,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        dry_run=dry_run,
    )
    if outcome.plan.refusal is not None:
        typer.echo(outcome.plan.diff(), err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo(outcome.plan.diff())
    else:
        typer.echo(f"wrote {item_page(slug).rel}")
        for line in outcome.plan.diff().splitlines()[1:]:
            typer.echo(line)
    if outcome.stamped is not None:
        typer.echo(f"  + sources[]: {outcome.stamped.source_id} -> {outcome.stamped.resource}")
        if outcome.plan_row:
            typer.echo(f"  + ## Plan row: Execute implementation plan: {outcome.stamped.resource}")
    if dry_run:
        typer.echo("lint check skipped (--dry-run)")
        return

    report = okf_validate(_bundle(root), today=day, extra_rules=rules)
    page = item_page(slug).rel
    mine = tuple(finding for finding in report.findings if finding.path == page)
    _echo_findings(mine, tuple(finding for finding in report.errors if finding.path == page))


@app.command()
def archive(
    root: Path = typer.Argument(..., help="Bundle root to archive within."),  # noqa: B008
    slugs: list[str] = typer.Argument(None, help="Slugs to archive. Omit to sweep every terminal item."),  # noqa: B008
    today: str | None = typer.Option(None, "--today", help="Stamp the `log.md` entry with YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of moving."),
) -> None:
    """Relocate terminal items to `work/_archive/`, repairing what pointed at them.

    A symmetric prefix move: the item page and everything under its working
    directory relocate in one batch, every inbound reference is repaired, the
    emptied directory is pruned, and both lane indexes are reconciled. **No
    frontmatter is written** -- an item reaching this path is already terminal.

    Planned through `ARCHIVE_IGNORE`, never `IGNORE`: `okf_ext.moves` never
    reads `bundle.ignored`, so the narrow recipe would plan a move covering
    only the item page.

    Sweep mode reports no skips -- a sweep's non-candidates were never
    candidates. Targeted mode reports one per named slug that did not move, and
    exits 1, because there the caller named the slug and is owed an answer.
    """
    bundle = _bundle(root, ignore=ARCHIVE_IGNORE)
    plan = plan_archive(bundle, slugs or None)
    typer.echo(plan.diff() or "nothing to archive")
    if not plan.ok:
        raise typer.Exit(code=1)
    if dry_run:
        return

    result = apply_archive(bundle, plan)
    for update in result.indexes:
        if update.changed:
            typer.echo(f"reconciled {update.path}")
    for relative in result.pruned:
        typer.echo(f"pruned {relative}")
    if result.archived:
        logged = append_lane_log(root, f"archived {', '.join(result.archived)}", on=_today(today))
        if logged is not None:
            typer.echo(f"wrote log.md: {logged}")
    for skip in result.skipped:
        typer.echo(f"skipped {skip.slug} ({skip.reason}): {skip.detail}", err=True)
    if result.skipped or not result.ok:
        raise typer.Exit(code=1)


@app.command("sync-children")
def sync_children(
    root: Path = typer.Argument(..., help="Bundle root to repair."),  # noqa: B008
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the drift instead of writing."),
) -> None:
    """Refresh every parent's derived `children:` key from what names it as `parent`.

    A whole-vault repair, and its own command rather than a side effect of
    `file`: filing writes **one page**, and folding this in would make filing
    one item write another item's page too.

    Changes existing pages' fields and nothing else, so it appends no `log.md`
    line (C6-I).
    """
    bundle = _bundle(root)
    syncs = plan_children_sync(bundle, load_items(bundle))
    if not syncs:
        typer.echo("no drift")
        return
    for sync in syncs:
        typer.echo(f"{sync.slug}: children {list(sync.before)} -> {list(sync.after)}")
    touched = apply_children_sync(bundle, syncs, dry_run=dry_run)
    typer.echo(f"{'would write' if dry_run else 'wrote'} {len(touched)} page(s)")


__all__ = ["advance", "app", "archive", "file", "init", "lint", "next_stage", "status", "sync_children"]
