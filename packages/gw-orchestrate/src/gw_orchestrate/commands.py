"""The dispatch plan: which stages run now, where, on what, saying what.

`plan()` is IO-free, mirroring `work_tracker_okf.workflow.route()`'s own split:
identical inputs produce an identical `OrchestratePlan`, and every config read,
`git` call sits in `run_orchestrate` above it. Nothing here launches a worker
-- `subagents-io` ships the dispatch value types and no execution backend.

Placement is computed, not chosen. Every dispatch of an item with a code
repository carries `placing.compute(...)`'s `Placement` -- the item's own
worktree under `worktrees_dir/<repo>/<branch>`, parented on its enclosing
owner's computed worktree or on the repository's default base -- in
`OrchestratePlan.placements`, keyed by dispatch key, and the inert
`WorktreePlacement(worktree, branch, parent_worktree)` on the dispatch. There
is no action vocabulary, no adoption, no anchor preparation and no reader
checkout: `gw work dispatch` makes the placement real through the gw-pipeline
engine. An item with no code repository (workspace-only, or no repository
resolved) has no placement and launches in the workspace primary checkout.

The frontier walk generalizes `hierarchy.descend()` from pick-one-leaf to
collect-all, **reusing** its `child_gated` predicate, `PICK_ORDER` and
`WALK_DEPTH_CAP` rather than restating them. Admission order then puts
dependency rank (`orchestrate.rank`) ahead of `PICK_ORDER`; that is an
ordering difference between the two, never an eligibility one. `--descend`
and auto-drive disagreeing about the same item is exactly the failure that
reuse prevents, and `test_orchestrate_plan.py` pins the agreement as a property.

Nothing Orca-shaped appears in this module. The four prompt lines are
vendor-neutral; the one place a vendor command may appear is a rule's
`prompt_tail`, which lives in workspace configuration (`pipeline.py`).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from gw_kernel.workspace.bundle import load_work_bundle
from gw_kernel.workspace.dispatch import (
    DispatchProfileError,
    DispatchResolution,
    DispatchRule,
    dispatch_attributes,
    resolve_dispatch,
)
from gw_kernel.workspace.dispatch_artifacts import routing_items
from gw_kernel.workspace.dispatch_config import load_dispatch_config
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.manifest import checked_bool, checked_int
from gw_kernel.workspace.pipeline import ASK_LINE, FINDINGS_LINE
from gw_kernel.workspace.provenance import default_base
from gw_kernel.workspace.repos import ItemRepo, resolve_item_repo
from gw_pipeline.decision_owner import HoldReport, holds_by_path, open_holds
from gw_pipeline.execute_return import pending_return
from gw_pipeline.finish import FinishPlan, FinishTarget, resolve_finish_targets
from gw_pipeline.landed import stale_by_path
from gw_pipeline.placement import Placement, _stable_stem, branch_name, enclosing_owner, stamp_row, stamped_worktrees
from gw_pipeline.repo_context import RepositoryContext, observe_repository, repository_identity
from subagents_io.dispatch import PlannedDispatch, WorktreePlacement
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.affects import code_affects, touches_workspace
from work_tracker_okf.asks import plan_checkpoints
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.hierarchy import PICK_ORDER, active_nonterminal_descendants, child_gated, decision_owner
from work_tracker_okf.items import WorkItem, load_items
from work_tracker_okf.pipeline import PACKAGED_DEFINITION, PipelineDefinition, dispatch_phases, read_only_phases
from work_tracker_okf.snapshot import as_snapshot
from work_tracker_okf.vocabulary import (
    PLAN_SOURCE_ID,
    TERMINAL_STATUSES,
)
from work_tracker_okf.workflow import BlockerKind, Dispatch, RouteResult, Stage, route, state_for

from gw_orchestrate.claims import (
    CODE_WRITE_PHASES,
    Claim,
    CodeScope,
    WorktreeScope,
    claims_for,
    first_conflicts,
    paths_overlap,
)
from gw_orchestrate.placing import compute, compute_chain
from gw_orchestrate.rank import dependent_counts

WALK_DEPTH_CAP = 10_000

#: The phases a stage can be dispatched at: every stage-table row. `done` is terminal.
DISPATCH_PHASES: frozenset[str] = dispatch_phases()

#: The plugin skill a dispatched worker runs. Named rather than inlined so the
#: namespace has one grep-able home when the fork lands. It is a skill, not a
#: command: `commands/` did not ship to Codex, so every entry point is a skill.
DISPATCH_COMMAND = "/gw:workflow"

#: The workspace pointer a dispatched session reads at startup.
WORKSPACE_VAR = "GRAPH_WORKS_DIR"

#: Why a candidate is not dispatching. A closed vocabulary so a consumer can
#: group by it; `BlockedItem.reason` is the sentence.
BLOCKED_KINDS: frozenset[str] = frozenset(
    {
        "deps",
        "capacity",
        "affects-overlap",
        "effort-required",
        "human",
        "relay-untailed",
        "worktree-pending",
        "worktree-unprovable",
        "worktree-ambiguous",
        "decisions",
        "cross-repo-child",
        "invalid",
    }
)


@dataclass(frozen=True, slots=True)
class PlannedAdvance:
    """A node with nothing to dispatch but a transition the coordinator applies
    itself: `mode == "advance"` for a satisfied completion (an epic whose
    children are all terminal), `mode == "repair"` for an off-path phase,
    `mode == "return"` for a backwards repair (an epic at
    `finish` reopened by a child filed afterwards) -- never a transition
    `advance()` would refuse with `children-open`. It carries no placement:
    an item's worktree is computed, never relayed through an advance."""

    path: str
    reason: str
    mode: Literal["advance", "return", "repair"] = "advance"


@dataclass(frozen=True, slots=True)
class BlockedItem:
    path: str
    kind: str  # one of BLOCKED_KINDS
    reason: str


@dataclass(frozen=True, slots=True)
class HumanCheckpoints:
    """An execute plan's checkpoint reading; only `declared` has a known count."""

    status: str
    items: tuple[str, ...] = ()


def read_human_checkpoints(bundle_root: Path, item: WorkItem) -> HumanCheckpoints:
    """Read an item's plan source; missing sources and unreadable files stay distinct."""
    source = next((s for s in item.sources if s.id == PLAN_SOURCE_ID and s.resource), None)
    if source is None or source.resource is None:
        return HumanCheckpoints("no-plan")
    try:
        text = (bundle_root / source.resource.removeprefix("/")).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return HumanCheckpoints("unreadable")
    parsed = plan_checkpoints(text)
    return HumanCheckpoints(parsed.status, parsed.items)


