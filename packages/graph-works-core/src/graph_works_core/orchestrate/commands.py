"""The dispatch plan: which stages run now, where, on what, saying what.

`plan()` is IO-free, mirroring `work_tracker_okf.workflow.route()`'s own split:
identical inputs produce an identical `OrchestratePlan`, and every config read,
`stat` and `git` call sits in `run_orchestrate` above it. Nothing here launches
a worker -- `subagents-io` ships the dispatch value types and no execution
backend, and choosing one is a separate work item. Design/plan stages read
dedicated detached checkouts pinned to committed branch tips; they never
reserve a mutable integration worktree.

The frontier walk generalizes `hierarchy.descend()` from pick-one-leaf to
collect-all, **reusing** its `child_gated` predicate, `PICK_ORDER` and
`WALK_DEPTH_CAP` rather than restating them. Admission order then puts
dependency rank (`orchestrate.rank`) ahead of `PICK_ORDER`; that is an
ordering difference between the two, never an eligibility one. `--descend`
and auto-drive disagreeing about the same item is exactly the failure that
reuse prevents, and `test_orchestrate_plan.py` pins the agreement as a property.

Nothing Orca-shaped appears in this module. The four prompt lines are
vendor-neutral; the one place a vendor command may appear is a variant's
`prompt_tail`, which lives in workspace configuration (`pipeline.py`).
"""

from __future__ import annotations

import hashlib
import itertools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal, cast

from okf_io import load_bundle
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.affects import code_affects, touches_workspace
from work_tracker_okf.asks import plan_checkpoints
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.hierarchy import PICK_ORDER, active_nonterminal_descendants, child_gated, decision_owner
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.placement import CODE_PHASES
from work_tracker_okf.vocabulary import (
    PHASES,
    PLAN_SOURCE_ID,
    SLUG_PREFIXES,
    TERMINAL_STATUSES,
)
from work_tracker_okf.workflow import VARIANTS_BY_STAGE, Dispatch, RouteResult, Stage, route, state_for

from graph_works_core.orchestrate.anchors import (
    Anchor,
    AnchorPreparation,
    AnchorRefusal,
    WorkspacePlacement,
    WorkspacePreparation,
    enclosing_owner,
    integration_branch,
    reader_anchor,
    select_anchor,
    select_workspace,
    verify_workspace_stamp,
)
from graph_works_core.orchestrate.claims import (
    CODE_WRITE_PHASES,
    Claim,
    CodeScope,
    WorktreeScope,
    claims_for,
    first_conflicts,
    paths_overlap,
)
from graph_works_core.orchestrate.rank import dependent_counts
from graph_works_core.workspace.decision_owner import HoldReport, holds_by_path, open_holds
from graph_works_core.workspace.dispatch import (
    DispatchProfileError,
    DispatchResolution,
    DispatchRule,
    dispatch_attributes,
    resolve_dispatch,
)
from graph_works_core.workspace.dispatch_artifacts import missing_design_source
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.finish import FinishPlan, FinishTarget, resolve_finish_targets
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_bool, checked_int
from graph_works_core.workspace.pipeline import ASK_LINE, FINDINGS_LINE
from graph_works_core.workspace.provenance import default_base, run_git
from graph_works_core.workspace.repo_context import (
    RepositoryContext,
    observe_branch_tips,
    observe_repository,
    repository_identity,
)
from graph_works_core.workspace.repos import ItemRepo, resolve_item_repo
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo

WALK_DEPTH_CAP = 10_000

#: The branch path segment for an item whose `type` is unrecognized. Reachable
#: only through the root's fallback branch name: a candidate with a bad `type`
#: is already blocked by the router's own validation.
_UNKNOWN_TYPE_SEGMENT = "work"

