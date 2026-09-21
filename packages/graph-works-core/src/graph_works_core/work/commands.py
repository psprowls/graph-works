"""Composed commands for the work-tracking vertical: `file`, `next`, `status`,
`lint`, `regen-indexes`, subtree relocation, and decision-ledger operations --
what `work_tracker_okf` ships as primitives plus `work_tracker_okf.compose`,
composed through a `WorkspaceLayout` the way the archive vertical already does
for `plan_archive`/`apply_archive`. `advance`, `archive`, and `sync-children`
remain out of scope here, as explained below.

Every function here takes a `WorkspaceLayout` and, where a schema/section
declaration is needed, a `code_wiki_okf.config.Config` for
`config.declarations_dir` -- the same pair
`graph_works_core.ingest.commands`, `graph_works_core.scan.commands`, and
`graph_works_core.query.commands` already take, rather than re-deriving a
declarations path from `layout.bundle_dir` as the standalone
`work_tracker_okf.cli` does.

`advance`, `archive` and `sync-children` are deliberately absent.
`graph_works_core.orchestrate.stage_advance.run_stage_advance` already composes
`work_tracker_okf.compose.advance_and_stamp` with worktree provenance and a
results stub; `graph_works_core.archive.commands.run_archive` already composes
`work_tracker_okf.archive`.
`sync-children` has no `WorkspaceLayout`-aware caller anywhere yet and is
left as a gap for a future item. Neither does `work_tracker_okf.cli`'s own
`init`, which this item does not touch.

Eight names are re-exported rather than defined here -- `DependencyEdge`,
`DependencyIssue`, `DependencyParse`, `parse_dependencies`, `Decision`,
`ChildRollup`, `Transition` and `WorkItem`. `graph-works-cli` is forbidden to
import `work_tracker_okf` at all (its own boundary test asserts it): `run_file`
takes typed dependency edges, and the CLI's renderers destructure decisions,
child rollups and routing transitions out of `NextResult`/`DecisionCommandResult`
-- without the re-export the CLI could construct or type-annotate none of it.
`WorkItem` is re-exported because the board's list read returns it. They are
listed in `__all__` so the re-export is a contract rather than an accident of
import order.

Front-door note: none of this module's symbols are re-exported through
`graph_works_core` package root. `run_lint` would collide with the
already-hoisted `graph_works_core.lint_drift.lint.run_lint`, and no consumer
forces a naming resolution yet -- `graph-works-cli`, the only planned caller,
is a sibling epic, and CLI wiring is explicitly out of scope here. Reachable at
`graph_works_core.work.commands.*` until that consumer decides how to
disambiguate.

Nothing here reads the clock: `on`/`today` are always caller-supplied, as
everywhere else in this package and in `work-tracker-okf`.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from code_wiki_okf.config import Config
from doc_wiki_okf.sources import SOURCE_TYPE, normalize_origin
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.shape import load_sections
from okf_io import Bundle, load_bundle, parse
from okf_io import validate as okf_validate
from okf_io.validate import Report
from work_tracker_okf import decisions as _decisions
from work_tracker_okf._selection import path_index
from work_tracker_okf.compose import (
    FilingCompositionPlan,
    plan_file_and_reconcile,
    rule_set,
    stamp_for,
)
from work_tracker_okf.decisions import Decision, DecisionPlan, ledger_ref
from work_tracker_okf.dependencies import (
    DependencyEdge,
    DependencyIssue,
    DependencyParse,
    parse_dependencies,
)
from work_tracker_okf.filing import FilingSeed
from work_tracker_okf.hierarchy import ChildRollup, DescendResult, decision_owner
from work_tracker_okf.hierarchy import descend as descend_to_leaf
from work_tracker_okf.holds import check_hold, prepare_checkpoint
from work_tracker_okf.indexes import LaneIndexPlan, plan_indexes
from work_tracker_okf.items import IGNORE, WORK_DIR, WorkItem, item_index, load_items, unreadable_detail
from work_tracker_okf.mutation import (
    DirectoryPrecondition,
    PlannedWrite,
    WorkMutationPlan,
)
from work_tracker_okf.paths import ArtifactRef, checkpoint_ref, child_lane, item_page
from work_tracker_okf.projection import ResumeSelection, Rollup, rollup, select_resume
from work_tracker_okf.reparent import plan_release_adoption, plan_reparent
from work_tracker_okf.sources import upsert
from work_tracker_okf.vocabulary import PARENT_TYPES, SPEC_SOURCE_ID, TERMINAL_STATUSES
from work_tracker_okf.workflow import RouteResult, RouteState, Transition, route, state_for

from graph_works_core.workspace.decision_owner import (
    DecisionContext,
    DecisionOwner,
    decision_context,
    hold_for,
    locked_decision_owner,
)
from graph_works_core.workspace.dispatch import (
    AttributeValue,
    DispatchResolution,
    DispatchRule,
    dispatch_attributes,
    packaged_rule,
    resolve_dispatch,
    rule_matches,
)
from graph_works_core.workspace.dispatch_artifacts import missing_design_source
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _repo_roots(layout: WorkspaceLayout) -> tuple[Path, ...]:
    """Every code repo `workspace.yaml` declares, for `apply_mutation`'s
    postcondition validation -- never `layout.repo_root`, which is a `.git`
    walk-up that lands on the vault in a split topology. All of them, not
    one: a path resolving under any declared repo is good, so a workspace
    declaring several never has to choose (and never refuses) here. Every
    `apply_mutation` call site in this module calls this instead of
    re-deriving it. Empty when none are declared, which `apply_mutation`
    treats as no override.
    """
    return resolve_repos(layout)


def _materialize(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _materialize(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_materialize(nested) for nested in value]
    return value


def _planned_write(
    member: str,
    before: bytes | None,
    after: bytes,
    *,
    source_member: str | None = None,
) -> PlannedWrite:
    """Retain the planner's preimage instead of resnapshotting live state."""
    return PlannedWrite(member, _digest(before) if before is not None else None, after, source_member)