@dataclass(frozen=True, slots=True)
class OrchestratePlan:
    path: str
    terminal: bool
    max_parallel: int
    supervise_merges: bool
    live: tuple[str, ...]
    slots_free: int
    dispatches: tuple[PlannedDispatch, ...]
    advances: tuple[PlannedAdvance, ...]
    blocked: tuple[BlockedItem, ...]
    warnings: tuple[str, ...]
    dispatch_resolutions: Mapping[str, DispatchResolution] = MappingProxyType({})
    dispatch_repos: Mapping[str, ItemRepo] = MappingProxyType({})
    placements: Mapping[str, Placement] = MappingProxyType({})
    finish_targets: Mapping[str, tuple[FinishTarget, ...]] = MappingProxyType({})
    human_checkpoints: Mapping[str, HumanCheckpoints] = field(default_factory=lambda: MappingProxyType({}))
    max_attend: int = 1
    attend_slots_free: int = 0


#: The character budget for a session name. Orca renders it in a task row and
#: a worker card; longer than this and the row elides the part that identifies
#: the item. The eight-hex tail is never what gets cut (see `session_name`).
SESSION_NAME_MAX = 64


#: Labels for dispatches sharing a phase, mapped back to that phase.
SESSION_LABELS: Mapping[str, str] = MappingProxyType({"reconcile": "plan"})


def session_name(path: str, type_: str, phase: str, *, label: str | None = None) -> str:
    """The one name a dispatched worker is known by, everywhere.

        work/epic-a/children/tech-debt-x  (TechDebt, design)
            -> gw-design-x-2f1a9c3d

    A sibling of `gw_pipeline.placement.branch_name` by construction -- same stem, different
    prefix -- so the branch a worker runs on and the session it runs in are
    visibly the same work. The kind lives in the branch segment; the phase
    lives here. Neither repeats the other.

    `label` replaces the phase head for dispatches sharing the same phase.

    Capped at `SESSION_NAME_MAX`. When the cap bites it is the *word* part
    that is truncated and the eight-hex tail that survives: the tail is the
    only thing making two same-basename siblings distinguishable, so
    trimming it would trade away exactly the property it exists to provide.
    """
    # A label distinguishes dispatches that run at the same phase.
    stem = _stable_stem(path, type_)
    head = f"gw-{label or phase}-"
    budget = SESSION_NAME_MAX - len(head)
    if len(stem) > budget:
        words, _, tail = stem.rpartition("-")
        keep = max(0, budget - len(tail) - 1)
        stem = f"{words[:keep].rstrip('-')}-{tail}" if keep else tail
    return f"{head}{stem}"


def session_index(items: Iterable[WorkItem]) -> tuple[dict[str, WorkItem], tuple[str, ...]]:
    """`session_name -> item`, over every (item, dispatchable phase) pair.

    The reverse of `session_name`, and the only reverse there is: the name
    carries a hash, so nothing recovers a path by parsing one. A few hundred
    items times four phases, computed once per plan -- cheap enough that
    caching it would be the more complicated thing.

    **Ambiguity is detected, never resolved by guessing.** Two pairs can
    collide only through an eight-hex `sha256` prefix collision or a
    `session_name` truncation collision. Either way the entry is dropped and
    a warning is returned. A `--live` key that names it then refuses the
    entire plan: its owner cannot be resolved safely, so neither affects
    nor worktree occupancy can be reserved for it.
    """
    index: dict[str, WorkItem] = {}
    collisions: dict[str, list[str]] = {}
    for item in items:
        for phase in (*sorted(DISPATCH_PHASES), *sorted(SESSION_LABELS)):
            name = session_name(
                item.path,
                item.type,
                SESSION_LABELS.get(phase, phase),
                label=phase if phase in SESSION_LABELS else None,
            )
            prior = index.get(name)
            if prior is not None and prior.path != item.path:
                collisions.setdefault(name, [prior.path]).append(item.path)
                continue
            if name in collisions:
                collisions[name].append(item.path)
                continue
            index[name] = item
    warnings: list[str] = []
    for name in sorted(collisions):
        index.pop(name, None)
        paths = ", ".join(sorted(collisions[name]))
        warnings.append(f"session name {name!r} is ambiguous ({paths}); dropped")
    return index, tuple(warnings)


def _live_phase(item: WorkItem, key: str) -> str:
    for phase in DISPATCH_PHASES:
        if session_name(item.path, item.type, phase) == key:
            return phase
    return next(
        phase
        for label, phase in SESSION_LABELS.items()
        if session_name(item.path, item.type, phase, label=label) == key
    )


#: Every route blocker kind maps into orchestrate's closed BLOCKED_KINDS.
#: A totality test makes new BlockerKind members require a classification.
BLOCKER_KIND_TO_BLOCKED: Mapping[BlockerKind, str] = MappingProxyType(
    {
        "dependencies": "deps",
        "effort-required": "effort-required",
        "hold": "decisions",
        "terminal": "human",
        "attribute-required": "human",
        "invalid": "invalid",
        "invalid-entry": "invalid",
        "done": "invalid",
        "no-children": "invalid",
        "waiting-on-children": "invalid",
        "phase-off-path": "invalid",
        "path-invalid": "invalid",
        # Appended by stage_advance, never seen by _frontier; here for totality.
        "finish-incomplete": "invalid",
    }
)


def _classify(kind: BlockerKind | None) -> str:
    """Classify a blocked route; absent blocker detail is invalid."""
    return "invalid" if kind is None else BLOCKER_KIND_TO_BLOCKED[kind]


def _frontier(
    items: Sequence[WorkItem],
    root: str,
    *,
    definition: PipelineDefinition,
    holds: Mapping[str, HoldFact] = MappingProxyType({}),
    stale: Mapping[str, tuple[str, ...]] = MappingProxyType({}),
) -> tuple[list[tuple[WorkItem, RouteResult]], list[PlannedAdvance], list[BlockedItem]]:
    """Every actionable node at or below *root*. Cycle-safe and depth-capped.

    Recurses through `child_gated` into its non-terminal children and routes
    every non-gated node it lands on. A terminal child is skipped only when it
    has no open descendants of its own -- a resolved child with nothing open
    beneath it is a satisfied input, not a blocked item, but a resolved child
    hiding an open grandchild is walked into instead of dropped (design's axis
    2). A node whose route repairs stale state (an epic at `finish` reopened
    by a child filed afterwards) is checked *before* the `child_gated` branch,
    so the widened gate never swallows the repair.
    """
    snapshot = as_snapshot(items)
    by_path = snapshot.by_path
    if root not in by_path:
        return [], [], [BlockedItem(path=root, kind="invalid", reason=f"unknown path {root!r}")]

    candidates: list[tuple[WorkItem, RouteResult]] = []
    advances: list[PlannedAdvance] = []
    blocked: list[BlockedItem] = []

    stack: list[tuple[str, int]] = [(root, 0)]
    visited = {root}
    while stack:
        path, depth = stack.pop()
        node = by_path.get(path)
        if node is None:  # pragma: no cover -- every pushed path is `root` (checked above) or
            # drawn from `snapshot.by_parent`, which indexes the same items as `by_path`
            blocked.append(BlockedItem(path=path, kind="invalid", reason=f"unknown path {path!r}"))
            continue
        if depth >= WALK_DEPTH_CAP:
            blocked.append(
                BlockedItem(
                    path=path,
                    kind="invalid",
                    reason=f"frontier walk depth cap ({WALK_DEPTH_CAP}) reached",
                )
            )
            continue
        children = snapshot.by_parent.get(path, ())
        state = state_for(snapshot, path, hold=holds.get(path), stale_spec=stale.get(path, ()))
        if state is None:  # pragma: no cover -- `path` came out of `by_path`
            continue
        result = route(state, definition=definition)
        if result.repair is not None and tuple(b.kind for b in result.blockers) == ("phase-off-path",):
            advances.append(PlannedAdvance(path=path, reason=result.reason, mode="repair"))
            continue
        if result.repair is not None and result.repair == result.on_return:
            advances.append(PlannedAdvance(path=path, reason=result.reason, mode="return"))
            continue
        if child_gated(snapshot, node):
            for child in children:
                if child.work_status in TERMINAL_STATUSES and not active_nonterminal_descendants(snapshot, child.path):
                    continue
                if child.path in visited:
                    blocked.append(
                        BlockedItem(
                            path=path,
                            kind="invalid",
                            reason=f"parent cycle detected at {child.path!r}",
                        )
                    )
                    continue
                visited.add(child.path)
                stack.append((child.path, depth + 1))
            continue
        if result.dispatch is not None and not result.blockers:
            candidates.append((node, result))
        elif result.on_complete is not None and not result.blockers:
            advances.append(PlannedAdvance(path=path, reason=result.reason))
        else:
            first = result.blockers[0] if result.blockers else None
            reason = first.message if first is not None else result.reason
            blocked.append(BlockedItem(path=path, kind=_classify(None if first is None else first.kind), reason=reason))
    return candidates, advances, blocked


