"""Composed commands for the work-tracking vertical: `file`, `next`, `status`,
`lint`, `regen-index`, child-spec adoption, and decision-ledger operations --
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

Eight names are re-exported rather than defined here -- `DependencyEdge`,
`DependencyIssue`, `DependencyParse`, `parse_dependencies`, `FilingOutcome`,
`Decision`, `ChildRollup` and `Transition`. `graph-works-cli` is forbidden to
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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from code_wiki_okf.config import Config
from okf_ext.shape import load_sections
from okf_io import Bundle, IndexUpdate, load, load_bundle, update_index
from okf_io import validate as okf_validate
from okf_io.validate import Report
from work_tracker_okf import decisions as _decisions
from work_tracker_okf._selection import active_preferred_slug_index
from work_tracker_okf.adoption import AdoptionApplication, AdoptionPlan, apply_adoption, plan_adoption
from work_tracker_okf.compose import (
    FilingApplication,
    FilingApplyError,
    FilingCompositionPlan,
    FilingOutcome,
    apply_file_and_reconcile,
    plan_file_and_reconcile,
    rule_set,
    stamp_for,
)
from work_tracker_okf.decisions import Decision, DecisionApplication, DecisionPlan
from work_tracker_okf.dependencies import (
    DependencyEdge,
    DependencyIssue,
    DependencyParse,
    parse_dependencies,
)
from work_tracker_okf.filing import FilingSeed
from work_tracker_okf.hierarchy import ChildRollup, DescendResult, nearest_epic
from work_tracker_okf.hierarchy import descend as descend_to_leaf
from work_tracker_okf.items import IGNORE, WORK_DIR, WorkItem, load_items
from work_tracker_okf.paths import ArtifactRef, decisions_ledger
from work_tracker_okf.projection import ResumeSelection, Rollup, rollup, select_resume
from work_tracker_okf.sources import upsert
from work_tracker_okf.workflow import RouteResult, RouteState, Transition, route, state_for

from graph_works_core.workspace.layout import WorkspaceLayout


def run_file(
    layout: WorkspaceLayout,
    config: Config,
    *,
    type: str,
    title: str,
    description: str,
    on: date,
    words: str | None = None,
    effort: str | None = None,
    blast_radius: str | None = None,
    target: str | None = None,
    owner: str | None = None,
    parent: str | None = None,
    depends_on: Sequence[DependencyEdge] = (),
    affects: Sequence[str] = (),
    tags: Sequence[str] = (),
    dry_run: bool = True,
) -> FilingOutcome:
    """Plan one graph-aware page/index/log filing and optionally apply it."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    seed = FilingSeed(
        type=type,
        title=title,
        description=description,
        on=on,
        words=words,
        effort=effort,
        blast_radius=blast_radius,
        target=target,
        owner=owner,
        parent=parent,
        depends_on=tuple(depends_on),
        affects=tuple(affects),
        tags=tuple(tags),
    )
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        seed,
        load_sections(config.declarations_dir / "_sections"),
    )
    if dry_run or outcome.plan.refusal is not None:
        return outcome
    return FilingOutcome(plan=outcome.plan, application=apply_file_and_reconcile(outcome.plan))


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
    """One missing canonical design-spec source to stamp on an item page."""

    slug: str
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

    requested_slug: str
    selected_slug: str
    state: RouteState
    route: RouteResult
    child_rollup: ChildRollup | None
    descent: DescendResult | None
    normalizations: tuple[SourceNormalization, ...]
    artifact: ArtifactRef | None = None
    application: NextApplication = NextApplication()
    warnings: tuple[str, ...] = ()


def _has_open_decision(items: Sequence[WorkItem], bundle_root: Path, slug: str) -> bool:
    """Whether an OPEN decision in *slug*'s owning epic's ledger names *slug*
    in its `affects` — the same owning-epic resolution used by
    `graph_works_core.orchestrate.commands`, scoped here to one slug.
    """
    epic = nearest_epic(items, slug)
    if epic is None:
        return False
    archived = next((item.archived for item in items if item.slug == epic), False)
    ledger = decisions_ledger(epic, archived=archived).path(bundle_root)
    entries = _decisions.load(ledger).entries
    return bool(_decisions.query(entries, status="open", affects=slug))