def _write_plan(
    root: Path,
    operation: Literal["file", "indexes"],
    writes: Sequence[PlannedWrite],
    *,
    mkdirs: Sequence[str] = (),
    validate_paths: Sequence[str] = (),
    warnings: Sequence[str] = (),
    directory_preconditions: Sequence[DirectoryPrecondition] = (),
) -> WorkMutationPlan:
    planned = tuple(sorted(writes, key=lambda write: write.member))
    directories = tuple(
        sorted(set(mkdirs) | {Path(write.member).parent.as_posix() for write in planned if "/" in write.member})
    )
    return WorkMutationPlan(
        root=root,
        operation=operation,
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=planned,
        deletes=(),
        mkdirs=directories,
        warnings=tuple(warnings),
        refusals=(),
        validate_paths=tuple(sorted(set(validate_paths))),
        directory_preconditions=tuple(sorted(directory_preconditions, key=lambda condition: condition.member)),
    )


@dataclass(frozen=True, slots=True)
class FilingRun:
    """A path-native filing plan and its optional journaled application."""

    plan: FilingCompositionPlan
    application: MutationApplication | None = None


def _filing_mutation(bundle: Bundle, plan: FilingCompositionPlan) -> WorkMutationPlan:
    filing = plan.filing
    document = parse("", path=filing.target)
    for key, value in filing.frontmatter.items():
        document.set(key, _materialize(value))
    document.set_body(filing.body)
    writes = [
        _planned_write(f"{filing.path}.md", None, document.serialize().encode("utf-8")),
        *(
            _planned_write(
                index.path.relative_to(bundle.root).as_posix(),
                index.before.encode("utf-8") if index.before is not None else None,
                index.after.encode("utf-8"),
            )
            for index in plan.indexes
        ),
    ]
    if plan.log is not None:
        log_path = Path(plan.log.path) if plan.log.path is not None else bundle.root / "log.md"
        log_member = log_path.relative_to(bundle.root).as_posix() if log_path.is_absolute() else log_path.as_posix()
        writes.append(_planned_write(log_member, plan.log.before.encode("utf-8"), plan.log.after.encode("utf-8")))
    writes.append(_planned_write(f"{filing.path}/references/.gitkeep", None, b""))
    owned_member = filing.owned_directory.relative_to(bundle.root).as_posix()
    return _write_plan(
        bundle.root,
        "file",
        writes,
        mkdirs=filing.required_directories,
        validate_paths=(filing.path,),
        warnings=plan.warnings,
        directory_preconditions=(DirectoryPrecondition(owned_member, None),),
    )


def run_file(
    layout: WorkspaceLayout,
    config: Config,
    *,
    type: str,
    title: str,
    description: str,
    on: date,
    name: str | None = None,
    effort: str | None = None,
    blast_radius: str | None = None,
    version: str | None = None,
    target_date: date | None = None,
    owner: str | None = None,
    parent_path: str | None = None,
    depends_on: Sequence[DependencyEdge] = (),
    affects: Sequence[str] = (),
    tags: Sequence[str] = (),
    dry_run: bool = True,
) -> FilingRun:
    """Plan one graph-aware page/index/log filing and optionally apply it."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    seed = FilingSeed(
        type=type,
        title=title,
        description=description,
        on=on,
        name=name,
        effort=effort,
        blast_radius=blast_radius,
        version=version,
        target_date=target_date,
        owner=owner,
        parent_path=parent_path,
        depends_on=tuple(depends_on),
        affects=tuple(affects),
        tags=tuple(tags),
    )
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        seed,
        load_sections(config.declarations_dir / SECTIONS_DIRNAME),
    )
    if dry_run or outcome.plan.refusal is not None:
        return FilingRun(plan=outcome.plan)

    return FilingRun(
        plan=outcome.plan,
        application=apply_mutation(
            layout,
            _filing_mutation(bundle, outcome.plan),
            repo_roots=_repo_roots(layout),
            baseline_bundle=bundle,
        ),
    )


@dataclass(frozen=True, slots=True)
class StatusReport:
    """The rollup and the item worth resuming, read together the way a
    status display wants them."""

    rollup: Rollup
    resume: ResumeSelection | None


def run_status(layout: WorkspaceLayout) -> StatusReport:
    """Count the active items and name the one worth resuming. Never writes."""
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    return StatusReport(rollup=rollup(items), resume=select_resume(items))


def run_work_list(layout: WorkspaceLayout) -> tuple[WorkItem, ...]:
    """Every active work item, sorted by canonical path. Never writes.

    Archived items are left out, the same population `rollup` counts, so a
    board built from this agrees with `gw work status`.
    """
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    return tuple(sorted((item for item in items if not item.archived), key=lambda item: item.path))


@dataclass(frozen=True, slots=True)
class ItemSource:
    """One `sources[]` entry, as authored."""

    id: str | None
    resource: str | None
    title: str | None


@dataclass(frozen=True, slots=True)
class ItemRead:
    """One work item read whole: frontmatter, body, sources and owned references.

    An unknown or unreadable target is a `refusal`, never a raise (ADR 2026-08-13-command-modules rule 5).
    """

    path: str
    frontmatter: Mapping[str, object]
    body: str
    sources: tuple[ItemSource, ...]
    references: tuple[str, ...]
    parse_error: str | None
    coercion_failures: tuple[str, ...]
    refusal: Literal["unknown-item", "unreadable"] | None
    detail: str | None


def _refused_item(path: str, refusal: Literal["unknown-item", "unreadable"], detail: str | None) -> ItemRead:
    return ItemRead(path, MappingProxyType({}), "", (), (), None, (), refusal, detail)


def _owned_references(bundle_root: Path, path: str) -> tuple[str, ...]:
    directory = bundle_root / path / "references"
    if not directory.is_dir():
        return ()
    return tuple(sorted(file.relative_to(bundle_root).as_posix() for file in directory.rglob("*") if file.is_file()))


def run_item_read(layout: WorkspaceLayout, path: str) -> ItemRead:
    """Read *path*'s work item and list its owned `references/`. Never writes."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    if path not in item_index(load_items(bundle)):
        detail = unreadable_detail(bundle, path)
        return _refused_item(path, "unreadable" if detail is not None else "unknown-item", detail)
    document = bundle.concepts[path]
    error = document.parse_error
    return ItemRead(
        path=path,
        frontmatter=MappingProxyType(document.fm_data(dates="iso")),
        body=document.body,
        sources=tuple(ItemSource(source.id, source.resource, source.title) for source in document.fm.sources),
        references=_owned_references(layout.bundle_dir, path),
        parse_error=None if error is None else f"{error.kind}: {error.message}",
        coercion_failures=tuple(sorted(document.fm.coercion_failures)),
        refusal=None,
        detail=None,
    )