def _session_label(result: RouteResult, stale_spec: tuple[str, ...]) -> str | None:
    """Plan-stage reconcile keeps its existing session name."""
    return (
        "reconcile"
        if result.dispatch == Dispatch("plan")
        and stale_spec
        and result.on_complete is not None
        and result.on_complete.phase == "plan"
        else None
    )


def _sorted(
    candidates: list[tuple[WorkItem, RouteResult]], ranks: Mapping[str, int]
) -> list[tuple[WorkItem, RouteResult]]:
    """Admission order: most transitive dependents first (D-003), then the
    status/opened/path order `hierarchy.descend()` also uses. Order only --
    every gate below still runs for every candidate."""
    return sorted(
        candidates,
        key=lambda pair: (
            -ranks.get(pair[0].path, 0),
            PICK_ORDER.get(pair[0].work_status, 99),
            pair[0].opened,
            pair[0].path,
        ),
    )


def _descendants(items: Sequence[WorkItem], root: str) -> list[WorkItem]:
    """Every descendant of *root* at any depth and any status -- the gated
    frontier walk would not visit a terminal or non-dispatchable child."""
    children_of = as_snapshot(items).by_parent
    out: list[WorkItem] = []
    stack = list(children_of.get(root, []))
    seen = {root}
    while stack:
        node = stack.pop()
        if node.path in seen:
            continue
        seen.add(node.path)
        out.append(node)
        stack.extend(children_of.get(node.path, []))
    return out


#: The phases whose stages write only into the vault. A live worker at one of
#: these phases holds no worktree or affects claim. Bound to the stage table's `read_only` column;
#: `stage_advance.RESULTS_PHASES` reads the `results` column of the same table,
#: so the two halves of `orchestrate` still share no module-level symbol (D-001)
#: and `test_the_read_only_and_results_phases_are_complements` pins them.
READ_ONLY_PHASES: frozenset[str] = read_only_phases()


#: Every supervised worker's placement instruction (D-006). Its coordinator
#: places the stage's worktree with `gw work place` (epic D-008), so a worker neither creates, infers
#: nor states one. Appended to every dispatch, root and descendant alike, after any
#: workspace-authored tail so authored prose cannot swallow it.
WORKER_PLACEMENT_LINE = (
    "Your coordinator placed this stage's worktree with `gw work place` and launched you in it. "
    "Never create, move or switch a worktree, and run every `gw work advance` without "
    "`--worktree`/`--branch`."
)


#: Every supervised worker's sub-dispatch rule (D-001 of
#: `bug-codex-execute-fenced-by-child-run`). Orca's worker preamble offers a
#: SUB-DISPATCH recipe; following it binds the worker's terminal to a child Run,
#: after which Orca fences the worker's parent-Run checks and refuses the
#: coordinator's follow-ups. A stage fans out in-process instead. Appended to
#: every dispatch, every stage, just before `WORKER_PLACEMENT_LINE`.
NO_SUB_DISPATCH_LINE = (
    "Do not use Orca's SUB-DISPATCH: never run `orca orchestration` `run-create`, `run-use`, "
    "`task-create` or `worker-start` from this stage. When a skill asks for subagents, use your "
    "agent's native in-process subagents (Claude's Agent tool; Codex's `spawn_agent` with "
    '`fork_turns: "none"` and an explicit `model` and `reasoning_effort`), or do the work inline.'
)


def _prompt(
    *,
    path: str,
    key: str,
    phase: str,
    workspace: str,
    merge_target: str,
    tail: str | None,
    mode: str,
    execute_artifact: str,
) -> str:
    """Five vendor-neutral lines, the ask line off attend, the tail, sub-dispatch, then placement.

    The tail is substituted with `str.replace` over a fixed placeholder set
    (`path`, `key`, `phase`, `workspace`, `merge_target`, `execute_artifact`)
    rather than `str.format`: a tail is workspace-authored text that may
    legitimately contain braces, and a formatting call that can raise on user
    config is a runtime failure where a literal is harmless.

    The command and workspace lines are built from `DISPATCH_COMMAND` and
    `WORKSPACE_VAR` -- the native `graph-works` namespace, matching this
    package's own `GRAPH_WORKS_DIR`-based resolution (`discovery.py`).

    No dispatch is told to record its own placement any more, including a
    root or a main-checkout worker: the placement a worker would have named
    was the planner's requested one, not the one Orca created. The
    coordinator records the observation instead; `WORKER_PLACEMENT_LINE`
    tells the worker to stay out of it.
    """
    lines = [
        f"Run {DISPATCH_COMMAND} {path}.",
        f"{WORKSPACE_VAR}={workspace}",
        f"Dispatch key: {key}",
        "Send worker_done when the stage artifact is written and the item advanced.",
        FINDINGS_LINE,
    ]
    if mode != "attend":
        lines.append(ASK_LINE)
    if tail:
        for placeholder, value in (
            ("{path}", path),
            ("{key}", key),
            ("{phase}", phase),
            ("{workspace}", workspace),
            ("{merge_target}", merge_target),
            ("{execute_artifact}", execute_artifact),
        ):
            tail = tail.replace(placeholder, value)
        lines.append(tail)
    lines.append(NO_SUB_DISPATCH_LINE)
    lines.append(WORKER_PLACEMENT_LINE)
    return "\n".join(lines)


def _scope_label(claim: Claim) -> str:
    scope = claim.scope
    if isinstance(scope, WorktreeScope):
        return scope.path
    if isinstance(scope, CodeScope):
        return scope.path if scope.path is not None else f"{scope.repo} (whole repository)"
    return "workspace"


