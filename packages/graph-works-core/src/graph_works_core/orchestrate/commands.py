"""The dispatch plan: which stages run now, where, on what, saying what.

`plan()` is IO-free, mirroring `work_tracker_okf.workflow.route()`'s own split:
identical inputs produce an identical `OrchestratePlan`, and every config read,
`stat` and `git` call sits in `run_orchestrate` above it. Nothing here launches
a worker -- `subagents-io` ships the dispatch value types and no execution
backend, and choosing one is a separate work item.

The frontier walk generalizes `hierarchy.descend()` from pick-one-leaf to
collect-all, **reusing** its `child_gated_node` predicate, `PICK_ORDER` and
`WALK_DEPTH_CAP` rather than restating them. `--descend` and auto-drive
disagreeing about the same item is exactly the failure that reuse prevents, and
`test_orchestrate_plan.py` pins the agreement as a property.

Nothing Orca-shaped appears in this module. The four prompt lines are
vendor-neutral; the one place a vendor command may appear is a variant's
`prompt_tail`, which lives in workspace configuration (`pipeline.py`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

from config_io import PlainYamlStore, dotted
from okf_io import load_bundle
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from subagents_io.routing import resolve_model, validate_rules
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.compose import AdvanceOutcome, advance_and_stamp
from work_tracker_okf.hierarchy import PICK_ORDER, WALK_DEPTH_CAP, child_gated_node, nearest_epic
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.paths import decisions_ledger
from work_tracker_okf.results import write_results
from work_tracker_okf.vocabulary import (
    EFFORTS,
    PHASES,
    SLUG_PREFIXES,
    TERMINAL_STATUSES,
    TYPES,
)
from work_tracker_okf.workflow import RouteResult, route, state_for

from graph_works_core.workspace import provenance
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_int, checked_str
from graph_works_core.workspace.pipeline import PipelineEntry, pipeline_table
from graph_works_core.workspace.provenance import run_git
from graph_works_core.workspace.repos import resolve_repo

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

#: The branch path segment for an item whose `type` is unrecognized. Reachable
#: only through the root's fallback branch name: a candidate with a bad `type`
#: is already blocked by the router's own validation.
_UNKNOWN_TYPE_SEGMENT = "work"

#: The phases a stage can be dispatched at. `done` is terminal.
DISPATCH_PHASES: frozenset[str] = frozenset(PHASES - {"done"})

#: The plugin command a dispatched worker runs. Named rather than inlined so the
#: namespace has one grep-able home when the fork lands.
DISPATCH_COMMAND = "/graph-works:next"

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
        "decisions",
        "invalid",
    }
)


@dataclass(frozen=True, slots=True)
class PlannedAdvance:
    """A node with nothing to dispatch but a satisfied completion transition --
    an epic whose children are all terminal. The coordinator applies it itself."""

    slug: str
    reason: str
    worktree: str | None = None
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class BlockedItem:
    slug: str
    kind: str  # one of BLOCKED_KINDS
    reason: str


@dataclass(frozen=True, slots=True)
class OrchestratePlan:
    slug: str
    terminal: bool
    max_parallel: int
    permission_mode: str
    live: tuple[str, ...]
    slots_free: int
    dispatches: tuple[PlannedDispatch, ...]
    advances: tuple[PlannedAdvance, ...]
    blocked: tuple[BlockedItem, ...]
    warnings: tuple[str, ...]


def branch_name(slug: str, type_: str) -> str:
    """Slug to branch, deterministically.

    Strips the `YYYY-MM-DD-` prefix and, for an epic's non-epic child, the
    `epic-` filing marker; the type's slug segment becomes the path segment.

        2026-08-11-epic-graph-works-core          (Epic)    -> epic/graph-works-core
        2026-08-13-epic-feature-work-pipeline-x   (Feature) -> feature/work-pipeline-x
    """
    segment = SLUG_PREFIXES.get(type_) or _UNKNOWN_TYPE_SEGMENT
    stripped = _DATE_PREFIX_RE.sub("", slug)
    if segment != "epic" and stripped.startswith("epic-"):
        stripped = stripped[len("epic-") :]
    if stripped.startswith(f"{segment}-"):
        stripped = stripped[len(segment) + 1 :]
    return f"{segment}/{stripped}"


def _fork_branch(slug: str, type_: str, *, base: str, phase: str) -> str:
    """A fork target distinct from *base*.

    Suffixed with the phase rather than given a new path segment: git refuses
    `feature/x/execute` while `feature/x` exists. Applied once -- a suffixed
    name that still equalled its base would be `<derived>-<phase>` == `<derived>`,
    which cannot happen, so there is no loop here.
    """
    derived = branch_name(slug, type_)
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
        if item.parent:
            grouped.setdefault(item.parent, []).append(item)
    return grouped


def _frontier(
    items: Sequence[WorkItem], root: str, *, held_decisions: frozenset[str] = frozenset()
) -> tuple[list[tuple[WorkItem, RouteResult]], list[PlannedAdvance], list[BlockedItem]]:
    """Every actionable node at or below *root*. Cycle-safe and depth-capped.

    Recurses through a `child_gated_node` into its non-terminal children and
    routes every non-gated node it lands on. A terminal child is skipped
    silently: a resolved child is a satisfied input, not a blocked item.
    """
    by_slug = {item.slug: item for item in items}
    if root not in by_slug:
        return [], [], [BlockedItem(slug=root, kind="invalid", reason=f"unknown slug {root!r}")]

    children_of = _children_of(items)
    candidates: list[tuple[WorkItem, RouteResult]] = []
    advances: list[PlannedAdvance] = []
    blocked: list[BlockedItem] = []

    stack: list[tuple[str, int]] = [(root, 0)]
    visited = {root}
    while stack:
        slug, depth = stack.pop()
        node = by_slug.get(slug)
        if node is None:  # pragma: no cover -- every pushed slug is `root` (checked above) or
            # drawn from `children_of`, which groups the same `items` `by_slug` was built from
            blocked.append(BlockedItem(slug=slug, kind="invalid", reason=f"unknown slug {slug!r}"))
            continue
        if depth >= WALK_DEPTH_CAP:
            blocked.append(
                BlockedItem(
                    slug=slug,
                    kind="invalid",
                    reason=f"frontier walk depth cap ({WALK_DEPTH_CAP}) reached",
                )
            )
            continue
        children = children_of.get(slug, [])
        if child_gated_node(node, children):
            for child in children:
                if child.workflow_status in TERMINAL_STATUSES:
                    continue
                if child.slug in visited:
                    blocked.append(
                        BlockedItem(
                            slug=slug,
                            kind="invalid",
                            reason=f"parent cycle detected at {child.slug!r}",
                        )
                    )
                    continue
                visited.add(child.slug)
                stack.append((child.slug, depth + 1))
            continue
        state = state_for(items, slug, has_open_decision=slug in held_decisions)
        if state is None:  # pragma: no cover -- `slug` came out of `by_slug`
            continue
        result = route(state)
        if result.dispatch is not None and not result.blockers:
            candidates.append((node, result))
        elif result.on_complete is not None and not result.blockers:
            advances.append(PlannedAdvance(slug=slug, reason=result.reason))
        else:
            reason = result.blockers[0] if result.blockers else result.reason
            blocked.append(BlockedItem(slug=slug, kind=_classify(reason), reason=reason))
    return candidates, advances, blocked


def _sorted(candidates: list[tuple[WorkItem, RouteResult]]) -> list[tuple[WorkItem, RouteResult]]:
    return sorted(
        candidates,
        key=lambda pair: (PICK_ORDER.get(pair[0].workflow_status, 99), pair[0].opened, pair[0].slug),
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
        if node.slug in seen:
            continue
        seen.add(node.slug)
        out.append(node)
        stack.extend(children_of.get(node.slug, []))
    return out


def _epic_stamp(items: Sequence[WorkItem], root_item: WorkItem) -> tuple[str, str] | None:
    """`(path, branch)` of "the epic worktree" rules 2-3 reuse or fork against:
    the root's own stamp, falling back to the first stamped descendant in pick
    order. The fallback is reproducible from vault state but depends on which
    child happened to run first -- recorded as a risk in the spec, not fixed
    here."""
    if root_item.worktree and root_item.branch:
        return root_item.worktree, root_item.branch
    stamped = [item for item in _descendants(items, root_item.slug) if item.worktree and item.branch]
    if not stamped:
        return None
    stamped.sort(key=lambda item: (PICK_ORDER.get(item.workflow_status, 99), item.opened, item.slug))
    chosen = stamped[0]
    assert chosen.worktree is not None and chosen.branch is not None
    return chosen.worktree, chosen.branch


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
    repo_path: str | None,
) -> tuple[WorktreeAction | None, bool]:
    """The four rules, in order: reuse the item's own stamp; else reuse the epic
    worktree when unoccupied; else fork a child branch off it; else start one --
    in the main checkout when a repo path is known, top-level otherwise.

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
    """

    def _occupied(path: str) -> bool:
        # Exclude this item's own contribution. `_frontier` does not filter the
        # live set out of the candidates, so an item is routinely re-proposed
        # while genuinely still live -- many on_dispatch transitions are no-ops
        # between a dispatch and its own advance. Its own stamp must never read
        # as "held by someone else", or it forks off itself.
        return bool(live_worktree_owners.get(path, set()) - {item.slug}) or path in accepted_worktrees

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
                    branch=_fork_branch(item.slug, item.type, base=default_base, phase=phase),
                    base_branch=default_base,
                    exists=None,
                ),
                False,
            )
        if not _occupied(item.worktree):
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
                branch=_fork_branch(item.slug, item.type, base=item.branch, phase=phase),
                base_branch=item.branch,
                exists=None,
            ),
            False,
        )
    if epic_worktree_path is not None:
        if not _occupied(epic_worktree_path):
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
                branch=_fork_branch(item.slug, item.type, base=epic_branch, phase=phase),
                base_branch=epic_branch,
                exists=None,
            ),
            False,
        )
    if epic_worktree_claimed:
        return None, False
    if repo_path is not None:
        # Cold start, opportunistic: the common case is one solo session, so
        # run in the checkout that already exists rather than paying for a
        # worktree nobody else is contending for. Claims the epic slot exactly
        # as `create-top-level` does.
        return (
            WorktreeAction(
                action="main",
                path=repo_path,
                branch=default_base,
                base_branch=None,
                exists=worktree_exists.get(repo_path),
            ),
            True,
        )
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
    slug: str,
    key: str,
    phase: str,
    workspace: str,
    merge_target: str,
    tail: str | None,
    worktree: WorktreeAction,
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

    A `"main"` action appends one further line. It is hardcoded rather than
    workspace-authored because the `prompt_tail` table is keyed per pipeline
    *variant*, while this fact is per *dispatch* -- resolved from the worktree
    action -- so no variant tail can express it. It is appended after the tail
    so authored prose cannot swallow it, and it names no CLI flag because this
    package ships none.
    """
    lines = [
        f"Run {DISPATCH_COMMAND} {slug}.",
        f"{WORKSPACE_VAR}={workspace}",
        f"Dispatch key: {key}",
        "Send worker_done when the stage artifact is written and the item advanced.",
    ]
    if tail:
        for placeholder, value in (
            ("{slug}", slug),
            ("{key}", key),
            ("{phase}", phase),
            ("{workspace}", workspace),
            ("{merge_target}", merge_target),
        ):
            tail = tail.replace(placeholder, value)
        lines.append(tail)
    if worktree.action == "main":
        lines.append(
            f"This stage runs in the main checkout on `{worktree.branch}`; no dedicated worktree "
            f"exists. Record the worktree as `{worktree.path}` and the branch as `{worktree.branch}` "
            "explicitly when you advance — it cannot be detected from where you are."
        )
    return "\n".join(lines)