@dataclass(frozen=True, slots=True)
class PendingIngest:
    """One terminal item whose design spec has no ingested `Source` page."""

    path: str
    work_status: str
    resource: str
    origin: str


@dataclass(frozen=True, slots=True)
class IngestQueueReport:
    """Every pending item, in canonical path order."""

    pending: tuple[PendingIngest, ...]


def _ingested_origins(bundle: Bundle) -> frozenset[str]:
    """Every normalized `origin` some `Source` page in *bundle* already carries.

    A `Source` page with **no** `origin` contributes nothing. It is not evidence
    of anything -- only 10 of the live vault's Source pages populate the field
    -- so its item stays queued. The queue therefore over-reports rather than
    under-reports, which is the correct direction of error: a re-ingest is
    idempotent (it appends a `## Re-ingest <date>` section) and costs a human
    one "already done, skip", while an under-report silently loses a design
    spec.
    """
    origins: set[str] = set()
    for document in bundle.concepts.values():
        if document.fm.type != SOURCE_TYPE:
            continue
        stored = document.fm.extra.get("origin")
        if isinstance(stored, str) and stored:
            origins.add(normalize_origin(stored, bundle.root))
    return frozenset(origins)


def run_ingest_queue(layout: WorkspaceLayout) -> IngestQueueReport:
    """Terminal items whose `design` source is not yet recorded as ingested.
    Never writes, and never reads the clock.

    **Derived, never stored.** There is no frontmatter field, no migration and
    no second source of truth that can drift from `sources[]`. The predicate is
    exactly: terminal `work_status`, a `sources[]` entry with
    `id: design`, and no `Source` page whose (normalized) `origin` identifies
    that artifact.

    **Band 3 by necessity.** It needs the item's `sources[]`
    (`work-tracker-okf`) and the set of `Source` pages with their `origin`
    (`doc-wiki-okf`, which owns `normalize_origin`). Band-2 packages may not
    couple sideways, so the composition of two band-2 lanes belongs here --
    the same reason `graph_works_core.archive.commands` holds the work-item and
    wiki archive halves together.

    Read-only for the same reason `run_next` is: a queue that mutates while you
    look at it cannot be polled safely.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    ingested = _ingested_origins(bundle)
    pending: list[PendingIngest] = []
    for item in load_items(bundle):
        if item.work_status not in TERMINAL_STATUSES:
            continue
        resource = next(
            (source.resource for source in item.sources if source.id == SPEC_SOURCE_ID and source.resource),
            None,
        )
        if resource is None:
            continue
        origin = normalize_origin(str(layout.bundle_dir / resource.lstrip("/")), layout.bundle_dir)
        if origin in ingested:
            continue
        pending.append(PendingIngest(path=item.path, work_status=item.work_status, resource=resource, origin=origin))
    return IngestQueueReport(pending=tuple(sorted(pending, key=lambda entry: entry.path)))


@dataclass(frozen=True, slots=True)
class SourceNormalization:
    """One missing canonical design source to stamp on an item page."""

    path: str
    page: Path
    ref: ArtifactRef
    title: str


@dataclass(frozen=True, slots=True)
class NextApplication:
    """The source normalizations that persisted during this invocation."""

    normalized: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NextResult:
    """The requested and selected routing state, preview, and application."""

    requested_path: str
    selected_path: str
    state: RouteState
    route: RouteResult
    child_rollup: ChildRollup | None
    descent: DescendResult | None
    normalizations: tuple[SourceNormalization, ...]
    artifact: ArtifactRef | None = None
    dispatch_resolution: DispatchResolution | None = None
    dispatch_preflight: str | None = None
    application: NextApplication = NextApplication()
    warnings: tuple[str, ...] = ()


def _plan_source_normalization(bundle_root: Path, item: WorkItem) -> SourceNormalization | None:
    """Plan the canonical design stamp when the artifact exists and no
    authored source already owns that id.
    """
    missing = missing_design_source(bundle_root, item)
    if missing is None:
        return None
    ref, title = missing
    return SourceNormalization(path=item.path, page=bundle_root / item.page_path, ref=ref, title=title)


def _apply_normalizations(
    layout: WorkspaceLayout,
    changes: Sequence[SourceNormalization],
    *,
    bundle: Bundle | None = None,
) -> tuple[NextApplication, tuple[str, ...]]:
    normalized: list[str] = []
    warnings: list[str] = []
    for change in changes:
        try:
            before = change.page.read_bytes()
            document = parse(before.decode("utf-8"), path=change.page)
            if any(source.id == change.ref.source_id for source in document.fm.sources):
                continue
            if upsert(document, change.ref, title=change.title):
                member = change.page.relative_to(layout.bundle_dir).as_posix()
                mutation = _write_plan(
                    layout.bundle_dir,
                    "file",
                    (_planned_write(member, before, document.serialize().encode("utf-8")),),
                    validate_paths=(change.path,),
                )
                application = apply_mutation(layout, mutation, repo_roots=_repo_roots(layout), baseline_bundle=bundle)
                if application.ok:
                    normalized.append(change.path)
                else:
                    warnings.extend(
                        f"{change.path}: design source normalization failed: {failure}"
                        for failure in application.failures
                    )
        except OSError as exc:
            warnings.append(f"{change.path}: design source normalization failed: {exc}")
    return NextApplication(normalized=tuple(normalized)), tuple(warnings)


def _stage_artifact(bundle_root: Path, item: WorkItem, result: RouteResult) -> ArtifactRef | None:
    """Where the dispatched stage writes its output, or `None`.

    `Transition.stamp_source` is the routing table's own statement of which
    artifact this stage produces, and `compose.stamp_for` is its inverse back
    to a path -- the same pair `_plan_source_normalization` already uses. A
    stage that stamps nothing (execute, finish, a terminal or invalid item)
    has no artifact, which is a shape and not a degrade.
    """
    if result.on_complete is None or result.on_complete.stamp_source is None:
        return None
    ref, _title = stamp_for(bundle_root, item, result.on_complete.stamp_source)
    return ref


def _load_rules(layout: WorkspaceLayout) -> tuple[DispatchRule, ...] | WorkspaceError:
    """The workspace's dispatch rules, or the error loading them raised."""
    try:
        return load_dispatch_config(layout).rules
    except WorkspaceError as exc:
        return exc