def _uncertain_members(affects: Sequence[str]) -> tuple[str | None, ...]:
    """Code members a live item could overlap when its repository is unknown."""
    paths = code_affects(affects)
    if paths:
        return paths
    return () if touches_workspace(affects) else (None,)


def _finish_worktrees(target: FinishTarget) -> tuple[str, ...]:
    """Every worktree a finish writes: its source and the checkout it merges into."""
    return tuple(dict.fromkeys(path for path in (target.worktree, target.target_worktree) if path))


def _live_is_attend(
    items: Sequence[WorkItem],
    item: WorkItem,
    live_phase: str,
    key: str,
    *,
    holds: Mapping[str, HoldFact],
    stale: Mapping[str, tuple[str, ...]],
    rules: tuple[DispatchRule, ...],
    warnings: list[str],
) -> bool:
    """Whether a live key counts against `max_attend` (see `plan`'s docstring)."""
    try:
        state = state_for(items, item.path, hold=holds.get(item.path), stale_spec=stale.get(item.path, ()))
        if state is None:
            raise WorkspaceError(f"no route state for {item.path!r}")
        stage = cast(Stage, live_phase)
        current = dispatch_attributes(state, Dispatch(stage))
        for has_spec, has_plan, spec_stale in itertools.product((False, True), repeat=3):
            attributes = {**current, "has_spec": has_spec, "has_plan": has_plan, "spec_stale": spec_stale}
            if resolve_dispatch(attributes, rules=rules).profile.mode == "attend":
                return True
        return False
    except WorkspaceError:  # DispatchProfileError is a WorkspaceError
        warnings.append(f"live key {key}: dispatch profile unresolvable; counted against max_attend")
        return True


