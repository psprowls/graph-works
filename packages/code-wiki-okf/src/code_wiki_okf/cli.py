"""Typer console app: `code-wiki-okf init <bundle-root> [--dry-run]` and `sync`.

The one module in this package that may read the clock — everything
downstream takes `today` as an argument and never reads it itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import typer
from code_graph_io import open_reader
from okf_ext.bundle import WriteFailure
from okf_ext.moves import Stranded, stranded_summary
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tags import VOCABULARY_FILENAME, load_vocabulary, vocabulary_rule
from okf_io import load_bundle
from okf_io import validate as okf_validate

from code_wiki_okf.config import Config, ConfigError, load_config
from code_wiki_okf.init import InitError, install_bundle
from code_wiki_okf.mirror.model import MirrorPlan, MirrorResult
from code_wiki_okf.placement import placement_rule
from code_wiki_okf.sync import sync_bundle
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import snapshot_bundle

app = typer.Typer(add_completion=False, no_args_is_help=True)


# Not dead code: with a single registered command, Typer otherwise collapses
# the app so its name is skipped on the command line (`init` would be
# swallowed as `bundle_root`) — this callback keeps `init` an explicit
# subcommand, so more commands can be added later without a breaking CLI
# change.
@app.callback()
def _callback() -> None:
    """Generate and update a standalone OKF v0.2 bundle from the shared code graph."""


def _resolved_config_dir(config_dir: Path | None) -> Path | None:
    """A `--config-dir` that is not a directory is a `ConfigError`.

    It names where configuration lives, so it follows the rule the rest of
    this package's configuration follows: config raises, content never does.
    """
    if config_dir is None:
        return None
    if not config_dir.is_dir():
        raise ConfigError(f"--config-dir {config_dir}: not a directory")
    return config_dir.resolve()


def _with_config_dir(config: Config, config_dir: Path | None) -> Config:
    """Apply a `--config-dir` override onto a loaded `Config`.

    `dataclasses.replace` because `Config` is frozen. The flag wins over the
    file for this one invocation and is not written back -- persisting it is
    `init`'s job, where there is no file yet to read it from.
    """
    resolved = _resolved_config_dir(config_dir)
    return config if resolved is None else replace(config, declarations_dir=resolved)


def _echo_failure(failure: WriteFailure) -> None:
    """One refusal line to stderr -- shared by the per-file loop and the
    singleton `log_failure`, so the two channels can never drift in wording.
    """
    typer.echo(f"refused {failure.path} ({failure.kind}): {failure.error}", err=True)


@app.command()
def init(
    bundle_root: Path = typer.Argument(..., help="Directory to install this package into."),  # noqa: B008
    config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-dir",
        help="Where `schema/`, `sections/` and `tags.yaml` live. Defaults to BUNDLE_ROOT.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan instead of writing."),
) -> None:
    """Install code-wiki-okf into BUNDLE_ROOT, scaffolding it if it is new.

    Additive and idempotent: a bundle another package already created is
    installed into rather than refused, and a re-run writes nothing. The only
    refusal left is a file this package owns that exists with content it did
    not write, reported per file -- its neighbours still land.
    """
    today = datetime.now(UTC).date()
    try:
        result = install_bundle(
            bundle_root,
            today=today,
            declarations_dir=_resolved_config_dir(config_dir),
            dry_run=dry_run,
        )
    except (ConfigError, InitError) as exc:
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
    if result.logged is not None:
        typer.echo(f"{verb} log.md: {result.logged}")
    if not result.ok:
        raise typer.Exit(code=1)


def _echo_plan(repo_name: str, plan: MirrorPlan) -> None:
    """One line per would-be change, `init`'s own convention -- a full mirror
    can run into the thousands of files, so a single line listing every path
    at once (the previous shape) stops being readable at exactly the scale
    this command exists for.
    """
    if plan.is_empty:
        typer.echo(f"{repo_name}: no mirror changes")
        return
    for rel_path in sorted(plan.creates):
        typer.echo(f"{repo_name}: would create {rel_path}")
    for concept_id in sorted(plan.updates):
        typer.echo(f"{repo_name}: would update {concept_id}")
    for move in plan.moves.moves:
        typer.echo(f"{repo_name}: would move {move.source} -> {move.dest}")
    for rel_path in plan.deletions:
        typer.echo(f"{repo_name}: would delete {rel_path}")
    for declined in plan.declined_deletions:
        typer.echo(f"{repo_name}: would decline deletion of {declined.path} ({declined.reason})")
    if plan.moves.stranded:
        # stderr, and never an exit code (2026-08-21 spec §4.5): `moves`
        # repairs OKF markdown links only, and this is how the lane says so.
        typer.echo(f"{repo_name}: {stranded_summary(plan.moves.stranded)}", err=True)


def _echo_result(repo_name: str, result: MirrorResult, stranded: tuple[Stranded, ...] = ()) -> None:
    """Summary line first (its exact wording is a stable contract other tests
    match on), then one line per file actually touched -- see `_echo_plan`.

    *stranded* is the matching plan's count, passed in rather than carried on
    `MirrorResult`: the count is a plan-time fact, and `MirrorSummary.plans`
    is populated in both modes. Defaulted, so a caller with nothing to report
    calls this exactly as before.
    """
    typer.echo(
        f"{repo_name}: created {len(result.created)}, updated {len(result.regenerated)}, "
        f"moved {len(result.moved)}, deleted {len(result.deleted)}"
    )
    for rel_path in result.created:
        typer.echo(f"{repo_name}: created {rel_path}")
    for concept_id in result.regenerated:
        typer.echo(f"{repo_name}: updated {concept_id}")
    for source, dest in result.moved:
        typer.echo(f"{repo_name}: moved {source} -> {dest}")
    for rel_path in result.deleted:
        typer.echo(f"{repo_name}: deleted {rel_path}")
    for declined in result.declined_deletions:
        typer.echo(f"{repo_name}: declined deletion of {declined.path} ({declined.reason})")
    if stranded:
        typer.echo(f"{repo_name}: {stranded_summary(stranded)}", err=True)


@app.command()
def sync(
    bundle_root: Path = typer.Argument(..., help="Bundle root to sync (must already be initialized)."),  # noqa: B008
    config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-dir",
        help="Where `schema/`, `sections/` and `tags.yaml` live. Defaults to BUNDLE_ROOT. "
        "Applies to this run only -- not persisted.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-path",
        help="Where `workspace.yaml` lives. Defaults to BUNDLE_ROOT/workspace.yaml -- "
        "point this at a graph-works workspace's own workspace.yaml to read that "
        "workspace's `repositories`/`ignore`/`state_gate` blocks instead.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print each lane's plan instead of writing."),
) -> None:
    """Preflight and sync every code-wiki page in one composite run.

    Reads `workspace.yaml`, loads the graph once, and refuses before writing
    when any entity or File target is invalid. `--dry-run` returns that same
    immutable plan without touching the bundle.

    `graph_dir` is always `bundle_root` here -- this standalone CLI has no
    workspace layout of its own to derive a separate graph location from, so
    `load_config`'s `graph_dir=` is fixed rather than exposed as a flag.
    `init` no longer seeds `workspace.yaml` (2026-08-22 tech-debt item), so a
    bundle that was `init`'d but never given a `workspace.yaml` -- by hand or
    via `--config-path` -- surfaces as an uncaught `OSError` here, not a
    clean `ConfigError` exit; a known, deliberately unfixed gap.
    """
    today = datetime.now(UTC).date()
    at = datetime.now(UTC).isoformat()
    try:
        config = _with_config_dir(load_config(bundle_root, config_path=config_path, graph_dir=bundle_root), config_dir)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        with open_reader(graph_dir=config.graph_dir) as reader:
            result = sync_bundle(
                bundle_root,
                config=config,
                reader=reader,
                at=at,
                today=today,
                dry_run=dry_run,
            )
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for warning in result.warnings:
        typer.echo(f"warning: {warning}", err=True)

    if dry_run:
        typer.echo("sync: dry run, no writes made")
        entity_changes = False
        for concept_id in result.entities.created:
            entity_changes = True
            typer.echo(f"entities: would create {concept_id}")
        for concept_id in result.entities.updated:
            entity_changes = True
            typer.echo(f"entities: would update {concept_id}")
        for concept_id in result.entities.deleted:
            entity_changes = True
            if concept_id in result.entities.deleted_lane_residue:
                typer.echo(f"entities: would delete {concept_id} (lane-residue)")
            else:
                typer.echo(f"entities: would delete {concept_id}")
        for concept_id, reason in result.entities.declined:
            entity_changes = True
            typer.echo(f"entities: would decline deletion of {concept_id} ({reason})")
        if not entity_changes:
            typer.echo("entities: no changes")

        catalog_changes = False
        for member in result.entities.catalog_created:
            catalog_changes = True
            typer.echo(f"catalog: would create {member}")
        for member in result.entities.catalog_updated:
            catalog_changes = True
            typer.echo(f"catalog: would update {member}")
        for path, reason in result.entities.catalog_declined:
            catalog_changes = True
            typer.echo(f"catalog: would refuse {path} ({reason})", err=True)
        if not catalog_changes:
            typer.echo("catalog: no changes")
    else:
        typer.echo(
            f"entities: written {len(result.entities.written)}, deleted {len(result.entities.deleted)} "
            f"({len(result.entities.deleted_lane_residue)} lane-residue), "
            f"declined {len(result.entities.declined)}"
        )
        for skipped in result.entities.skipped:
            typer.echo(f"entities: write incomplete: {skipped}", err=True)
        for path, reason in result.entities.catalog_declined:
            typer.echo(f"catalog: write incomplete: {path} ({reason})", err=True)

    for repo_name in result.mirror.skipped_repos:
        typer.echo(f"{repo_name}: not a git checkout, skipping", err=True)
    if dry_run:
        for plan in result.mirror.plans:
            _echo_plan(plan.repo, plan)
    else:
        stranded_by_repo = {plan.repo: plan.moves.stranded for plan in result.mirror.plans}
        for mirror_result in result.mirror.results:
            _echo_result(mirror_result.repo, mirror_result, stranded_by_repo.get(mirror_result.repo, ()))
    for repo_name, error in result.mirror.failed_repos:
        typer.echo(f"{repo_name}: sync failed: {error}", err=True)

    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def validate(
    bundle_root: Path = typer.Argument(..., help="Bundle directory to validate."),  # noqa: B008
    config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-dir",
        help="Where `schema/`, `sections/` and `tags.yaml` live. Defaults to BUNDLE_ROOT. "
        "Applies to this run only -- not persisted.",
    ),
    config_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-path",
        help="Where `workspace.yaml` lives. Defaults to BUNDLE_ROOT/workspace.yaml -- "
        "point this at a graph-works workspace's own workspace.yaml to read that "
        "workspace's `repositories`/`ignore`/`state_gate` blocks instead.",
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as failures."),
) -> None:
    """Report drift and conformance findings for BUNDLE_ROOT. Never writes.

    `graph_dir` is always `bundle_root` -- see `sync`'s docstring for why,
    and for the known gap on a `workspace.yaml`-less bundle.
    """
    today = datetime.now(UTC).date()
    at = datetime.now(UTC)
    try:
        config = _with_config_dir(load_config(bundle_root, config_path=config_path, graph_dir=bundle_root), config_dir)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    bundle = load_bundle(bundle_root)
    try:
        with open_reader(graph_dir=config.graph_dir) as reader:
            snapshot = snapshot_bundle(bundle, config, reader, at=at)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        schema_set = load_schemas(config.declarations_dir / "schema")
        section_set = load_sections(config.declarations_dir / "sections")
        vocabulary = load_vocabulary(config.declarations_dir / VOCABULARY_FILENAME)
    except (OSError, ValueError) as exc:
        # `load_schemas`/`load_sections` raise `SchemaError`/`SectionError`
        # (both `ValueError`) for a malformed set and propagate `OSError` for
        # a missing directory; `load_vocabulary` does the same with
        # `VocabularyError` for a missing/malformed `tags.yaml`. All three
        # are caller-configuration problems -- matching `ConfigError` and
        # `snapshot_bundle`'s `ValueError` above, not a raw traceback.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    report = okf_validate(
        bundle,
        today=today,
        extra_rules=[
            sync_rule(snapshot),
            schema_rule(schema_set),
            section_rule(section_set),
            vocabulary_rule(vocabulary),
            placement_rule(severity="error"),
        ],
        strict=strict,
    )
    for finding in report.errors:
        typer.echo(f"error  {finding.code}: {finding.message}", err=True)
    for finding in report.warnings:
        typer.echo(f"warn   {finding.code}: {finding.message}")
    # Not just `report.ok`: that property only tracks error severity, so a
    # `sync.*` finding -- warn by default -- would leave a drifted bundle
    # exiting 0 outside `--strict`. Drift is this command's whole reason to
    # exist, so any `sync.*` finding fails the run regardless of severity;
    # every other topic keeps the catalog's own error-only bar. `--strict`
    # still promotes every finding to error first, so it subsumes this check.
    has_sync_finding = any(finding.code.startswith("sync.") for finding in report.findings)
    if not report.ok or has_sync_finding:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover -- exercised via __main__.py / the console script
    app()