#: The phases a stage can be dispatched at. `done` is terminal.
DISPATCH_PHASES: frozenset[str] = frozenset(PHASES - {"done"})
OWNER_TYPES: frozenset[str] = frozenset({"Epic", "Release"})

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
        "workspace-pending",
        "worktree-unsupported",
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
    children are all terminal), `mode == "return"` for a repair (an epic at
    `finish` reopened by a child filed afterwards) -- never a transition
    `advance()` would refuse with `children-open`."""

    path: str
    reason: str
    worktree: str | None = None
    branch: str | None = None
    mode: Literal["advance", "return"] = "advance"


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
class _Refusal:
    """`_resolve_worktree` declining to place a dispatch, rather than guessing.

    Distinct from the `None` return, which means "the epic worktree is being
    created by another dispatch this batch" -- a condition that self-resolves
    next cycle. A refusal never self-resolves: both its kinds need a human to
    say where the work is.
    """

    kind: str  # one of BLOCKED_KINDS
    reason: str


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
    preparations: tuple[AnchorPreparation, ...] = ()
    finish_targets: Mapping[str, tuple[FinishTarget, ...]] = MappingProxyType({})
    human_checkpoints: Mapping[str, HumanCheckpoints] = field(default_factory=lambda: MappingProxyType({}))
    max_attend: int = 1
    attend_slots_free: int = 0
    workspace_preparations: tuple[WorkspacePreparation, ...] = ()
    workspace_placements: Mapping[str, WorkspacePlacement] = MappingProxyType({})


#: The character budget for a session name. Orca renders it in a task row and
#: a worker card; longer than this and the row elides the part that identifies
#: the item. The eight-hex tail is never what gets cut (see `session_name`).
SESSION_NAME_MAX = 64


def _stable_stem(path: str, type_: str) -> str:
    """`"<basename minus its type prefix>-<sha256(path)[:8]>"`.

    The shared middle of `branch_name` and `session_name`. Extracted so a
    branch and the session that runs on it cannot drift apart: they are the
    same stem under two different prefixes.
    """
    segment = SLUG_PREFIXES.get(type_) or _UNKNOWN_TYPE_SEGMENT
    basename = PurePosixPath(path).name
    stripped = basename
    if segment != "epic" and stripped.startswith("epic-"):
        stripped = stripped[len("epic-") :]
    if stripped.startswith(f"{segment}-"):
        stripped = stripped[len(segment) + 1 :]
    suffix = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    return f"{stripped}-{suffix}"


def branch_name(path: str, type_: str) -> str:
    """Canonical path to a readable, collision-safe branch name.

    Uses the path-native basename and strips its type prefix into the branch
    segment. Legacy filename interpretation belongs only to the migration.

        epic-graph-works-core    (Epic)    -> epic/graph-works-core
        feature-work-pipeline-x (Feature) -> feature/work-pipeline-x
    """
    segment = SLUG_PREFIXES.get(type_) or _UNKNOWN_TYPE_SEGMENT
    return f"{segment}/{_stable_stem(path, type_)}"


def session_name(path: str, type_: str, phase: str) -> str:
    """The one name a dispatched worker is known by, everywhere.

        work/epic-a/children/tech-debt-x  (TechDebt, design)
            -> gw-design-x-2f1a9c3d

    A sibling of `branch_name` by construction -- same stem, different
    prefix -- so the branch a worker runs on and the session it runs in are
    visibly the same work. The kind lives in the branch segment; the phase
    lives here. Neither repeats the other.

    Capped at `SESSION_NAME_MAX`. When the cap bites it is the *word* part
    that is truncated and the eight-hex tail that survives: the tail is the
    only thing making two same-basename siblings distinguishable, so
    trimming it would trade away exactly the property it exists to provide.
    """
    stem = _stable_stem(path, type_)
    head = f"gw-{phase}-"
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
        for phase in sorted(DISPATCH_PHASES):
            name = session_name(item.path, item.type, phase)
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


def _fork_branch(path: str, type_: str, *, base: str, phase: str) -> str:
    """A fork target distinct from *base*.

    Suffixed with the phase rather than given a new path segment: git refuses
    `feature/x/execute` while `feature/x` exists. Applied once -- a suffixed
    name that still equalled its base would be `<derived>-<phase>` == `<derived>`,
    which cannot happen, so there is no loop here.
    """
    derived = branch_name(path, type_)
    return f"{derived}-{phase}" if derived == base else derived


def _classify(reason: str) -> str:
    if reason.startswith("blocked on dependencies"):
        return "deps"
    if reason.startswith("effort required"):
        return "effort-required"
    if reason.startswith("open decision"):
        return "decisions"
    if "never dispatches" in reason or "human-owned" in reason:
        return "human"
    return "invalid"


def _children_of(items: Sequence[WorkItem]) -> dict[str, list[WorkItem]]:
    grouped: dict[str, list[WorkItem]] = {}
    for item in items:
        if item.parent_path:
            grouped.setdefault(item.parent_path, []).append(item)
    return grouped


def _frontier(
    items: Sequence[WorkItem], root: str, *, holds: Mapping[str, HoldFact] = MappingProxyType({})
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
    by_path = {item.path: item for item in items}
    if root not in by_path:
        return [], [], [BlockedItem(path=root, kind="invalid", reason=f"unknown path {root!r}")]

    children_of = _children_of(items)
    candidates: list[tuple[WorkItem, RouteResult]] = []
    advances: list[PlannedAdvance] = []
    blocked: list[BlockedItem] = []

    stack: list[tuple[str, int]] = [(root, 0)]
    visited = {root}
    while stack:
        path, depth = stack.pop()
        node = by_path.get(path)
        if node is None:  # pragma: no cover -- every pushed path is `root` (checked above) or
            # drawn from `children_of`, which groups the same `items` `by_path` was built from
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
        children = children_of.get(path, [])
        state = state_for(items, path, hold=holds.get(path))
        if state is None:  # pragma: no cover -- `path` came out of `by_path`
            continue
        result = route(state)
        if result.repair is not None:
            advances.append(PlannedAdvance(path=path, reason=result.reason, mode="return"))
            continue
        if child_gated(items, node):
            for child in children:
                if child.work_status in TERMINAL_STATUSES and not active_nonterminal_descendants(items, child.path):
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
            reason = result.blockers[0] if result.blockers else result.reason
            blocked.append(BlockedItem(path=path, kind=_classify(reason), reason=reason))
    return candidates, advances, blocked


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
    """Every descendant of *root* at any depth and any status -- what the epic
    worktree fallback reads, because the gated frontier walk would not visit
    a terminal or non-dispatchable child that nonetheless carries the stamp."""
    children_of = _children_of(items)
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


def _epic_stamp(
    items: Sequence[WorkItem],
    root_item: WorkItem,
    *,
    repo: str | None = None,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """`(path, branch)` of "the epic worktree" rules 2-3 reuse or fork against.

    `repo=None` is the epic's own repository: the root's scalar stamp, falling
    back to the first stamped descendant in pick order -- skipping *exclude*,
    the descendants that resolve to another repository, whose scalar pair
    names a checkout of *that* repository. A named *repo* reads
    `repo_stamps[repo]` the same way. The fallback is reproducible from vault
    state but depends on which child happened to run first -- recorded as a
    risk in the spec, not fixed here.
    """

    def pair(item: WorkItem) -> tuple[str, str] | None:
        if repo is None:
            return (item.worktree, item.branch) if item.worktree and item.branch else None
        stamp = item.repo_stamps.get(repo)
        return (stamp.worktree, stamp.branch) if stamp is not None else None

    own = pair(root_item)
    if own is not None:
        return own
    stamped = [
        item for item in _descendants(items, root_item.path) if item.path not in exclude and pair(item) is not None
    ]
    if not stamped:
        return None
    stamped.sort(key=lambda item: (PICK_ORDER.get(item.work_status, 99), item.opened, item.path))
    return pair(stamped[0])


def _adopt(item: WorkItem, *, inventory: Mapping[str, str]) -> tuple[str, str] | _Refusal | None:
    """`(path, branch)` of the worktree holding this item's work, if findable.

    Four steps, every one an **exact** match, tried in order: the planned
    branch; a branch whose last segment equals the planned branch flattened to
    hyphens; a worktree directory whose basename equals the item's slug.
    Two or more matches at a step refuses; nothing at any step answers `None`.

    No prefix matching appears here, deliberately, and the two reasons are
    worth keeping in view because the idea reads as an obvious improvement:

    * A renamed branch does not *start* with the planned name. Orca replaces
      the first slash segment with the git username and flattens the rest
      (`bug/x-1a2b3c4d` -> `psprowls/bug-x-1a2b3c4d`), so the planned name ends
      up in the middle. Step 2 matches the **last segment**, which the
      substitution never touches, so no username lookup -- and no network call
      -- is needed.
    * A prefix-matched directory finds the decoy. Orca appends `-2` on a
      genuine directory collision, and the suffixed directory is the empty one:
      clean, at the base commit, no work in it. Step 3 therefore demands
      basename equality; a lone `<slug>-2` adopts nothing, which is correct,
      because it means the real worktree is gone.
    """
    planned = branch_name(item.path, item.type)
    if planned in inventory:
        return inventory[planned], planned

    flattened = planned.replace("/", "-")
    renamed = [(path, branch) for branch, path in inventory.items() if branch.rsplit("/", 1)[-1] == flattened]
    if len(renamed) == 1:
        return renamed[0]
    if renamed:
        return _Refusal(
            kind="worktree-ambiguous",
            reason=(
                f"{len(renamed)} worktrees carry a branch ending {flattened!r}: "
                + ", ".join(sorted(path for path, _ in renamed))
            ),
        )

    slug = PurePosixPath(item.path).name
    by_dir = [(path, branch) for branch, path in inventory.items() if PurePosixPath(path).name == slug]
    if len(by_dir) == 1:
        return by_dir[0]
    if by_dir:
        return _Refusal(
            kind="worktree-ambiguous",
            reason=(f"{len(by_dir)} worktrees are named {slug!r}: " + ", ".join(sorted(path for path, _ in by_dir))),
        )
    return None


#: The phases whose stages write only into the vault. A stage in this set
#: gets READER_ACTION and cannot produce a commit or acquire the placement stamp that
#: decides where later commits land -- the governing invariant of this
#: module's placement policy. Deliberately spelled out here rather than
#: imported as the complement of `stage_advance.RESULTS_PHASES`: the two
#: halves of `orchestrate` share no module-level symbol by design (D-001),
#: and `test_the_read_only_and_results_phases_are_complements` pins them
#: against each other instead.
READ_ONLY_PHASES: frozenset[str] = frozenset({"design", "plan"})


#: A dedicated checkout detached by the launcher at the observed committed tip.
READER_ACTION = "pin-detached"
READER_BASELINE_LINE = (
    "Reader baseline: this stage reads a detached checkout of {branch} at {sha}; "
    "cite that commit in the stage artifact and do not commit in this checkout."
)


def _reader_source(
    item: WorkItem,
    *,
    root: str,
    by_path: Mapping[str, WorkItem],
    item_repo: ItemRepo | None,
    item_repos: Mapping[str, ItemRepo] | None,
    context: RepositoryContext | None,
    inventory: Mapping[str, str],
    exists: Mapping[str, bool | None],
    default_base: str,
) -> tuple[str | None, str] | _Refusal:
    """Select the nearest owner's verified ref, or an unanchored root's base.

    Old descendant stamps never supply integration provenance. A dirty anchor
    is a valid source: the reader uses its committed ref, never its checkout.
    """
    if context is not None and (not context.identity_known or not context.inventory_known):
        return _Refusal("worktree-unprovable", "repository Git identity or inventory is unavailable")
    owner = enclosing_owner(item, by_path)
    if owner is None and (item.path != root or item.parent_path is not None):
        return _Refusal("worktree-unprovable", "cannot prove the reader's integration owner")
    if owner is None and item_repo is not None and item_repo.name == WORKSPACE_REPO:
        assert context is not None
        return None, context.default_base
    source = owner if owner is not None else item
    if context is not None:
        assert item_repos is not None and item_repo is not None
        selected = reader_anchor(source, repos=item_repos, repo=item_repo, context=context)
        if isinstance(selected, AnchorRefusal):
            return _Refusal(selected.kind, selected.reason)
        if selected is not None:
            return selected.worktree, selected.branch
    else:
        path, branch = source.worktree, source.branch
        if bool(path) != bool(branch) or {"worktree", "branch"}.intersection(source.invalid_optional_fields):
            return _Refusal("worktree-unprovable", f"repair invalid integration stamp on {source.path}")
        if path and branch:
            if inventory.get(branch) != path or exists.get(path) is not True:
                return _Refusal("worktree-unprovable", "integration stamp is not verified in this repository")
            return path, branch
    if owner is not None:
        return _Refusal("worktree-unprovable", f"integration owner {owner.path} requires an explicit anchor stamp")
    return None, context.default_base if context is not None else default_base


def _pin_reader(
    source: tuple[str | None, str], *, sha: str | None, trunk_checkout: str | None, where: str
) -> WorktreeAction | _Refusal:
    path, branch = source
    if sha is None or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", sha) is None:
        return _Refusal(
            "worktree-unprovable",
            f"cannot resolve {branch!r} to a full commit object ID in {where}; "
            "a reader is never placed in a mutable checkout",
        )
    return WorktreeAction(
        READER_ACTION, None, None, branch, None, None if path is None or path == trunk_checkout else path, sha
    )


def _code_work_may_exist(item: WorkItem, phase: str) -> bool:
    """Whether a stage that writes code could already have run for *item*.

    A read-only phase cannot have produced a commit (see `READ_ONLY_PHASES`).
    Neither can an `execute` still at `work_status: accepted`: plan completion
    enters `execute` as `accepted`, and every execute dispatch flips it to
    `in-progress` (`work_tracker_okf.workflow`, pinned by
    `test_accepted_at_execute_is_the_state_no_execute_stage_has_started_from`).
    Any other `execute`, and every `finish`, may have code work somewhere.
    """
    if phase in READ_ONLY_PHASES:
        return False
    return not (phase == "execute" and item.work_status == "accepted")


def _resolve_worktree(
    item: WorkItem,
    *,
    epic_worktree_path: str | None,
    epic_branch: str,
    live_worktree_owners: Mapping[str, set[str]],
    accepted_worktrees: set[str],
    epic_worktree_claimed: bool,
    worktree_exists: Mapping[str, bool | None],
    default_base: str,
    phase: str,
    is_root: bool,
    repo_path: str | None,
    inventory: Mapping[str, str],
    code_repo: str | None = None,
) -> tuple[WorktreeAction | _Refusal | None, bool]:
    """The four rules, in order: reuse the item's own stamp; else reuse the epic
    worktree when unoccupied and this dispatch is entitled to it; else fork a
    child branch off it; else mint the epic worktree -- which only the subtree
    root may do.

    Rule 2's entitlement belongs only to the root. Read-only stages never
    reach this function (see `READER_ACTION`); code-writing descendants get
    their own fork to review and merge. There is no opportunistic
    main-checkout placement at any phase: `default_base` is trunk.

    Returns `(action, claims_the_epic_slot)`. `action` is `None` only for the
    worktree-pending case -- the epic worktree is being created by another
    dispatch in this same plan -- which the caller turns into a blocker without
    consuming a slot.

    A stamp equal to `repo_path` resolves to `"main"` rather than `"reuse"`
    every time. This distinguishes the main checkout from a dedicated worktree;
    the coordinator records the observed placement for either action.

    Both fork targets go through `_fork_branch`, which keeps them distinct from
    their own base: rule 1's base is the item's stamped branch (or `default_base`
    when evicting out of the main checkout) and rule 2/3's is the epic branch,
    and either can already equal the item's derived name.

    Every fork also names its `parent_path` -- the worktree whose branch it
    forks -- so the launch links lineage from the plan, never from the
    coordinator's location.

    `_fork_parent` compares its source against `code_repo`, not the possibly-
    withheld `repo_path`: `repo_path` goes `None` whenever `run_orchestrate`'s
    dirty-checkout guard fires, but the repository itself is still known and
    still the thing that decides "is this trunk work" -- a withheld `repo_path`
    must not let a fork off a dirty main checkout slip through with a parent.
    `repo_path` remains the source of truth for `"main"` vs `"reuse"`
    (`WorktreeAction.parent_path`, unrelated to `WorkItem.parent_path`), which
    is deliberately unaffected by this.

    A `_Refusal` is the third return shape: the placement could not be proved
    and no worktree was findable to adopt (`worktree-unprovable`), or more than
    one matched (`worktree-ambiguous`). Neither self-resolves. The governing
    policy is that when the planner cannot prove where an item's prior work
    lives it searches for it and blocks if the search fails -- it never
    guesses, because a plan naming the wrong directory is well-formed and
    silent, and the three reproductions behind this rule were all caught only
    because a human happened to look.

    The search-then-block rule applies only where code work may exist
    (`_code_work_may_exist`). A cold-start item whose every prior stage was
    vault-only -- an attended design or plan leaves no worktree -- has
    nothing to lose: it adopts a findable worktree if there is one, and
    otherwise is placed as a first dispatch (the root mints, a descendant
    waits for its root).
    """

    def _occupied(path: str) -> bool:
        # Retain defensive owner exclusion: if this item ever reaches placement
        # despite its own live contribution, its stamp must not read as "held by
        # someone else" and make it fork off itself. In the normal `plan()` live
        # path, the earlier candidate guard excludes live paths before placement.
        return bool(live_worktree_owners.get(path, set()) - {item.path}) or path in accepted_worktrees

    def _fork_parent(source: str) -> str | None:
        # The worktree a fork is linked beneath, as data, so no launch has to
        # take it from wherever its coordinator is running. A fork off the
        # repository's own checkout is trunk work, not a child of that
        # checkout, so it gets no lineage -- the eviction rule, generalized.
        # Compare against `code_repo` when the caller resolved it separately
        # from `repo_path` -- `repo_path` alone can be `None` (withheld by a
        # dirty checkout) while the repository itself is still known.
        against = code_repo if code_repo is not None else repo_path
        return None if against is not None and source == against else source

    def _place_adopted(path: str, branch: str, reason: str) -> tuple[WorktreeAction | _Refusal, bool]:
        """Shape an adopted `(path, branch)` into the action to take."""
        if worktree_exists.get(path) is False:
            # Adopted a path the inventory names, but it is provably gone too
            # (e.g. a prunable-but-not-yet-pruned worktree). Adopting it would
            # report a placement as proven when it cannot be verified at all.
            return _Refusal(kind="worktree-unprovable", reason=reason), False
        if _occupied(path):
            # Adopted, but someone else is in it this batch: fork off the
            # branch we just proved holds the work, rather than sharing.
            return (
                WorktreeAction(
                    action="fork-child",
                    path=None,
                    branch=_fork_branch(item.path, item.type, base=branch, phase=phase),
                    base_branch=branch,
                    exists=None,
                    parent_path=_fork_parent(path),
                    start_sha=None,
                ),
                False,
            )
        is_main = repo_path is not None and path == repo_path
        return (
            WorktreeAction(
                action="main" if is_main else "reuse",
                path=path,
                branch=branch,
                base_branch=None,
                exists=worktree_exists.get(path, True),
                parent_path=None,
                start_sha=None,
            ),
            is_main,
        )

    def _adopted_action(reason: str) -> tuple[WorktreeAction | _Refusal, bool]:
        """Search for the item's real worktree; the action to take, or a refusal."""
        found = _adopt(item, inventory=inventory)
        if isinstance(found, _Refusal):
            return found, False
        if found is None:
            return _Refusal(kind="worktree-unprovable", reason=reason), False
        return _place_adopted(*found, reason)

    if item.worktree and item.branch:
        if repo_path is not None and item.worktree == repo_path:
            if not _occupied(item.worktree):
                return (
                    WorktreeAction(
                        action="main",
                        path=item.worktree,
                        branch=item.branch,
                        base_branch=None,
                        exists=worktree_exists.get(item.worktree),
                        parent_path=None,
                        start_sha=None,
                    ),
                    False,
                )
            # Eviction. Another dispatch now holds the shared main checkout, so
            # fork off `default_base`'s current HEAD -- which already contains
            # whatever this item's own solo phases landed directly on it.
            return (
                WorktreeAction(
                    action="fork-child",
                    path=None,
                    branch=_fork_branch(item.path, item.type, base=default_base, phase=phase),
                    base_branch=default_base,
                    exists=None,
                    parent_path=None,
                    start_sha=None,
                ),
                False,
            )
        if not _occupied(item.worktree):
            if worktree_exists.get(item.worktree) is False:
                # The stamp names a directory that is no longer on disk.
                # `reuse` here produces `--worktree path:<gone>`, which fails
                # far from this decision; search instead.
                return _adopted_action(
                    f"stamped worktree {item.worktree!r} no longer exists and no worktree "
                    "in the inventory matches this item"
                )
            return (
                WorktreeAction(
                    action="reuse",
                    path=item.worktree,
                    branch=item.branch,
                    base_branch=None,
                    exists=worktree_exists.get(item.worktree),
                    parent_path=None,
                    start_sha=None,
                ),
                False,
            )
        # The item's own recorded path is claimed by a live or already-planned
        # dispatch this batch -- almost always because it is a shared epic
        # worktree that provenance auto-stamped onto more than one child, not
        # a genuinely dedicated one. Fork off the item's own branch rather
        # than dispatching two workers into the same directory: the hazard
        # rules 2-4's occupancy checks exist to prevent applies here too.
        return (
            WorktreeAction(
                action="fork-child",
                path=None,
                branch=_fork_branch(item.path, item.type, base=item.branch, phase=phase),
                base_branch=item.branch,
                exists=None,
                parent_path=_fork_parent(item.worktree),
                start_sha=None,
            ),
            False,
        )
    if epic_worktree_path is not None:
        # The epic anchor is the root's work context. Code-writing
        # descendants must not land in it: two workers committing in one directory
        # is the hazard rule 3 exists for, and a child that commits on the
        # epic branch leaves nothing of its own to review or merge. It falls
        # through to the fork below instead.
        reuses_anchor = is_root
        if reuses_anchor and not _occupied(epic_worktree_path):
            if worktree_exists.get(epic_worktree_path) is False:
                # The epic anchor can be stale for the same reason a stamp can.
                return _adopted_action(
                    f"epic worktree {epic_worktree_path!r} no longer exists and no worktree "
                    "in the inventory matches this item"
                )
            # Same "main" vs "reuse" split as above: a child inheriting the
            # epic's stamp is still just cd'd into the main checkout when that
            # stamp is the repo path itself.
            action = "main" if repo_path is not None and epic_worktree_path == repo_path else "reuse"
            return (
                WorktreeAction(
                    action=action,
                    path=epic_worktree_path,
                    branch=epic_branch,
                    base_branch=None,
                    exists=worktree_exists.get(epic_worktree_path),
                    parent_path=None,
                    start_sha=None,
                ),
                False,
            )
        return (
            WorktreeAction(
                action="fork-child",
                path=None,
                branch=_fork_branch(item.path, item.type, base=epic_branch, phase=phase),
                base_branch=epic_branch,
                exists=None,
                parent_path=_fork_parent(epic_worktree_path),
                start_sha=None,
            ),
            False,
        )
    if epic_worktree_claimed:
        return None, False
    # Cold start: no stamp of the item's own, and no epic anchor to inherit.
    # Only the subtree root may mint the anchor here, so the refusal reason a
    # descendant gets names that remedy rather than asking for a directory
    # nobody can supply.
    cold_reason = (
        f"no worktree is recorded for this item at phase {phase!r} and none in the "
        "inventory matches it; say where the prior work is"
        if is_root
        else (
            f"no worktree is recorded for this item at phase {phase!r}, none in the inventory "
            "matches it, and this epic's subtree root has no recorded worktree to anchor "
            "against; dispatch the subtree root first"
        )
    )
    if item.phase is not None and phase != "design":
        # At `plan()`'s own call site `phase` always equals `item.phase` when
        # the latter is set; the two-clause form only diverges for a direct
        # caller (e.g. a test) that passes a different `phase` -- keep both
        # clauses. `item.phase is None` excludes a never-advanced item (e.g. a
        # freshly filed TestGap routed straight to execute or plan): its
        # first-ever dispatch has no prior work to find. Deliberately ahead of
        # the descendant refusal below: a descendant whose real worktree is
        # findable is placed in it, and only an unfindable one blocks.
        if _code_work_may_exist(item, phase):
            # A code stage may have run, so its work is somewhere. Dispatching
            # the finish stage with nothing to merge is worse than not
            # dispatching it: search, and refuse if the search fails.
            return _adopted_action(cold_reason)
        # Every stage so far was vault-only -- typically an attended
        # `/gw:workflow` design or plan, which never allocates a worktree.
        # There is no code work to lose. Still adopt an existing branch
        # worktree if one can be proved; ambiguous matches refuse. Readers
        # never reach this ladder or supply branch stamps. Finding nothing
        # falls through to the cold-start tail.
        found = _adopt(item, inventory=inventory)
        if isinstance(found, _Refusal):
            return found, False
        if found is not None:
            return _place_adopted(*found, cold_reason)
    if not is_root:
        # A descendant reached cold start, so it is dispatching before its own
        # subtree root ever did. Minting the anchor here would mean stamping
        # the epic worktree onto a *child*, which is the exact leak this rule
        # exists to close -- `_epic_stamp`'s descendant-scan fallback would
        # then read that child's stamp back as "the epic worktree". Block
        # instead: a visible refusal beats a silent misplacement.
        return _Refusal(kind="worktree-unprovable", reason=cold_reason), False
    # The subtree root's first dispatch mints the epic worktree. There is no
    # opportunistic main-checkout placement: `default_base` is trunk, and a
    # stage dispatched onto trunk commits onto trunk. `claimed=True` is what
    # makes the next item in this same batch return the `worktree-pending`
    # blocker above rather than minting a second one.
    return (
        WorktreeAction(
            action="create-top-level",
            path=None,
            branch=epic_branch,
            base_branch=default_base,
            exists=None,
            parent_path=None,
            start_sha=None,
        ),
        True,
    )