def plan(
    items: Sequence[WorkItem],
    root: str,
    *,
    dispatch_rules: tuple[DispatchRule, ...],
    definition: PipelineDefinition = PACKAGED_DEFINITION,
    max_parallel: int,
    max_attend: int = 1,
    supervise_merges: bool = False,
    live: tuple[str, ...] = (),
    holds: Mapping[str, HoldFact] = MappingProxyType({}),
    stale: Mapping[str, tuple[str, ...]] = MappingProxyType({}),
    workspace: str,
    default_base: str,
    worktrees_dir: str,
    repo_refusals: Mapping[str, BlockedItem] = MappingProxyType({}),
    item_repos: Mapping[str, ItemRepo] | None = None,
    repo_contexts: Mapping[str, RepositoryContext] | None = None,
    finish_plans: Mapping[str, FinishPlan] = MappingProxyType({}),
    checkpoints: Mapping[str, HumanCheckpoints] = MappingProxyType({}),
) -> OrchestratePlan:
    """The whole dispatch plan for *root*'s subtree. Mutates nothing, reads nothing.

    `definition` is the pipeline definition every node routes with;
    `run_orchestrate` passes the workspace's.

    `holds` maps every canonical path currently named in an *open* decision to
    its lowest hold (resolved by `run_orchestrate` from the decision owner's
    ledger, since a ledger read is IO and this function stays pure). A held
    item at any phase is blocked with kind `decisions`, never becomes a
    candidate, and therefore reserves nothing.

    `stale` maps canonical paths to overlapping siblings landed since the spec
    baseline, resolved by the caller from git; this planner remains pure.

    `worktrees_dir` is the layout's (resolved) worktrees directory and
    `default_base` the fallback default base. Every dispatch of an item whose
    repository resolves to a code repository carries
    `placing.compute(item, items, repo, worktrees_dir=..., default_base=...)`
    in `OrchestratePlan.placements` under its dispatch key, at every stage,
    with the repository's observed default base when its context is known.
    Placement is a pure function of those facts: nothing here stats a path,
    reads an inventory or chooses an action, so no placement-specific blocker
    exists. An item with no code repository (workspace-only, or no repository
    in `item_repos`) has no placement entry and claims no worktree; its
    dispatch launches in *workspace*, the workspace primary checkout.

    A candidate whose placement chain passes through a worktree that a live or
    already-accepted dispatch holds -- typically an enclosing owner's worktree
    that a sibling's finish is merging into -- blocks `worktree-pending`
    (D-010): the engine would refresh that worktree under the merge. Every
    emitted code dispatch claims its own computed worktree; every live
    code-writing worker charges its item's computed worktree.

    `repo_refusals` is `path -> BlockedItem` for items whose repository
    cannot be resolved. `run_orchestrate` computes it because resolution
    reads `workspace.yaml`; a refused path reserves nothing.

    `item_repos` and `repo_contexts` select independently observed Git
    evidence for each item. Contexts are keyed by canonical common-directory
    identity. A candidate without its own evidence refuses locally. Affects
    reservations include repository identity, while capacity and holds remain
    global. With `item_repos=None` no item has a code repository.

    `checkpoints` maps execute-phase item paths to their plan readings, collected
    by `run_orchestrate`. Accepted execute dispatches carry those readings in
    `OrchestratePlan.human_checkpoints`, keyed by dispatch key.

    A path named by a `live` key is never a dispatch candidate: it is in
    `plan.live`, holds its claims against every other candidate, and appears
    in neither `dispatches` nor `blocked` merely because its stage is still
    routable.

    `max_attend` is the separate pool for dispatches whose resolved profile
    has `mode == "attend"`; `max_parallel` covers every other mode. A live
    key is attend-classified when any rule for its stage resolves to
    `attend` against the item's current non-artifact attributes and either
    value of the artifact and staleness flags. This is conservative because a live key
    retains neither its launched rule nor the artifact flags at launch;
    writing a spec or plan may change both during a run. An unresolvable
    live profile counts as attend, with a warning.
    """
    items = as_snapshot(items)
    by_path = items.by_path
    worktrees = Path(worktrees_dir)
    # A session name carries a hash, so nothing recovers a path by parsing
    # one -- `session_index` is the only reverse there is. Validate every
    # live owner before planning, including for an already-terminal root.
    by_session, warnings_list = session_index(items)
    unknown = tuple(dict.fromkeys(key for key in live if key not in by_session))
    if unknown:
        message = f"unknown live dispatch key(s): {', '.join(unknown)}; use exact dispatch keys emitted by orchestrate"
        if warnings_list:
            message += "; " + "; ".join(warnings_list)
        raise ValueError(message)
    warnings = [*warnings_list]

    root_item = by_path.get(root)
    if root_item is not None and (root_item.work_status in TERMINAL_STATUSES or root_item.phase == "done"):
        return OrchestratePlan(
            path=root,
            terminal=True,
            max_parallel=max_parallel,
            supervise_merges=supervise_merges,
            live=tuple(live),
            slots_free=0,
            dispatches=(),
            advances=(),
            blocked=(),
            warnings=tuple(warnings),
            max_attend=max_attend,
            attend_slots_free=0,
        )

    candidates, advances, blocked = _frontier(items, root, definition=definition, holds=holds, stale=stale)
    candidates = _sorted(candidates, dependent_counts(items, root))

    contexts_by_path = {
        path: context
        for context in (repo_contexts or {}).values()
        for path in {context.path, *context.checkout_usable_by_path}
    }

    def evidence(item: WorkItem) -> tuple[ItemRepo | None, RepositoryContext | None]:
        if item_repos is None:
            return None, None
        item_repo = item_repos.get(item.path)
        return item_repo, contexts_by_path.get(str(item_repo.path)) if item_repo and item_repo.path else None

    def code_repository(item: WorkItem, item_repo: ItemRepo | None) -> ItemRepo | None:
        # A workspace-only item has no code placement wherever its affects resolve.
        if item_repo is None or (touches_workspace(item.affects) and not code_affects(item.affects)):
            return None
        return item_repo

    def base_of(context: RepositoryContext | None) -> str:
        return context.default_base if context is not None else default_base

    def placement_of(item: WorkItem, item_repo: ItemRepo | None, context: RepositoryContext | None) -> Placement | None:
        repo = code_repository(item, item_repo)
        if repo is None:
            return None
        return compute(item, by_path, repo, worktrees_dir=worktrees, default_base=base_of(context))

    live_claims: list[Claim] = []
    # `(owner, member)` for a live item whose repository is unknown; a None
    # member is the whole repository. A candidate overlapping one of these
    # in any identity refuses as `worktree-unprovable` (fail closed).
    uncertain_live: list[tuple[str, str | None]] = []
    live_worktree_owners: dict[str, set[str]] = {}
    uncertain_finish_owners: set[str] = set()
    live_attend = 0
    for key in live:
        item = by_session[key]
        live_phase = _live_phase(item, key)
        if _live_is_attend(
            items, item, live_phase, key, holds=holds, stale=stale, rules=dispatch_rules, warnings=warnings
        ):
            live_attend += 1
        mutable_worker = live_phase not in READ_ONLY_PHASES
        item_repo, context = evidence(item)
        # Only code-writing stages hold affects claims. A live reader with an
        # unknown repository cannot hide a code claim either.
        code_writer = live_phase in CODE_WRITE_PHASES
        if item_repos is not None and context is None and code_writer:
            uncertain_live.extend((item.path, member) for member in _uncertain_members(item.affects))
        identity = context.identity if context is not None else "<legacy>"
        live_claims.extend(claims_for(item.path, live_phase, identity, item.affects))
        # Live occupancy outlives fresh admission checks. A code-writing
        # worker occupies its item's computed worktree -- never whatever its
        # stamp names. Readers hold no
        # worktree claim. The live key, not an advanced item, owns phase.
        if mutable_worker:
            live_placement = placement_of(item, item_repo, context)
            if live_placement is not None:
                live_worktree_owners.setdefault(str(live_placement.worktree), set()).add(item.path)
        live_finish = finish_plans.get(item.path)
        for target in live_finish.targets if live_finish is not None else ():
            target_context = contexts_by_path.get(str(target.repo.path))
            target_identity = target_context.identity if target_context else str(target.repo.path)
            live_claims.extend(claims_for(item.path, live_phase, target_identity, item.affects))
            if mutable_worker:
                for path in _finish_worktrees(target):
                    live_worktree_owners.setdefault(path, set()).add(item.path)
        if live_phase == "finish":
            if live_finish is not None and live_finish.occupancy is not None:
                for occupied_path in live_finish.occupancy.worktrees:
                    live_worktree_owners.setdefault(occupied_path, set()).add(item.path)
                if not live_finish.occupancy.complete:
                    uncertain_finish_owners.add(item.path)
            elif (
                live_finish is None
                or not live_finish.targets
                or live_finish.blockers
                or any(target.target_worktree is None for target in live_finish.targets)
            ):
                uncertain_finish_owners.add(item.path)
        if live_finish is not None and (not live_finish.targets or live_finish.blockers) and live_phase == "finish":
            owner = enclosing_owner(item, by_path)
            if owner is not None:
                for known in stamped_worktrees(owner):
                    live_worktree_owners.setdefault(known, set()).add(item.path)
    # A live item already consumes a slot and holds its claims. Re-proposing
    # it would either dispatch a duplicate (owner self-exclusion lets its own
    # claims through) or report it blocked by itself; it is neither.
    live_paths = frozenset(by_session[key].path for key in live)
    slots_free = max(0, max_parallel - (len(live) - live_attend))
    attend_slots_free = max(0, max_attend - live_attend)
    # Every observed live worktree is a write claim by each of its owners.
    live_claims.extend(
        Claim(WorktreeScope(path), "write", owner) for path, owners in live_worktree_owners.items() for owner in owners
    )

    # One ordered acceptance loop. Every reservation -- affects, a computed
    # worktree, a slot -- describes a dispatch this call actually emits,
    # never a candidate still on its way through the gates: reserving for a
    # candidate that a later gate refuses starves every overlapping sibling
    # behind it, identically on each cycle.
    accepted_claims: list[Claim] = []
    dispatches: list[PlannedDispatch] = []
    resolutions: dict[str, DispatchResolution] = {}
    dispatch_repos: dict[str, ItemRepo] = {}
    placements: dict[str, Placement] = {}
    finish_targets: dict[str, tuple[FinishTarget, ...]] = {}
    human_checkpoints: dict[str, HumanCheckpoints] = {}

    def _emit(
        item: WorkItem,
        phase: str,
        resolution: DispatchResolution,
        worktree: WorktreePlacement,
        placement: Placement | None,
        merge_target: str,
        item_repo: ItemRepo | None,
        *,
        label: str | None = None,
        auto_merge: bool = False,
        targets: tuple[FinishTarget, ...] = (),
        claims: Sequence[Claim] = (),
    ) -> None:
        entry = resolution.profile
        key = session_name(item.path, item.type, phase, label=label)
        resolutions[key] = resolution
        finish_targets[key] = targets
        if phase == "execute" and item.path in checkpoints:
            human_checkpoints[key] = checkpoints[item.path]
        if item_repo is not None:
            dispatch_repos[key] = item_repo
        if placement is not None:
            placements[key] = placement
        dispatches.append(
            PlannedDispatch(
                key=key,
                slug=item.path,
                phase=phase,
                kind=item.type,
                effort=item.effort,
                skill=entry.skill,
                mode=entry.mode,
                agent=entry.agent,
                model=entry.model,
                reasoning_effort=entry.reasoning_effort,
                worktree=worktree,
                merge_target=merge_target,
                auto_merge=auto_merge,
                prompt=_prompt(
                    path=item.path,
                    key=key,
                    phase=phase,
                    workspace=workspace,
                    merge_target=merge_target,
                    tail=entry.prompt_tail,
                    execute_artifact=definition.artifacts["execute"].file,
                    mode=entry.mode,
                ),
            )
        )
        accepted_claims.extend(claims)

    for item, result in candidates:
        if item.path in live_paths:
            continue
        repo_refusal = repo_refusals.get(item.path)
        if repo_refusal is not None:
            blocked.append(repo_refusal)
            continue
        finish = finish_plans.get(item.path)
        if finish is not None and finish.blockers:
            blocked.append(BlockedItem(item.path, "worktree-unprovable", "; ".join(finish.blockers)))
            continue
        targets = finish.targets if finish is not None else ()
        target_claims = tuple(
            Claim(WorktreeScope(path), "write", item.path) for target in targets for path in _finish_worktrees(target)
        )
        held = first_conflicts(target_claims, (*live_claims, *accepted_claims))
        if held:
            blocked.append(
                BlockedItem(
                    item.path,
                    "worktree-pending",
                    "a finish target is occupied by another worker: "
                    + "; ".join(sorted({f"{_scope_label(mine)} held by {theirs.owner}" for mine, theirs in held})),
                )
            )
            continue
        item_repo, context = evidence(item)
        if item_repos is not None and (item_repo is None or (item_repo.path is not None and context is None)):
            blocked.append(BlockedItem(item.path, "invalid", "repository evidence unavailable for this item"))
            continue
        # The stage decides which affects claims this candidate would hold.
        assert result.dispatch is not None, "every candidate carries a dispatch (see _frontier)"
        on_dispatch_phase = result.on_dispatch.phase if result.on_dispatch else None
        phase = item.phase or on_dispatch_phase or result.dispatch.stage
        identity = context.identity if context is not None else "<legacy>"
        identities = [identity]
        for target in targets:
            target_context = next(
                (c for c in (repo_contexts or {}).values() if str(target.repo.path) in c.checkout_usable_by_path), None
            )
            identities.append(target_context.identity if target_context else str(target.repo.path))
        # Empty affects claims each repository identity as a whole. Duplicate
        # identity and workspace claims collapse to one value. A design/plan
        # candidate holds none (`claims_for`).
        candidate_claims = tuple(
            dict.fromkeys(claim for ident in identities for claim in claims_for(item.path, phase, ident, item.affects))
        )
        if phase in CODE_WRITE_PHASES and any(
            owner != item.path and paths_overlap(member, held_member)
            for member in _uncertain_members(item.affects)
            for owner, held_member in uncertain_live
        ):
            blocked.append(
                BlockedItem(item.path, "worktree-unprovable", "live item's repository is unavailable for affects check")
            )
            continue
        held = first_conflicts(candidate_claims, (*live_claims, *accepted_claims))
        if held:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="affects-overlap",
                    reason=(
                        "affects overlap with a live or already-planned dispatch: "
                        + "; ".join(
                            sorted(
                                {
                                    f"{_scope_label(mine)} (held by {theirs.owner} as {_scope_label(theirs)})"
                                    for mine, theirs in held
                                }
                            )
                        )
                    ),
                )
            )
            continue
        # Missing live target evidence is an admission-evidence refusal, not
        # an alternative conflict predicate. Proven claims take precedence.
        if uncertain_finish_owners and phase not in READ_ONLY_PHASES:
            blocked.append(
                BlockedItem(
                    item.path,
                    "worktree-unprovable",
                    "live finish target occupancy is unavailable for: " + ", ".join(sorted(uncertain_finish_owners)),
                )
            )
            continue

        # Keyed on `mode`, not on the `finish` stage -- a workspace may set
        # any stage to `relay`, and mode is the property that means "no human
        # is in the room but a decision is needed".
        state = state_for(items, item.path, hold=holds.get(item.path), stale_spec=stale.get(item.path, ()))
        assert state is not None
        try:
            resolution = resolve_dispatch(dispatch_attributes(state, result.dispatch), rules=dispatch_rules)
        except DispatchProfileError as exc:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind=exc.kind,
                    reason=(
                        f"dispatch rules for stage {result.dispatch.stage!r}: {exc}; "
                        "check the shared/local dispatch file"
                    ),
                )
            )
            continue
        entry = resolution.profile

        # Capacity is per pool and keyed on the resolved mode. Refused
        # candidates never enter dispatches and consume no slot.
        if entry.mode == "attend":
            accepted_attend = sum(1 for dispatch in dispatches if dispatch.mode == "attend")
            if accepted_attend >= attend_slots_free:
                blocked.append(
                    BlockedItem(
                        path=item.path,
                        kind="capacity",
                        reason=(
                            f"ready, but no attend slot free "
                            f"({live_attend} live + {accepted_attend} planned of max_attend={max_attend})"
                        ),
                    )
                )
                continue
        elif sum(1 for dispatch in dispatches if dispatch.mode != "attend") >= slots_free:
            blocked.append(BlockedItem(path=item.path, kind="capacity", reason="ready, but no worker slot free"))
            continue

        # Placement is computed in the item's own repository, at every stage
        # -- a finish runs in the item's own worktree and merges from there.
        code_repo = code_repository(item, item_repo)
        chain = (
            compute_chain(item, by_path, code_repo, worktrees_dir=worktrees, default_base=base_of(context))
            if code_repo is not None
            else ()
        )
        placement = chain[-1] if chain else None
        # The engine refreshes every worktree in the chain at stage start, so
        # a chain worktree another worker holds -- an owner a sibling's finish
        # is merging into, above all -- must wait a cycle (D-010).
        chain_claims = {Claim(WorktreeScope(str(step.worktree)), "write", item.path): step for step in chain}
        held = first_conflicts(tuple(chain_claims), (*live_claims, *accepted_claims))
        if held:
            blocked.append(
                BlockedItem(
                    item.path,
                    "worktree-pending",
                    "a worktree in this item's placement chain is occupied by another worker: "
                    + "; ".join(
                        sorted(
                            {
                                f"{chain_claims[mine].path} ({_scope_label(mine)}) held by {theirs.owner}"
                                for mine, theirs in held
                            }
                        )
                    ),
                )
            )
            continue

        owner = enclosing_owner(item, by_path)
        if placement is not None:
            worktree = WorktreePlacement(str(placement.worktree), placement.branch, str(placement.parent_worktree))
            merge_target = placement.parent_branch
        else:
            # No code placement (D-008): the workspace primary checkout, with no
            # worktree claim. The owner's integration branch has the same
            # deterministic name in every repository.
            worktree = WorktreePlacement(workspace, None, None)
            merge_target = branch_name(owner.path, owner.type) if owner is not None else base_of(context)
        has_integration_owner = owner is not None
        # Verdict for the coordinator's finish-relay question: only a non-root
        # item merging into its owner's integration branch (never the release
        # base, never the root's own finish) is auto-answered, and only when
        # merges are unsupervised.
        auto_merge = (
            phase == "finish"
            and entry.mode == "relay"
            and not supervise_merges
            and has_integration_owner
            and item.path != root
        )
        own_claims = (Claim(WorktreeScope(str(placement.worktree)), "write", item.path),) if placement else ()
        claims = tuple(dict.fromkeys((*candidate_claims, *target_claims, *own_claims)))
        _emit(
            item,
            phase,
            resolution,
            worktree,
            placement,
            merge_target,
            item_repo,
            label=_session_label(result, stale.get(item.path, ())),
            auto_merge=auto_merge,
            targets=targets,
            claims=claims,
        )

    return OrchestratePlan(
        path=root,
        terminal=False,
        max_parallel=max_parallel,
        supervise_merges=supervise_merges,
        live=tuple(live),
        slots_free=slots_free,
        dispatches=tuple(dispatches),
        advances=tuple(advances),
        blocked=tuple(blocked),
        warnings=tuple(warnings),
        dispatch_resolutions=MappingProxyType(resolutions),
        dispatch_repos=MappingProxyType(dispatch_repos),
        placements=MappingProxyType(placements),
        finish_targets=MappingProxyType(finish_targets),
        human_checkpoints=MappingProxyType(human_checkpoints),
        max_attend=max_attend,
        attend_slots_free=attend_slots_free,
    )


