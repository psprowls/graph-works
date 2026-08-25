"""Typer console app: `doc-wiki-okf` — install the lane, and drive its proposals.

The one module in this package that may read the clock. Everything downstream
takes `today`/`at` as arguments and never reads it itself, which is okf-io's own
rule for `validate()` and `append_log_entry`.

**Proposals are addressed by target path**, because that is identity. The
`<kind>-<target_slug>` id the old CLI took no longer exists, and there is no
compatibility alias for it: `mode` in particular is now derived and never
stored, so an honest alias cannot be written. See
`docs/cutover-key-mapping.md`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, cast

import typer
from okf_ext.bundle import apply as apply_bundle
from okf_ext.bundle import plan_install, plan_scaffold
from okf_ext.moves import stranded_warning
from okf_ext.proposals import (
    PAGE_STATUSES,
    ApplyResult,
    Decision,
    PageStatus,
    Plan,
    Proposal,
    WriteFailure,
    apply,
    list_proposals,
    plan_decide,
)
from okf_ext.schemas import SchemaSet, load_schemas
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle, load_bundle

from doc_wiki_okf.ingest import (
    BatchBrief,
    DocumentBrief,
    FolderBrief,
    plan_batch_brief,
    plan_document_brief,
    plan_folder_brief,
    resolve_source_path,
)
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.lanes import LaneSet, lane_set
from doc_wiki_okf.proposals.migrate import MigrationPlan, migrate_and_move, plan_migrate
from doc_wiki_okf.proposals.promote import plan_promotion
from doc_wiki_okf.proposals.render import ReviewRenderer
from doc_wiki_okf.resources import seed_files
from doc_wiki_okf.sources import SOURCE_TYPE, plan_ingest, seed_source_kinds, source_kinds

#: `schema/` and `sections/` are declarations, not concepts, and
#: `sources/references/` holds copies of ingested material. Two patterns each,
#: for the reason `okf_ext.schemas.DEFAULT_IGNORE` gives: the first is anchored
#: at the start and so never matches a nested one.
#:
#: Ignored still means present: `has_member` counts these, which is what the
#: writer's occupancy check depends on.
#:
#: `*/.DS_Store` is the nested half of what okf-io already drops at the bundle
#: root. ADR-0028 scoped the walk's dot exclusion to the root on the argument
#: that a nested dot-entry "only exists because something deliberately created
#: a path there" -- true of `.agents/`, false of `.DS_Store`, which Finder
#: writes into every directory it is asked to display. The root pattern is
#: absent because the walk still excludes that one; only the nested case
#: reaches here.
IGNORE = (
    "schema/*",
    "*/schema/*",
    "sections/*",
    "*/sections/*",
    "sources/references/*",
    "*/sources/references/*",
    "*/.DS_Store",
)

app = typer.Typer(add_completion=False, no_args_is_help=True)
proposal_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Act on one proposal.")
app.add_typer(proposal_app, name="proposal")
source_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Record ingested material.")
app.add_typer(source_app, name="source")


@app.callback()
def _callback() -> None:
    """The documentation-wiki lane over OKF v0.2."""


def _today(value: str | None) -> date:
    """`--today`, or the clock. Visible rather than hidden because driving a
    vault *as of* a date is a real thing to want, and because it is what lets
    the CLI tests pin a dated ADR filename."""
    if value is None:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        typer.echo(f"--today {value!r}: not an ISO 8601 date (YYYY-MM-DD)", err=True)
        raise typer.Exit(code=1) from exc


def _at(today: date) -> datetime:
    """The aware instant `generated.at` and `verified[].at` are stamped with.

    Midnight UTC on *today* rather than `now()`: the two must agree, and a
    command run with `--today` should not stamp a different day than the
    filename it just computed.
    """
    return datetime.combine(today, time(), tzinfo=UTC)


def _bundle(root: Path) -> Bundle:
    if not root.is_dir():
        typer.echo(f"{root}: not a directory", err=True)
        raise typer.Exit(code=1)
    return load_bundle(root, ignore=IGNORE)


def _schema_set(root: Path, declarations_dir: Path | None) -> SchemaSet:
    declarations = root if declarations_dir is None else declarations_dir
    try:
        return load_schemas(declarations / "schema")
    except (OSError, ValueError, KeyError) as exc:
        typer.echo(f"{declarations / 'schema'}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _lanes(root: Path, declarations_dir: Path | None) -> LaneSet:
    try:
        return lane_set(_schema_set(root, declarations_dir))
    except KeyError as exc:
        typer.echo(f"{root}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _sections(root: Path, declarations_dir: Path | None) -> SectionSet:
    """The bundle's own `sections/`, not this package's assets.

    Normally identical -- `init` seeds byte-for-byte copies -- but they diverge
    the moment a human edits the bundle's `sections/Explanation.yaml`, and then
    `promote` writes a body from one shape while a lint checks it against
    another. One bundle, one answer.
    """
    declarations = root if declarations_dir is None else declarations_dir
    try:
        return load_sections(declarations / "sections")
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _emit(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2))


def _echo_failure(failure: WriteFailure) -> None:
    typer.echo(f"refused {failure.path} ({failure.kind}): {failure.error}", err=True)


def _proposal_json(proposal: Proposal, lanes: LaneSet) -> dict[str, Any]:
    """The §6 shape. Every value survives `json.dumps` with no encoder, which
    is the promise `fm_data` already makes and the reason
    `okf_ext.proposals.model` carries sources as plain data."""
    lane = lanes.lane_for(proposal.target)
    return {
        "member": proposal.member,
        "target": proposal.target,
        "lane": None if lane is None else lane.name,
        "title": proposal.title,
        "description": proposal.description,
        "page_status": proposal.page_status,
        "sources": [dict(source) for source in proposal.sources],
        "verified": [dict(entry) for entry in proposal.verified],
        "malformed": proposal.malformed,
    }


def _find(bundle: Bundle, target: str) -> Proposal:
    """The proposal whose target is *target*. Identity is the target path."""
    wanted = target.strip().replace("\\", "/").lstrip("/")
    for proposal in list_proposals(bundle):
        if proposal.target == wanted:
            return proposal
    typer.echo(f"no proposal targets {target!r} in {bundle.root}", err=True)
    raise typer.Exit(code=1)


def _page_status(value: str | None) -> PageStatus | None:
    if value is None:
        return None
    if value not in PAGE_STATUSES:
        typer.echo(f"--page-status {value!r}: expected one of {list(PAGE_STATUSES)}", err=True)
        raise typer.Exit(code=1)
    return value


def _bundle_source_kinds(root: Path, declarations_dir: Path | None) -> tuple[str, ...]:
    """The vocabulary *this bundle* declares, or this package's seed when it
    declares none.

    K-D says the vault is the truth; the fallback is what makes that safe to
    apply to `ingest`, which briefs a document against a `wiki/` that need not
    be an initialized bundle and writes nothing.

    A bundle that declares `Source` but gives it no `source_kind` enum is a
    different thing from a bundle that declares nothing, and it does not fall
    back: falling back would validate against a vocabulary this vault never
    agreed to, silently, which is the failure the consolidation exists to
    remove. It exits naming the file instead.
    """
    declarations = root if declarations_dir is None else declarations_dir
    try:
        schema_set = load_schemas(declarations / "schema")
    except (OSError, ValueError):
        return seed_source_kinds()
    if SOURCE_TYPE not in schema_set.schemas:
        return seed_source_kinds()
    try:
        return source_kinds(schema_set)
    except KeyError as exc:
        typer.echo(exc.args[0], err=True)
        raise typer.Exit(code=1) from exc


def _source_kind(value: str, kinds: tuple[str, ...]) -> str:
    """*value* if the bundle declares it, else exit 1 naming what it does declare.

    The check runs *before* a plan is built, so a bad `--source-kind` is a
    usage error rather than a `schemas.invalid` finding on a page that already
    landed. That property predates K-D and survives it -- only the source of
    the list changed.
    """
    if value not in kinds:
        typer.echo(f"--source-kind {value!r}: expected one of {list(kinds)}", err=True)
        raise typer.Exit(code=1)
    return value


def _material_payload(material: Path) -> str | bytes:
    """Read MATERIAL, as UTF-8 text where it decodes and as raw bytes where it does not.

    One read on both paths: decoding an already-read `bytes` rather than calling
    `read_text` and then `read_bytes` on the fallback, which would read a large
    PDF twice. Exits 1 naming the file on an `OSError`, unchanged -- a file that
    is not there or cannot be opened is still a command-level error.
    """
    try:
        data = material.read_bytes()
    except OSError as exc:
        typer.echo(f"{material}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data


def _report(plan: Plan, *, dry_run: bool, bundle: Bundle, json_output: bool) -> None:
    """Print a plan, then apply it unless this is a dry run.

    One helper for every mutating command, so `--dry-run` cannot mean three
    slightly different things.
    """
    payload = {
        "ok": plan.ok,
        "writes": [{"member": write.member, "mode": write.mode} for write in plan.writes],
        "refusals": [
            {"path": refusal.path, "kind": refusal.kind, "detail": refusal.detail} for refusal in plan.refusals
        ],
        "applied": False,
    }
    if not plan.ok:
        if json_output:
            _emit(payload)
        else:
            for refusal in plan.refusals:
                typer.echo(f"refused {refusal.path} ({refusal.kind}): {refusal.detail}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        if json_output:
            _emit(payload)
        else:
            for write in plan.writes:
                typer.echo(f"would {write.mode} {write.member}")
            if plan.is_empty:
                typer.echo("nothing to do")
        return

    result: ApplyResult = apply(bundle, plan)
    payload["applied"] = result.ok
    payload["written"] = list(result.written)
    if json_output:
        _emit(payload)
    else:
        for member in result.written:
            typer.echo(f"wrote {member}")
        if plan.is_empty:
            typer.echo("nothing to do")
        for failure in result.failed:
            _echo_failure(failure)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def init(
    bundle_root: Path = typer.Argument(..., help="Directory to install this lane into."),  # noqa: B008
    declarations_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--declarations-dir",
        help="Where `schema/` and `sections/` are written. Defaults to BUNDLE_ROOT. Not persisted.",
    ),
    today_option: str | None = typer.Option(
        None, "--today", help="Stamp the `log.md` entry with YYYY-MM-DD instead of now."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
) -> None:
    """Install doc-wiki-okf into BUNDLE_ROOT, scaffolding it if it is new.

    Additive and idempotent: a bundle another package already created is
    installed into rather than refused, and a re-run writes nothing. The only
    refusal is a file this package owns that exists with content it did not
    write, reported per file -- its neighbours still land.
    """
    today = _today(today_option)
    scaffold = plan_scaffold(bundle_root, today=today, declarations_dir=declarations_dir)
    install = plan_install(bundle_root, seed_files(), declarations_dir=declarations_dir)

    if dry_run:
        for planned in (*scaffold.writes, *install.writes):
            typer.echo(f"would write {planned.member}")
        return

    ok = True
    for plan in (scaffold, install):
        result = apply_bundle(plan)
        for member in result.written:
            typer.echo(f"wrote {member}")
        for item in result.skipped:
            typer.echo(f"skipped {item.path}")
        for failure in result.failed:
            _echo_failure(failure)
        ok = ok and result.ok
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def proposals(
    root: Path = typer.Argument(..., help="Bundle root to read."),  # noqa: B008
    page_status: str | None = typer.Option(
        None, "--page-status", help=f"Filter on the coerced value; one of {list(PAGE_STATUSES)}."
    ),
    declarations_dir: Path | None = typer.Option(None, "--declarations-dir", help="Where `schema/` lives."),  # noqa: B008
    json_output: bool = typer.Option(False, "--json", help="Emit the list as JSON."),
) -> None:
    """List every proposal in ROOT, malformed ones included. Never writes.

    A malformed proposal is listed rather than dropped -- a proposal nobody can
    see is one nobody fixes -- and never satisfies a `--page-status` filter,
    because a filter is how a caller picks documents to act on.
    """
    bundle = _bundle(root)
    lanes = _lanes(root, declarations_dir)
    found = list_proposals(bundle, page_status=_page_status(page_status))
    if json_output:
        _emit([_proposal_json(proposal, lanes) for proposal in found])
        return
    for proposal in found:
        lane = lanes.lane_for(proposal.target)
        flag = f" [{proposal.malformed}]" if proposal.malformed else ""
        typer.echo(f"{proposal.raw_page_status or '?':<9} {lane.name if lane else '-':<11} {proposal.target}{flag}")
    if not found:
        typer.echo("no proposals")


@proposal_app.command("show")
def show(
    root: Path = typer.Argument(..., help="Bundle root to read."),  # noqa: B008
    target: Path = typer.Argument(..., help="The page the proposal argues for, bundle-relative."),  # noqa: B008
    declarations_dir: Path | None = typer.Option(None, "--declarations-dir", help="Where `schema/` lives."),  # noqa: B008
    json_output: bool = typer.Option(False, "--json", help="Emit the proposal as JSON instead."),
) -> None:
    """Print the review artifact for the proposal targeting TARGET.

    Rendered live from the stored `sources[]` rather than read off the document,
    so what a reviewer sees is what the next merge would write.
    """
    bundle = _bundle(root)
    lanes = _lanes(root, declarations_dir)
    found = _find(bundle, str(target))
    if json_output:
        _emit(_proposal_json(found, lanes))
        return
    lane = lanes.lane_for(found.target)
    if lane is None:
        typer.echo(f"{found.target}: in no declared lane directory", err=True)
        raise typer.Exit(code=1)
    raw_target = bundle.member_id(found.target)
    resolved = raw_target if raw_target is not None else found.target
    render = ReviewRenderer(
        lane=lane,
        target=resolved,
        mode="update" if raw_target is not None else "create",
    )
    typer.echo(render(description=found.description, sources=found.sources), nl=False)


@proposal_app.command("file")
def file_proposal(
    root: Path = typer.Argument(..., help="Bundle root to file into."),  # noqa: B008
    lane: str = typer.Option(..., "--lane", help="Which lane the page belongs to."),
    title: str = typer.Option(..., "--title", help="The proposed page's title; the target slugs from it."),
    description: str = typer.Option("", "--description", help="One line, written into the proposal's frontmatter."),
    identifier: str = typer.Option(..., "--id", help="The source's `id`, used as its footnote key."),
    resource: str = typer.Option(..., "--resource", help="The source's `resource` -- what dedup keys on."),
    rationale: str = typer.Option("", "--rationale", help="Why this source argues for the page."),
    evidence: list[str] = typer.Option(None, "--evidence", help="Repeatable evidence bullet."),  # noqa: B008
    by: str = typer.Option("agent:doc-wiki-okf", "--by", help="Who is filing; stamped into `generated.by`."),
    declarations_dir: Path | None = typer.Option(None, "--declarations-dir", help="Where `schema/` lives."),  # noqa: B008
    today_option: str | None = typer.Option(None, "--today", help="Stamp `generated.at` from YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """File one source's argument for a page, or merge it into the live proposal.

    An upsert on the target, because that is identity. A second source for a
    page that already has a proposal merges into its `sources[]` and re-renders
    the body from the merged result; a decided proposal is refused rather than
    silently reopened.
    """
    today = _today(today_option)
    bundle = _bundle(root)
    lanes = _lanes(root, declarations_dir)
    lane_names = [declared.name for declared in lanes.lanes]
    if lane not in lane_names:
        typer.echo(f"--lane {lane!r}: expected one of {lane_names}", err=True)
        raise typer.Exit(code=1)

    source: dict[str, Any] = {"id": identifier, "resource": resource}
    if rationale:
        source["rationale"] = rationale
    if evidence:
        source["evidence"] = list(evidence)

    plan = plan_file(
        bundle, lanes, lane=lane, title=title, description=description, source=source, by=by, at=_at(today)
    )
    _report(plan, dry_run=dry_run, bundle=bundle, json_output=json_output)


def _decide(
    root: Path, target: Path, decision: str, by: str, today_option: str | None, dry_run: bool, json_output: bool
) -> None:
    """The body `approve` and `reject` share, so the two cannot drift."""
    today = _today(today_option)
    bundle = _bundle(root)
    found = _find(bundle, str(target))
    plan = plan_decide(bundle, found, cast(Decision, decision), by=by, at=_at(today))
    _report(plan, dry_run=dry_run, bundle=bundle, json_output=json_output)


@proposal_app.command("approve")
def approve(
    root: Path = typer.Argument(..., help="Bundle root."),  # noqa: B008
    target: Path = typer.Argument(..., help="The page the proposal argues for."),  # noqa: B008
    by: str = typer.Option("human", "--by", help="Who decided; appended to `verified[]`."),
    today_option: str | None = typer.Option(None, "--today", help="Stamp the decision from YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Approve one proposal. A decision is OKF §5.2's `verified` event, not a
    `decided:` key, and no body is ever re-rendered after it."""
    _decide(root, target, "approved", by, today_option, dry_run, json_output)


