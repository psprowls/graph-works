"""The dispatch plan: which stages run now, where, on what, saying what.

`plan()` is IO-free, mirroring `work_tracker_okf.workflow.route()`'s own split:
identical inputs produce an identical `OrchestratePlan`, and every config read,
`stat` and `git` call sits in `run_orchestrate` above it. Nothing here launches
a worker -- `subagents-io` ships the dispatch value types and no execution
backend, and choosing one is a separate work item.

The frontier walk generalizes `hierarchy.descend()` from pick-one-leaf to
collect-all, **reusing** its `child_gated` predicate, `PICK_ORDER` and
`WALK_DEPTH_CAP` rather than restating them. `--descend` and auto-drive
disagreeing about the same item is exactly the failure that reuse prevents, and
`test_orchestrate_plan.py` pins the agreement as a property.

Nothing Orca-shaped appears in this module. The four prompt lines are
vendor-neutral; the one place a vendor command may appear is a variant's
`prompt_tail`, which lives in workspace configuration (`pipeline.py`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

from okf_io import load_bundle
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.hierarchy import PICK_ORDER, active_nonterminal_descendants, child_gated, decision_owner
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.vocabulary import (
    PHASES,
    SLUG_PREFIXES,
    TERMINAL_STATUSES,
)
from work_tracker_okf.workflow import RouteResult, route, state_for

from graph_works_core.orchestrate.anchors import (
    Anchor,
    AnchorPreparation,
    AnchorRefusal,
    enclosing_owner,
    integration_branch,
    select_anchor,
)
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
from graph_works_core.workspace.provenance import default_base, run_git
from graph_works_core.workspace.repo_context import RepositoryContext, observe_repository, repository_identity
from graph_works_core.workspace.repos import ItemRepo, resolve_item_repo

WALK_DEPTH_CAP = 10_000

#: The branch path segment for an item whose `type` is unrecognized. Reachable
#: only through the root's fallback branch name: a candidate with a bad `type`
#: is already blocked by the router's own validation.
_UNKNOWN_TYPE_SEGMENT = "work"

#: The phases a stage can be dispatched at. `done` is terminal.
DISPATCH_PHASES: frozenset[str] = frozenset(PHASES - {"done"})

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


def _sorted(candidates: list[tuple[WorkItem, RouteResult]]) -> list[tuple[WorkItem, RouteResult]]:
    return sorted(
        candidates,
        key=lambda pair: (PICK_ORDER.get(pair[0].work_status, 99), pair[0].opened, pair[0].path),
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
#: cannot produce a commit, so it must not acquire the placement stamp that
#: decides where later commits land -- the governing invariant of this
#: module's placement policy. Deliberately spelled out here rather than
#: imported as the complement of `stage_advance.RESULTS_PHASES`: the two
#: halves of `orchestrate` share no module-level symbol by design (D-001),
#: and `test_the_read_only_and_results_phases_are_complements` pins them
#: against each other instead.
READ_ONLY_PHASES: frozenset[str] = frozenset({"design", "plan"})


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

    Rule 2's entitlement is `is_root or phase in READ_ONLY_PHASES`. A stage
    that writes no code may share the anchor because it cannot collide there;
    a descendant that *does* commit gets its own fork instead, so it has a
    branch of its own to review and merge. There is no opportunistic
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
    """

    def _occupied(path: str) -> bool:
        # Exclude this item's own contribution. `_frontier` does not filter the
        # live set out of the candidates, so an item is routinely re-proposed
        # while genuinely still live -- many on_dispatch transitions are no-ops
        # between a dispatch and its own advance. Its own stamp must never read
        # as "held by someone else", or it forks off itself.
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

    def _adopted_action(reason: str) -> tuple[WorktreeAction | _Refusal, bool]:
        """Search for the item's real worktree; the action to take, or a refusal."""
        found = _adopt(item, inventory=inventory)
        if isinstance(found, _Refusal):
            return found, False
        if found is None:
            return _Refusal(kind="worktree-unprovable", reason=reason), False
        path, branch = found
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
            ),
            is_main,
        )

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
            ),
            False,
        )
    if epic_worktree_path is not None:
        # The epic anchor is a *read* context for a descendant's vault-only
        # stage and a *work* context for the root. A descendant at a code
        # phase must not land in it: two workers committing in one directory
        # is the hazard rule 3 exists for, and a child that commits on the
        # epic branch leaves nothing of its own to review or merge. It falls
        # through to the fork below instead.
        reuses_anchor = is_root or phase in READ_ONLY_PHASES
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
        # At `plan()`'s own call site this is equivalent to
        # `item.phase not in (None, "design")`, since the resolved `phase`
        # argument always equals `item.phase` when the latter is set. The
        # two-clause form only diverges for a direct caller (e.g. a test)
        # that passes a `phase` different from `item.phase` -- keep both
        # clauses; collapsing them changes that contract.
        # Cold start, but the item has advanced at least once and is not at
        # design: it has run before, so its work is somewhere. Dispatching
        # the finish stage with nothing to merge is worse than not
        # dispatching it. `item.phase is None` excludes a never-advanced item
        # (e.g. a freshly filed TestGap routed straight to execute or plan)
        # -- its first-ever dispatch has no prior work to find, so it is not
        # held to this rule. Deliberately ahead of the descendant refusal
        # below: a descendant whose real worktree is findable is placed in
        # it, and only an unfindable one blocks.
        return _adopted_action(cold_reason)
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