@dataclass(frozen=True, slots=True)
class OrchestrateResult:
    """The plan, the nearest owner's decisions, and every hold in the subtree.

    `holds` covers every item in the planned subtree, from any ledger, unlike
    the root-owner decision fields (`open_decisions` and `assumed_decisions`).

    Routing, root-owner decisions, and subtree holds read ledgers separately.
    These reads are not one atomic snapshot, so a
    decision answered between them leaves this response internally
    inconsistent. Deduplicating means threading parsed ledger state out of the
    frontier walk, and is deliberately not done.

    `code_repo` is the resolved root code repository. `code_repo_name` and
    `code_repo_source` say *why* it was chosen -- `ItemRepo.name`/`.source`
    from `resolve_item_repo` -- and are both `None` for an explicit `repo=`,
    which bypasses that resolution entirely.
    """

    plan: OrchestratePlan
    decisions_owner_path: str | None = None
    decisions_ledger_path: str | None = None
    open_decisions: tuple[_decisions.Decision, ...] = ()
    assumed_decisions: tuple[_decisions.Decision, ...] = ()
    decision_counts: Mapping[str, int] = MappingProxyType({})
    warnings: tuple[str, ...] = ()
    holds: tuple[HoldReport, ...] = ()
    code_repo: str | None = None
    code_repo_name: str | None = None
    code_repo_source: str | None = None

    @property
    def path(self) -> str:
        return self.plan.path

    @property
    def terminal(self) -> bool:
        return self.plan.terminal

    @property
    def max_parallel(self) -> int:
        return self.plan.max_parallel

    @property
    def supervise_merges(self) -> bool:
        return self.plan.supervise_merges

    @property
    def slots_free(self) -> int:
        return self.plan.slots_free

    @property
    def max_attend(self) -> int:
        return self.plan.max_attend

    @property
    def attend_slots_free(self) -> int:
        return self.plan.attend_slots_free

    @property
    def live(self) -> tuple[str, ...]:
        return self.plan.live

    @property
    def dispatches(self) -> tuple[PlannedDispatch, ...]:
        return self.plan.dispatches

    @property
    def dispatch_resolutions(self) -> Mapping[str, DispatchResolution]:
        return self.plan.dispatch_resolutions

    @property
    def dispatch_repos(self) -> Mapping[str, ItemRepo]:
        return self.plan.dispatch_repos

    @property
    def finish_targets(self) -> Mapping[str, tuple[FinishTarget, ...]]:
        return self.plan.finish_targets

    @property
    def human_checkpoints(self) -> Mapping[str, HumanCheckpoints]:
        return self.plan.human_checkpoints

    @property
    def placements(self) -> Mapping[str, Placement]:
        return self.plan.placements

    @property
    def advances(self) -> tuple[PlannedAdvance, ...]:
        return self.plan.advances

    @property
    def blocked(self) -> tuple[BlockedItem, ...]:
        return self.plan.blocked