def _resolve_dispatch_with(
    rules: tuple[DispatchRule, ...] | WorkspaceError, state: RouteState, computed: RouteResult
) -> tuple[DispatchResolution | None, str | None]:
    """Fold *computed*'s dispatch with *rules*; a load error or refused profile is the preflight."""
    if computed.dispatch is None:
        return None, None
    if isinstance(rules, WorkspaceError):
        return None, str(rules)
    try:
        return resolve_dispatch(dispatch_attributes(state, computed.dispatch), rules=rules), None
    except WorkspaceError as exc:
        return None, str(exc)


def _resolve_next_dispatch(
    layout: WorkspaceLayout, state: RouteState, computed: RouteResult
) -> tuple[DispatchResolution | None, str | None]:
    if computed.dispatch is None:
        return None, None
    return _resolve_dispatch_with(_load_rules(layout), state, computed)


def _plan_route(bundle: Bundle, items: Sequence[WorkItem], path: str, *, descend: bool) -> tuple[NextResult, WorkItem]:
    """The dry-run routing preview for *path* over an already-loaded bundle.

    Resolves no dispatch and writes nothing: the planned source
    normalizations are applied to the in-memory items only, so routing sees
    the repaired state without the page being touched. `run_next`,
    `run_dispatch_explain` and `run_work_queue` all route through here.
    """
    requested = next((item for item in items if item.path == path), None)
    if requested is None:
        detail = unreadable_detail(bundle, path)
        if detail is not None:
            raise ValueError(f"{path}.md {detail}")
        raise ValueError(f"unknown work item {path!r}")

    descent_result = descend_to_leaf(items, path) if descend else None
    selected_path = descent_result.leaf if descent_result is not None and descent_result.leaf is not None else path
    selected = next(item for item in items if item.path == selected_path)

    normalization_items = (requested,) if requested.path == selected.path else (requested, selected)
    normalizations = tuple(
        change for item in normalization_items if (change := _plan_source_normalization(bundle.root, item)) is not None
    )
    normalized_paths = {change.path for change in normalizations}
    planned_items = tuple(
        replace(item, has_design_artifact=True) if item.path in normalized_paths else item for item in items
    )
    state = state_for(
        planned_items,
        selected_path,
        hold=hold_for(planned_items, bundle.root, selected_path),
    )
    assert state is not None
    computed = route(state)
    preview = NextResult(
        requested_path=path,
        selected_path=selected_path,
        state=state,
        route=computed,
        child_rollup=state.child_rollup,
        descent=descent_result,
        normalizations=normalizations,
        artifact=_stage_artifact(bundle.root, selected, computed),
    )
    return preview, selected


def _plan_next(layout: WorkspaceLayout, path: str, *, descend: bool) -> tuple[NextResult, Bundle, WorkItem]:
    """Load the bundle once and plan *path* over it (`_plan_route`)."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    preview, selected = _plan_route(bundle, tuple(load_items(bundle)), path, descend=descend)
    return preview, bundle, selected


def run_next(
    layout: WorkspaceLayout,
    path: str,
    *,
    descend: bool = False,
    dry_run: bool = True,
) -> NextResult:
    """Plan what to dispatch for *path* and optionally descend to its leaf.

    Resolves the hold for *this path* specifically, not just "the owner has
    some open decision" — the behavioral improvement over the standalone
    `work_tracker_okf.cli`, which does not resolve decision holds from a real
    ledger.

    Dry-run is the default. An `effort=` override is not exposed here or by the
    standalone CLI.

    The single write is the canonical design-source repair and nothing else;
    `test_run_next.py`'s confinement tests pin that.
    """
    preview, bundle, selected = _plan_next(layout, path, descend=descend)
    if dry_run:
        resolution, preflight = _resolve_next_dispatch(layout, preview.state, preview.route)
        return replace(preview, dispatch_resolution=resolution, dispatch_preflight=preflight)

    application, warnings = _apply_normalizations(layout, preview.normalizations, bundle=bundle)
    persisted_bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    persisted_items = load_items(persisted_bundle)
    persisted_state = state_for(
        persisted_items,
        preview.selected_path,
        hold=hold_for(
            persisted_items,
            persisted_bundle.root,
            preview.selected_path,
        ),
    )
    assert persisted_state is not None
    persisted_route = route(persisted_state)
    resolution, preflight = _resolve_next_dispatch(layout, persisted_state, persisted_route)
    return replace(
        preview,
        dispatch_resolution=resolution,
        dispatch_preflight=preflight,
        state=persisted_state,
        route=persisted_route,
        child_rollup=persisted_state.child_rollup,
        artifact=_stage_artifact(persisted_bundle.root, selected, persisted_route),
        application=application,
        warnings=warnings,
    )


@dataclass(frozen=True, slots=True)
class DispatchExplanation:
    """Why *path* resolves to its dispatch profile, rule by rule.

    `next_result` is the dry-run `NextResult` `/v1/work/next` projects, with
    this read's resolution or preflight folded in, so an interface derives
    blockers through the one function both use. With no dispatch -- a
    blocked, terminal or gate item, or a profile the fold refuses --
    `attributes`, `packaged_rule` and `resolution` are `None` and no rule is
    marked matched.
    """

    path: str
    attributes: Mapping[str, AttributeValue] | None
    packaged_rule: DispatchRule | None
    rules: tuple[tuple[DispatchRule, bool], ...]
    resolution: DispatchResolution | None
    next_result: NextResult


def run_dispatch_explain(layout: WorkspaceLayout, path: str) -> DispatchExplanation:
    """Explain `gw next`'s dispatch fold for *path*, without descending. Never writes.

    Unlike `run_next`, a malformed dispatch file is not folded into a
    preflight blocker: it raises `WorkspaceError`, because the rules are this
    read's subject rather than an input to something else. A profile the fold
    refuses (`DispatchProfileError`) is still the preflight `next` reports.
    Raises `ValueError` for an unknown or unreadable *path*.
    """
    preview, _bundle, _selected = _plan_next(layout, path, descend=False)
    rules = load_dispatch_config(layout).rules
    unmatched = tuple((rule, False) for rule in rules)
    dispatch = preview.route.dispatch
    if dispatch is None:
        return DispatchExplanation(path, None, None, unmatched, None, preview)
    attributes = dispatch_attributes(preview.state, dispatch)
    try:
        resolution = resolve_dispatch(attributes, rules=rules)
    except WorkspaceError as exc:
        return DispatchExplanation(path, None, None, unmatched, None, replace(preview, dispatch_preflight=str(exc)))
    return DispatchExplanation(
        path=path,
        attributes=attributes,
        packaged_rule=packaged_rule(dispatch.variant),
        rules=tuple((rule, rule_matches(rule.match, attributes)) for rule in rules),
        resolution=resolution,
        next_result=replace(preview, dispatch_resolution=resolution),
    )


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One active, non-terminal item and the dry-run `NextResult` for it.

    `result` is what `run_next(layout, item.path, dry_run=True)` returns, so an
    interface derives skill, mode and blockers through the functions
    `/v1/work/next` uses.
    """

    item: WorkItem
    result: NextResult


