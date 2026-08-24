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
`graph_works_core.orchestrate.commands.run_stage_advance` already composes
`work_tracker_okf.compose.advance_and_stamp` with worktree provenance and a
results stub; `graph_works_core.archive.commands.run_archive` already composes
`work_tracker_okf.archive`.
`sync-children` has no `WorkspaceLayout`-aware caller anywhere yet and is
left as a gap for a future item. Neither does `work_tracker_okf.cli`'s own
`init`, which this item does not touch.

Seven names are re-exported rather than defined here -- `DependencyEdge`,
`DependencyIssue`, `DependencyParse`, `parse_dependencies`, `Decision`,
`ChildRollup` and `Transition`. `graph-works-cli` is forbidden to
import `work_tracker_okf` at all (its own boundary test asserts it): `run_file`
takes typed dependency edges, and the CLI's renderers destructure decisions,
child rollups and routing transitions out of `NextResult`/`DecisionCommandResult`
-- without the re-export the CLI could construct or type-annotate none of it.
They are listed in `__all__` so the re-export is a contract rather than an
accident of import order.

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

import fcntl
import hashlib
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from code_wiki_okf.config import Config
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
from work_tracker_okf.hierarchy import ChildRollup, DescendResult, nearest_parent
from work_tracker_okf.hierarchy import descend as descend_to_leaf
from work_tracker_okf.indexes import LaneIndexPlan, plan_indexes
from work_tracker_okf.items import IGNORE, WORK_DIR, WorkItem, load_items
from work_tracker_okf.mutation import (
    DirectoryPrecondition,
    PlannedWrite,
    WorkMutationPlan,
)
from work_tracker_okf.paths import ArtifactRef, child_lane, item_page
from work_tracker_okf.projection import ResumeSelection, Rollup, rollup, select_resume
from work_tracker_okf.reparent import plan_release_adoption, plan_reparent
from work_tracker_okf.sources import upsert
from work_tracker_okf.vocabulary import PARENT_TYPES, SPEC_SOURCE_ID
from work_tracker_okf.workflow import RouteResult, RouteState, Transition, route, state_for

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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

    return FilingRun(plan=outcome.plan, application=apply_mutation(layout, _filing_mutation(bundle, outcome.plan)))


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
    application: NextApplication = NextApplication()
    warnings: tuple[str, ...] = ()


def _has_open_decision(items: Sequence[WorkItem], bundle_root: Path, path: str) -> bool:
    """Whether an OPEN decision in *path*'s nearest owner ledger names *path*."""
    owner = nearest_parent(items, path)
    if owner is None:
        return False
    ledger = ledger_ref(owner).path(bundle_root)
    entries = _decisions.load(ledger).entries
    return bool(_decisions.query(entries, status="open", affects=path))


def _plan_source_normalization(bundle_root: Path, item: WorkItem) -> SourceNormalization | None:
    """Plan the canonical design stamp when the artifact exists and no
    authored source already owns that id.
    """
    if item.has_design_artifact:
        return None
    ref, title = stamp_for(bundle_root, item, SPEC_SOURCE_ID)
    if not ref.path(bundle_root).exists():
        return None
    return SourceNormalization(path=item.path, page=bundle_root / item.page_path, ref=ref, title=title)


