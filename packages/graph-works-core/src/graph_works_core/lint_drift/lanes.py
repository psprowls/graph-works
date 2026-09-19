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

import work_tracker_okf
from code_graph_io import GraphReader
from code_wiki_okf.config import Config, ConfigError
from code_wiki_okf.placement import placement_rule as code_wiki_placement_rule
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import snapshot_bundle
from config_io import PROJECTION_FILENAME
from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from okf_ext.health import health_rule
from okf_ext.render import render_rule
from okf_ext.schemas import SchemaError, load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import SectionError, load_sections
from okf_ext.tags import VOCABULARY_FILENAME, VocabularyError, load_vocabulary, vocabulary_rule
from okf_io import Finding, Rule, RuleContext
from work_tracker_okf.compose import rule_set

from graph_works_core.workspace.layout import WorkspaceLayout

#: The two lane names a default workspace composes, in report order.
WIKI_LANE = "wiki"
WORK_LANE = "work"


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


def _wiki_rules(config: Config, reader: GraphReader | None, *, at: datetime) -> tuple[Rule, ...]:
    """The wiki lane's rule set: three unconditional, three declaration-gated,
    one reader-gated.

    `health` and `render` read only the bundle, so they are always on. The
    code-wiki placement rule reads resource identity and type ownership, not
    generic schema directory prefixes, so it is unconditional too.
    """
    rules: list[Rule] = [health_rule(), render_rule(), code_wiki_placement_rule(severity="error")]

    schema_dir = config.declarations_dir / SCHEMA_DIRNAME
    if schema_dir.is_dir():
        schema_set = load_schemas(schema_dir)
        rules.append(schema_rule(schema_set))

    sections_dir = config.declarations_dir / SECTIONS_DIRNAME
    if sections_dir.is_dir():
        rules.append(section_rule(load_sections(sections_dir)))

    tags_path = config.declarations_dir / VOCABULARY_FILENAME
    if tags_path.is_file():
        rules.append(vocabulary_rule(load_vocabulary(tags_path), severity="error"))

    if reader is not None:
        rules.append(_deferred_sync_rule(config, reader, at=at))
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


def _work_ignore(layout: WorkspaceLayout) -> tuple[str, ...]:
    """The work lane's `ignore=`: every top-level bundle member except `work/`,
    plus the lane's own recipe.

    Symmetric with `_wiki_ignore`, and that symmetry is the point — the wiki
    lane names the work lane's directory, the work lane names the wiki lane's,
    and neither hardcodes the other's contents. Without it both lanes walk the
    same tree: no wrong finding fires, because the work-lane rules self-scope
    through `load_items`, but `validate()` always runs okf-io's built-in
    catalog and it ran on both walks.

    Derived from the directory rather than written as a literal list because
    okf-io's `ignore=` has no negation, and "everything except `work/`" spelled
    out is unmaintainable — a new curated directory would silently rejoin the
    work lane. A directory contributes `f"{name}/*"`; a root-level file
    contributes its own name, which `fnmatchcase` matches exactly against the
    bundle-relative path.

    Ignoring the root-level files is intended, not incidental: `index.md` and
    `log.md` are the wiki lane's members, and the core catalog's index and log
    rules should fire once, there. `work-index.json` goes with them and costs
    nothing — every lane rule reads the projection `load_items` builds from
    `bundle.concepts`, never the sidecar. `work/_archive/` is unaffected: it
    sits under `work/`, and `work_tracker_okf.IGNORE` already says what to drop
    beneath it.

    This reads the filesystem, which is stated rather than hidden: this is
    already a band-3 module whose whole job is knowing what this workspace
    looks like on disk. A bundle directory that is not there raises `OSError`,
    which `compose_lanes` reports as one work-lane error like any other.
    """
    siblings = tuple(
        f"{entry.name}/*" if entry.is_dir() else entry.name
        for entry in sorted(layout.bundle_dir.iterdir(), key=lambda path: path.name)
        if entry.name != work_tracker_okf.WORK_DIR
    )
    return (*siblings, *work_tracker_okf.IGNORE)


def _compose_wiki(layout: WorkspaceLayout, config: Config, reader: GraphReader | None, *, at: datetime) -> Lane:
    return Lane(name=WIKI_LANE, root=layout.bundle_dir, ignore=_wiki_ignore(), rules=_wiki_rules(config, reader, at=at))


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
    `graph_works_core.work.commands.run_lint` uses, so the standalone and
    combined checks can never validate the work lane differently. That widens
    what this lane needs to compose: `rule_set` requires `schema/` and
    `sections/` to exist under `config.declarations_dir` and raises
    `OSError` when either is missing, where bare `lane_rules` read neither
    directory at all. `compose_lanes` still catches that as one lane error,
    same as a malformed declaration.

    Its `ignore=` partitions the bundle against the wiki lane's — see
    `_work_ignore`. The root does **not** move: re-rooting at `<bundle>/work`
    would strip the `work/` prefix every `work_tracker_okf` rule selects on,
    and break every root-absolute link inside a work item.
    """
    return Lane(
        name=WORK_LANE,
        root=layout.bundle_dir,
        ignore=_work_ignore(layout),
        rules=rule_set(
            layout.bundle_dir, repo_root=repo_root, repo_roots=repo_roots, declarations_dir=config.declarations_dir
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

    *reader* is optional because the mechanical pass is useful without a graph:
    with no reader the `sync` capability contributes no rule, exactly as an
    absent declaration directory does.
    """
    lanes: list[Lane] = []
    errors: list[str] = []
    builders: tuple[tuple[str, Callable[[], Lane]], ...] = (
        (WIKI_LANE, lambda: _compose_wiki(layout, config, reader, at=at)),
        (WORK_LANE, lambda: _compose_work(layout, config, repo_root=repo_root, repo_roots=repo_roots)),
    )
    for name, build in builders:
        try:
            lanes.append(build())
        except _DECLARATION_ERRORS as exc:
            errors.append(f"{name} lane: {exc}")
    projection_error = _check_config_projection(layout)
    if projection_error is not None:
        errors.append(projection_error)
    return LaneSet(lanes=tuple(lanes), errors=tuple(errors))


__all__ = ["WIKI_LANE", "WORK_LANE", "Lane", "LaneSet", "compose_lanes"]