def _plan_source_normalization(bundle_root: Path, item: WorkItem) -> SourceNormalization | None:
    """Plan the canonical design-spec stamp when the artifact exists and no
    authored source already owns that id.
    """
    if item.has_spec_doc:
        return None
    ref, title = stamp_for(bundle_root, item, "design-spec")
    if not ref.path(bundle_root).exists():
        return None
    return SourceNormalization(slug=item.slug, page=bundle_root / item.path, ref=ref, title=title)


def _apply_normalizations(
    changes: Sequence[SourceNormalization],
) -> tuple[NextApplication, tuple[str, ...]]:
    normalized: list[str] = []
    warnings: list[str] = []
    for change in changes:
        try:
            document = load(change.page)
            if any(source.id == change.ref.source_id for source in document.fm.sources):
                continue
            if upsert(document, change.ref, title=change.title):
                document.save()
                normalized.append(change.slug)
        except OSError as exc:
            warnings.append(f"{change.slug}: design-spec source normalization failed: {exc}")
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
    slug: str,
    *,
    descend: bool = False,
    dry_run: bool = True,
) -> NextResult:
    """Plan what to dispatch for *slug* and optionally descend to its leaf.

    Resolves `has_open_decision` for *this slug* specifically, not just "the
    epic has some open decision" — the behavioral improvement over the
    standalone `work_tracker_okf.cli`, which does not resolve decision holds
    from a real ledger.

    Dry-run is the default. An `effort=` override is not exposed here or by the
    standalone CLI.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    requested = next((item for item in items if item.slug == slug), None)
    if requested is None:
        raise ValueError(f"unknown work item {slug!r}")

    descent_result = descend_to_leaf(items, slug) if descend else None
    selected_slug = descent_result.leaf if descent_result is not None and descent_result.leaf is not None else slug
    selected = next(item for item in items if item.slug == selected_slug)

    normalization_items = (requested,) if requested.slug == selected.slug else (requested, selected)
    normalizations = tuple(
        change for item in normalization_items if (change := _plan_source_normalization(bundle.root, item)) is not None
    )
    normalized_slugs = {change.slug for change in normalizations}
    planned_items = tuple(replace(item, has_spec_doc=True) if item.slug in normalized_slugs else item for item in items)
    state = state_for(
        planned_items,
        selected_slug,
        has_open_decision=_has_open_decision(planned_items, bundle.root, selected_slug),
    )
    assert state is not None
    computed = route(state)
    preview = NextResult(
        requested_slug=slug,
        selected_slug=selected_slug,
        state=state,
        route=computed,
        child_rollup=state.child_rollup,
        descent=descent_result,
        normalizations=normalizations,
        artifact=_stage_artifact(bundle.root, selected, computed),
    )
    if dry_run:
        return preview

    application, warnings = _apply_normalizations(normalizations)
    persisted_bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    persisted_items = load_items(persisted_bundle)
    persisted_state = state_for(
        persisted_items,
        selected_slug,
        has_open_decision=_has_open_decision(
            persisted_items,
            persisted_bundle.root,
            selected_slug,
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
    rules = rule_set(layout.bundle_dir, repo_root=repo_root, declarations_dir=config.declarations_dir)
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


def run_regen_index(layout: WorkspaceLayout, *, dry_run: bool = True) -> IndexUpdate:
    """Rebuild `work/index.md` from what's on disk. Never touches `log.md` —
    this changes derived index content, not what the vault contains, the same
    rule `sync-children` follows.

    `create_missing=True`: a vault whose first act is `file` already gets one
    through `work_tracker_okf.compose.plan_file_and_reconcile`, but a bundle
    that predates this command would otherwise never get one.

    `update_index` always returns one `IndexUpdate` per requested directory;
    exactly one is requested here, so the tuple is unwrapped rather than
    handed back — a length-1 tuple would be dead API surface at every call
    site.
    """
    updates = update_index(
        load_bundle(layout.bundle_dir, ignore=IGNORE),
        directories=[WORK_DIR],
        create_missing=True,
        dry_run=dry_run,
    )
    return updates[0]


@dataclass(frozen=True, slots=True)
class AdoptChildSpecsResult:
    """The planned child-spec adoptions and any persisted changes."""

    plan: AdoptionPlan
    application: AdoptionApplication = field(default_factory=AdoptionApplication)


def run_adopt_child_specs(
    layout: WorkspaceLayout,
    epic_slug: str,
    *,
    dry_run: bool = True,
) -> AdoptChildSpecsResult:
    """Plan migrated child-spec adoption and optionally apply it."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    if not any(item.slug == epic_slug for item in items):
        raise ValueError(f"unknown work item {epic_slug!r}")
    plan = plan_adoption(bundle, items, epic_slug)
    if dry_run or plan.refusal is not None:
        return AdoptChildSpecsResult(plan=plan)
    return AdoptChildSpecsResult(plan=plan, application=apply_adoption(plan))