def run_work_queue(layout: WorkspaceLayout) -> tuple[QueueEntry, ...]:
    """Route every active, non-terminal item as a dry-run `run_next` would. Never writes.

    The bundle and the dispatch config are each loaded once for the whole
    queue. A malformed dispatch file is each dispatchable item's preflight
    blocker, exactly as `next` reports it, rather than a failure of the read.
    Epics waiting on their children are included with that blocker, so no
    active item is silently dropped.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = tuple(load_items(bundle))
    rules = _load_rules(layout)
    entries: list[QueueEntry] = []
    for item in sorted(items, key=lambda candidate: candidate.path):
        if item.archived or item.work_status in TERMINAL_STATUSES:
            continue
        preview, _selected = _plan_route(bundle, items, item.path, descend=False)
        resolution, preflight = _resolve_dispatch_with(rules, preview.state, preview.route)
        entries.append(QueueEntry(item, replace(preview, dispatch_resolution=resolution, dispatch_preflight=preflight)))
    return tuple(entries)


def run_lint(
    layout: WorkspaceLayout,
    config: Config,
    *,
    repo_root: Path | None = None,
    repo_roots: tuple[Path, ...] = (),
    strict: bool = False,
    today: date,
) -> Report:
    """Report conformance and lane findings for this workspace's work bundle.
    Never writes.

    `work_tracker_okf.compose.rule_set` fed to `okf_io.validate` — single
    bundle, single lane. This is **not** a replacement for
    `graph_works_core.lint_drift.lint.run_mechanical`'s two-lane workspace
    check; it is the fast, synchronous, LLM-free "does the work lane conform"
    check `work_tracker_okf.cli` already provides, now
    `WorkspaceLayout`-shaped. `repo_root=None` with no `repo_roots` skips
    `targets.affects-missing` (`plan.action-target-missing` still checks the
    vault root), the same contract `work_tracker_okf.compose.rule_set`
    documents. `repo_roots` is every declared repo in a multi-repository
    workspace: a path resolving under any of them is good.

    `strict=True` promotes every warning to an error first, which fails even
    a conformant vault. A caller wiring this into an acceptance gate wants
    `strict=False`.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=_work_only_ignore(layout))
    rules = rule_set(
        layout.bundle_dir,
        repo_root=repo_root,
        repo_roots=repo_roots,
        vault_root=layout.root,
        declarations_dir=config.declarations_dir,
    )
    return okf_validate(bundle, today=today, extra_rules=rules, strict=strict)


def _work_only_ignore(layout: WorkspaceLayout) -> tuple[str, ...]:
    """Every top-level bundle member except `work/`, plus the lane's own
    recipe — the same partition `graph_works_core.lint_drift.lanes` computes
    for the combined workspace lint's work lane, duplicated rather than
    shared. The package's import contract keeps verticals independent, so the
    two copies stay small rather than reach across that forbidden edge.

    Without this, `load_bundle` walks the whole bundle — wiki content
    included — and `okf_io.validate`'s built-in catalog (links, lifecycle,
    frontmatter) reports on pages this function was never asked about,
    contradicting `run_lint`'s own "does the work lane conform" contract:
    a broken link in an unrelated concept page must not fail a work-item
    check.
    """
    siblings = tuple(
        f"{entry.name}/*" if entry.is_dir() else entry.name
        for entry in sorted(layout.bundle_dir.iterdir(), key=lambda path: path.name)
        if entry.name != WORK_DIR
    )
    return (*siblings, *IGNORE)


@dataclass(frozen=True, slots=True)
class RegenIndexesResult:
    """Every required lane index plan plus an optional journaled application."""

    plans: tuple[LaneIndexPlan, ...]
    mutation: WorkMutationPlan
    application: MutationApplication | None = None


def _absent_index_lane_preconditions(root: Path, items: Sequence[WorkItem]) -> Mapping[str, DirectoryPrecondition]:
    """Retain ownership of lanes absent before the domain planner reads them.

    The lane set must equal `work_tracker_okf.indexes._required_lanes`' -- a
    precondition on a lane the planner never plans is a claim on a directory
    nothing creates. It is duplicated rather than imported for the same reason
    `_work_only_ignore` duplicates the lane partition: the vertical stays
    independent, and the copy stays small.

    The archived child lane is claimed only for a parent that already has
    archived children, matching the root-only archive policy.
    """
    lanes = {"work", "work/_archive"}
    for item in items:
        if not item.archived and item.type in PARENT_TYPES:
            lanes.add(child_lane(item.path))
            if item.archived_child_paths:
                lanes.add(child_lane(item.path, archived=True))
    conditions = {lane: DirectoryPrecondition(lane, None) for lane in sorted(lanes) if not os.path.lexists(root / lane)}
    return MappingProxyType(conditions)