#: Every supervised worker's placement instruction (D-006). Its coordinator
#: records the observed worktree/branch with `gw work record-placement` right
#: after launch, so a worker neither infers a placement from its cwd nor
#: states one. Appended to every dispatch, root and descendant alike, after any
#: workspace-authored tail so authored prose cannot swallow it.
WORKER_PLACEMENT_LINE = (
    "Your coordinator records where this stage runs. Run every `gw work advance` with "
    "`--no-infer-worktree` and without `--worktree`/`--branch`."
)


#: Content instructions precede the mandatory worker placement instruction.
WORKSPACE_CONTENT_LINE = (
    "Workspace content root: {worktree} (branch {branch}). Write workspace content there -- run "
    "content-producing commands with GRAPH_WORKS_DIR={worktree} -- and commit it on that branch. "
    "Run every gw work verb against GRAPH_WORKS_DIR={workspace}."
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
    reader: tuple[str, str] | None = None,
    content_root: WorkspacePlacement | None = None,
) -> str:
    """Five vendor-neutral lines, the ask line off attend, the tail, reader baseline, then placement.

    The tail is substituted with `str.replace` over a fixed placeholder set
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
        ):
            tail = tail.replace(placeholder, value)
        lines.append(tail)
    if reader is not None:
        lines.append(READER_BASELINE_LINE.format(branch=reader[0], sha=reader[1]))
    if content_root is not None:
        lines.append(
            WORKSPACE_CONTENT_LINE.replace("{worktree}", content_root.worktree)
            .replace("{branch}", content_root.branch)
            .replace("{workspace}", workspace)
        )
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
    rules: tuple[DispatchRule, ...],
    warnings: list[str],
) -> bool:
    """Whether a live key counts against `max_attend` (see `plan`'s docstring)."""
    try:
        state = state_for(items, item.path, hold=holds.get(item.path))
        if state is None:
            raise WorkspaceError(f"no route state for {item.path!r}")
        stage = cast(Stage, live_phase)
        for variant in VARIANTS_BY_STAGE[stage]:
            current = dispatch_attributes(state, Dispatch(stage, variant))
            for has_spec, has_plan in itertools.product((False, True), repeat=2):
                attributes = {**current, "has_spec": has_spec, "has_plan": has_plan}
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
    max_parallel: int,
    max_attend: int = 1,
    supervise_merges: bool = False,
    live: tuple[str, ...] = (),
    worktree_exists: Mapping[str, bool | None] | None = None,
    holds: Mapping[str, HoldFact] = MappingProxyType({}),
    provisions_worktrees: bool = True,
    workspace: str,
    default_base: str,
    repo_path: str | None = None,
    worktree_inventory: Mapping[str, str] | None = None,
    branch_tips: Mapping[str, str] | None = None,
    repo_known: bool = True,
    code_repo: str | None = None,
    repo_refusals: Mapping[str, BlockedItem] = MappingProxyType({}),
    item_repos: Mapping[str, ItemRepo] | None = None,
    repo_contexts: Mapping[str, RepositoryContext] | None = None,
    finish_plans: Mapping[str, FinishPlan] = MappingProxyType({}),
    workspace_repo: ItemRepo | None = None,
    workspace_context: RepositoryContext | None = None,
    workspace_worktrees_dir: str | None = None,
    checkpoints: Mapping[str, HumanCheckpoints] = MappingProxyType({}),
) -> OrchestratePlan:
    """The whole dispatch plan for *root*'s subtree. Mutates nothing, reads nothing.

    `holds` maps every canonical path currently named in an *open* decision to
    its lowest hold (resolved by `run_orchestrate` from the decision owner's
    ledger, since a ledger read is IO and this function stays pure). A held
    item at any phase is blocked with kind `decisions`, never becomes a
    candidate, and therefore reserves nothing.

    `provisions_worktrees` mirrors `subagents_io.backend.DispatchBackend`'s
    flag of the same name -- the caller resolves which backend a plan's
    dispatches will actually run against and passes its capability in, since
    `plan()` names no backend itself. Defaults `True` (today's behaviour: a
    `fork-child`/`create-top-level` action with `path=None` dispatches,
    trusting the backend to provision it) so every existing caller is
    unaffected until it opts into the check. `False` turns what would
    otherwise be a `WorktreeNotProvisioned` raised far away, at `launch()`
    time, into a `worktree-unsupported` blocker computed here, at plan time.

    `repo_path` is the resolved code repository's own checkout, when the caller
    knows it. It labels an item whose *stamp* (rule 1), inherited epic anchor
    (rule 2) or adopted worktree is that checkout as a `"main"` action rather
    than a `"reuse"`. The coordinator records either observed placement, and
    every worker receives the same placement instruction. It no longer influences cold
    start: a cold start mints the epic worktree whether or not a checkout is
    known.

    `worktree_inventory` is `branch -> worktree path` as git sees it, resolved
    by `run_orchestrate` from `git worktree list --porcelain`. It is what lets
    the placement rules *find* an item's prior work instead of assuming it,
    and it defaults to `{}` -- with no inventory the rules that would search
    refuse instead, which is still an improvement on naming a directory that
    holds nothing, but adoption is the point.

    `branch_tips` carries full committed branch IDs for the legacy path;
    `None` means unknown and refuses readers. Repository contexts carry their
    own tips. Design/plan dispatches always pin dedicated detached checkouts,
    reserving no mutable worktree or accepted claims.

    `repo_known` says whether the caller resolved the code repository at all
    (independently of whether its checkout is withheld as `repo_path`). A
    creation -- an action with no `path` -- names its repository only through
    that resolution, so with `repo_known=False` it blocks as
    `worktree-unprovable` rather than leaving a backend to place it wherever
    its caller runs. Defaults `True` so direct callers are unaffected.

    `code_repo` is the resolved code repository itself, un-withheld even when
    `repo_path` is `None` because the checkout was dirty. `_fork_parent` (see
    `_resolve_worktree`) compares against `code_repo` when given, so a fork
    off a dirty main checkout still carries no parent (design D5) instead of
    silently linking trunk work beneath it. Defaults `None`, which falls back
    to comparing against `repo_path` -- unchanged behaviour for a direct
    caller that only ever passed `repo_path`.

    `repo_refusals` is `path -> BlockedItem` for items whose repository
    cannot be resolved. `run_orchestrate` computes it because resolution
    reads `workspace.yaml`; a refused path reserves nothing.

    `item_repos` and `repo_contexts` select independently observed Git
    evidence for each item. Contexts are keyed by canonical common-directory
    identity; the singular arguments above remain the compatibility path.
    A candidate without its own evidence refuses locally. Affects reservations
    include repository identity, while capacity and holds remain global.

    `workspace_repo`/`workspace_context`/`workspace_worktrees_dir` enable workspace
    placement when all three are given; `run_orchestrate` passes `None` when
    `workspace_repo(layout)` is disabled.

    `checkpoints` maps execute-phase item paths to their plan readings, collected
    by `run_orchestrate`. Accepted execute dispatches carry those readings in
    `OrchestratePlan.human_checkpoints`, keyed by dispatch key.

    A path named by a `live` key is never a dispatch candidate: it is in
    `plan.live`, holds its claims against every other candidate, and appears
    in neither `dispatches` nor `blocked` merely because its stage is still
    routable.

    `max_attend` is the separate pool for dispatches whose resolved profile
    has `mode == "attend"`; `max_parallel` covers every other mode. A live
    key is attend-classified when any variant of its stage resolves to
    `attend` against the item's current non-artifact attributes and either
    value of both artifact flags. This is conservative because a live key
    retains neither its launched variant nor the artifact flags at launch;
    writing a spec or plan may change both during a run. An unresolvable
    live profile counts as attend, with a warning.
    """
    workspace_enabled = (
        workspace_repo is not None and workspace_context is not None and workspace_worktrees_dir is not None
    )
    exists = worktree_exists or {}
    inventory = worktree_inventory or {}
    by_path = {item.path: item for item in items}
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

    candidates, advances, blocked = _frontier(items, root, holds=holds)
    candidates = _sorted(candidates, dependent_counts(items, root))

    # `live_worktree_owners` stays an owner map for `_resolve_worktree`'s
    # placement rules. Admission -- code affects and finish-target occupancy --
    # goes through claims (`orchestrate.claims`), whose owner self-exclusion
    # covers the same "held by the very candidate" case.
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
        live_phase = next(phase for phase in DISPATCH_PHASES if session_name(item.path, item.type, phase) == key)
        if _live_is_attend(items, item, live_phase, key, holds=holds, rules=dispatch_rules, warnings=warnings):
            live_attend += 1
        mutable_worker = live_phase not in READ_ONLY_PHASES
        _, context = evidence(item)
        # Only code-writing stages hold affects claims. A live reader with an
        # unknown repository cannot hide a code claim either.
        code_writer = live_phase in CODE_WRITE_PHASES
        if item_repos is not None and context is None and code_writer:
            uncertain_live.extend((item.path, member) for member in _uncertain_members(item.affects))
        identity = context.identity if context is not None else "<legacy>"
        live_claims.extend(claims_for(item.path, live_phase, identity, item.affects))
        # Live occupancy outlives fresh admission checks. In particular a
        # dirty enclosing target must not release a still-running source.
        # Readers hold no affects claims, and their old stamps describe no
        # mutable occupancy. The live key, not an advanced item, owns phase.
        for name, live_stamp in item.repo_stamps.items():
            if name == WORKSPACE_REPO:
                if mutable_worker:
                    live_worktree_owners.setdefault(live_stamp.worktree, set()).add(item.path)
                continue
            if mutable_worker:
                live_worktree_owners.setdefault(live_stamp.worktree, set()).add(item.path)
            stamp_context = next(
                (
                    c
                    for c in (repo_contexts or {}).values()
                    if any(live_stamp.worktree in paths for paths in c.inventory.values())
                ),
                None,
            )
            if stamp_context is None:
                if code_writer:
                    uncertain_live.extend((item.path, member) for member in _uncertain_members(item.affects))
            else:
                live_claims.extend(claims_for(item.path, live_phase, stamp_context.identity, item.affects))
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
                for known in (owner.worktree, *(stamp.worktree for stamp in owner.repo_stamps.values())):
                    if known:
                        live_worktree_owners.setdefault(known, set()).add(item.path)
        if mutable_worker and item.worktree:
            live_worktree_owners.setdefault(item.worktree, set()).add(item.path)
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

    stamp = None
    if root_item is not None:
        if item_repos is None:
            stamp = _epic_stamp(items, root_item, exclude=frozenset(repo_refusals))
        elif root_item.worktree and root_item.branch:
            stamp = (root_item.worktree, root_item.branch)
    epic_worktree_path = stamp[0] if stamp else None
    epic_branch = stamp[1] if stamp else branch_name(root, root_item.type if root_item else "")

    # Carry the epic's known worktree onto the *root's* planned advance only.
    # A coordinator-applied advance has no worker cwd to infer from, and the
    # root's stamp is the anchor. A descendant never inherits it: stamping the
    # shared epic pair onto a child makes the next plan reuse the epic worktree
    # where it should fork (D-006). Its own placement, when it has one, is
    # recorded from observation and left intact by an advance that carries no pair.
    if epic_worktree_path is not None:
        advances = [
            replace(a, worktree=epic_worktree_path, branch=epic_branch) if a.path == root else a for a in advances
        ]

    # One ordered acceptance loop. Every reservation -- affects, the epic
    # worktree slot, `accepted_worktrees`, a slot -- describes a dispatch this
    # call actually emits, never a candidate still on its way through the
    # gates: reserving for a candidate that a later gate refuses starves every
    # overlapping sibling behind it, identically on each cycle.
    accepted_claims: list[Claim] = []
    accepted_worktrees: set[str] = set()
    epic_worktree_claimed: set[str] = set()
    dispatches: list[PlannedDispatch] = []
    resolutions: dict[str, DispatchResolution] = {}
    dispatch_repos: dict[str, ItemRepo] = {}
    finish_targets: dict[str, tuple[FinishTarget, ...]] = {}
    human_checkpoints: dict[str, HumanCheckpoints] = {}
    preparations: dict[tuple[str, str], AnchorPreparation] = {}
    workspace_preparations: dict[str, WorkspacePreparation] = {}
    workspace_placements: dict[str, WorkspacePlacement] = {}

    def _emit(
        item: WorkItem,
        phase: str,
        resolution: DispatchResolution,
        action: WorktreeAction,
        merge_target: str,
        item_repo: ItemRepo | None,
        *,
        auto_merge: bool = False,
        targets: tuple[FinishTarget, ...] = (),
        claims: Sequence[Claim] = (),
        reader: tuple[str, str] | None = None,
        content_root: WorkspacePlacement | None = None,
    ) -> None:
        entry = resolution.profile
        key = session_name(item.path, item.type, phase)
        resolutions[key] = resolution
        finish_targets[key] = targets
        if phase == "execute" and item.path in checkpoints:
            human_checkpoints[key] = checkpoints[item.path]
        accepted_worktrees.update(path for target in targets for path in _finish_worktrees(target))
        if item_repo is not None:
            dispatch_repos[key] = item_repo
        if content_root is not None:
            workspace_placements[key] = content_root
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
                worktree=action,
                merge_target=merge_target,
                auto_merge=auto_merge,
                prompt=_prompt(
                    path=item.path,
                    key=key,
                    phase=phase,
                    workspace=workspace,
                    merge_target=merge_target,
                    tail=entry.prompt_tail,
                    mode=entry.mode,
                    reader=reader,
                    content_root=content_root,
                ),
            )
        )
        accepted_claims.extend(claims)
        if action.path:
            accepted_worktrees.add(action.path)

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

        # Ahead of `_resolve_worktree` deliberately: an item that cannot
        # dispatch should not claim the epic worktree slot or add to
        # `accepted_worktrees` on its way out. Keyed on `mode`, not on the
        # `branch` variant -- a workspace may set any variant to `relay`, and
        # mode is the property that means "no human is in the room but a
        # decision is needed".
        variant = result.dispatch.variant
        state = state_for(items, item.path, hold=holds.get(item.path))
        assert state is not None
        try:
            resolution = resolve_dispatch(dispatch_attributes(state, result.dispatch), rules=dispatch_rules)
        except DispatchProfileError as exc:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind=exc.kind,
                    reason=f"dispatch rules for variant {variant!r}: {exc}; check the shared/local dispatch file",
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

        content_root: WorkspacePlacement | None = None
        if workspace_enabled and phase in CODE_PHASES:
            assert workspace_context is not None and workspace_worktrees_dir is not None
            selected_ws: WorkspacePlacement | WorkspacePreparation | AnchorRefusal | None
            if phase == "execute":
                selected_ws = select_workspace(
                    item, items=by_path, context=workspace_context, worktrees_dir=workspace_worktrees_dir
                )
            else:
                # Finish never prepares a branch for an item executed before
                # workspace placement was enabled.
                selected_ws = verify_workspace_stamp(item, workspace_context)
            if isinstance(selected_ws, AnchorRefusal):
                blocked.append(BlockedItem(item.path, selected_ws.kind, selected_ws.reason))
                continue
            if isinstance(selected_ws, WorkspacePreparation):
                workspace_preparations.setdefault(selected_ws.owner_path, selected_ws)
                blocked.append(
                    BlockedItem(
                        item.path,
                        "workspace-pending",
                        f"workspace branch for {selected_ws.owner_path} requires `gw work prepare-workspace`",
                    )
                )
                continue
            content_root = selected_ws
        content_claims = (
            (Claim(WorktreeScope(content_root.worktree), "write", item.path),) if content_root is not None else ()
        )
        held = first_conflicts(content_claims, (*live_claims, *accepted_claims))
        if held:
            blocked.append(
                BlockedItem(
                    item.path,
                    "worktree-pending",
                    "workspace content checkout is occupied by another worker: "
                    + "; ".join(sorted({f"{_scope_label(mine)} held by {theirs.owner}" for mine, theirs in held})),
                )
            )
            continue
        workspace_only = workspace_enabled and touches_workspace(item.affects) and not code_affects(item.affects)
        if workspace_only and phase == "execute" and content_root is None:
            blocked.append(BlockedItem(item.path, "workspace-pending", "workspace-only item has no workspace branch"))
            continue

        if phase in READ_ONLY_PHASES:
            reader_repos = item_repos
            if workspace_only:
                # Read the workspace integration lineage without preparing any
                # code anchor or claiming its mutable checkout.
                item_repo, context = workspace_repo, workspace_context
                if reader_repos is None:
                    # Explicit code overrides have no named item map. Their
                    # scalar placements still belong to code; workspace owner
                    # provenance must come from the foreign _workspace stamp.
                    override = ItemRepo(None, Path(code_repo) if code_repo else None, "flag")
                    reader_repos = dict.fromkeys(by_path, override)
            source = _reader_source(
                item,
                root=root,
                by_path=by_path,
                item_repo=item_repo,
                item_repos=reader_repos,
                context=context,
                inventory=inventory,
                exists=exists,
                default_base=default_base,
            )
            if isinstance(source, _Refusal):
                blocked.append(BlockedItem(item.path, source.kind, source.reason))
                continue
            tips = (context.branch_tips if context.branch_tips_known else None) if context else branch_tips
            reader_action = _pin_reader(
                source,
                sha=tips.get(source[1]) if tips is not None else None,
                trunk_checkout=(str(item_repo.path) if item_repo and item_repo.path else None)
                if context
                else (code_repo or repo_path),
                where=context.identity if context else (code_repo or "the code repository"),
            )
            if isinstance(reader_action, _Refusal):
                blocked.append(BlockedItem(item.path, reader_action.kind, reader_action.reason))
                continue
            if not provisions_worktrees:
                blocked.append(
                    BlockedItem(
                        item.path,
                        "worktree-unsupported",
                        "a pinned reader needs a backend that prepares detached checkouts",
                    )
                )
                continue
            if context is None and not repo_known:
                blocked.append(
                    BlockedItem(item.path, "worktree-unprovable", "a pinned reader needs a resolved code repository")
                )
                continue
            assert reader_action.start_sha is not None
            owner = enclosing_owner(item, by_path)
            merge_target = source[1] if owner is not None else (context.default_base if context else default_base)
            _emit(
                item,
                phase,
                resolution,
                reader_action,
                merge_target,
                item_repo,
                reader=(source[1], reader_action.start_sha),
                content_root=content_root,
            )
            continue

        local_exists = exists
        local_inventory = inventory
        local_repo_path = repo_path
        local_code_repo = code_repo
        local_base = default_base
        local_epic_path = epic_worktree_path
        local_epic_branch = epic_branch
        local_repo_known = repo_known
        is_root = item.path == root
        action: WorktreeAction | _Refusal | None
        if workspace_only and phase == "execute":
            assert content_root is not None and workspace_repo is not None
            action, claimed_now = (
                WorktreeAction(
                    action="reuse",
                    path=content_root.worktree,
                    branch=content_root.branch,
                    base_branch=None,
                    exists=True,
                    parent_path=None,
                    start_sha=None,
                ),
                False,
            )
            item_repo = workspace_repo
        else:
            if context is not None and not targets:
                local_exists = context.path_exists
                selected_path = str(item_repo.path) if item_repo and item_repo.path else context.path
                usable = context.checkout_usable_by_path.get(
                    selected_path, context.checkout_usable if selected_path == context.path else False
                )
                local_repo_path = selected_path if usable else None
                local_code_repo = selected_path
                local_base = context.default_base
                local_repo_known = context.identity_known and context.inventory_known
                owner = enclosing_owner(item, by_path)
                local_epic_path = None
                local_epic_branch = branch_name(item.path, item.type)
                if owner is None and item.path == root:
                    local_epic_path = item.worktree
                    local_epic_branch = item.branch or local_epic_branch
                if not usable and (item.worktree == selected_path or local_epic_path == selected_path):
                    blocked.append(
                        BlockedItem(
                            item.path, "worktree-unprovable", "selected checkout has uncommitted or unreadable state"
                        )
                    )
                    continue
                if not context.identity_known or not context.inventory_known:
                    blocked.append(
                        BlockedItem(
                            item.path, "worktree-unprovable", "repository Git identity or inventory is unavailable"
                        )
                    )
                    continue
                matching = context.inventory.get(item.branch or "", ())
                if item.worktree and item.branch and (len(matching) != 1 or item.worktree != matching[0]):
                    blocked.append(
                        BlockedItem(
                            item.path,
                            "worktree-unprovable",
                            "stamped worktree and branch are not verified in this repository",
                        )
                    )
                    continue
                if any(len(paths) > 1 for paths in context.inventory.values()):
                    blocked.append(
                        BlockedItem(
                            item.path,
                            "worktree-ambiguous",
                            "repository inventory contains duplicate branch observations",
                        )
                    )
                    continue
                if owner is not None and item_repo is not None and item_repos is not None:
                    selected = select_anchor(
                        owner,
                        items=by_path,
                        repos=item_repos,
                        repo=item_repo,
                        context=context,
                        prepare=True,
                    )
                    if isinstance(selected, AnchorRefusal):
                        blocked.append(BlockedItem(item.path, selected.kind, selected.reason))
                        continue
                    if isinstance(selected, AnchorPreparation):
                        if selected.worktree.path is None and not provisions_worktrees:
                            blocked.append(
                                BlockedItem(
                                    item.path,
                                    "worktree-unsupported",
                                    "integration preparation needs a backend that provisions worktrees",
                                )
                            )
                            continue
                        preparations.setdefault((selected.owner_path, context.identity), selected)
                        blocked.append(
                            BlockedItem(
                                item.path, "worktree-pending", "repository integration anchor requires preparation"
                            )
                        )
                        continue
                    if isinstance(selected, Anchor):
                        local_epic_path, local_epic_branch = selected.worktree, selected.branch
                if local_epic_path is not None:
                    anchor_paths = context.inventory.get(local_epic_branch, ())
                    if len(anchor_paths) != 1 or local_epic_path != anchor_paths[0]:
                        blocked.append(
                            BlockedItem(
                                item.path,
                                "worktree-unprovable",
                                "integration anchor is not verified in this repository",
                            )
                        )
                        continue
                local_inventory = {branch: paths[0] for branch, paths in context.inventory.items() if paths}

            placement_item = item
            if targets:
                first = targets[0]
                item_repo = first.repo
                placement_item = replace(item, worktree=first.worktree, branch=first.source_branch)
                local_exists = {first.worktree: True}
                local_inventory = {first.source_branch: first.worktree}
                local_base = first.target_branch
                local_epic_branch = first.target_branch
                local_epic_path = None
            action, claimed_now = _resolve_worktree(
                placement_item,
                epic_worktree_path=local_epic_path,
                epic_branch=local_epic_branch,
                live_worktree_owners=live_worktree_owners,
                accepted_worktrees=accepted_worktrees,
                epic_worktree_claimed=identity in epic_worktree_claimed,
                worktree_exists=local_exists,
                default_base=local_base,
                phase=phase,
                is_root=is_root,
                repo_path=local_repo_path,
                inventory=local_inventory,
                code_repo=local_code_repo,
            )
        if isinstance(action, WorktreeAction) and action.action == "create-top-level" and item.type in OWNER_TYPES:
            blocked.append(
                BlockedItem(
                    item.path, "worktree-unprovable", "Epic/Release anchors are prepared at execute, never minted"
                )
            )
            continue
        if isinstance(action, _Refusal):
            # Consumes no slot and claims no worktree: a refused item is not a
            # dispatch that failed, it is a dispatch that was never made.
            blocked.append(BlockedItem(path=item.path, kind=action.kind, reason=action.reason))
            continue
        if action is None:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="worktree-pending",
                    reason="epic worktree is being created by another dispatch in this plan",
                )
            )
            continue
        if action.path is None and not provisions_worktrees:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="worktree-unsupported",
                    reason=(
                        f"{action.action} needs a backend that provisions its own worktrees; "
                        "the target backend does not"
                    ),
                )
            )
            continue
        if action.path is None and not local_repo_known:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="worktree-unprovable",
                    reason=(
                        f"{action.action} creates a worktree, but no code repository was resolved; "
                        "declare one under `repositories` in workspace.yaml"
                    ),
                )
            )
            continue
        if claimed_now:
            epic_worktree_claimed.add(identity)
        has_integration_owner = enclosing_owner(item, by_path) is not None if context is not None else not is_root
        merge_target = local_epic_branch if has_integration_owner else local_base
        # Verdict for the coordinator's finish-relay question: only a non-root
        # item merging into its owner's integration branch (never the release
        # base, never the root's own finish) is auto-answered, and only when
        # merges are unsupervised.
        auto_merge = (
            phase == "finish"
            and entry.mode == "relay"
            and not supervise_merges
            and has_integration_owner
            and not is_root
        )
        claims = (*candidate_claims, *target_claims, *content_claims)
        if action.path:
            action_claim = Claim(WorktreeScope(action.path), "write", item.path)
            if action_claim not in content_claims:
                claims += (action_claim,)
        _emit(
            item,
            phase,
            resolution,
            action,
            merge_target,
            item_repo,
            auto_merge=auto_merge,
            targets=targets,
            claims=claims,
            content_root=content_root,
        )

    # An owner entering execute needs its integration anchors even while all
    # children are still readers. The children retain their own refusal path.
    owners = [i for i in ((root_item,) if root_item else ()) + tuple(_descendants(items, root))]
    for owner in owners:
        if (
            owner.type not in OWNER_TYPES
            or owner.phase != "execute"
            or owner.work_status in TERMINAL_STATUSES
            or owner.path in repo_refusals
            or owner.path in holds
        ):
            continue
        if item_repos is not None and repo_contexts:
            needed: dict[str, tuple[ItemRepo, RepositoryContext]] = {}
            for member in _descendants(items, owner.path):
                if (
                    member.work_status in TERMINAL_STATUSES
                    or member.phase == "done"
                    or (touches_workspace(member.affects) and not code_affects(member.affects))
                ):
                    continue
                member_repo = item_repos.get(member.path)
                member_context = (
                    contexts_by_path.get(str(member_repo.path)) if member_repo and member_repo.path else None
                )
                if member_repo is not None and member_context is not None:
                    needed.setdefault(member_context.identity, (member_repo, member_context))
            for identity, (member_repo, member_context) in needed.items():
                selected = select_anchor(
                    owner, items=by_path, repos=item_repos, repo=member_repo, context=member_context, prepare=True
                )
                if isinstance(selected, AnchorPreparation) and (
                    selected.worktree.path is not None or provisions_worktrees
                ):
                    preparations.setdefault((selected.owner_path, identity), selected)
        if workspace_enabled:
            assert workspace_context is not None and workspace_worktrees_dir is not None
            selected_ws = select_workspace(
                owner, items=by_path, context=workspace_context, worktrees_dir=workspace_worktrees_dir
            )
            if isinstance(selected_ws, WorkspacePreparation):
                workspace_preparations.setdefault(selected_ws.owner_path, selected_ws)

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
        preparations=tuple(preparations.values()),
        workspace_preparations=tuple(workspace_preparations.values()),
        workspace_placements=MappingProxyType(workspace_placements),
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

    `code_repo` is the resolved code repository, reported even when the
    dirty-checkout rule withholds it from `plan()`. `code_repo_name` and
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
    def preparations(self) -> tuple[AnchorPreparation, ...]:
        return self.plan.preparations

    @property
    def workspace_preparations(self) -> tuple[WorkspacePreparation, ...]:
        return self.plan.workspace_preparations

    @property
    def workspace_placements(self) -> Mapping[str, WorkspacePlacement]:
        return self.plan.workspace_placements

    @property
    def advances(self) -> tuple[PlannedAdvance, ...]:
        return self.plan.advances

    @property
    def blocked(self) -> tuple[BlockedItem, ...]:
        return self.plan.blocked