def _apply_normalizations(
    layout: WorkspaceLayout,
    changes: Sequence[SourceNormalization],
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
                application = apply_mutation(layout, mutation)
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


def run_next(
    layout: WorkspaceLayout,
    path: str,
    *,
    descend: bool = False,
    dry_run: bool = True,
) -> NextResult:
    """Plan what to dispatch for *path* and optionally descend to its leaf.

    Resolves `has_open_decision` for *this path* specifically, not just "the
    owner has some open decision" — the behavioral improvement over the
    standalone `work_tracker_okf.cli`, which does not resolve decision holds
    from a real ledger.

    Dry-run is the default. An `effort=` override is not exposed here or by the
    standalone CLI.

    The single write is the canonical design-source repair and nothing else;
    `test_run_next.py`'s confinement tests pin that.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    requested = next((item for item in items if item.path == path), None)
    if requested is None:
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
        has_open_decision=_has_open_decision(planned_items, bundle.root, selected_path),
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
    if dry_run:
        return preview

    application, warnings = _apply_normalizations(layout, normalizations)
    persisted_bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    persisted_items = load_items(persisted_bundle)
    persisted_state = state_for(
        persisted_items,
        selected_path,
        has_open_decision=_has_open_decision(
            persisted_items,
            persisted_bundle.root,
            selected_path,
        ),
    )
    assert persisted_state is not None
    persisted_route = route(persisted_state)
    return replace(
        preview,
        state=persisted_state,
        route=persisted_route,
        child_rollup=persisted_state.child_rollup,
        artifact=_stage_artifact(persisted_bundle.root, selected, persisted_route),
        application=application,
        warnings=warnings,
    )


def run_lint(
    layout: WorkspaceLayout,
    config: Config,
    *,
    repo_root: Path | None = None,
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
    `WorkspaceLayout`-shaped. `repo_root=None` skips
    `targets.affects-missing` / `plan.action-target-missing`, the same
    contract `work_tracker_okf.compose.rule_set` documents.

    `strict=True` promotes every warning to an error first, which fails even
    a conformant vault. A caller wiring this into an acceptance gate wants
    `strict=False`.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=_work_only_ignore(layout))
    rules = rule_set(
        layout.bundle_dir, repo_root=repo_root, vault_root=layout.root, declarations_dir=config.declarations_dir
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
    """Retain ownership of lanes absent before the domain planner reads them."""
    lanes = {"work", "work/_archive"}
    for item in items:
        if not item.archived and item.type in PARENT_TYPES:
            lanes.add(child_lane(item.path))
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
    application = None if dry_run else apply_mutation(layout, mutation)
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
    bundle = load_bundle(layout.bundle_dir, ignore=())
    plan = plan_reparent(bundle, load_items(bundle), source_path, parent_path)
    return PathMutationResult(plan, None if dry_run or not plan.ok else apply_mutation(layout, plan))


def run_release_adoption(
    layout: WorkspaceLayout,
    source_path: str,
    release_path: str,
    *,
    dry_run: bool = True,
) -> PathMutationResult:
    bundle = load_bundle(layout.bundle_dir, ignore=())
    plan = plan_release_adoption(bundle, load_items(bundle), source_path, release_path)
    return PathMutationResult(plan, None if dry_run or not plan.ok else apply_mutation(layout, plan))


@dataclass(frozen=True, slots=True)
class DecisionOwner:
    """The nearest Release/Epic/Feature ledger owner for a requested path."""

    owner_path: str
    redirected_from: str | None
    ledger: Path


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
class DecisionContext:
    """One bundle projection and its nearest decision owner for a command."""

    owner: DecisionOwner
    bundle: Bundle
    items: tuple[WorkItem, ...]


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


def _decision_lock_path(layout: WorkspaceLayout, owner_path: str) -> Path:
    digest = hashlib.sha256(owner_path.encode("utf-8")).hexdigest()
    return layout.cache_dir / "decisions" / f"{digest}.lock"


@contextmanager
def _decision_lock(layout: WorkspaceLayout, owner_path: str) -> Iterator[None]:
    """Serialize decision composition on caller-owned, replace-stable cache state."""
    lock = _decision_lock_path(layout, owner_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _decision_context(layout: WorkspaceLayout, path: str) -> DecisionContext:
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    selected = path_index(items)
    requested = selected.get(path)
    if requested is None:
        raise ValueError(f"unknown work item {path!r}")
    owner_path = nearest_parent(tuple(selected.values()), path)
    if owner_path is None:
        raise ValueError(f"{path!r} has no Release, Epic, or Feature decision owner")
    ledger = ledger_ref(owner_path).path(bundle.root)
    return DecisionContext(
        owner=DecisionOwner(owner_path, None if path == owner_path else path, ledger),
        bundle=bundle,
        items=items,
    )


@contextmanager
def _locked_decision_context(layout: WorkspaceLayout, path: str) -> Iterator[DecisionContext]:
    """Lock a candidate owner, then retain only a matching fresh projection."""
    while True:
        candidate = _decision_context(layout, path)
        candidate_owner = candidate.owner.owner_path
        with _decision_lock(layout, candidate_owner):
            current = _decision_context(layout, path)
            if current.owner.owner_path != candidate_owner:
                continue
            yield current
            return


def _optional_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _decision_mutation(
    context: DecisionContext,
    plan: DecisionPlan,
    ledger_before: bytes | None,
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
        ),
        validate_paths=(owner_path,),
        warnings=plan.warnings,
    )


def _apply_decision(
    layout: WorkspaceLayout,
    context: DecisionContext,
    plan: DecisionPlan,
    ledger_before: bytes | None,
) -> MutationApplication | None:
    if plan.refusal is not None:
        return None
    return apply_mutation(layout, _decision_mutation(context, plan, ledger_before))


def _decision_result(
    context: DecisionContext,
    plan: DecisionPlan,
    application: MutationApplication | None,
) -> DecisionCommandResult:
    entries = plan.after
    warnings = plan.warnings
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
    dry_run: bool = True,
) -> DecisionCommandResult:
    """Plan an append in *path*'s nearest owner ledger and optionally apply it."""

    def planned(context: DecisionContext) -> DecisionPlan:
        return _decisions.plan_append(
            context.owner.ledger,
            question=question,
            status=status,
            answer=answer,
            rationale=rationale,
            if_wrong=if_wrong,
            affects=affects,
            on=on,
            decided_by=decided_by,
        )

    if dry_run:
        context = _decision_context(layout, path)
        plan = planned(context)
        application = None
    else:
        with _locked_decision_context(layout, path) as context:
            ledger_before = _optional_bytes(context.owner.ledger)
            plan = planned(context)
            application = _apply_decision(layout, context, plan, ledger_before)
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
        context = _decision_context(layout, path)
        plan = planned(context)
        application = None
    else:
        with _locked_decision_context(layout, path) as context:
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
    context = _decision_context(layout, path)
    parsed = _decisions.load(context.owner.ledger)
    selected = tuple(_decisions.query(parsed.entries, status=status, affects=affects, cites=cites))
    return DecisionCommandResult(
        owner=context.owner,
        entries=selected,
        counts=MappingProxyType(_decisions.counts(parsed.entries)),
        warnings=tuple(parsed.warnings),
    )


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
        context = _decision_context(layout, path)
        plan = planned(context)
        application = None
    else:
        with _locked_decision_context(layout, path) as context:
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
        context = _decision_context(layout, path)
        combined = planned(context)
        return OverturnResult(owner=context.owner, plan=combined)
    with _locked_decision_context(layout, path) as context:
        ledger_before = _optional_bytes(context.owner.ledger)
        combined = planned(context)
        if combined.refusal is not None:
            return OverturnResult(owner=context.owner, plan=combined)
        mutation = _merge_write_mutations(
            context.bundle.root,
            _decision_mutation(context, combined.decision, ledger_before),
            _filing_mutation(context.bundle, combined.filing),
        )
        application = apply_mutation(layout, mutation)
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
    "DecisionOwner",
    "DependencyEdge",
    "DependencyIssue",
    "DependencyParse",
    "FilingRun",
    "NextApplication",
    "NextResult",
    "OverturnApplication",
    "OverturnApplyError",
    "OverturnPlan",
    "OverturnResult",
    "PathMutationResult",
    "RegenIndexesResult",
    "SourceNormalization",
    "StatusReport",
    "Transition",
    "parse_dependencies",
    "run_decision_add",
    "run_decision_answer",
    "run_decision_list",
    "run_decision_overturn",
    "run_decision_supersede",
    "run_file",
    "run_lint",
    "run_next",
    "run_regen_indexes",
    "run_release_adoption",
    "run_reparent",
    "run_status",
]