def run_regen_indexes(layout: WorkspaceLayout, *, dry_run: bool = True) -> RegenIndexesResult:
    """Reconcile every required root and parent-owned work lane."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    lane_preconditions = _absent_index_lane_preconditions(bundle.root, items)
    plans = plan_indexes(bundle.root, items)
    changed_lanes = {plan.lane for plan in plans if plan.changed}
    mutation = _write_plan(
        bundle.root,
        "indexes",
        tuple(
            _planned_write(
                plan.path.relative_to(bundle.root).as_posix(),
                plan.before.encode("utf-8") if plan.before is not None else None,
                plan.after.encode("utf-8"),
            )
            for plan in plans
            if plan.changed
        ),
        directory_preconditions=tuple(
            condition for lane, condition in lane_preconditions.items() if lane in changed_lanes
        ),
    )
    application = (
        None if dry_run else apply_mutation(layout, mutation, repo_roots=_repo_roots(layout), baseline_bundle=bundle)
    )
    return RegenIndexesResult(plans=plans, mutation=mutation, application=application)


@dataclass(frozen=True, slots=True)
class PathMutationResult:
    """A canonical subtree mutation and its optional journaled application."""

    plan: WorkMutationPlan
    application: MutationApplication | None = None


def run_reparent(
    layout: WorkspaceLayout,
    source_path: str,
    parent_path: str,
    *,
    dry_run: bool = True,
) -> PathMutationResult:
    # Loaded with ignore=() (not IGNORE) -- do not pass this bundle as baseline_bundle,
    # it would silently validate a different corpus than the postcondition gate expects.
    bundle = load_bundle(layout.bundle_dir, ignore=())
    plan = plan_reparent(bundle, load_items(bundle), source_path, parent_path)
    return PathMutationResult(
        plan, None if dry_run or not plan.ok else apply_mutation(layout, plan, repo_roots=_repo_roots(layout))
    )


def run_release_adoption(
    layout: WorkspaceLayout,
    source_path: str,
    release_path: str,
    *,
    dry_run: bool = True,
) -> PathMutationResult:
    # Loaded with ignore=() (not IGNORE) -- do not pass this bundle as baseline_bundle,
    # it would silently validate a different corpus than the postcondition gate expects.
    bundle = load_bundle(layout.bundle_dir, ignore=())
    plan = plan_release_adoption(bundle, load_items(bundle), source_path, release_path)
    return PathMutationResult(
        plan, None if dry_run or not plan.ok else apply_mutation(layout, plan, repo_roots=_repo_roots(layout))
    )


@dataclass(frozen=True, slots=True)
class DecisionCommandResult:
    """Domain-native decision entries, rollup, and optional mutation outcome."""

    owner: DecisionOwner
    entries: tuple[Decision, ...]
    counts: Mapping[str, int]
    warnings: tuple[str, ...]
    plan: DecisionPlan | None = None
    application: MutationApplication | None = None


@dataclass(frozen=True, slots=True)
class OverturnPlan:
    """A decision supersession and its peer follow-up filing, preflighted together."""

    decision: DecisionPlan
    filing: FilingCompositionPlan
    refusal: Literal["decision-refused", "follow-up-refused"] | None = None


@dataclass(frozen=True, slots=True)
class OverturnApplication:
    """The single journaled application of both overturn effects."""

    mutation: MutationApplication | None = None


@dataclass(frozen=True, slots=True)
class OverturnResult:
    """The resolved owner, full preflight, and observable overturn effects."""

    owner: DecisionOwner
    plan: OverturnPlan
    application: OverturnApplication = OverturnApplication()
    warnings: tuple[str, ...] = ()


class OverturnApplyError(OSError):
    """Retained result error type for callers that choose to reject a failed journal."""


def _optional_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _decision_mutation(
    context: DecisionContext,
    plan: DecisionPlan,
    ledger_before: bytes | None,
    extra_writes: Sequence[PlannedWrite] = (),
) -> WorkMutationPlan:
    owner_path = context.owner.owner_path
    owner_page = item_page(owner_path).path(context.bundle.root)
    owner_before = owner_page.read_bytes()
    owner_document = parse(owner_before.decode("utf-8"), path=owner_page)
    upsert(owner_document, ledger_ref(owner_path), title="Decisions")
    ledger_member = context.owner.ledger.relative_to(context.bundle.root).as_posix()
    owner_member = item_page(owner_path).rel
    ledger_text = _decisions.render(_decisions.parse(plan.snapshot.text).preamble, plan.after)
    return _write_plan(
        context.bundle.root,
        "file",
        (
            _planned_write(ledger_member, ledger_before, ledger_text.encode("utf-8")),
            _planned_write(owner_member, owner_before, owner_document.serialize().encode("utf-8")),
            *extra_writes,
        ),
        validate_paths=(owner_path,),
        warnings=plan.warnings,
    )


def _apply_decision(
    layout: WorkspaceLayout,
    context: DecisionContext,
    plan: DecisionPlan,
    ledger_before: bytes | None,
    extra_writes: Sequence[PlannedWrite] = (),
    *,
    allowed_new_findings: tuple[tuple[str, str], ...] = (),
) -> MutationApplication | None:
    if plan.refusal is not None:
        return None
    return apply_mutation(
        layout,
        _decision_mutation(context, plan, ledger_before, extra_writes),
        repo_roots=_repo_roots(layout),
        baseline_bundle=context.bundle,
        allowed_new_findings=allowed_new_findings,
    )


def _decision_result(
    context: DecisionContext,
    plan: DecisionPlan,
    application: MutationApplication | None,
) -> DecisionCommandResult:
    entries = plan.after
    warnings = plan.warnings if application is None else application.warnings
    if application is not None and not application.ok:
        persisted = _decisions.load(context.owner.ledger)
        entries = tuple(persisted.entries)
        warnings = (*warnings, *application.failures, *persisted.warnings)
    return DecisionCommandResult(
        owner=context.owner,
        entries=entries,
        counts=MappingProxyType(_decisions.counts(entries)),
        warnings=warnings,
        plan=plan,
        application=application,
    )


def run_decision_add(
    layout: WorkspaceLayout,
    path: str,
    *,
    question: str,
    status: str = "open",
    answer: str | None = None,
    rationale: str | None = None,
    if_wrong: str | None = None,
    affects: Sequence[str] = (),
    on: date,
    decided_by: str,
    hold: str | None = None,
    phase: str | None = None,
    checkpoint: Path | None = None,
    dry_run: bool = True,
) -> DecisionCommandResult:
    """Plan an append in *path*'s owner ledger and optionally apply it.

    With `hold`, the entry is a typed hold (design §6). Every hold check runs
    against the projection re-read inside the decision-owner lock -- the lock
    `gw work advance` also takes -- and the ledger entry plus a park's
    checkpoint are applied as one journaled mutation before it is released.
    """
    if hold is None and (phase is not None or checkpoint is not None):
        raise ValueError("--phase and --checkpoint are only valid with --hold park|skip")
    if hold is not None and hold not in _decisions.HOLD_SHAPES:
        raise ValueError(f"unknown hold {hold!r}; expected one of {sorted(_decisions.HOLD_SHAPES)}")
    draft = checkpoint.read_bytes().decode("utf-8") if checkpoint is not None else None
    target_affects = tuple(affects) or ((path,) if hold is not None else ())

    def planned(context: DecisionContext) -> tuple[DecisionPlan, tuple[PlannedWrite, ...]]:
        ledger = context.owner.ledger
        checkpoint_ref_value: str | None = None
        extra: tuple[PlannedWrite, ...] = ()
        if hold is not None:
            item = path_index(context.items)[path]
            refused = check_hold(
                item,
                status=status,
                hold=hold,
                phase=phase,
                affects=target_affects,
                has_checkpoint=draft is not None,
            )
            if refused is not None:
                return _decisions.plan_refusal(ledger, refused.refusal, refused.detail), ()
            if hold == "park":
                assert phase is not None and draft is not None
                decision_id, _number = _decisions.next_id(_decisions.load(ledger).entries)
                ref = checkpoint_ref(path, phase, decision_id)
                if ref.path(context.bundle.root).exists():
                    detail = f"{ref.rel} already exists; checkpoints are never overwritten"
                    return _decisions.plan_refusal(ledger, "checkpoint-exists", detail), ()
                stamped, invalid = prepare_checkpoint(
                    draft,
                    item_path=path,
                    phase=phase,
                    decision_id=decision_id,
                )
                if invalid is not None:
                    return _decisions.plan_refusal(ledger, invalid.refusal, invalid.detail), ()
                checkpoint_ref_value = ref.resource
                extra = (_planned_write(ref.rel, None, stamped.encode("utf-8")),)
        plan = _decisions.plan_append(
            ledger,
            question=question,
            status=status,
            answer=answer,
            rationale=rationale,
            if_wrong=if_wrong,
            affects=target_affects,
            on=on,
            decided_by=decided_by,
            hold=hold,
            phase=phase,
            checkpoint=checkpoint_ref_value,
        )
        if checkpoint_ref_value is not None and plan.primary is not None:
            assert checkpoint_ref_value.endswith(f"-{plan.primary.id}.md"), "allocation moved under the lock"
        return plan, extra

    if dry_run:
        context = decision_context(layout, path)
        plan, _extra = planned(context)
        application = None
    else:
        with locked_decision_owner(layout, path) as context:
            ledger_before = _optional_bytes(context.owner.ledger)
            plan, extra = planned(context)
            allowed_new_findings: tuple[tuple[str, str], ...] = ()
            owner_path = context.owner.owner_path
            owner_item = path_index(context.items)[owner_path]
            entry = plan.primary
            # A finish owner reports the finding even when the affected item
            # is a child. Only a real, nonterminal hold in this owner's ledger
            # earns the exception; unnamed/unresolvable questions do not.
            if (
                owner_item.phase == "finish"
                and entry is not None
                and any(
                    not item.archived
                    and item.work_status not in TERMINAL_STATUSES
                    and item.phase != "done"
                    and _decisions.holds_for((entry,), item.path)
                    and decision_owner(context.items, item.path) == owner_path
                    for item in context.items
                )
            ):
                allowed_new_findings = ((item_page(owner_path).rel, "decisions.open-at-finish"),)
            application = _apply_decision(
                layout, context, plan, ledger_before, extra, allowed_new_findings=allowed_new_findings
            )
    return _decision_result(context, plan, application)


def run_decision_answer(
    layout: WorkspaceLayout,
    path: str,
    decision_id: str,
    *,
    answer: str,
    rationale: str | None = None,
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult:
    """Plan an answer in *path*'s nearest-owner ledger and optionally apply it."""

    def planned(context: DecisionContext) -> DecisionPlan:
        return _decisions.plan_update(
            context.owner.ledger,
            decision_id,
            answer=answer,
            rationale=rationale,
            on=on,
            decided_by=decided_by,
        )

    if dry_run:
        context = decision_context(layout, path)
        plan = planned(context)
        application = None
    else:
        with locked_decision_owner(layout, path) as context:
            ledger_before = _optional_bytes(context.owner.ledger)
            plan = planned(context)
            application = _apply_decision(layout, context, plan, ledger_before)
    return _decision_result(context, plan, application)


