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
from typing import Any, Literal

from config_io import PlainYamlStore, dotted
from okf_io import load_bundle
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from subagents_io.routing import resolve_model, validate_rules
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.hierarchy import PICK_ORDER, active_nonterminal_descendants, child_gated, nearest_parent
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.vocabulary import (
    EFFORTS,
    PHASES,
    SLUG_PREFIXES,
    TERMINAL_STATUSES,
    TYPES,
)
from work_tracker_okf.workflow import RouteResult, route, state_for

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_bool, checked_int, checked_str
from graph_works_core.workspace.pipeline import PipelineEntry, pipeline_table
from graph_works_core.workspace.provenance import default_base, run_git
from graph_works_core.workspace.repos import resolve_repo

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
DISPATCH_COMMAND = "/graph-works:workflow"

#: The workspace pointer a dispatched session reads at startup.
WORKSPACE_VAR = "GRAPH_WORKS_DIR"

#: What `run_orchestrate` hands `subagents_io.routing.validate_rules`. Bound
#: here because the match keys are this module's choice, and the sets they are
#: checked against are `work-tracker-okf`'s. Note `kind` carries a **`TYPES`**
#: value (`Feature`, not `feature`): the projection's field is `type`, and the
#: rules block names the dimension `kind`.
ROUTING_VOCABULARIES: Mapping[str, frozenset[str]] = {
    "phase": DISPATCH_PHASES,
    "kind": TYPES,
    "effort": EFFORTS,
}

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
    permission_mode: str
    supervise_merges: bool
    live: tuple[str, ...]
    slots_free: int
    dispatches: tuple[PlannedDispatch, ...]
    advances: tuple[PlannedAdvance, ...]
    blocked: tuple[BlockedItem, ...]
    warnings: tuple[str, ...]


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
    a warning is returned: a `--live` key that names it then reports
    `matches no known item`, which is a true statement, where binding it to
    whichever item happened to be enumerated first would be a false one.
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
    items: Sequence[WorkItem], root: str, *, held_decisions: frozenset[str] = frozenset()
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
        state = state_for(items, path, has_open_decision=path in held_decisions)
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


def _epic_stamp(items: Sequence[WorkItem], root_item: WorkItem) -> tuple[str, str] | None:
    """`(path, branch)` of "the epic worktree" rules 2-3 reuse or fork against:
    the root's own stamp, falling back to the first stamped descendant in pick
    order. The fallback is reproducible from vault state but depends on which
    child happened to run first -- recorded as a risk in the spec, not fixed
    here."""
    if root_item.worktree and root_item.branch:
        return root_item.worktree, root_item.branch
    stamped = [item for item in _descendants(items, root_item.path) if item.worktree and item.branch]
    if not stamped:
        return None
    stamped.sort(key=lambda item: (PICK_ORDER.get(item.work_status, 99), item.opened, item.path))
    chosen = stamped[0]
    assert chosen.worktree is not None and chosen.branch is not None
    return chosen.worktree, chosen.branch


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
    every time. The distinction is not cosmetic: a worker in the main checkout
    cannot detect its own worktree (git reports none), so `_prompt` owes it an
    explicit instruction that only the `"main"` label triggers.

    Both fork targets go through `_fork_branch`, which keeps them distinct from
    their own base: rule 1's base is the item's stamped branch (or `default_base`
    when evicting out of the main checkout) and rule 2/3's is the epic branch,
    and either can already equal the item's derived name.

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
        ),
        True,
    )