def _prompt(
    *,
    path: str,
    key: str,
    phase: str,
    workspace: str,
    merge_target: str,
    tail: str | None,
) -> str:
    """Four vendor-neutral lines, the variant's tail, then the placement line.

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
    ]
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
    lines.append(WORKER_PLACEMENT_LINE)
    return "\n".join(lines)


def plan(
    items: Sequence[WorkItem],
    root: str,
    *,
    dispatch_rules: tuple[DispatchRule, ...],
    max_parallel: int,
    supervise_merges: bool = False,
    live: tuple[str, ...] = (),
    worktree_exists: Mapping[str, bool | None] | None = None,
    holds: Mapping[str, HoldFact] = MappingProxyType({}),
    provisions_worktrees: bool = True,
    workspace: str,
    default_base: str,
    repo_path: str | None = None,
    worktree_inventory: Mapping[str, str] | None = None,
    repo_known: bool = True,
    code_repo: str | None = None,
    repo_refusals: Mapping[str, BlockedItem] = MappingProxyType({}),
    item_repos: Mapping[str, ItemRepo] | None = None,
    repo_contexts: Mapping[str, RepositoryContext] | None = None,
    finish_plans: Mapping[str, FinishPlan] = MappingProxyType({}),
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
    """
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
        )

    candidates, advances, blocked = _frontier(items, root, holds=holds)
    candidates = _sorted(candidates)
    slots_free = max(0, max_parallel - len(live))

    # `live_worktree_owners` is an owner map, not a bare set, for the reason
    # `_resolve_worktree` needs one: `_frontier` does not filter the live set
    # out of the candidates, so an item is routinely re-proposed as its own
    # candidate while still live, and a bare set can't tell "claimed by
    # someone else" from "claimed by the very candidate being checked". The
    # affects gate below is deliberately NOT given the same treatment here --
    # its self-collision when a live item is re-proposed is pre-existing,
    # unrelated behaviour this task does not touch (see
    # `test_worktree_rule_3_forks_a_child_branch_when_the_epic_worktree_is_live`,
    # which pins the self-blocking as-is).
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

    live_affects: set[tuple[str, str]] = set()
    uncertain_live_affects: set[str] = set()
    live_worktree_owners: dict[str, set[str]] = {}
    for key in live:
        item = by_session[key]
        _, context = evidence(item)
        if item_repos is not None and context is None:
            uncertain_live_affects.update(item.affects)
        identity = context.identity if context is not None else "<legacy>"
        live_affects.update((identity, member) for member in item.affects)
        live_finish = finish_plans.get(item.path)
        for target in live_finish.targets if live_finish is not None else ():
            target_context = contexts_by_path.get(str(target.repo.path))
            target_identity = target_context.identity if target_context else str(target.repo.path)
            live_affects.update((target_identity, member) for member in item.affects)
            live_worktree_owners.setdefault(target.worktree, set()).add(item.path)
        if item.worktree:
            live_worktree_owners.setdefault(item.worktree, set()).add(item.path)

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
    accepted_affects: set[tuple[str, str]] = set()
    accepted_worktrees: set[str] = set()
    epic_worktree_claimed: set[str] = set()
    dispatches: list[PlannedDispatch] = []
    resolutions: dict[str, DispatchResolution] = {}
    dispatch_repos: dict[str, ItemRepo] = {}
    finish_targets: dict[str, tuple[FinishTarget, ...]] = {}
    preparations: dict[tuple[str, str], AnchorPreparation] = {}
    for item, result in candidates:
        repo_refusal = repo_refusals.get(item.path)
        if repo_refusal is not None:
            blocked.append(repo_refusal)
            continue
        finish = finish_plans.get(item.path)
        if finish is not None and finish.blockers:
            blocked.append(BlockedItem(item.path, "worktree-unprovable", "; ".join(finish.blockers)))
            continue
        targets = finish.targets if finish is not None else ()
        if any(t.worktree in accepted_worktrees or t.worktree in live_worktree_owners for t in targets):
            blocked.append(BlockedItem(item.path, "worktree-pending", "a finish target is occupied by another worker"))
            continue
        item_repo, context = evidence(item)
        if item_repos is not None and (item_repo is None or (item_repo.path is not None and context is None)):
            blocked.append(BlockedItem(item.path, "invalid", "repository evidence unavailable for this item"))
            continue
        identity = context.identity if context is not None else "<legacy>"
        affects = {(identity, member) for member in item.affects}
        for target in targets:
            target_context = next(
                (c for c in (repo_contexts or {}).values() if str(target.repo.path) in c.checkout_usable_by_path), None
            )
            target_identity = target_context.identity if target_context else str(target.repo.path)
            affects.update((target_identity, member) for member in item.affects)
        if not affects:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="affects-overlap",
                    reason="declare affects to allow parallel dispatch",
                )
            )
            continue
        if item_repos is not None and any(member in uncertain_live_affects for _, member in affects):
            blocked.append(
                BlockedItem(item.path, "worktree-unprovable", "live item's repository is unavailable for affects check")
            )
            continue
        overlap = affects & (live_affects | accepted_affects)
        if overlap:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="affects-overlap",
                    reason=(
                        "affects overlap with a live or already-planned dispatch: "
                        + ", ".join(sorted(member for _, member in overlap))
                    ),
                )
            )
            continue
        if len(dispatches) >= slots_free:
            blocked.append(BlockedItem(path=item.path, kind="capacity", reason="ready, but no worker slot free"))
            continue
        assert result.dispatch is not None, "every candidate carries a dispatch (see _frontier)"
        on_dispatch_phase = result.on_dispatch.phase if result.on_dispatch else None
        phase = item.phase or on_dispatch_phase or result.dispatch.stage

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

        local_exists = exists
        local_inventory = inventory
        local_repo_path = repo_path
        local_code_repo = code_repo
        local_base = default_base
        local_epic_path = epic_worktree_path
        local_epic_branch = epic_branch
        local_repo_known = repo_known
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
                    BlockedItem(item.path, "worktree-unprovable", "repository Git identity or inventory is unavailable")
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
                        item.path, "worktree-ambiguous", "repository inventory contains duplicate branch observations"
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
                    prepare=phase not in READ_ONLY_PHASES,
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
                        BlockedItem(item.path, "worktree-pending", "repository integration anchor requires preparation")
                    )
                    continue
                if isinstance(selected, Anchor):
                    local_epic_path, local_epic_branch = selected.worktree, selected.branch
                elif phase in READ_ONLY_PHASES:
                    # Vault-only work needs a reading checkout, not a new owner stamp.
                    local_epic_path = local_repo_path
                    local_epic_branch = next(
                        (branch for branch, paths in context.inventory.items() if local_repo_path in paths), local_base
                    )
            if local_epic_path is not None:
                anchor_paths = context.inventory.get(local_epic_branch, ())
                if len(anchor_paths) != 1 or local_epic_path != anchor_paths[0]:
                    blocked.append(
                        BlockedItem(
                            item.path, "worktree-unprovable", "integration anchor is not verified in this repository"
                        )
                    )
                    continue
            local_inventory = {branch: paths[0] for branch, paths in context.inventory.items() if paths}

        is_root = item.path == root
        placement_item = item
        placement_root = is_root
        if context is not None and phase in READ_ONLY_PHASES and local_epic_path is None and not item.worktree:
            placement_item = replace(item, phase=None)
            placement_root = True
            local_epic_branch = branch_name(item.path, item.type)
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
            is_root=placement_root,
            repo_path=local_repo_path,
            inventory=local_inventory,
            code_repo=local_code_repo,
        )
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
        # Same entitlement as `_resolve_worktree`'s rule 2: a read-only
        # descendant (design/plan) inheriting the *epic anchor* writes no
        # code, so its `reuse` has nothing to protect and must not occupy the
        # slot for anyone else -- doing so was the bug that forced a second
        # read-only dispatch off the shared anchor into a needless fork. An
        # item reusing its *own* recorded stamp (rule 1) always claims,
        # regardless of phase: that path is the specific hazard rule 1's own
        # occupancy check exists to serialize (two items provenance-stamped
        # onto the same directory), unrelated to the epic-anchor-sharing
        # exemption. The root always claims too.
        own_stamp = bool(item.worktree and item.branch)
        if action.path and (own_stamp or is_root or phase not in READ_ONLY_PHASES):
            accepted_worktrees.add(action.path)

        has_integration_owner = enclosing_owner(item, by_path) is not None if context is not None else not is_root
        merge_target = local_epic_branch if has_integration_owner else local_base
        key = session_name(item.path, item.type, phase)
        resolutions[key] = resolution
        finish_targets[key] = targets
        accepted_worktrees.update(t.worktree for t in targets)
        if item_repo is not None:
            dispatch_repos[key] = item_repo
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
                prompt=_prompt(
                    path=item.path,
                    key=key,
                    phase=phase,
                    workspace=workspace,
                    merge_target=merge_target,
                    tail=entry.prompt_tail,
                ),
            )
        )
        accepted_affects |= affects

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
        finish_targets=MappingProxyType(finish_targets),
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
    def preparations(self) -> tuple[AnchorPreparation, ...]:
        return self.plan.preparations

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
    branch and are omitted -- an entry the planner cannot name is an entry it
    cannot adopt.

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
                inventory[branch] = current
                current_branch = branch
        elif line.startswith("prunable") and current_branch is not None:
            del inventory[current_branch]
            current_branch = None
        elif not line:
            current = None
            current_branch = None
    return inventory


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
    supervise_merges = checked_bool(layout, "workflow.auto_drive.supervise_merges")

    by_path = {item.path: item for item in items}
    repo_refusals: dict[str, BlockedItem] = {}
    descendant_repo_notes: tuple[str, ...] = ()
    item_repos: dict[str, ItemRepo] | None = None
    repo_contexts: dict[str, RepositoryContext] | None = None
    if repo is not None:
        root_repo = ItemRepo(None, repo, "flag")
    else:
        root_repo = resolve_item_repo(layout, by_path.get(path), by_path, repo_name=repo_name)
        repo_refusals, descendant_repo_notes, item_repos = _repo_refusals(layout, items, path, root_repo)
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
        if item.path in subtree_paths and item.phase == "finish"
    }

    computed = plan(
        planning_items,
        path,
        dispatch_rules=config.rules,
        max_parallel=max_parallel,
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
        repo_known=code_repo is not None,
        code_repo=code_repo,
        repo_refusals=repo_refusals,
        item_repos=item_repos,
        repo_contexts=repo_contexts,
        finish_plans=finish_plans,
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
        warnings=computed.warnings + decisions.warnings + ((repo_note,) if repo_note else ()) + descendant_repo_notes,
        holds=open_holds(items, bundle.root, subtree),
        code_repo=code_repo,
        code_repo_name=root_repo.name if repo is None else None,
        code_repo_source=root_repo.source if repo is None else None,
    )


__all__ = [
    "BLOCKED_KINDS",
    "DISPATCH_COMMAND",
    "DISPATCH_PHASES",
    "WORKER_PLACEMENT_LINE",
    "WORKSPACE_VAR",
    "AnchorPreparation",
    "BlockedItem",
    "HoldReport",
    "OrchestratePlan",
    "OrchestrateResult",
    "PlannedAdvance",
    "branch_name",
    "integration_branch",
    "plan",
    "run_orchestrate",
]