@dataclass(frozen=True, slots=True)
class _Decisions:
    """The nearest decision owner's ledger, resolved. A type rather than a six-tuple: six
    same-shaped positional returns at a call site is a transposition waiting to
    happen, which is the reason `ResultsFacts` is a type too."""

    owner_path: str | None = None
    ledger_path: str | None = None
    open_: tuple[_decisions.Decision, ...] = ()
    assumed: tuple[_decisions.Decision, ...] = ()
    counts: Mapping[str, int] = MappingProxyType({})
    warnings: tuple[str, ...] = ()


def _resolve_decisions(items: Sequence[WorkItem], bundle_root: Path, path: str) -> _Decisions:
    """The decision owner's open and assumed decisions.

    Empty throughout when *path* is unknown -- a shape rather than an error.
    Release, Epic, and Feature items own their own ledgers, while a lone leaf
    owns itself.
    """
    owner = decision_owner(items, path)
    if owner is None:
        return _Decisions()
    ledger = _decisions.ledger_ref(owner).path(bundle_root)
    parsed = _decisions.load(ledger)  # an absent file reads as empty, never raises
    return _Decisions(
        owner_path=owner,
        ledger_path=str(ledger),
        open_=tuple(_decisions.query(parsed.entries, status="open")),
        assumed=tuple(_decisions.query(parsed.entries, status="assumed")),
        counts=MappingProxyType(_decisions.counts(parsed.entries)),
        warnings=tuple(parsed.warnings),
    )


def _repo_refusals(
    layout: WorkspaceLayout, items: Sequence[WorkItem], root: str, root_repo: ItemRepo
) -> tuple[dict[str, BlockedItem], tuple[str, ...], dict[str, ItemRepo]]:
    """Descendant refusals, notes, and each resolved `ItemRepo`.

    Resolve ancestors and descendants independently. Untagged descendants
    retain the root's fallback (including `--repo-name`); malformed tags
    surface notes, and undeclared repository names refuse only that item.
    """
    by_path = {item.path: item for item in items}
    inherited = replace(root_repo, source="fallback", note=None)
    refusals: dict[str, BlockedItem] = {}
    notes: list[str] = []
    item_repos = {root: root_repo}
    ancestors = []
    parent = by_path.get(root)
    seen = {root}
    while parent is not None and parent.parent_path and parent.parent_path not in seen:
        seen.add(parent.parent_path)
        parent = by_path.get(parent.parent_path)
        if parent is not None:
            ancestors.append(parent)
    for node in [*ancestors, *_descendants(items, root)]:
        try:
            resolved = resolve_item_repo(layout, node, by_path, fallback=lambda: inherited)
        except WorkspaceError as exc:
            refusals[node.path] = BlockedItem(path=node.path, kind="invalid", reason=str(exc))
            continue
        if resolved.note:
            notes.append(resolved.note)
        item_repos[node.path] = resolved
    return refusals, tuple(notes), item_repos


