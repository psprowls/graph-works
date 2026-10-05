"""Which bundles this workspace lints, and with which rules.

A band-3 job by definition: only this package knows what a workspace is, which
capabilities it enabled, and where its declarations live. One `Lane` per lane,
each carrying the root to walk, the `ignore=` recipe to walk it with, and the
`extra_rules=` tuple to validate it with — so the aggregator is a loop over
lanes and a new lane is a declaration, not an aggregator edit.

**Absent declarations are a fact about the workspace, not a fault — for the
wiki lane.** An empty `schema/` means the workspace declared no schemas, so
that capability contributes no rule and no error. A declaration directory
that is *present but malformed* is a caller-configuration mistake and becomes
one lane error, with every other lane still composed. **The work lane does
not share this softness.** It validates through
`work_tracker_okf.compose.rule_set` — the same function
`graph_works_core.work.commands.run_lint` uses, so the standalone and
combined checks can never validate the work lane differently — and that
function requires `schema/` and `sections/` to exist, matching
`work-tracker-okf/cli.py`'s own `lint` command. A missing declarations
directory there is reported the same way as a malformed one: one lane error,
wiki lane unaffected.

`sync` is contributed as a **deferred** rule. `snapshot_bundle` needs the
loaded `Bundle`, which does not exist when this function runs, so the snapshot
is computed inside a `Rule` closure from `ctx.bundle`. `Lane.rules` therefore
keeps its plain `tuple[Rule, ...]` shape, and carrying a filesystem question
into a pure `RuleContext` through a closure is the idiom
`work_tracker_okf.rules.lane_rules(repo_root=…)` already established.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import repositories_okf
import work_tracker_okf
from code_graph_io import GraphReader
from code_wiki_okf.about import about_rule
from code_wiki_okf.config import Config, ConfigError
from code_wiki_okf.placement import placement_rule as code_wiki_placement_rule
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import snapshot_bundle
from config_io import PROJECTION_FILENAME
from doc_wiki_okf.proposals.adr import adr_directory
from doc_wiki_okf.proposals.pool import refused_type_rule
from doc_wiki_okf.sources import drain_rule, entry_keys_from
from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from okf_ext.health import health_rule
from okf_ext.placement import placement_rule
from okf_ext.render import render_rule
from okf_ext.schemas import SchemaError, SchemaSet, declared_about, declared_proposables, load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import SectionError, load_sections
from okf_ext.tags import VOCABULARY_FILENAME, VocabularyError, load_vocabulary, vocabulary_rule
from okf_io import Finding, Rule, RuleContext
from repositories_okf.git import Git
from repositories_okf.lifecycle import lane_pages
from repositories_okf.repository import repository_rule
from work_tracker_okf.compose import rule_set

from graph_works_core.workspace.bundle import work_scope
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.lane_facts import gather_lane_facts, has_lane_pages, runner
from graph_works_core.workspace.layout import WorkspaceLayout

#: The two lane names a default workspace composes, in report order.
WIKI_LANE = "wiki"
WORK_LANE = "work"

#: The curated-page claims contract's severity (epic decision 010). The
#: backfill landed — every live curated page now carries `about:` and its
#: entries — so the contract composes at error. A composition-time knob, not
#: a per-page override.
CONTRACT_SEVERITY: Literal["error", "warning"] = "error"


def _check_config_projection(layout: WorkspaceLayout) -> str | None:
    """A missing `config.json` projection is a silent-dormancy risk, not a
    bundle fault: every consumer of the projection (`pre-agent-model-routing`,
    `pre-taskcreate-model-tier`, `pre-askuser-handoff-guard`, `session-start`,
    and `plugins/gw/hooks/skill-doc-routing`) treats an absent file
    as dormant and allows silently. Reported here so `gw lint` names it rather
    than letting the absence stay invisible until someone happens to run
    `gw config sync`.
    """
    projection = layout.cache_dir / PROJECTION_FILENAME
    if projection.is_file():
        return None
    return f"config projection missing: {projection} — run `gw config sync`"


#: What a malformed declaration set raises, named one by one rather than caught
#: through their shared `ValueError` base.
#:
#: `SchemaError`, `SectionError` and `VocabularyError` are all `ValueError`
#: subclasses, so a `ValueError` arm would also catch a `ValueError` a rule
#: factory raised from its own logic — turning a bug into a lane error line.
#: `lint.py` holds the opposite line for `validate()` (a rule blowing up is a
#: bug, and bugs must never be laundered into report text) and composition
#: holds it too. Each of the three loaders wraps even a YAML parse failure into
#: its own typed error, so nothing real is lost by naming them.
#:
#: `ConfigError` stays for documentation symmetry: `config` always arrives
#: pre-loaded from the caller, so this module cannot reach it independently.
_DECLARATION_ERRORS = (ConfigError, SchemaError, SectionError, VocabularyError, OSError)


@dataclass(frozen=True, slots=True)
class Lane:
    """One bundle to lint, and what to lint it with."""

    name: str
    root: Path
    ignore: tuple[str, ...]
    rules: tuple[Rule, ...]
    prune: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LaneSet:
    """Every lane that composed, and one line per lane that did not."""

    lanes: tuple[Lane, ...]
    errors: tuple[str, ...]


def _deferred_sync_rule(config: Config, reader: GraphReader, *, at: datetime) -> Rule:
    """`sync_rule`, with its snapshot computed from the bundle being validated."""

    def rule(ctx: RuleContext) -> Iterable[Finding]:
        return sync_rule(snapshot_bundle(ctx.bundle, config, reader, at=at))(ctx)

    return rule


def _deferred_repository_rule(layout: WorkspaceLayout, git: Git) -> Rule:
    """Gather repository facts from the bundle being validated."""

    def rule(ctx: RuleContext) -> Iterable[Finding]:
        return repository_rule(gather_lane_facts(layout, git, lane_pages(ctx.bundle)))(ctx)

    return rule


def _wiki_schema_set(config: Config) -> SchemaSet | None:
    """This workspace's declared schema set, or None when `schema/` is absent.

    Raises the loader's `SchemaError` on a malformed declaration: the wiki
    lane's composition turns that into one lane error line.
    """
    schema_dir = config.declarations_dir / SCHEMA_DIRNAME
    return load_schemas(schema_dir) if schema_dir.is_dir() else None


def _entry_keys(schema_set: SchemaSet) -> dict[str, str]:
    """The one derivation of `{type: entries key}`. The drain rule and the
    coverage line both go through it, so they cannot disagree about which list
    a ledger ref resolves under."""
    return entry_keys_from(schema_set)


def wiki_entry_keys(config: Config) -> dict[str, str] | None:
    """`{type: entries key}` from this workspace's `schema/`, or None.

    None when there is no `schema/`, and None when the declarations are
    malformed -- never a raise. A malformed declaration is already one
    `wiki lane: …` error line from `compose_lanes`, and a lane that failed to
    compose contributed no drain rule, so there is no coverage to report.
    """
    try:
        schema_set = _wiki_schema_set(config)
    except _DECLARATION_ERRORS:
        return None
    return None if schema_set is None else _entry_keys(schema_set)


def wiki_adr_directory(config: Config) -> str | None:
    """The `Adr` schema's `x-okf-directory`, or None.

    None when there is no `schema/`, no `Adr` schema, or the declarations are
    malformed -- never a raise, the contract `wiki_entry_keys` states.
    """
    try:
        schema_set = _wiki_schema_set(config)
    except _DECLARATION_ERRORS:
        return None
    return None if schema_set is None else adr_directory(schema_set)


def _wiki_rules(
    layout: WorkspaceLayout,
    config: Config,
    reader: GraphReader | None,
    *,
    at: datetime,
    repo_roots: tuple[Path, ...] = (),
    repository_git: Git | None = None,
) -> tuple[Rule, ...]:
    """The wiki lane's rule set: three unconditional, seven declaration-gated,
    one reader-gated, one lane-gated.

    `health` and `render` read only the bundle, so they are always on. The
    code-wiki placement rule reads resource identity and type ownership, not
    generic schema directory prefixes, so it is unconditional too. The claims
    contract (`about_rule`) rides the schema gate: its mandate is the schema
    set's `x-okf-about` annotation, so no schemas means no mandate, and a set
    declaring none composes a rule that finds nothing. *repo_roots* is where
    its `constrains` paths resolve. The drain rule rides the same gate for the
    same reason: its entry keys come from `x-okf-about` too, so no schemas
    means no ledger vocabulary to check refs against. The repositories lane's
    placement rides the schema gate: its directories come from
    `x-okf-directory`, allow-listed to the lane's two types so code-wiki's own
    placement stays the only authority over its types.
    The proposal pool's refused-type warning rides the schema gate: refusals
    are a fact about the declared schemas.
    The `repository.*` rules are lane-gated: composed only when `repositories/`
    holds a page, with git resolved up front so a missing executable is one
    lane error rather than a finding per page.
    """
    rules: list[Rule] = [health_rule(), render_rule(), code_wiki_placement_rule(severity="error")]

    schema_set = _wiki_schema_set(config)
    if schema_set is not None:
        rules.append(schema_rule(schema_set))
        rules.append(refused_type_rule(declared_proposables(schema_set)))
        rules.append(about_rule(declared_about(schema_set), repo_roots=repo_roots, severity=CONTRACT_SEVERITY))
        rules.append(drain_rule(_entry_keys(schema_set)))
        rules.append(
            placement_rule(
                repositories_okf.placement_directories(schema_set),
                depth={type_name: "exact" for type_name in repositories_okf.TYPES},
                severity="error",
            )
        )

    sections_dir = config.declarations_dir / SECTIONS_DIRNAME
    if sections_dir.is_dir():
        rules.append(section_rule(load_sections(sections_dir)))

    tags_path = config.declarations_dir / VOCABULARY_FILENAME
    if tags_path.is_file():
        rules.append(vocabulary_rule(load_vocabulary(tags_path), severity="error"))

    if reader is not None:
        rules.append(_deferred_sync_rule(config, reader, at=at))
    if repository_git is not None:
        rules.append(_deferred_repository_rule(layout, repository_git))
    return tuple(rules)


def _wiki_ignore() -> tuple[str, ...]:
    """The wiki lane's `ignore=`: the declaration directories, plus the work
    lane's own directory.

    The work lane's directory comes from `work_tracker_okf.WORK_DIR`, not from
    a literal here: lane directories are bundle-declared (constraint 5), so the
    capability that owns the lane is the one that names it. okf-io's `*`
    crosses `/`, so one pattern covers `work/` and `work/_archive/` alike.

    Ignoring is not hiding: `Bundle.has_member` counts ignored members, so a
    wiki page linking into `work/` still has a working link.
    """
    return (f"{work_tracker_okf.WORK_DIR}/*", *work_tracker_okf.IGNORE)


def _compose_wiki(
    layout: WorkspaceLayout,
    config: Config,
    reader: GraphReader | None,
    *,
    at: datetime,
    repo_roots: tuple[Path, ...] = (),
    repository_git: Git | None = None,
) -> Lane:
    return Lane(
        name=WIKI_LANE,
        root=layout.bundle_dir,
        ignore=_wiki_ignore(),
        rules=_wiki_rules(layout, config, reader, at=at, repo_roots=repo_roots, repository_git=repository_git),
    )


def _compose_work(
    layout: WorkspaceLayout, config: Config, *, repo_root: Path | None, repo_roots: tuple[Path, ...] = ()
) -> Lane:
    """The work lane. `repo_root=None` with no `repo_roots` is not an error —
    `rule_set`'s own `lane_rules` component skips the two rules that ask a
    question about a repository, which is its documented behaviour and the
    right one: not knowing where the repo is says nothing about whether the
    paths are good.

    Rules come from `work_tracker_okf.compose.rule_set`, not bare
    `lane_rules(repo_root=...)` — the same function
    `graph_works_core.work.commands.run_lint` uses, and now called with the same
    `vault_root=layout.bundle_dir` that both `run_lint` and the mutation gate
    pass, so `plan.action-target-missing` reads identically from every entry
    point. Dropping `vault_root` here used to leave `gw wiki lint` silently
    unable to resolve a plan action's own vault-relative artifact path, a
    finding `gw work lint` and the gate did not share. That widens
    what this lane needs to compose: `rule_set` requires `schema/` and
    `sections/` to exist under `config.declarations_dir` and raises
    `OSError` when either is missing, where bare `lane_rules` read neither
    directory at all. `compose_lanes` still catches that as one lane error,
    same as a malformed declaration.

    Its scope comes from `graph_works_core.workspace.bundle.work_scope`, the
    one definition `run_lint` shares. Both use `BundleScope.as_ignore` to
    ignore the other directories and root files, symmetric with `_wiki_ignore`
    naming `work/`. Lint retains ignored-member identity until the separate
    okf-io pruned resolver issue is fixed; clone-only pruning remains in the
    shared loader.
    A missing bundle directory raises `OSError`, which `compose_lanes` reports
    as one work-lane error. The root does **not** move: re-rooting at
    `<bundle>/work` would strip the `work/` prefix every `work_tracker_okf`
    rule selects on, and break every root-absolute link inside a work item.
    """
    scope = work_scope(layout)
    return Lane(
        name=WORK_LANE,
        root=layout.bundle_dir,
        ignore=scope.as_ignore(),
        rules=rule_set(
            layout.bundle_dir,
            repo_root=repo_root,
            repo_roots=repo_roots,
            vault_root=layout.bundle_dir,
            declarations_dir=config.declarations_dir,
            definition=load_dispatch_config(layout).definition,
        ),
    )


def compose_lanes(
    layout: WorkspaceLayout,
    config: Config,
    *,
    repo_root: Path | None = None,
    repo_roots: tuple[Path, ...] = (),
    at: datetime,
    reader: GraphReader | None = None,
) -> LaneSet:
    """Every lane this workspace lints, with its rules.

    *repo_root* and *repo_roots* are where the work lane resolves repo paths
    (`affects`, plan actions); a multi-repository workspace passes every
    declared repo as *repo_roots*, and a path under any one of them is good.
    The wiki lane's claims contract resolves `constrains` paths against the
    same roots.

    *reader* is optional because the mechanical pass is useful without a graph:
    with no reader the `sync` capability contributes no rule, exactly as an
    absent declaration directory does.
    """
    lanes: list[Lane] = []
    errors: list[str] = []
    repository_git: Git | None = None
    if has_lane_pages(layout):
        resolved = runner(layout)
        if isinstance(resolved, str):
            errors.append(f"{WIKI_LANE} lane: repository.* rules skipped — {resolved}; set toolchain.git")
        else:
            repository_git = resolved
    contract_roots = tuple(dict.fromkeys((*repo_roots, *((repo_root,) if repo_root is not None else ()))))
    builders: tuple[tuple[str, Callable[[], Lane]], ...] = (
        (
            WIKI_LANE,
            lambda: _compose_wiki(
                layout, config, reader, at=at, repo_roots=contract_roots, repository_git=repository_git
            ),
        ),
        (WORK_LANE, lambda: _compose_work(layout, config, repo_root=repo_root, repo_roots=repo_roots)),
    )
    for name, build in builders:
        try:
            lanes.append(build())
        except (*_DECLARATION_ERRORS, WorkspaceError) as exc:
            errors.append(f"{name} lane: {exc}")
    projection_error = _check_config_projection(layout)
    if projection_error is not None:
        errors.append(projection_error)
    return LaneSet(lanes=tuple(lanes), errors=tuple(errors))


__all__ = [
    "CONTRACT_SEVERITY",
    "WIKI_LANE",
    "WORK_LANE",
    "Lane",
    "LaneSet",
    "compose_lanes",
    "wiki_adr_directory",
    "wiki_entry_keys",
]