def _checkout_is_dirty(repo: Path) -> bool:
    """Whether *repo*'s working tree carries uncommitted changes.

    Fails closed: an unreadable checkout answers `True`. Occupancy tracking
    only ever sees work items, so a human editing the shared checkout by hand
    is invisible to it -- and dispatching a worker on top of those edits is
    exactly the collision main-mode exists to avoid.
    """
    out = run_git(repo, "status", "--porcelain")
    return out is None or bool(out.strip())


def _stat_worktrees(items: Sequence[WorkItem], repo_path: str | None = None) -> dict[str, bool | None]:
    """Every distinct stamped path, stat'd once, plus the repo checkout itself
    when one is known. `None` means the stat failed.

    The repo path is included because a `"main"` action points at it, and an
    action whose `exists` is `None` reads as "unknown" when the answer is
    cheaply available."""
    stats: dict[str, bool | None] = {}
    candidates = [item.worktree for item in items if item.worktree]
    if repo_path:
        candidates.append(repo_path)
    for path in candidates:
        if path in stats:
            continue
        try:
            stats[path] = Path(path).is_dir()
        except OSError:
            stats[path] = None
    return stats


def _worktree_inventory(repo: Path | None) -> dict[str, str]:
    """`branch -> worktree path`, from `git worktree list --porcelain`.

    The key is the branch with its `refs/heads/` prefix stripped, so it is
    comparable to a `branch_name()` result. Detached and bare entries carry no
    branch and are omitted. Duplicate branch observations are also omitted:
    this legacy projection cannot prove which checkout is the anchor.

    A `prunable <reason>` line means the worktree's directory is gone but
    `git worktree prune` hasn't run yet -- porcelain keeps listing it anyway.
    `prunable` appears *after* the record's `branch` line, so the branch is
    provisionally inserted and then removed once `prunable` arrives; a blank
    line separates records and resets tracking for the next one.

    `{}` on any failure, `_stat_worktrees`'s own degrade contract: an absent
    inventory reproduces the pre-adoption behaviour rather than raising.
    """
    if repo is None:
        return {}
    out = run_git(repo, "worktree", "list", "--porcelain")
    if not out:
        return {}
    inventory: dict[str, str] = {}
    seen: set[str] = set()
    ambiguous: set[str] = set()
    current: str | None = None
    current_branch: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree ") :].strip()
            current_branch = None
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch ") :].strip()
            branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
            if branch:
                if branch in seen:
                    ambiguous.add(branch)
                seen.add(branch)
                inventory[branch] = current
                current_branch = branch
        elif line.startswith("prunable") and current_branch is not None:
            del inventory[current_branch]
            current_branch = None
        elif not line:
            current = None
            current_branch = None
    return {branch: path for branch, path in inventory.items() if branch not in ambiguous}


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
    provisions_worktrees: bool = True,
) -> OrchestrateResult:
    """Compute the dispatch plan for *path*'s subtree. Read-only throughout.

    Never mutates a work item, a worktree or the manifest.

    `repo` defaults to the root item's resolved repository
    (`resolve_item_repo(layout, root_item, by_path, repo_name=repo_name)`),
    not the layout's `repo_root`. An explicit `repo` still wins and **skips
    that resolution entirely**: an argument is not a default. `repo_name`
    selects among several declared repositories and is ignored when `repo`
    is given. Descendants use independently observed repository contexts;
    an undeclared `repo:` blocks only that descendant.

    `provisions_worktrees` passes straight through to `plan()` -- see its
    docstring; this shell resolves no backend itself; that is a caller's job.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = tuple(
        replace(item, has_design_artifact=True) if missing_design_source(bundle.root, item) is not None else item
        for item in load_items(bundle)
    )
    config = load_dispatch_config(layout)
    # Checked, not coerced. A silent `2` from a mistyped `max_parallel` is
    # indistinguishable from a deliberate `2`. Dispatch configuration is
    # independently validated above.
    max_parallel = checked_int(layout, "workflow.auto_drive.max_parallel")
    max_attend = checked_int(layout, "workflow.auto_drive.max_attend")
    supervise_merges = checked_bool(layout, "workflow.auto_drive.supervise_merges")

    by_path = {item.path: item for item in items}
    by_session, _ = session_index(items)
    live_finishes = {
        by_session[key].path
        for key in live
        if key in by_session and key == session_name(by_session[key].path, by_session[key].type, "finish")
    }
    repo_refusals: dict[str, BlockedItem] = {}
    descendant_repo_notes: tuple[str, ...] = ()
    item_repos: dict[str, ItemRepo] | None = None
    repo_contexts: dict[str, RepositoryContext] | None = None
    if repo is not None:
        root_repo = ItemRepo(None, repo, "flag")
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
        repo_contexts = {}
        canonical_repos: dict[str, ItemRepo] = {}
        for item_path, selected in item_repos.items():
            if selected.path is None:
                continue
            canonical_path = str(selected.path.resolve())
            canonical_repos[item_path] = replace(selected, path=Path(canonical_path))
        item_repos.update(canonical_repos)
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
                paths=(Path(item.worktree) for item in items if item.path in selected_paths and item.worktree),
                checkouts=(selected.path for _, selected in selected_items if selected.path is not None),
                identity=proven_identities[key],
            )
            repo_contexts[context.identity] = context
    selected_root = item_repos.get(path, root_repo) if item_repos is not None else root_repo
    resolved_repo, repo_note = selected_root.path, root_repo.note
    root_context = None
    if resolved_repo is not None and repo_contexts is not None:
        root_context = next(
            (ctx for ctx in repo_contexts.values() if str(resolved_repo) in ctx.checkout_usable_by_path), None
        )

    code_repo = str(resolved_repo) if resolved_repo is not None else None
    repo_path = code_repo
    if root_context is not None:
        repo_path = code_repo if root_context.checkout_usable_by_path[str(resolved_repo)] else None
    elif resolved_repo is not None and _checkout_is_dirty(resolved_repo):
        # Withhold the checkout rather than dispatch into someone's edits.
        # `None` is the fully-supported "behave as before" value, so this
        # degrades to today's create-top-level cold start. The repository
        # itself is still known and still reported.
        repo_path = None

    planning_items = items
    if item_repos is not None:
        planning_items = tuple(
            replace(
                item,
                worktree=str(Path(item.worktree).resolve()) if item.worktree else None,
                repo_stamps=MappingProxyType(
                    {
                        name: replace(stamp, worktree=str(Path(stamp.worktree).resolve()))
                        for name, stamp in item.repo_stamps.items()
                    }
                ),
            )
            for item in items
        )

    subtree_paths = {path, *(item.path for item in _descendants(items, path))}
    finish_plans = {
        item.path: resolve_finish_targets(
            layout,
            items,
            item.path,
            single_repo=root_repo if repo is not None else None,
            repo_contexts=repo_contexts or {},
        )
        for item in items
        if (item.path in subtree_paths and item.phase == "finish") or item.path in live_finishes
    }
    checkpoints = {
        item.path: read_human_checkpoints(bundle.root, item)
        for item in items
        if item.path in subtree_paths and item.phase == "execute"
    }

    ws_repo, ws_note = workspace_repo(layout)
    ws_context = None
    if ws_repo is not None:
        assert ws_repo.path is not None
        ws_context = observe_repository(
            ws_repo.path,
            paths=(
                Path(stamp.worktree)
                for item in items
                for name, stamp in item.repo_stamps.items()
                if name == WORKSPACE_REPO
            ),
        )

    computed = plan(
        planning_items,
        path,
        dispatch_rules=config.rules,
        max_parallel=max_parallel,
        max_attend=max_attend,
        supervise_merges=supervise_merges,
        live=live,
        worktree_exists=root_context.path_exists if root_context is not None else _stat_worktrees(items, repo_path),
        holds=holds_by_path(items, bundle.root),
        provisions_worktrees=provisions_worktrees,
        workspace=str(layout.root),
        default_base=root_context.default_base if root_context is not None else default_base(resolved_repo),
        repo_path=repo_path,
        worktree_inventory=(
            {branch: paths[0] for branch, paths in root_context.inventory.items() if len(paths) == 1}
            if root_context is not None
            else _worktree_inventory(resolved_repo)
        ),
        branch_tips=observe_branch_tips(resolved_repo) if repo is not None and resolved_repo is not None else None,
        repo_known=code_repo is not None,
        code_repo=code_repo,
        repo_refusals=repo_refusals,
        item_repos=item_repos,
        repo_contexts=repo_contexts,
        finish_plans=finish_plans,
        workspace_repo=ws_repo,
        workspace_context=ws_context,
        workspace_worktrees_dir=str(layout.worktrees_dir.resolve()) if ws_repo else None,
        checkpoints=checkpoints,
    )

    if repo is not None:
        # The explicit override retains singular scheduling and must not resolve
        # authored foreign assignments. Its accepted dispatches still carry the
        # same mandatory repository metadata as normally resolved dispatches.
        # Workspace readers retain the repository that supplied their pinned SHA.
        computed = replace(
            computed,
            dispatch_repos=MappingProxyType(
                {
                    dispatch.key: ws_repo
                    if ws_repo is not None
                    and dispatch.phase in READ_ONLY_PHASES
                    and computed.dispatch_repos.get(dispatch.key) == ws_repo
                    else root_repo
                    for dispatch in computed.dispatches
                }
            ),
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
            + ((repo_note,) if repo_note else ())
            + descendant_repo_notes
            + ((ws_note,) if ws_note else ())
        ),
        holds=open_holds(items, bundle.root, subtree),
        code_repo=code_repo,
        code_repo_name=root_repo.name if repo is None else None,
        code_repo_source=root_repo.source if repo is None else None,
    )


__all__ = [
    "BLOCKED_KINDS",
    "DISPATCH_COMMAND",
    "DISPATCH_PHASES",
    "READER_ACTION",
    "WORKER_PLACEMENT_LINE",
    "WORKSPACE_VAR",
    "AnchorPreparation",
    "BlockedItem",
    "HoldReport",
    "HumanCheckpoints",
    "OrchestratePlan",
    "OrchestrateResult",
    "PlannedAdvance",
    "WorkspacePlacement",
    "WorkspacePreparation",
    "branch_name",
    "integration_branch",
    "plan",
    "read_human_checkpoints",
    "run_orchestrate",
]