def plan(
    items: Sequence[WorkItem],
    root: str,
    *,
    pipeline: Mapping[str, PipelineEntry],
    auto_drive: Mapping[str, Any],
    max_parallel: int,
    permission_mode: str,
    live: tuple[str, ...] = (),
    worktree_exists: Mapping[str, bool | None] | None = None,
    held_decisions: frozenset[str] = frozenset(),
    provisions_worktrees: bool = True,
    workspace: str,
    default_base: str,
    repo_path: str | None = None,
) -> OrchestratePlan:
    """The whole dispatch plan for *root*'s subtree. Mutates nothing, reads nothing.

    `held_decisions` is the set of slugs currently named in an *open* decision
    (resolved by `run_orchestrate` from the owning epic's ledger, since a
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
    knows it. Supplying it turns cold start and repo-root-stamped items into
    `"main"` actions that run there directly instead of provisioning a
    worktree. `None` -- the default -- reproduces the previous behaviour
    exactly, so no existing caller changes until it opts in.
    """
    exists = worktree_exists or {}
    by_slug = {item.slug: item for item in items}
    warnings = [f"live key {key!r} matches no known item" for key in live if key.split("#", 1)[0] not in by_slug]

    root_item = by_slug.get(root)
    if root_item is not None and (root_item.workflow_status in TERMINAL_STATUSES or root_item.phase == "done"):
        return OrchestratePlan(
            slug=root,
            terminal=True,
            max_parallel=max_parallel,
            permission_mode=permission_mode,
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
        item = by_slug.get(key.split("#", 1)[0])
        if item is None:
            continue
        live_affects.update(item.affects)
        if item.worktree:
            live_worktree_owners.setdefault(item.worktree, set()).add(item.slug)

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
                    slug=item.slug,
                    kind="affects-overlap",
                    reason="declare affects to allow parallel dispatch",
                )
            )
            continue
        overlap = affects & (live_affects | accepted_affects)
        if overlap:
            blocked.append(
                BlockedItem(
                    slug=item.slug,
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
            blocked.append(BlockedItem(slug=item.slug, kind="capacity", reason="ready, but no worker slot free"))
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
                    slug=item.slug,
                    kind="relay-untailed",
                    reason=(
                        f"variant {variant!r} dispatches in relay mode with no prompt tail, so a "
                        "worker would fall into an interactive menu with nobody watching; set "
                        f"workflow.pipeline.{variant}.prompt_tail"
                    ),
                )
            )
            continue

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
            repo_path=repo_path,
        )
        if action is None:
            blocked.append(
                BlockedItem(
                    slug=item.slug,
                    kind="worktree-pending",
                    reason="epic worktree is being created by another dispatch in this plan",
                )
            )
            continue
        if action.path is None and not provisions_worktrees:
            blocked.append(
                BlockedItem(
                    slug=item.slug,
                    kind="worktree-unsupported",
                    reason=(
                        f"{action.action} needs a backend that provisions its own worktrees; "
                        "the target backend does not"
                    ),
                )
            )
            continue
        epic_worktree_claimed = epic_worktree_claimed or claimed_now
        if action.path:
            accepted_worktrees.add(action.path)

        merge_target = epic_branch if item.slug != root else default_base
        resolution = resolve_model(
            auto_drive,
            {"phase": phase, "kind": item.type, "effort": item.effort},
            default_key="phase",
        )
        key = f"{item.slug}#{phase}"
        dispatches.append(
            PlannedDispatch(
                key=key,
                slug=item.slug,
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
                    slug=item.slug,
                    key=key,
                    phase=phase,
                    workspace=workspace,
                    merge_target=merge_target,
                    tail=entry.prompt_tail,
                    worktree=action,
                ),
            )
        )

    return OrchestratePlan(
        slug=root,
        terminal=False,
        max_parallel=max_parallel,
        permission_mode=permission_mode,
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

#: What `default_base` answers when git cannot. The repo's own default branch is
#: the right answer and this is the fallback, not a preference.
FALLBACK_BASE = "main"


@dataclass(frozen=True, slots=True)
class OrchestrateResult:
    """The plan, plus the owning epic's open questions.

    The decision fields are empty for an item with no epic ancestor -- the
    lone-item case -- which is a shape, not an error.

    **The ledger is read twice per plan**: once by whatever consults it for the
    routing gate, once here. The two reads are not one atomic snapshot, so a
    decision answered between them leaves this response internally
    inconsistent. Deduplicating means threading parsed ledger state out of the
    frontier walk, and is deliberately not done.
    """

    plan: OrchestratePlan
    decisions_epic_slug: str | None = None
    decisions_ledger_path: str | None = None
    open_decisions: tuple[_decisions.Decision, ...] = ()
    assumed_decisions: tuple[_decisions.Decision, ...] = ()
    decision_counts: Mapping[str, int] = MappingProxyType({})
    warnings: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        return self.plan.slug

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


def default_base(repo: Path | None) -> str:
    """*repo*'s default branch, best-effort; `FALLBACK_BASE` on any failure.

    Uses `graph_works_core.workspace.provenance`'s git runner rather than a second
    subprocess helper -- one module in this package runs git.
    """
    if repo is None:
        return FALLBACK_BASE
    out = run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    if out is None or not out.strip():
        return FALLBACK_BASE
    return out.strip().rsplit("/", 1)[-1]


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


@dataclass(frozen=True, slots=True)
class _Decisions:
    """The owning epic's ledger, resolved. A type rather than a six-tuple: six
    same-shaped positional returns at a call site is a transposition waiting to
    happen, which is the reason `ResultsFacts` is a type too."""

    epic_slug: str | None = None
    ledger_path: str | None = None
    open_: tuple[_decisions.Decision, ...] = ()
    assumed: tuple[_decisions.Decision, ...] = ()
    counts: Mapping[str, int] = MappingProxyType({})
    warnings: tuple[str, ...] = ()


def _resolve_decisions(items: Sequence[WorkItem], bundle_root: Path, slug: str) -> _Decisions:
    """The owning epic's ledger: open and assumed entries plus the whole-epic
    counts.

    Empty throughout when *slug* has no epic ancestor -- auto-drive's lone-item
    case, a shape rather than an error. `nearest_epic` counts *slug* itself, so
    an epic resolves to its own ledger, and it is cycle-safe and depth-capped
    because a `parent` chain that closes on itself is a lint finding, not this
    walk's problem to diagnose.
    """
    epic = nearest_epic(items, slug)
    if epic is None:
        return _Decisions()
    archived = next((item.archived for item in items if item.slug == epic), False)
    ledger = decisions_ledger(epic, archived=archived).path(bundle_root)
    parsed = _decisions.load(ledger)  # an absent file reads as empty, never raises
    return _Decisions(
        epic_slug=epic,
        ledger_path=str(ledger),
        open_=tuple(_decisions.query(parsed.entries, status="open")),
        assumed=tuple(_decisions.query(parsed.entries, status="assumed")),
        counts=MappingProxyType(_decisions.counts(parsed.entries)),
        warnings=tuple(parsed.warnings),
    )


def _held_decisions(items: Sequence[WorkItem], bundle_root: Path) -> frozenset[str]:
    """Every slug currently named in an *open* decision's `affects`, across the
    whole item set -- what `plan()`'s `held_decisions` gates re-dispatch on.

    One ledger read per distinct epic, not per item: every item under the same
    epic shares the same ledger, and `_resolve_decisions` already pays this
    same one-read-per-epic cost for the root alone.
    """
    entries_by_epic: dict[str, tuple[_decisions.Decision, ...]] = {}
    held: set[str] = set()
    for item in items:
        epic = nearest_epic(items, item.slug)
        if epic is None:
            continue
        if epic not in entries_by_epic:
            archived = next((candidate.archived for candidate in items if candidate.slug == epic), False)
            ledger = decisions_ledger(epic, archived=archived).path(bundle_root)
            entries_by_epic[epic] = tuple(_decisions.load(ledger).entries)
        if _decisions.query(entries_by_epic[epic], status="open", affects=item.slug):
            held.add(item.slug)
    return frozenset(held)


def run_orchestrate(
    layout: WorkspaceLayout,
    slug: str,
    *,
    live: tuple[str, ...] = (),
    repo: Path | None = None,
    repo_name: str | None = None,
    pipeline: Mapping[str, PipelineEntry] | None = None,
    provisions_worktrees: bool = True,
) -> OrchestrateResult:
    """Compute the dispatch plan for *slug*'s subtree. Read-only throughout.

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
        slug,
        pipeline=pipeline if pipeline is not None else pipeline_table(layout=layout),
        auto_drive=rules,
        max_parallel=max_parallel,
        permission_mode=permission_mode,
        live=live,
        worktree_exists=_stat_worktrees(items, repo_path),
        held_decisions=_held_decisions(items, bundle.root),
        provisions_worktrees=provisions_worktrees,
        workspace=str(layout.root),
        default_base=default_base(resolved_repo),
        repo_path=repo_path,
    )

    decisions = _resolve_decisions(items, bundle.root, slug)
    return OrchestrateResult(
        plan=computed,
        decisions_epic_slug=decisions.epic_slug,
        decisions_ledger_path=decisions.ledger_path,
        open_decisions=decisions.open_,
        assumed_decisions=decisions.assumed,
        decision_counts=decisions.counts,
        warnings=computed.warnings + decisions.warnings + ((repo_note,) if repo_note else ()),
    )


#: The phases whose *completion* produces a results stub. A design or plan
#: stage leaves an artifact of its own; only the two that touch code leave a
#: commit range worth summarizing.
RESULTS_PHASES: frozenset[str] = frozenset({"execute", "finish"})


@dataclass(frozen=True, slots=True)
class StageAdvance:
    """What one stage completion did: the advance, plus its provenance.

    `results_path` and `pointer_path` are `None` for a dry run, for a refusal,
    when the stage produced nothing to capture (a non-code phase for
    `results_path`, a `done` landing for `pointer_path`), and whenever the
    corresponding capture degraded -- provenance never fails an advance, so
    "nothing was written" is a normal outcome, not an error.

    `repo_note` carries `resolve_repo`'s note: the reason no code repo was
    resolved. Named for exactly that and not for a general provenance log --
    when it is set, every git-derived field above it is `None` for one known
    reason rather than for an unknown one.
    """

    outcome: AdvanceOutcome
    results_path: Path | None = None
    pointer_path: Path | None = None
    repo_note: str | None = None

    @property
    def changed(self) -> bool:
        return self.outcome.changed


def run_stage_advance(
    layout: WorkspaceLayout,
    slug: str,
    *,
    today: date,
    effort: str | None = None,
    owner: str | None = None,
    resolved_in: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    cwd: Path | None = None,
    repo: Path | None = None,
    repo_name: str | None = None,
    start_sha: str | None = None,
    dry_run: bool = True,
) -> StageAdvance:
    """Complete one stage: advance the item and capture what the stage left.

    Worktree provenance has two paths. An explicit `worktree`/`branch` pair is
    the caller's own resolved `plan()` decision -- main-mode eviction,
    fork-child, cold start -- never a guess, so it is applied unconditionally
    and overwrites an already-valid recorded stamp. That is what lets an item
    be evicted out of a shared main checkout: a stamp that is still a live
    directory could otherwise never be repointed. Without the pair, provenance
    falls back to cwd inference under the original conservative guard -- stamp
    only when the recorded path is unset or gone -- so an advance run from an
    unrelated cwd cannot silently repoint a live item. Inference also cannot
    see the main checkout at all (`provenance.worktree_state` answers `None`
    when `--git-dir` and `--git-common-dir` agree), which is why a main-mode
    worker is told to pass the pair explicitly.

    `start_sha` is a **caller argument**, not a frontmatter field. The reference
    implementation stamped a `phase_started_commit` key; adding one here is a
    schema change this item's spec does not take (it takes exactly two
    provenance fields, 3.3), so the caller that knows where the phase started
    supplies it. Without it, no stub is written.

    `repo` defaults to `resolve_repo(layout, repo_name=repo_name)` rather than
    to the layout's `repo_root`: in a split topology -- the workspace and the
    code in different git repositories -- the walk-up resolves to the
    workspace's own repo, and both `worktree_state` and `results_facts` then
    degrade to `None` without a word. An explicit `repo` wins and skips the
    config read. `repo_name` selects among several declared repositories and
    is ignored when `repo` is given.

    `dry_run=True` is okf-io's writer default throughout this workspace: the
    call plans and writes nothing -- not the page, not the stub, not the
    pointer.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    item = next((candidate for candidate in items if candidate.slug == slug), None)
    old_phase = item.phase if item is not None else None
    repo_note: str | None = None
    resolved_repo = repo
    if resolved_repo is None:
        resolved_repo, repo_note = resolve_repo(layout, repo_name=repo_name)

    stamped_worktree: str | None = None
    stamped_branch: str | None = None
    if worktree and branch:
        stamped_worktree, stamped_branch = worktree, branch
    elif item is not None and resolved_repo is not None:
        recorded = item.worktree
        if not recorded or not Path(recorded).is_dir():
            detected = provenance.worktree_state(cwd or Path.cwd(), resolved_repo)
            if detected is not None:
                stamped_worktree, stamped_branch = detected

    outcome = advance_and_stamp(
        bundle,
        slug,
        today=today,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        worktree=stamped_worktree,
        branch=stamped_branch,
        dry_run=dry_run,
    )
    if dry_run or outcome.plan.refusal is not None or outcome.plan.transition is None:
        return StageAdvance(outcome=outcome, repo_note=repo_note)

    new_phase = outcome.plan.transition.phase or old_phase

    results_path: Path | None = None
    facts_root = _facts_root(item, stamped_worktree, resolved_repo)
    if (
        facts_root is not None
        and start_sha
        and item is not None
        and old_phase in RESULTS_PHASES
        and new_phase != old_phase
    ):
        facts = provenance.results_facts(
            facts_root, phase=old_phase, start_sha=start_sha, paths=item.affects, opened=item.opened
        )
        if facts is not None:
            results_path = write_results(bundle.root, slug, facts, archived=item.archived if item else False)

    pointer_path: Path | None = None
    if new_phase is not None and new_phase != "done":
        pointer_path = provenance.write_active_work(layout, slug, new_phase, updated=today.isoformat())
    return StageAdvance(outcome=outcome, results_path=results_path, pointer_path=pointer_path, repo_note=repo_note)


def _facts_root(item: WorkItem | None, worktree: str | None, repo: Path | None) -> Path | None:
    """Where the stage's commits actually landed: the worktree this call
    detected, then the item's recorded one, then the repo. A stub gathered from
    the main checkout when the work happened in a worktree is a stub of the
    wrong range."""
    for candidate in (worktree, item.worktree if item is not None else None):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return repo


__all__ = [
    "AUTO_DRIVE_KEY",
    "BLOCKED_KINDS",
    "DISPATCH_COMMAND",
    "DISPATCH_PHASES",
    "FALLBACK_BASE",
    "RESULTS_PHASES",
    "ROUTING_VOCABULARIES",
    "WORKSPACE_VAR",
    "BlockedItem",
    "OrchestratePlan",
    "OrchestrateResult",
    "PlannedAdvance",
    "StageAdvance",
    "branch_name",
    "default_base",
    "plan",
    "run_orchestrate",
    "run_stage_advance",
]