def run_decision_list(
    layout: WorkspaceLayout,
    path: str,
    *,
    status: str | None = None,
    affects: str | None = None,
    cites: str | None = None,
) -> DecisionCommandResult:
    """Read and filter *path*'s nearest-owner ledger without writing."""
    context = decision_context(layout, path)
    parsed = _decisions.load(context.owner.ledger)
    selected = tuple(_decisions.query(parsed.entries, status=status, affects=affects, cites=cites))
    return DecisionCommandResult(
        owner=context.owner,
        entries=selected,
        counts=MappingProxyType(_decisions.counts(parsed.entries)),
        warnings=tuple(parsed.warnings),
    )


@dataclass(frozen=True, slots=True)
class OpenDecision:
    """One open ledger entry, the ledger it lives in, and the active items it holds."""

    owner_path: str
    ledger: Path
    held: tuple[str, ...]
    decision: Decision


def run_open_decisions(layout: WorkspaceLayout) -> tuple[OpenDecision, ...]:
    """Every `status: open` entry in the ledger of every active item's decision owner. Never writes.

    Each owner's ledger is read once, and an absent ledger reads as empty, as
    `gw work decision list` does. Archived items are dropped before any ledger
    is read. `held` is the entry's `affects` paths that are active items whose
    own decision owner is this ledger's owner, in `affects` order: exactly the
    items `hold_for` would report this entry as holding.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = tuple(load_items(bundle))
    active = tuple(item for item in items if not item.archived)
    active_paths = {item.path for item in active}
    owners = sorted({owner for item in active if (owner := decision_owner(items, item.path)) is not None})
    found: list[OpenDecision] = []
    for owner in owners:
        ledger = _decisions.ledger_ref(owner).path(bundle.root)
        for entry in sorted(_decisions.load(ledger).entries, key=lambda decision: decision.number):
            if entry.status != "open":
                continue
            held = tuple(
                path for path in entry.affects if path in active_paths and decision_owner(items, path) == owner
            )
            found.append(OpenDecision(owner_path=owner, ledger=ledger, held=held, decision=entry))
    return tuple(found)


def run_decision_supersede(
    layout: WorkspaceLayout,
    path: str,
    decision_id: str,
    *,
    question: str,
    answer: str,
    rationale: str | None = None,
    affects: Sequence[str] | None = None,
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult:
    """Plan a supersession in *path*'s nearest-owner ledger and optionally apply it."""

    def planned(context: DecisionContext) -> DecisionPlan:
        return _decisions.plan_supersede(
            context.owner.ledger,
            decision_id,
            question=question,
            answer=answer,
            rationale=rationale,
            affects=affects,
            on=on,
            decided_by=decided_by,
        )

    if dry_run:
        context = decision_context(layout, path)
        plan = planned(context)
        application = None
    else:
        with locked_decision_owner(layout, path) as context:
            ledger_before = _optional_bytes(context.owner.ledger)
            plan = planned(context)
            application = _apply_decision(layout, context, plan, ledger_before)
    return _decision_result(context, plan, application)