@dataclass(frozen=True, slots=True)
class DecisionOwner:
    """The epic-owned ledger selected for a requested work item."""

    epic_slug: str
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
    application: DecisionApplication = field(default_factory=DecisionApplication)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """One bundle projection and the resolved epic ownership for a command."""

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
    """Effects completed while applying a preflighted overturn."""

    decision: DecisionApplication = field(default_factory=DecisionApplication)
    filing: FilingApplication = field(default_factory=FilingApplication)


@dataclass(frozen=True, slots=True)
class OverturnResult:
    """The resolved owner, full preflight, and observable overturn effects."""

    owner: DecisionOwner
    plan: OverturnPlan
    application: OverturnApplication = OverturnApplication()
    warnings: tuple[str, ...] = ()


class OverturnApplyError(OSError):
    """An overturn whose ordered apply failed after observable partial effects."""

    def __init__(self, message: str, application: OverturnApplication) -> None:
        super().__init__(message)
        self.application = application


def _decision_context(layout: WorkspaceLayout, slug: str) -> DecisionContext:
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    selected = active_preferred_slug_index(items)
    requested = selected.get(slug)
    if requested is None:
        raise ValueError(f"unknown work item {slug!r}")
    epic_slug = nearest_epic(tuple(selected.values()), slug)
    if epic_slug is None:
        raise ValueError(f"{slug!r} has no epic ancestor; decisions ledgers are epic-owned")
    epic = selected[epic_slug]
    ledger = decisions_ledger(epic_slug, archived=epic.archived).path(bundle.root)
    return DecisionContext(
        owner=DecisionOwner(epic_slug, None if slug == epic_slug else slug, ledger),
        bundle=bundle,
        items=items,
    )