def run_orchestrate(
    layout: WorkspaceLayout,
    path: str,
    *,
    live: tuple[str, ...] = (),
    repo: Path | None = None,
    repo_name: str | None = None,
) -> OrchestrateResult:
    """Compute the dispatch plan for *path*'s subtree. Read-only throughout.

    Never mutates a work item, a worktree or the manifest.

    `repo` defaults to the root item's resolved repository
    (`resolve_item_repo(layout, root_item, by_path, repo_name=repo_name)`),
    not the layout's `repo_root`. An explicit `repo` still wins and **skips
    that resolution entirely**: an argument is not a default. Every item
    then belongs to that one repository, named by the directory's basename
    for placement (D-009). `repo_name` selects among several declared
    repositories and is ignored when `repo` is given. Descendants use
    independently observed repository contexts; an undeclared `repo:` blocks
    only that descendant. Placement is computed under the layout's worktrees
    directory; nothing here lists worktrees or inspects a checkout for it.
    """
    bundle = load_work_bundle(layout)
    config = load_dispatch_config(layout)
    items = routing_items(bundle.root, load_items(bundle), definition=config.definition)
    # Checked, not coerced. A silent `2` from a mistyped `max_parallel` is
    # indistinguishable from a deliberate `2`. Dispatch configuration is
    # independently validated above.
    max_parallel = checked_int(layout, "workflow.auto_drive.max_parallel")
    max_attend = checked_int(layout, "workflow.auto_drive.max_attend")
    supervise_merges = checked_bool(layout, "workflow.auto_drive.supervise_merges")

    by_path = items.by_path
    by_session, _ = session_index(items)
    live_finishes = {
        by_session[key].path
        for key in live
        if key in by_session and key == session_name(by_session[key].path, by_session[key].type, "finish")
    }
    repo_refusals: dict[str, BlockedItem] = {}
    descendant_repo_notes: tuple[str, ...] = ()
    item_repos: dict[str, ItemRepo]
    if repo is not None:
        root_repo = ItemRepo(None, repo, "flag")
        # The explicit override is single-repository compatibility: every
        # item belongs to it, and no authored assignment is resolved.
        item_repos = dict.fromkeys(by_path, ItemRepo(None, repo.resolve(), "flag"))
    else:
        root_repo = resolve_item_repo(layout, by_path.get(path), by_path, repo_name=repo_name)
        repo_refusals, descendant_repo_notes, item_repos = _repo_refusals(layout, items, path, root_repo)
        # Live keys describe active work even after its page advances or when
        # the caller requests a different subtree. Never inherit that caller's
        # repository fallback for an unrelated live finish or its owners.
        for live_path in sorted(live_finishes):
            for member in (live_path, *by_path[live_path].ancestor_paths):
                if member in item_repos or member not in by_path:
                    continue
                try:
                    item_repos[member] = resolve_item_repo(layout, by_path[member], by_path)
                except WorkspaceError:
                    # The finish resolver retains this as incomplete evidence.
                    continue
        item_repos.update(
            {
                item_path: replace(selected, path=selected.path.resolve())
                for item_path, selected in item_repos.items()
                if selected.path is not None
            }
        )
    repo_contexts: dict[str, RepositoryContext] = {}
    selected_by_identity: dict[str, list[tuple[str, ItemRepo]]] = {}
    proven_identities: dict[str, str | None] = {}
    for item_path, selected in item_repos.items():
        if selected.path is not None:
            identity = repository_identity(selected.path)
            key = identity or str(selected.path)
            proven_identities[key] = identity
            selected_by_identity.setdefault(key, []).append((item_path, selected))
    for key, selected_items in selected_by_identity.items():
        checkout = selected_items[0][1].path
        assert checkout is not None
        selected_paths = {item_path for item_path, _ in selected_items}
        context = observe_repository(
            checkout,
            paths=(
                Path(worktree)
                for item in items
                if item.path in selected_paths
                for row in [stamp_row(item, None)]
                if row is not None
                for worktree in [row.worktree]
                if worktree
            ),
            checkouts=(selected.path for _, selected in selected_items if selected.path is not None),
            identity=proven_identities[key],
        )
        repo_contexts[context.identity] = context
    resolved_repo = item_repos[path].path if path in item_repos else root_repo.path
    root_context = None
    if resolved_repo is not None:
        root_context = next(
            (ctx for ctx in repo_contexts.values() if str(resolved_repo) in ctx.checkout_usable_by_path), None
        )
    code_repo = str(root_repo.path) if repo is not None else (str(resolved_repo) if resolved_repo else None)

    subtree_paths = {path, *(item.path for item in _descendants(items, path))}
    finish_plans = {
        item.path: resolve_finish_targets(
            layout,
            items,
            item.path,
            repo_contexts=repo_contexts,
        )
        for item in items
        if (item.path in subtree_paths and item.phase == "finish") or item.path in live_finishes
    }
    checkpoints = {
        item.path: read_human_checkpoints(bundle.root, item)
        for item in items
        if item.path in subtree_paths and item.phase == "execute"
    }

    repo_refusals.update(
        {
            item.path: BlockedItem(item.path, "invalid", "execute return is pending; inspect or resume before dispatch")
            for item in items
            if pending_return(layout, item.path) is not None
        }
    )
    worktrees_dir = str(layout.worktrees_dir.resolve())
    computed = plan(
        items,
        path,
        dispatch_rules=config.rules,
        definition=config.definition,
        max_parallel=max_parallel,
        max_attend=max_attend,
        supervise_merges=supervise_merges,
        live=live,
        holds=holds_by_path(items, bundle.root),
        stale=stale_by_path(
            layout, items, {item.path for item in items if item.path in subtree_paths and item.phase == "plan"}
        ),
        workspace=str(layout.root),
        default_base=root_context.default_base if root_context is not None else default_base(resolved_repo),
        worktrees_dir=worktrees_dir,
        repo_refusals=repo_refusals,
        item_repos=item_repos,
        repo_contexts=repo_contexts,
        finish_plans=finish_plans,
        checkpoints=checkpoints,
    )

    if repo is not None:
        # The explicit override's accepted dispatches report the caller's
        # repository as given.
        computed = replace(
            computed,
            dispatch_repos=MappingProxyType({dispatch.key: root_repo for dispatch in computed.dispatches}),
        )

    decisions = _resolve_decisions(items, bundle.root, path)
    subtree = {path, *(item.path for item in _descendants(items, path))}
    return OrchestrateResult(
        plan=computed,
        decisions_owner_path=decisions.owner_path,
        decisions_ledger_path=decisions.ledger_path,
        open_decisions=decisions.open_,
        assumed_decisions=decisions.assumed,
        decision_counts=decisions.counts,
        warnings=(
            computed.warnings
            + decisions.warnings
            + ((root_repo.note,) if root_repo.note else ())
            + descendant_repo_notes
        ),
        holds=open_holds(items, bundle.root, subtree),
        code_repo=code_repo,
        code_repo_name=root_repo.name if repo is None else None,
        code_repo_source=root_repo.source if repo is None else None,
    )


__all__ = [
    "BLOCKED_KINDS",
    "BLOCKER_KIND_TO_BLOCKED",
    "DISPATCH_COMMAND",
    "DISPATCH_PHASES",
    "NO_SUB_DISPATCH_LINE",
    "WORKER_PLACEMENT_LINE",
    "WORKSPACE_VAR",
    "BlockedItem",
    "HoldReport",
    "HumanCheckpoints",
    "OrchestratePlan",
    "OrchestrateResult",
    "PlannedAdvance",
    "plan",
    "read_human_checkpoints",
    "run_orchestrate",
]