def _merge_write_mutations(root: Path, *plans: WorkMutationPlan) -> WorkMutationPlan:
    writes: dict[str, PlannedWrite] = {}
    for plan in plans:
        for write in plan.writes:
            if write.member in writes and writes[write.member] != write:
                raise ValueError(f"conflicting planned writes for {write.member}")
            writes[write.member] = write
    return _write_plan(
        root,
        "file",
        tuple(writes.values()),
        mkdirs=tuple(directory for plan in plans for directory in plan.mkdirs),
        validate_paths=tuple(path for plan in plans for path in plan.validate_paths),
        warnings=tuple(warning for plan in plans for warning in plan.warnings),
        directory_preconditions=tuple(condition for plan in plans for condition in plan.directory_preconditions),
    )


def run_decision_overturn(
    layout: WorkspaceLayout,
    config: Config,
    path: str,
    decision_id: str,
    *,
    answer: str,
    rationale: str | None,
    follow_up_title: str,
    follow_up_type: str = "TechDebt",
    follow_up_affects: Sequence[str] = (),
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> OverturnResult:
    """Preflight a decision supersession and peer follow-up before either write."""

    def planned(context: DecisionContext) -> OverturnPlan:
        decision = _decisions.plan_supersede(
            context.owner.ledger,
            decision_id,
            question=follow_up_title,
            answer=answer,
            rationale=rationale,
            affects=None,
            on=on,
            decided_by=decided_by,
        )
        replacement_id = decision.primary.id if decision.primary is not None else "unallocated"
        filing_outcome = plan_file_and_reconcile(
            context.bundle,
            context.items,
            FilingSeed(
                type=follow_up_type,
                title=follow_up_title,
                description=f"Follow-up from overturned decision {replacement_id} — see the owner's decisions ledger",
                on=on,
                parent_path=None,
                depends_on=(),
                affects=tuple(follow_up_affects),
            ),
            load_sections(config.declarations_dir / SECTIONS_DIRNAME),
        )
        refusal: Literal["decision-refused", "follow-up-refused"] | None = (
            "decision-refused"
            if decision.refusal is not None
            else "follow-up-refused"
            if filing_outcome.plan.refusal is not None
            else None
        )
        return OverturnPlan(decision=decision, filing=filing_outcome.plan, refusal=refusal)

    if dry_run:
        context = decision_context(layout, path)
        combined = planned(context)
        return OverturnResult(owner=context.owner, plan=combined)
    with locked_decision_owner(layout, path) as context:
        ledger_before = _optional_bytes(context.owner.ledger)
        combined = planned(context)
        if combined.refusal is not None:
            return OverturnResult(owner=context.owner, plan=combined)
        mutation = _merge_write_mutations(
            context.bundle.root,
            _decision_mutation(context, combined.decision, ledger_before),
            _filing_mutation(context.bundle, combined.filing),
        )
        application = apply_mutation(layout, mutation, repo_roots=_repo_roots(layout), baseline_bundle=context.bundle)
    return OverturnResult(
        owner=context.owner,
        plan=combined,
        application=OverturnApplication(mutation=application),
        warnings=application.failures,
    )


__all__ = [
    "ChildRollup",
    "Decision",
    "DecisionCommandResult",
    "DecisionContext",
    "DecisionOwner",
    "DependencyEdge",
    "DependencyIssue",
    "DependencyParse",
    "DispatchExplanation",
    "FilingRun",
    "IngestQueueReport",
    "NextApplication",
    "NextResult",
    "OpenDecision",
    "OverturnApplication",
    "OverturnApplyError",
    "OverturnPlan",
    "OverturnResult",
    "PathMutationResult",
    "PendingIngest",
    "QueueEntry",
    "RegenIndexesResult",
    "SourceNormalization",
    "StatusReport",
    "Transition",
    "WorkItem",
    "parse_dependencies",
    "run_decision_add",
    "run_decision_answer",
    "run_decision_list",
    "run_decision_overturn",
    "run_decision_supersede",
    "run_dispatch_explain",
    "run_file",
    "run_ingest_queue",
    "run_lint",
    "run_next",
    "run_open_decisions",
    "run_regen_indexes",
    "run_release_adoption",
    "run_reparent",
    "run_status",
    "run_work_list",
    "run_work_queue",
]