def run_decision_add(
    layout: WorkspaceLayout,
    slug: str,
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
    """Plan an append in *slug*'s epic-owned ledger and optionally apply it."""
    context = _decision_context(layout, slug)
    plan = _decisions.plan_append(
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
    application = DecisionApplication() if dry_run else _decisions.apply_plan(plan)
    return DecisionCommandResult(
        owner=context.owner,
        entries=plan.after,
        counts=MappingProxyType(_decisions.counts(plan.after)),
        warnings=plan.warnings,
        plan=plan,
        application=application,
    )


def run_decision_answer(
    layout: WorkspaceLayout,
    slug: str,
    decision_id: str,
    *,
    answer: str,
    rationale: str | None = None,
    on: date,
    decided_by: str,
    dry_run: bool = True,
) -> DecisionCommandResult:
    """Plan an answer in *slug*'s epic-owned ledger and optionally apply it."""
    context = _decision_context(layout, slug)
    plan = _decisions.plan_update(
        context.owner.ledger,
        decision_id,
        answer=answer,
        rationale=rationale,
        on=on,
        decided_by=decided_by,
    )
    application = DecisionApplication() if dry_run else _decisions.apply_plan(plan)
    return DecisionCommandResult(
        owner=context.owner,
        entries=plan.after,
        counts=MappingProxyType(_decisions.counts(plan.after)),
        warnings=plan.warnings,
        plan=plan,
        application=application,
    )


def run_decision_list(
    layout: WorkspaceLayout,
    slug: str,
    *,
    status: str | None = None,
    affects: str | None = None,
    cites: str | None = None,
) -> DecisionCommandResult:
    """Read and filter *slug*'s epic-owned ledger without writing."""
    context = _decision_context(layout, slug)
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
    slug: str,
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
    """Plan a supersession in *slug*'s epic-owned ledger and optionally apply it."""
    context = _decision_context(layout, slug)
    plan = _decisions.plan_supersede(
        context.owner.ledger,
        decision_id,
        question=question,
        answer=answer,
        rationale=rationale,
        affects=affects,
        on=on,
        decided_by=decided_by,
    )
    application = DecisionApplication() if dry_run else _decisions.apply_plan(plan)
    return DecisionCommandResult(
        owner=context.owner,
        entries=plan.after,
        counts=MappingProxyType(_decisions.counts(plan.after)),
        warnings=plan.warnings,
        plan=plan,
        application=application,
    )


def _apply_overturn(owner: DecisionOwner, plan: OverturnPlan) -> OverturnResult:
    decision_application = _decisions.apply_plan(plan.decision)
    partial = OverturnApplication(decision=decision_application)
    if decision_application.stale:
        return OverturnResult(
            owner=owner,
            plan=plan,
            application=partial,
            warnings=("stale-decision-plan: ledger changed after preflight; follow-up was not filed",),
        )
    if not decision_application.written:
        return OverturnResult(owner=owner, plan=plan, application=partial)
    try:
        filing_application = apply_file_and_reconcile(plan.filing)
    except FilingApplyError as exc:
        application = OverturnApplication(decision=decision_application, filing=exc.application)
        raise OverturnApplyError(str(exc), application) from exc
    return OverturnResult(
        owner=owner,
        plan=plan,
        application=OverturnApplication(decision=decision_application, filing=filing_application),
    )


def run_decision_overturn(
    layout: WorkspaceLayout,
    config: Config,
    slug: str,
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
    context = _decision_context(layout, slug)
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
            description=f"Follow-up from overturned decision {replacement_id} — see the epic's decisions ledger",
            on=on,
            parent=None,
            depends_on=(),
            affects=tuple(follow_up_affects),
        ),
        load_sections(config.declarations_dir / "_sections"),
    )
    refusal: Literal["decision-refused", "follow-up-refused"] | None = (
        "decision-refused"
        if decision.refusal is not None
        else "follow-up-refused"
        if filing_outcome.plan.refusal is not None
        else None
    )
    combined = OverturnPlan(decision=decision, filing=filing_outcome.plan, refusal=refusal)
    if dry_run or refusal is not None:
        return OverturnResult(owner=context.owner, plan=combined)
    return _apply_overturn(context.owner, combined)


__all__ = [
    "AdoptChildSpecsResult",
    "ChildRollup",
    "Decision",
    "DecisionCommandResult",
    "DecisionOwner",
    "DependencyEdge",
    "DependencyIssue",
    "DependencyParse",
    "FilingOutcome",
    "NextApplication",
    "NextResult",
    "OverturnApplication",
    "OverturnApplyError",
    "OverturnPlan",
    "OverturnResult",
    "SourceNormalization",
    "StatusReport",
    "Transition",
    "parse_dependencies",
    "run_adopt_child_specs",
    "run_decision_add",
    "run_decision_answer",
    "run_decision_list",
    "run_decision_overturn",
    "run_decision_supersede",
    "run_file",
    "run_lint",
    "run_next",
    "run_regen_index",
    "run_status",
]