@proposal_app.command("reject")
def reject(
    root: Path = typer.Argument(..., help="Bundle root."),  # noqa: B008
    target: Path = typer.Argument(..., help="The page the proposal argues for."),  # noqa: B008
    by: str = typer.Option("human", "--by", help="Who decided; appended to `verified[]`."),
    today_option: str | None = typer.Option(None, "--today", help="Stamp the decision from YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Reject one proposal. Same transition as `approve`, other value."""
    _decide(root, target, "rejected", by, today_option, dry_run, json_output)


@proposal_app.command("promote")
def promote(
    root: Path = typer.Argument(..., help="Bundle root."),  # noqa: B008
    target: Path = typer.Argument(..., help="The page the proposal argues for."),  # noqa: B008
    by: str = typer.Option("agent:doc-wiki-okf", "--by", help="Stamped into the new page's `generated.by`."),
    declarations_dir: Path | None = typer.Option(None, "--declarations-dir", help="Where the declarations live."),  # noqa: B008
    today_option: str | None = typer.Option(None, "--today", help="The date an ADR's filename carries."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Promote an approved proposal into its page.

    Three effects in one plan: the dated target is resolved, the page is written
    with its section skeleton, and the proposal flips to `created` carrying the
    new target. They commit together or not at all.
    """
    today = _today(today_option)
    bundle = _bundle(root)
    lanes = _lanes(root, declarations_dir)
    found = _find(bundle, str(target))
    try:
        plan = plan_promotion(
            bundle, lanes, found, section_set=_sections(root, declarations_dir), by=by, at=_at(today), on=today
        )
    except KeyError as exc:
        typer.echo(f"{found.target}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _report(plan, dry_run=dry_run, bundle=bundle, json_output=json_output)


def _migration_payload(plan: MigrationPlan) -> dict[str, Any]:
    return {
        "ok": plan.ok,
        "writes": [write.member for write in plan.writes],
        "placements": dict(plan.placements),
        "refusals": [
            {"member": refusal.member, "kind": refusal.kind, "detail": refusal.detail} for refusal in plan.refusals
        ],
        "stranded": [{"member": entry.member, "target": entry.target, "line": entry.line} for entry in plan.stranded],
        "applied": False,
    }


@app.command()
def migrate(
    root: Path = typer.Argument(..., help="Bundle root to rewrite."),  # noqa: B008
    apply_changes: bool = typer.Option(False, "--apply", help="Write the rewrite and perform the moves."),
    by: str = typer.Option("agent:doc-wiki-okf", "--by", help="Stamped into each migrated `generated.by`."),
    today_option: str | None = typer.Option(None, "--today", help="Stamp `generated.at` from YYYY-MM-DD."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Rewrite every old-dialect proposal in ROOT into `okf_ext.proposals` shape.

    Old dialect means frontmatter carrying `target_slug` and no `type`. Each
    such document is rewritten **in place** -- frontmatter only, body carried
    verbatim -- and then relocated by `okf_ext.moves`, which repairs the **OKF
    markdown references** that pointed at it. `[[wikilink]]` forms are not an
    OKF link form, are never rewritten, and are reported as `stranded`
    instead. A document already carrying `type: Proposal`
    is skipped, so a re-run is an empty plan rather than an error.

    Refusals are all-or-nothing **per document**: one unusable `kind`,
    `status` or `target_slug` declines that proposal and its neighbours still
    migrate. Any refusal exits non-zero, but `--apply` still writes what it
    can, because nothing here is computed across documents.

    **Previews by default.** Every other mutating command in this app writes
    unless given `--dry-run`; this one inverts that on purpose -- a whole-
    bundle rewrite plus a batch move should not happen because someone typed a
    path. `doc-wiki-okf migrate ROOT`, not `doc-wiki-okf proposals migrate
    ROOT`: `proposals` is already a leaf command that lists the ledger, and
    typer cannot make one name both a command and a group.
    """
    today = _today(today_option)
    at = _at(today)

    if not apply_changes:
        bundle = _bundle(root)
        plan = plan_migrate(bundle, by=by, at=at)
        payload = _migration_payload(plan)
        if json_output:
            _emit(payload)
        else:
            for write in plan.writes:
                typer.echo(f"would rewrite {write.member} -> {write.placement}")
            if plan.is_empty:
                typer.echo("nothing to do")
            for refusal in plan.refusals:
                typer.echo(f"refused {refusal.member} ({refusal.kind}): {refusal.detail}", err=True)
            warning = stranded_warning(plan.stranded)
            if warning is not None:
                typer.echo(warning, err=True)
        if not plan.ok:
            raise typer.Exit(code=1)
        return

    if not root.is_dir():
        typer.echo(f"{root}: not a directory", err=True)
        raise typer.Exit(code=1)

    outcome = migrate_and_move(root, by=by, at=at, ignore=IGNORE)
    payload = _migration_payload(outcome.plan)
    payload["applied"] = outcome.rewrite.ok
    payload["written"] = list(outcome.rewrite.written)
    payload["moved"] = [list(pair) for pair in outcome.move.moved]
    if json_output:
        _emit(payload)
    else:
        for member in outcome.rewrite.written:
            typer.echo(f"rewrote {member}")
        for source_path, destination in outcome.move.moved:
            typer.echo(f"moved {source_path} -> {destination}")
        if outcome.plan.is_empty:
            typer.echo("nothing to do")
        for refusal in outcome.plan.refusals:
            typer.echo(f"refused {refusal.member} ({refusal.kind}): {refusal.detail}", err=True)
        for move_refusal in outcome.move_plan.refusals:
            typer.echo(f"refused move {move_refusal.path} ({move_refusal.kind}): {move_refusal.detail}", err=True)
        for failure in (*outcome.rewrite.failed, *outcome.move.failed):
            _echo_failure(failure)
        warning = stranded_warning(outcome.plan.stranded)
        if warning is not None:
            typer.echo(warning, err=True)
    if not outcome.ok:
        raise typer.Exit(code=1)


def _batch_lines(brief: BatchBrief) -> list[str]:
    head = f"batch {brief.kind_folder}: {brief.unit_count} of {brief.total_count} units under {brief.root}"
    return [head, *(f"  {unit.unit_type} {unit.rel}" for unit in brief.units)]


def _folder_lines(brief: FolderBrief) -> list[str]:
    noun = "file" if brief.file_count == 1 else "files"
    lines = [f"folder {brief.root}: {brief.file_count} {noun}, {brief.total_size} bytes"]
    if brief.representative_file is not None:
        lines.append(f"  representative: {brief.representative_file}")
    lines.extend(f"  warning ({warning.kind}): {warning.detail}" for warning in brief.warnings)
    return lines


def _document_lines(brief: DocumentBrief) -> list[str]:
    return [
        brief.title,
        f"  kind: {brief.source_kind}",
        f"  slug: {brief.slug}",
        f"  words: {brief.word_count}",
        f"  source page: {brief.suggested_summary_path} ({'merge' if brief.merge_mode else 'create'})",
        f"  entity: {brief.entity_match.uri or '-'}",
    ]


@app.command()
def ingest(
    source: Path = typer.Argument(..., help="The file or directory to brief."),  # noqa: B008
    workspace: Path = typer.Option(  # noqa: B008
        Path(), "--workspace", help="The workspace root. `wiki/` is its child; the material need not be."
    ),
    wiki: Path | None = typer.Option(None, "--wiki", help="The bundle root. Defaults to <workspace>/wiki."),  # noqa: B008
    repo: Path | None = typer.Option(  # noqa: B008
        None, "--repo", help="What a relative SOURCE resolves against. Defaults to WORKSPACE."
    ),
    kind: str | None = typer.Option(None, "--kind", help="Brief SOURCE as a batch of this kind, e.g. `articles`."),
    source_kind: str | None = typer.Option(
        None,
        "--source-kind",
        help="The kind of material this is; one of the values this bundle's `Source` schema declares.",
    ),
    limit: int = typer.Option(10, "--limit", help="Cap a batch manifest at N units."),
    all_units: bool = typer.Option(False, "--all", help="No cap on a batch manifest; overrides --limit."),
    today_option: str | None = typer.Option(None, "--today", help="Compute the source page as of YYYY-MM-DD."),
    json_output: bool = typer.Option(False, "--json", help="Emit the brief as JSON."),
) -> None:
    """Print the brief for SOURCE. Reads; never writes.

    **Batch is opt-in.** `--kind` says "treat this directory as a batch of that
    kind"; without it a directory briefs as a folder and a file briefs as a
    single document.

    Skill detection is deliberately absent. A skill directory briefs as a folder
    here -- chunking one into guidance pages is a layer above this one.
    """
    today = _today(today_option)
    workspace = workspace.resolve()
    wiki_root = (workspace / "wiki" if wiki is None else wiki).resolve()
    repo_root = (workspace if repo is None else repo).resolve()

    target = resolve_source_path(source, repo_root)
    if not target.exists():
        typer.echo(f"{source}: no such file or directory", err=True)
        raise typer.Exit(code=1)

    if kind is not None:
        batch = plan_batch_brief(
            source, kind=kind, repo=repo_root, workspace_root=workspace, limit=None if all_units else limit
        )
        if batch is None:
            typer.echo(f"{source}: --kind briefs a directory, and this is not one", err=True)
            raise typer.Exit(code=1)
        _emit_brief(batch.as_data(), _batch_lines(batch), json_output=json_output)
        return

    if target.is_dir():
        folder = plan_folder_brief(source, repo=repo_root, workspace_root=workspace)
        _emit_brief(folder.as_data(), _folder_lines(folder), json_output=json_output)
        if not json_output:
            for refusal in folder.refusals:
                typer.echo(f"refused {folder.root} ({refusal.kind}): {refusal.detail}", err=True)
        if not folder.ok:
            raise typer.Exit(code=1)
        return

    kinds = _bundle_source_kinds(wiki_root, None)
    if source_kind is None:
        typer.echo(
            f"--source-kind is required for a single document; expected one of {list(kinds)}",
            err=True,
        )
        raise typer.Exit(code=1)

    document = plan_document_brief(
        source,
        wiki=wiki_root,
        repo=repo_root,
        workspace_root=workspace,
        today=today,
        source_kind=_source_kind(source_kind, kinds),
    )
    _emit_brief(document.as_data(), _document_lines(document), json_output=json_output)


def _emit_brief(payload: dict[str, Any], lines: list[str], *, json_output: bool) -> None:
    """One brief, in whichever of the two modes was asked for."""
    if json_output:
        _emit(payload)
        return
    for line in lines:
        typer.echo(line)


@source_app.command("add")
def source_add(
    root: Path = typer.Argument(..., help="Bundle root to record into."),  # noqa: B008
    material: Path = typer.Argument(..., help="The file to record. May live anywhere."),  # noqa: B008
    title: str = typer.Option(..., "--title", help="The page's title; the slug derives from it."),
    description: str = typer.Option(..., "--description", help="One line, the page's `description`."),
    source_kind: str = typer.Option(
        ...,
        "--source-kind",
        help="The kind of material this is; one of the values this bundle's `Source` schema declares.",
    ),
    origin: str = typer.Option(..., "--origin", help="Where the material came from: URL, path, or how it arrived."),
    entity_uri: str = typer.Option("", "--entity-uri", help="The code entity this material is about."),
    authors: list[str] = typer.Option(None, "--author", help="Repeatable author name."),  # noqa: B008
    source_date: str = typer.Option("", "--source-date", help="When the material itself was written, YYYY-MM-DD."),
    tokens: int | None = typer.Option(None, "--tokens", help="Approximate token count of the material."),
    by: str = typer.Option("agent:doc-wiki-okf", "--by", help="Stamped into `generated.by`."),
    declarations_dir: Path | None = typer.Option(None, "--declarations-dir", help="Where the declarations live."),  # noqa: B008
    today_option: str | None = typer.Option(None, "--today", help="Compute the page path as of YYYY-MM-DD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
    json_output: bool = typer.Option(False, "--json", help="Emit the plan as JSON."),
) -> None:
    """Record MATERIAL as a Source page, and copy it into the bundle beside it.

    Two writes, one plan: `sources/<YYYY-MM>-<slug>.md` and
    `sources/references/<YYYY-MM>-<slug><ext>`. They land together or not at
    all.

    The material is **copied**, never moved or archived -- it may live outside
    this workspace entirely, and the copy is the only durable location it is
    guaranteed to have. Re-recording material whose page already exists is
    refused rather than merged: delete the page and re-run to redo one.

    Binary material -- a PDF, an image -- is recorded byte-for-byte. Its page
    body is the section skeleton plus the `--title` and `--description` given
    here; nothing extracts text from it.

    `--source-kind` has no default. Guessing it from a folder name is what this
    command's arrival retires, and a human running this by hand knows what the
    material is -- the schema field is optional (K-C) for the agent path, not
    for this one.
    """
    today = _today(today_option)
    checked = _source_kind(source_kind, _bundle_source_kinds(root, declarations_dir))
    payload = _material_payload(material)
    bundle = _bundle(root)
    try:
        plan = plan_ingest(
            bundle,
            _schema_set(root, declarations_dir),
            _sections(root, declarations_dir),
            material,
            content=payload,
            title=title,
            description=description,
            source_kind=checked,
            origin=origin,
            by=by,
            at=_at(today),
            today=today,
            entity_uri=entity_uri,
            authors=list(authors or ()),
            source_date=source_date,
            tokens=tokens,
        )
    except KeyError as exc:
        typer.echo(f"{root}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _report(plan, dry_run=dry_run, bundle=bundle, json_output=json_output)