def _prompt(
    *,
    path: str,
    key: str,
    phase: str,
    workspace: str,
    merge_target: str,
    tail: str | None,
    worktree: WorktreeAction,
    is_root: bool,
) -> str:
    """Four vendor-neutral lines, plus the variant's tail.

    The tail is substituted with `str.replace` over a fixed placeholder set
    rather than `str.format`: a tail is workspace-authored text that may
    legitimately contain braces, and a formatting call that can raise on user
    config is a runtime failure where a literal is harmless.

    The command and workspace lines are built from `DISPATCH_COMMAND` and
    `WORKSPACE_VAR` -- the native `graph-works` namespace, matching this
    package's own `GRAPH_WORKS_DIR`-based resolution (`discovery.py`). No
    emission in this package names the legacy `graph-wiki` plugin or
    `GRAPH_WIKI_WORKSPACE` any more.

    A `"main"` action appends one further line, and so does a dispatch of the
    subtree **root**, for two different reasons that produce the same
    instruction. The `"main"` case is a detection gap: git reports no worktree
    for the main checkout, so the worker genuinely cannot find its own. The
    root case is an anchoring one: `_epic_stamp` prefers the root's own stamp
    over its unstable descendant-scan fallback, and asking every root worker to
    record its placement is what gets that stamp written -- once, on the epic's
    first dispatch, after which the anchor never moves again. Both are per
    *dispatch* rather than per pipeline *variant*, so no workspace-authored
    `prompt_tail` could express either; both are appended after the tail so
    authored prose cannot swallow them. Neither is appended for a read-only
    **descendant** dispatch: a `design` or `plan` stage writes no code, so the
    placement it happens to occupy is not a fact worth recording -- and recording
    it would pin the item's later code phases to it.
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
    # A read-only *descendant* is told nothing: it is sitting in the epic
    # worktree (or the checkout) purely to read, and a stamp acquired there
    # would pin its own later code phases to a directory a vault-only stage
    # happened to occupy. The root keeps the line at every phase -- its stamp
    # is the epic anchor every descendant resolves against, and an epic's
    # `execute` dispatches children rather than a worker for itself, so a root
    # that skipped `design`/`plan` would never stamp at all.
    if is_root or (worktree.action == "main" and phase not in READ_ONLY_PHASES):
        if worktree.action == "main":
            line = (
                f"This stage runs in the main checkout on `{worktree.branch}`; no dedicated worktree "
                f"exists. Record the worktree as `{worktree.path}` and the branch as `{worktree.branch}` "
                "explicitly when you advance — it cannot be detected from where you are."
            )
        elif worktree.path is None:
            # A pathless root action (`create-top-level` or `fork-child`): the
            # epic's first-ever dispatch has no stamp yet and no known worktree
            # to name, so ask for whatever the worker ends up in instead of
            # rendering the literal `None`.
            line = (
                f"Record the worktree you end up in, and the branch as `{worktree.branch}`, "
                "explicitly when you advance — this is the subtree root, and its stamp is what "
                "every later dispatch in this epic resolves its placement against."
            )
        else:
            line = (
                f"Record the worktree as `{worktree.path}` and the branch as `{worktree.branch}` "
                "explicitly when you advance — this is the subtree root, and its stamp is what "
                "every later dispatch in this epic resolves its placement against."
            )
        lines.append(line)
    return "\n".join(lines)


def plan(
    items: Sequence[WorkItem],
    root: str,
    *,
    pipeline: Mapping[str, PipelineEntry],
    auto_drive: Mapping[str, Any],
    max_parallel: int,
    permission_mode: str,
    supervise_merges: bool = False,
    live: tuple[str, ...] = (),
    worktree_exists: Mapping[str, bool | None] | None = None,
    held_decisions: frozenset[str] = frozenset(),
    provisions_worktrees: bool = True,
    workspace: str,
    default_base: str,
    repo_path: str | None = None,
    worktree_inventory: Mapping[str, str] | None = None,
) -> OrchestratePlan:
    """The whole dispatch plan for *root*'s subtree. Mutates nothing, reads nothing.

    `held_decisions` is the set of canonical paths currently named in an
    *open* decision (resolved by `run_orchestrate` from the nearest owner's ledger, since a
    ledger read is IO and this function stays pure) -- a design-stage item in
    that set is blocked rather than redispatched, which is what stops a
    reconciling-spec pass from looping on a question only a human can answer.

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
    than a `"reuse"`, because a worker there cannot detect its own worktree and
    `_prompt` owes it an explicit instruction. It no longer influences cold
    start: a cold start mints the epic worktree whether or not a checkout is
    known.

    `worktree_inventory` is `branch -> worktree path` as git sees it, resolved
    by `run_orchestrate` from `git worktree list --porcelain`. It is what lets
    the placement rules *find* an item's prior work instead of assuming it,
    and it defaults to `{}` -- with no inventory the rules that would search
    refuse instead, which is still an improvement on naming a directory that
    holds nothing, but adoption is the point.
    """
    exists = worktree_exists or {}
    inventory = worktree_inventory or {}
    by_path = {item.path: item for item in items}
    # A session name carries a hash, so nothing recovers a path by parsing
    # one -- `session_index` is the only reverse there is, and its own
    # ambiguity warnings ride the same channel.
    by_session, warnings_list = session_index(items)
    warnings = [*warnings_list]
    warnings += [f"live key {key!r} matches no known item" for key in live if key not in by_session]

    root_item = by_path.get(root)
    if root_item is not None and (root_item.work_status in TERMINAL_STATUSES or root_item.phase == "done"):
        return OrchestratePlan(
            path=root,
            terminal=True,
            max_parallel=max_parallel,
            permission_mode=permission_mode,
            supervise_merges=supervise_merges,
            live=tuple(live),
            slots_free=0,
            dispatches=(),
            advances=(),
            blocked=(),
            warnings=tuple(warnings),
        )

    candidates, advances, blocked = _frontier(items, root, held_decisions=held_decisions)
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
    live_affects: set[str] = set()
    live_worktree_owners: dict[str, set[str]] = {}
    for key in live:
        item = by_session.get(key)
        if item is None:
            continue
        live_affects.update(item.affects)
        if item.worktree:
            live_worktree_owners.setdefault(item.worktree, set()).add(item.path)

    stamp = _epic_stamp(items, root_item) if root_item is not None else None
    epic_worktree_path = stamp[0] if stamp else None
    epic_branch = stamp[1] if stamp else branch_name(root, root_item.type if root_item else "")

    # Carry the epic's known worktree onto every planned advance: a
    # coordinator-applied advance dispatches no worker, so it has no cwd inside
    # the worktree to infer one from, and would otherwise silently skip the
    # stamp. Only when a real path is known -- `epic_branch` alone is a name,
    # not somewhere to cd.
    if epic_worktree_path is not None:
        advances = [replace(a, worktree=epic_worktree_path, branch=epic_branch) for a in advances]

    # Pass 1: affects serialization, over ALL candidates, in sorted order.
    accepted_affects: set[str] = set()
    survivors: list[tuple[WorkItem, RouteResult]] = []
    for item, result in candidates:
        affects = set(item.affects)
        if not affects:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="affects-overlap",
                    reason="declare affects to allow parallel dispatch",
                )
            )
            continue
        overlap = affects & (live_affects | accepted_affects)
        if overlap:
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="affects-overlap",
                    reason=("affects overlap with a live or already-planned dispatch: " + ", ".join(sorted(overlap))),
                )
            )
            continue
        survivors.append((item, result))
        accepted_affects |= affects

    # Pass 2: the first `slots_free` survivors dispatch; the rest block.
    accepted_worktrees: set[str] = set()
    epic_worktree_claimed = False
    dispatches: list[PlannedDispatch] = []
    for item, result in survivors:
        if len(dispatches) >= slots_free:
            blocked.append(BlockedItem(path=item.path, kind="capacity", reason="ready, but no worker slot free"))
            continue
        assert result.dispatch is not None, "every survivor carries a dispatch (see _frontier)"
        on_dispatch_phase = result.on_dispatch.phase if result.on_dispatch else None
        phase = item.phase or on_dispatch_phase or result.dispatch.stage

        # Ahead of `_resolve_worktree` deliberately: an item that cannot
        # dispatch should not claim the epic worktree slot or add to
        # `accepted_worktrees` on its way out. Keyed on `mode`, not on the
        # `branch` variant -- a workspace may set any variant to `relay`, and
        # mode is the property that means "no human is in the room but a
        # decision is needed".
        variant = result.dispatch.variant
        entry = pipeline[variant]
        if entry.mode == "relay" and not (entry.prompt_tail or "").strip():
            blocked.append(
                BlockedItem(
                    path=item.path,
                    kind="relay-untailed",
                    reason=(
                        f"variant {variant!r} dispatches in relay mode with no prompt tail, so a "
                        "worker would fall into an interactive menu with nobody watching; set "
                        f"workflow.pipeline.{variant}.prompt_tail"
                    ),
                )
            )
            continue

        is_root = item.path == root
        action, claimed_now = _resolve_worktree(
            item,
            epic_worktree_path=epic_worktree_path,
            epic_branch=epic_branch,
            live_worktree_owners=live_worktree_owners,
            accepted_worktrees=accepted_worktrees,
            epic_worktree_claimed=epic_worktree_claimed,
            worktree_exists=exists,
            default_base=default_base,
            phase=phase,
            is_root=is_root,
            repo_path=repo_path,
            inventory=inventory,
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
        epic_worktree_claimed = epic_worktree_claimed or claimed_now
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

        merge_target = epic_branch if item.path != root else default_base
        resolution = resolve_model(
            auto_drive,
            {"phase": phase, "kind": item.type, "effort": item.effort},
            default_key="phase",
        )
        key = session_name(item.path, item.type, phase)
        dispatches.append(
            PlannedDispatch(
                key=key,
                slug=item.path,
                phase=phase,
                kind=item.type,
                effort=item.effort,
                skill=entry.skill,
                mode=entry.mode,
                model=resolution.model if resolution else None,
                reasoning_effort=resolution.reasoning_effort if resolution else None,
                worktree=action,
                merge_target=merge_target,
                prompt=_prompt(
                    path=item.path,
                    key=key,
                    phase=phase,
                    workspace=workspace,
                    merge_target=merge_target,
                    tail=entry.prompt_tail,
                    worktree=action,
                    is_root=item.path == root,
                ),
            )
        )

    return OrchestratePlan(
        path=root,
        terminal=False,
        max_parallel=max_parallel,
        permission_mode=permission_mode,
        supervise_merges=supervise_merges,
        live=tuple(live),
        slots_free=slots_free,
        dispatches=tuple(dispatches),
        advances=tuple(advances),
        blocked=tuple(blocked),
        warnings=tuple(warnings),
    )


#: The manifest key holding the routing rules block. Read raw rather than
#: through the catalog: `models` is a mapping and `overrides` a list of
#: mappings, and no `ConfigEntry` type expresses either. The catalog owns the
#: two scalars beside it; `validate_rules` owns the rest.
AUTO_DRIVE_KEY = "workflow.auto_drive"


@dataclass(frozen=True, slots=True)
class OrchestrateResult:
    """The plan, plus the nearest decision owner's open questions.

    The decision fields are empty for an item with no parent-capable owner -- the
    lone-item case -- which is a shape, not an error.

    **The ledger is read twice per plan**: once by whatever consults it for the
    routing gate, once here. The two reads are not one atomic snapshot, so a
    decision answered between them leaves this response internally
    inconsistent. Deduplicating means threading parsed ledger state out of the
    frontier walk, and is deliberately not done.
    """

    plan: OrchestratePlan
    decisions_owner_path: str | None = None
    decisions_ledger_path: str | None = None
    open_decisions: tuple[_decisions.Decision, ...] = ()
    assumed_decisions: tuple[_decisions.Decision, ...] = ()
    decision_counts: Mapping[str, int] = MappingProxyType({})
    warnings: tuple[str, ...] = ()

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
    def permission_mode(self) -> str:
        return self.plan.permission_mode

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
    def advances(self) -> tuple[PlannedAdvance, ...]:
        return self.plan.advances

    @property
    def blocked(self) -> tuple[BlockedItem, ...]:
        return self.plan.blocked


def _routing_rules(layout: WorkspaceLayout) -> Mapping[str, Any]:
    """The raw `workflow.auto_drive` block, membership-checked.

    A hand-edited manifest bypasses config-io's set-time checks, so this is the
    startup gate the routing module's contract asks for: a rule naming a value
    outside its vocabulary is a dead rule, and a dead rule silently routes the
    wrong model.
    """
    raw = PlainYamlStore(layout.manifest_path).read_explicit()
    block = dotted.get(raw, AUTO_DRIVE_KEY)
    rules: Mapping[str, Any] = block if isinstance(block, dict) else {}
    errors = validate_rules(rules, ROUTING_VOCABULARIES, default_key="phase")
    if errors:
        raise WorkspaceError(f"{layout.manifest_path}: {AUTO_DRIVE_KEY}: " + "; ".join(errors))
    return rules


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
    """The nearest parent-capable owner's open and assumed decisions.

    Empty throughout when *path* has no parent-capable owner -- a shape rather
    than an error. Release, Epic, and Feature items own their own ledgers.
    """
    owner = nearest_parent(items, path)
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


def _held_decisions(items: Sequence[WorkItem], bundle_root: Path) -> frozenset[str]:
    """Every canonical path currently named in an *open* decision's `affects`, across the
    whole item set -- what `plan()`'s `held_decisions` gates re-dispatch on.

    One ledger read per distinct owner, not per item: every leaf under the same
    owner shares the same ledger, and `_resolve_decisions` already pays this
    same one-read-per-owner cost for the root alone.
    """
    entries_by_owner: dict[str, tuple[_decisions.Decision, ...]] = {}
    held: set[str] = set()
    for item in items:
        owner = nearest_parent(items, item.path)
        if owner is None:
            continue
        if owner not in entries_by_owner:
            ledger = _decisions.ledger_ref(owner).path(bundle_root)
            entries_by_owner[owner] = tuple(_decisions.load(ledger).entries)
        if _decisions.query(entries_by_owner[owner], status="open", affects=item.path):
            held.add(item.path)
    return frozenset(held)


def run_orchestrate(
    layout: WorkspaceLayout,
    path: str,
    *,
    live: tuple[str, ...] = (),
    repo: Path | None = None,
    repo_name: str | None = None,
    pipeline: Mapping[str, PipelineEntry] | None = None,
    provisions_worktrees: bool = True,
) -> OrchestrateResult:
    """Compute the dispatch plan for *path*'s subtree. Read-only throughout.

    Never mutates a work item, a worktree or the manifest.

    `repo` defaults to `resolve_repo(layout, repo_name=repo_name)` -- the code
    repo `workspace.yaml` declares, not the layout's `repo_root`. An
    explicit `repo` still wins and **skips the config read entirely**: an
    argument is not a default. `repo_name` selects among several declared
    repositories and is ignored when `repo` is given.

    `provisions_worktrees` passes straight through to `plan()` -- see its
    docstring; this shell resolves no backend itself; that is a caller's job.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    rules = _routing_rules(layout)
    # Checked, not coerced. A silent `2` from a mistyped `max_parallel` is
    # indistinguishable from a deliberate `2`, and `_routing_rules` ten lines
    # above already refuses a hand-edited manifest for the same threat.
    max_parallel = checked_int(layout, "workflow.auto_drive.max_parallel")
    permission_mode = checked_str(layout, "workflow.auto_drive.permission_mode")
    supervise_merges = checked_bool(layout, "workflow.auto_drive.supervise_merges")

    repo_note: str | None = None
    resolved_repo = repo
    if resolved_repo is None:
        resolved_repo, repo_note = resolve_repo(layout, repo_name=repo_name)

    repo_path = str(resolved_repo) if resolved_repo is not None else None
    if resolved_repo is not None and _checkout_is_dirty(resolved_repo):
        # Withhold the checkout rather than dispatch into someone's edits.
        # `None` is the fully-supported "behave as before" value, so this
        # degrades to today's create-top-level cold start.
        repo_path = None

    computed = plan(
        items,
        path,
        pipeline=pipeline if pipeline is not None else pipeline_table(layout=layout),
        auto_drive=rules,
        max_parallel=max_parallel,
        permission_mode=permission_mode,
        supervise_merges=supervise_merges,
        live=live,
        worktree_exists=_stat_worktrees(items, repo_path),
        held_decisions=_held_decisions(items, bundle.root),
        provisions_worktrees=provisions_worktrees,
        workspace=str(layout.root),
        default_base=default_base(resolved_repo),
        repo_path=repo_path,
        worktree_inventory=_worktree_inventory(resolved_repo),
    )

    decisions = _resolve_decisions(items, bundle.root, path)
    return OrchestrateResult(
        plan=computed,
        decisions_owner_path=decisions.owner_path,
        decisions_ledger_path=decisions.ledger_path,
        open_decisions=decisions.open_,
        assumed_decisions=decisions.assumed,
        decision_counts=decisions.counts,
        warnings=computed.warnings + decisions.warnings + ((repo_note,) if repo_note else ()),
    )


__all__ = [
    "AUTO_DRIVE_KEY",
    "BLOCKED_KINDS",
    "DISPATCH_COMMAND",
    "DISPATCH_PHASES",
    "ROUTING_VOCABULARIES",
    "WORKSPACE_VAR",
    "BlockedItem",
    "OrchestratePlan",
    "OrchestrateResult",
    "PlannedAdvance",
    "branch_name",
    "plan",
    "run_orchestrate",
]
