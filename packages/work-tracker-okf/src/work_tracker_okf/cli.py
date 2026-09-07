"""Typer console app: `work-tracker-okf init <bundle-root> [--dry-run]`.

The one module in this package that may read the clock -- everything
downstream takes `today` as an argument and never reads it itself.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import typer
from okf_ext.bundle import WriteFailure
from okf_ext.shape import SectionSet, load_sections
from okf_ext.writing import body_digest
from okf_io import Bundle, Finding, Rule, load_bundle
from okf_io import Document as OkfDocument
from okf_io import validate as okf_validate

from work_tracker_okf.archive import plan_archive
from work_tracker_okf.compose import (
    FilingOutcome,
    advance_and_stamp,
    append_lane_log,
    apply_file_and_reconcile,
    plan_file_and_reconcile,
    rule_set,
)
from work_tracker_okf.dependencies import DependencyEdge, parse_dependencies
from work_tracker_okf.filing import FilingSeed
from work_tracker_okf.hierarchy import ChildRollup
from work_tracker_okf.init import InitError, install_bundle
from work_tracker_okf.items import ARCHIVE_IGNORE, IGNORE, load_items
from work_tracker_okf.mutation import WorkMutationPlan, directory_manifest_digest
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
    return _without_none(
        {
            "phase": transition.phase,
            "work_status": transition.work_status,
            "document_status": transition.document_status,
            "requires": list(transition.requires),
            "sync_plan_table": transition.sync_plan_table,
            "stamp_source": transition.stamp_source,
        }
    )


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    """Omit absent optionals while retaining false and empty values."""
    return {key: value for key, value in values.items() if value is not None}


def _rollup_json(child_rollup: ChildRollup | None) -> dict[str, Any] | None:
    if child_rollup is None:
        return None
    return {
        "total": child_rollup.total,
        "terminal": child_rollup.terminal,
        "open_paths": list(child_rollup.open_paths),
    }


def _finding_json(finding: Finding) -> dict[str, Any]:
    return _without_none(
        {
            "code": finding.code,
            "severity": finding.severity,
            "message": finding.message,
            "spec": finding.spec,
            "path": finding.path,
            "line": finding.line,
        }
    )


def _echo_findings(findings: tuple[Finding, ...]) -> None:
    """Send every human-readable diagnostic to stderr."""
    for finding in findings:
        line = f"{finding.severity:<6} {finding.code}: {finding.message}"
        typer.echo(line, err=True)


def _dependency_edges(values: list[str]) -> tuple[DependencyEdge, ...]:
    decoded: list[object] = []
    required = ("path", "blocks", "needs")
    for value in values:
        entry: dict[str, str] = {}
        for fragment in value.split(","):
            key, separator, raw = fragment.partition("=")
            key, raw = key.strip(), raw.strip()
            if not separator or key not in required or not raw or key in entry:
                raise ValueError(f"--dep {value!r}: expected path=...,blocks=...,needs=...")
            entry[key] = raw
        missing = [key for key in required if key not in entry]
        if missing:
            raise ValueError(f"--dep {value!r}: missing {', '.join(missing)}")
        decoded.append(entry)
    parsed = parse_dependencies(decoded)
    if parsed.issues:
        detail = "; ".join(f"{issue.code}: {issue.detail}" for issue in parsed.issues)
        raise ValueError(f"invalid --dep mapping: {detail}")
    return parsed.edges


def _sections(root: Path, declarations_dir: Path | None) -> SectionSet:
    """The bundle's own `sections/`, not this package's assets.

    Normally identical -- `init` seeds byte-for-byte copies -- but they diverge
    the moment a human edits the bundle's `sections/Bug.yaml`, and then `file`
    writes a body from one shape while `lint` checks it against another. One
    bundle, one answer about what a `Bug` page looks like. `code-wiki-okf`'s
    `sync` makes the identical call for the identical reason.
    """
    declarations = root if declarations_dir is None else declarations_dir
    try:
        return load_sections(declarations / "sections")
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def init(
    bundle_root: Path = typer.Argument(..., help="Directory to install this package into."),  # noqa: B008
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--declarations-dir",
        help="Where `schema/` and `sections/` live. Defaults to BUNDLE_ROOT. Not persisted: "
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
            typer.echo(f"{verb} tags.yaml: {name}")
        for drifted in result.vocabulary.drift:
            typer.echo(f"kept your description of `{drifted.name}` in tags.yaml; ours differs", err=True)
    for failure in result.vocabulary_problems:
        _echo_failure(failure)
    if result.logged is not None:
        typer.echo(f"{verb} log.md: {result.logged}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("next")
def next_stage(
    root: Path = typer.Argument(..., help="Bundle root to read."),  # noqa: B008
    path: str = typer.Argument(..., help="Canonical work-item path to route."),
    json_output: bool = typer.Option(False, "--json", help="Emit the routing decision as JSON."),
) -> None:
    """Report what to dispatch for PATH, and what advancing would change.

    Never writes and never reads the clock -- routing is a pure function of the
    item graph, so this command takes no `--today`.
    """
    items = load_items(_bundle(root))
    state = state_for(items, path)
    if state is None:
        typer.echo(f"unknown path {path!r}: no item page at that canonical path", err=True)
        raise typer.Exit(code=1)
    result: RouteResult = route(state)
    if json_output:
        payload = _without_none(
            {
                "path": path,
                "type": state.type,
                "work_status": state.work_status,
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
        _emit(payload)
        return
    if result.dispatch is None:
        typer.echo(f"{path}: nothing to dispatch -- {result.reason}")
    else:
        typer.echo(f"{path}: {result.dispatch.stage}/{result.dispatch.variant} -- {result.reason}")
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
        payload: dict[str, Any] = {
            "total": counts.total,
            "by_work_status": dict(counts.by_work_status),
            "by_type": dict(counts.by_type),
            "by_phase": dict(counts.by_phase),
            "children": {path: _rollup_json(rolled) for path, rolled in counts.children.items()},
        }
        if resume is not None:
            payload["resume"] = {
                "primary": {"path": resume.primary.path, "title": resume.primary.title},
                "alternatives": [{"path": item.path, "title": item.title} for item in resume.alternatives],
            }
        _emit(payload)
        return
    typer.echo(f"{counts.total} item(s) under work/")
    for label, mapping in (
        ("work_status", counts.by_work_status),
        ("type", counts.by_type),
        ("phase", counts.by_phase),
    ):
        rendered = ", ".join(f"{key} {value}" for key, value in mapping.items())
        typer.echo(f"  by {label}: {rendered or '-'}")
    if resume is not None:
        typer.echo(f"  resume: {resume.primary.path} -- {resume.primary.title}")


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
        help="Where `schema/` and `sections/` live. Defaults to ROOT. Not persisted.",
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
        _echo_findings(report.findings)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def file(
    root: Path = typer.Argument(..., help="Bundle root to file into."),  # noqa: B008
    type_: str = typer.Option(..., "--type", help="Release | Epic | Feature | Bug | TechDebt | TestGap | Spike."),
    title: str = typer.Option(..., "--title", help="The item's title."),
    description: str = typer.Option(..., "--description", help="One line, for the index entry."),
    name: str | None = typer.Option(None, "--name", help="Stable basename words. Defaults to the title."),
    parent_path: str | None = typer.Option(None, "--parent-path", help="Canonical parent item path."),
    depends_on: list[str] = typer.Option(  # noqa: B008
        [], "--dep", help="Repeatable edge: path=<canonical>,blocks=<phase>,needs=<phase|resolved>."
    ),
    affects: list[str] = typer.Option([], "--affects", help="Repeatable repo path."),  # noqa: B008
    tags: list[str] = typer.Option([], "--tags", help="Repeatable."),  # noqa: B008
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None, "--declarations-dir", help="Where `schema/` and `sections/` live. Defaults to ROOT. Not persisted."
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
    try:
        dependency_edges = _dependency_edges(depends_on)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        FilingSeed(
            type=type_,
            title=title,
            description=description,
            on=_today(today),
            name=name,
            parent_path=parent_path,
            depends_on=dependency_edges,
            affects=tuple(affects),
            tags=tuple(tags),
        ),
        _sections(root, declarations_dir),
    )
    for warning in outcome.plan.warnings:
        typer.echo(warning, err=True)
    if outcome.plan.refusal is not None:
        typer.echo(outcome.plan.filing.diff(), err=True)
        typer.echo(f"{outcome.plan.filing.path}: refused ({outcome.plan.refusal})", err=True)
        raise typer.Exit(code=1)
    if dry_run:
        typer.echo(outcome.plan.filing.diff())
        for index in outcome.plan.indexes:
            if index.changed:
                typer.echo(f"would reconcile {index.path}")
        if outcome.plan.log is not None and outcome.plan.log.changed:
            typer.echo(outcome.plan.log.diff())
        return
    outcome = FilingOutcome(plan=outcome.plan, application=apply_file_and_reconcile(outcome.plan))
    # Report the preflighted canonical target rather than composing it twice.
    typer.echo(f"wrote {outcome.plan.filing.target.relative_to(root).as_posix()}")
    for update in outcome.application.indexes:
        if update.changed:
            typer.echo(f"reconciled {update.path}")
    if outcome.application.log is not None:
        typer.echo(f"wrote log.md: {outcome.application.log.entry.removeprefix('- ')}")


@app.command()
def advance(
    root: Path = typer.Argument(..., help="Bundle root to write into."),  # noqa: B008
    path: str = typer.Argument(..., help="Canonical work-item path to advance."),
    effort: str | None = typer.Option(None, "--effort", help="xtra-small | small | medium | large | xtra-large."),
    owner: str | None = typer.Option(None, "--owner", help="Handle to record when execution starts."),
    resolved_in: str | None = typer.Option(None, "--resolved-in", help="PR or commit ref."),
    released_at: str | None = typer.Option(None, "--released-at", help="Release date (YYYY-MM-DD)."),
    repo_root: Path | None = typer.Option(  # noqa: B008
        None, "--repo-root", help="Where `affects` and plan-action tokens resolve, for the post-write check."
    ),
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None, "--declarations-dir", help="Where `schema/` and `sections/` live. Defaults to ROOT."
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
    # The rule set is composed *first*: a malformed `schema/` is caller
    # configuration, and discovering it after the write would leave the page
    # advanced and the check unreportable.
    rules = _rules(root, repo_root=repo_root, declarations_dir=declarations_dir)
    outcome = advance_and_stamp(
        _bundle(root),
        path,
        today=day,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        released_at=None if released_at is None else _today(released_at),
        dry_run=dry_run,
    )
    if outcome.plan.refusal is not None:
        typer.echo(outcome.plan.diff(), err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo(outcome.plan.diff())
    else:
        typer.echo(f"wrote {item_page(path).rel}")
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
    page = item_page(path).rel
    mine = tuple(finding for finding in report.findings if finding.path == page)
    mine_errors = tuple(finding for finding in report.errors if finding.path == page)
    _echo_findings(mine)
    if mine_errors:
        raise typer.Exit(code=1)


def _mutation_diff(plan: WorkMutationPlan) -> str:
    lines = [
        *(f"! {refusal.path}: {refusal.kind} -- {refusal.detail}" for refusal in plan.refusals),
        *(f"  {move.source} -> {move.dest}" for move in (() if plan.move_plan is None else plan.move_plan.moves)),
        *(f"  ~ {write.member}" for write in plan.writes),
    ]
    return "\n".join(lines)


def _safe_mutation_path(root: Path, member: str) -> Path:
    pure = PurePosixPath(member)
    if not member or pure.is_absolute() or pure.as_posix() != member or any(part in {".", ".."} for part in pure.parts):
        raise ValueError(f"{member}: effect path is not a canonical bundle-relative path")
    try:
        resolved_root = root.resolve(strict=True)
        target = root.joinpath(*pure.parts)
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{member}: cannot validate effect path: {exc}") from exc
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"{member}: effect path escapes the bundle root")
    return target


def _preflight_mutation_paths(plan: WorkMutationPlan) -> dict[str, Path]:
    members = {
        *plan.mkdirs,
        *plan.deletes,
        *(condition.member for condition in plan.directory_preconditions),
        *(write.member for write in plan.writes),
        *((write.source_member or write.member) for write in plan.writes),
        *(move.source for move in plan.moves),
        *(move.dest for move in plan.moves),
    }
    if plan.move_plan is not None:
        members.update(plan.move_plan.digests)
        members.update(move.source for move in plan.move_plan.moves)
        members.update(move.dest for move in plan.move_plan.moves)
    return {member: _safe_mutation_path(plan.root, member) for member in sorted(members)}


def _apply_mutation(plan: WorkMutationPlan) -> None:
    """Commit a preflighted domain effect set; the planner itself never writes."""
    if not plan.ok:
        raise ValueError("cannot apply a refused work mutation")
    root = plan.root
    effect_paths = _preflight_mutation_paths(plan)
    for condition in plan.directory_preconditions:
        target = effect_paths[condition.member]
        if condition.before_digest is None:
            if os.path.lexists(target):
                raise ValueError(f"{condition.member}: directory changed since planning (now exists); re-plan")
            continue
        if not os.path.lexists(target):
            raise ValueError(f"{condition.member}: directory changed since planning (now missing); re-plan")
        try:
            current_digest = directory_manifest_digest(target)
        except OSError as exc:
            raise ValueError(f"{condition.member}: directory changed since planning ({exc}); re-plan") from exc
        if current_digest != condition.before_digest:
            raise ValueError(f"{condition.member}: directory changed since planning; re-plan")
    for write in plan.writes:
        target = root / write.member
        preimage_member = write.source_member or write.member
        preimage = root / preimage_member
        if write.before_digest is None:
            if write.source_member is not None:
                raise ValueError(f"{preimage_member}: invalid plan without a source digest")
            if os.path.lexists(target):
                raise ValueError(f"{write.member}: changed since planning (now exists); re-plan")
            continue
        try:
            current = preimage.read_bytes()
        except OSError as exc:
            raise ValueError(f"{preimage_member}: changed since planning ({exc}); re-plan") from exc
        if hashlib.sha256(current).hexdigest() != write.before_digest:
            raise ValueError(f"{preimage_member}: changed since planning; re-plan")
        if write.source_member is not None and write.source_member != write.member and os.path.lexists(target):
            raise ValueError(f"{write.member}: changed since planning (now exists); re-plan")
    if plan.move_plan is not None:
        for member, digest in plan.move_plan.digests.items():
            try:
                document = OkfDocument.load(root / member)
            except OSError as exc:
                raise ValueError(f"{member}: changed since planning ({exc}); re-plan") from exc
            if body_digest(document.body) != digest:
                raise ValueError(f"{member}: changed since planning; re-plan")
    for move in plan.moves:
        if not os.path.lexists(root / move.source) or os.path.lexists(root / move.dest):
            raise ValueError(f"{move.source}: direct move changed since planning; re-plan")

    for relative in plan.mkdirs:
        (root / relative).mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    try:
        for write in plan.writes:
            target = root / write.member
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(write.after)
            staged[write.member] = temporary

        destinations = {
            move.dest
            for move in (() if plan.move_plan is None else plan.move_plan.moves)
            if not move.is_asset and not move.opaque
        }
        for write in plan.writes:
            target = root / write.member
            if write.member in destinations or not target.exists():
                staged.pop(write.member).replace(target)
        for move in plan.moves:
            (root / move.source).replace(root / move.dest)
        for member, temporary in tuple(staged.items()):
            temporary.replace(root / member)
            staged.pop(member)
        directory_deletes: list[Path] = []
        for member in plan.deletes:
            target = root / member
            if target.is_dir() and not target.is_symlink():
                directory_deletes.append(target)
                continue
            with suppress(FileNotFoundError):
                target.unlink()
        for directory in sorted(directory_deletes, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ValueError(f"{directory.relative_to(root)}: source directory did not empty: {exc}") from exc
    finally:
        for temporary in staged.values():
            with suppress(OSError):
                temporary.unlink()

    candidates = {(root / member).parent for member in (*plan.deletes, *(move.source for move in plan.moves))}
    for directory in sorted(candidates, key=lambda path: len(path.parts), reverse=True):
        cursor = directory
        while cursor != root and cursor.name not in {"work", "_archive"}:
            try:
                cursor.rmdir()
            except OSError:
                break
            cursor = cursor.parent


@app.command()
def archive(
    root: Path = typer.Argument(..., help="Bundle root to archive within."),  # noqa: B008
    paths: list[str] = typer.Argument(  # noqa: B008
        None, help="Canonical paths to archive. Omit to sweep every outermost terminal subtree."
    ),
    today: str | None = typer.Option(None, "--today", help="Stamp the `log.md` entry with YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of moving."),
) -> None:
    """Archive complete terminal subtrees into their nearest owner lanes."""
    bundle = _bundle(root, ignore=ARCHIVE_IGNORE)
    plan = plan_archive(bundle, load_items(bundle), paths or None)
    typer.echo(_mutation_diff(plan) or "nothing to archive")
    for warning in plan.warnings:
        typer.echo(warning, err=True)
    if not plan.ok:
        raise typer.Exit(code=1)
    if dry_run:
        return
    try:
        _apply_mutation(plan)
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if paths:
        archived = tuple(dict.fromkeys(paths))
    else:
        archived = tuple(
            source
            for source in plan.path_mapping
            if not any(source.startswith(f"{other}/children/") for other in plan.path_mapping if other != source)
        )
    if archived:
        logged = append_lane_log(root, f"archived {', '.join(archived)}", on=_today(today))
        if logged is not None:
            typer.echo(f"wrote log.md: {logged}")


def force_lf_newlines(*streams: object) -> None:
    """Pin the process's own text streams to LF.

    `sys.stdout` is built by the interpreter with `newline=None`, which translates every
    `"\n"` written through it to `os.linesep` -- CRLF on Windows. Every `typer.echo` in
    this module therefore emits CRLF there, `--json` payloads included, which breaks byte
    comparison even though the content is identical. No `open()` call is involved, so
    `scripts/check_text_io_explicit.py` cannot see this site; it is fixed once here
    instead of at every echo.

    The `isinstance` narrowing is load-bearing twice over: `TextIO` has no `reconfigure`
    in typeshed (so `sys.stdout.reconfigure(...)` fails `mypy --strict` on both platform
    arms), and a substituted stream -- pytest capture, `CliRunner`, `pythonw`'s `None` --
    is left untouched rather than crashed on.
    """
    for stream in streams:
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(newline="\n")


def main() -> None:  # pragma: no cover -- crosses a process boundary; see test_stdout_newlines.py
    """The console script. Configures the process, then hands off to Typer."""
    force_lf_newlines(sys.stdout, sys.stderr)
    app()


if __name__ == "__main__":  # pragma: no cover -- exercised via the console script
    main()


__all__ = ["advance", "app", "archive", "file", "force_lf_newlines", "init", "lint", "main", "next_stage", "status"]
