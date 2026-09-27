"""Seeded claim-gate properties for planned and live dispatches."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate.claims import CODE_WRITE_PHASES, Claim, claims_for, conflicts
from graph_works_core.workspace.finish import FinishPlan, FinishTarget
from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from test_orchestrate_plan import _branch_tail_rules, _item
from work_tracker_okf.items import Stamp, WorkItem

ROOT = "work/epic-prop"
PATHS = (
    "packages/a",
    "packages/a/src",
    "packages/a/src/x",
    "packages/ab",
    "packages/b",
    "packages/b/tests",
    "docs",
)
REPOS = ("one", "two")
SEEDS = range(200)
RELAY = _branch_tail_rules()


def _context(name: str, children: list[tuple[str, str]]) -> RepositoryContext:
    anchor = (f"/wt/epic-{name}", f"epic/{name}")
    worktrees = [anchor, *children]
    return RepositoryContext(
        f"git-{name}",
        f"/repo/{name}",
        "main",
        True,
        {branch: (path,) for path, branch in worktrees},
        {path: True for path, _ in worktrees},
        True,
        checkout_usable_by_path={path: True for path, _ in worktrees},
        branch_tips={branch: hashlib.sha1(branch.encode()).hexdigest() for _, branch in worktrees} | {"main": "0" * 40},
    )


def _scenario(seed: int):
    rng = random.Random(seed)
    count = rng.randint(2, 5)
    paths = [f"{ROOT}/children/feature-{index}" for index in range(count)]
    repo_of = {path: rng.choice(REPOS) for path in paths}
    phase_of = {path: rng.choice(("plan", "execute")) for path in paths}
    stamp_of = {path: (f"/wt/c{index}", f"feature/c{index}") for index, path in enumerate(paths)}
    children: list[WorkItem] = [
        _item(
            path,
            phase=phase_of[path],
            has_plan_artifact=True,
            affects=tuple(rng.sample(PATHS, rng.randint(1, 2))),
            worktree=stamp_of[path][0],
            branch=stamp_of[path][1],
            opened=f"2026-08-{index + 1:02d}",
        )
        for index, path in enumerate(paths)
    ]
    root = _item(
        ROOT,
        type="Epic",
        phase="execute",
        child_paths=tuple(paths),
        affects=("unrelated/root",),
        worktree="/wt/epic-one",
        branch="epic/one",
        repo_stamps={"two": Stamp("/wt/epic-two", "epic/two")},
    )
    repos = {ROOT: ItemRepo("one", Path("/repo/one"), "frontmatter")}
    repos.update({path: ItemRepo(repo_of[path], Path(f"/repo/{repo_of[path]}"), "frontmatter") for path in paths})
    contexts = {f"git-{name}": _context(name, [stamp_of[p] for p in paths if repo_of[p] == name]) for name in REPOS}
    live_paths = [path for path in paths if rng.random() < 0.3]
    live = tuple(orchestrate.session_name(path, "Feature", phase_of[path]) for path in live_paths)
    return (root, *children), repos, contexts, live, set(live_paths), repo_of


def _claims(item: WorkItem, repo: str, phase: str | None = None) -> tuple[Claim, ...]:
    """Use D-002's deliberate stage policy: readers reserve no affects claims.

    Live keys are named from `phase_of`, which is also the page phase, so
    `item.phase` is the live phase here.
    """
    return claims_for(item.path, phase or item.phase or "", f"git-{repo}", item.affects)


def _plan(seed: int):
    items, repos, contexts, live, live_paths, repo_of = _scenario(seed)
    result = orchestrate.plan(
        items,
        ROOT,
        dispatch_rules=(),
        max_parallel=len(items) + len(live),
        max_attend=len(items) + len(live),
        live=live,
        workspace="/ws",
        default_base="main",
        item_repos=repos,
        repo_contexts=contexts,
    )
    return items, result, live_paths, repo_of


def _finish_scenario(seed: int):
    rng = random.Random(seed)
    count = rng.randint(2, 5)
    paths = [f"{ROOT}/children/feature-finish-{index}" for index in range(count)]
    repo_of = {path: rng.choice(REPOS) for path in paths}
    stamps = {path: (f"/wt/finish-{index}", f"feature/finish-{index}") for index, path in enumerate(paths)}
    targets = {path: rng.choice(("/wt/epic-one", "/wt/epic-two", None)) for path in paths}
    root = _item(
        ROOT,
        type="Epic",
        phase="execute",
        child_paths=tuple(paths),
        affects=("unrelated/root",),
        worktree="/wt/epic-one",
        branch="epic/one",
        repo_stamps={"two": Stamp("/wt/epic-two", "epic/two")},
    )
    children = [
        _item(
            path,
            phase="finish",
            work_status="in-progress",
            affects=(f"packages/c{index}",),
            worktree=stamps[path][0],
            branch=stamps[path][1],
            opened=f"2026-08-{index + 1:02d}",
        )
        for index, path in enumerate(paths)
    ]
    repos = {ROOT: ItemRepo("one", Path("/repo/one"), "frontmatter")}
    repos.update({path: ItemRepo(repo_of[path], Path(f"/repo/{repo_of[path]}"), "frontmatter") for path in paths})
    contexts = {
        f"git-{name}": _context(name, [stamps[path] for path in paths if repo_of[path] == name]) for name in REPOS
    }
    finish_plans = {
        path: FinishPlan(
            (FinishTarget(repos[path], stamps[path][0], stamps[path][1], f"epic/{repo_of[path]}", targets[path]),),
            (),
        )
        for path in paths
    }
    live = tuple(orchestrate.session_name(path, "Feature", "finish") for path in paths if rng.random() < 0.3)
    return (root, *children), contexts, repos, finish_plans, live


@pytest.mark.parametrize("seed", SEEDS)
def test_claim_gate_property(seed: int) -> None:
    """D-002 admits readers beside writers but keeps writer claims disjoint."""
    items, result, live_paths, repo_of = _plan(seed)
    by_path = {item.path: item for item in items}
    dispatched = [by_path[d.slug] for d in result.dispatches]
    new = [c for item in dispatched for c in _claims(item, repo_of[item.path])]
    held_live = [c for path in live_paths for c in _claims(by_path[path], repo_of[path])]

    accepted = []
    for dispatch in result.dispatches:
        candidate = _claims(by_path[dispatch.slug], repo_of[dispatch.slug])
        assert not [(a, b) for a in candidate for b in (*held_live, *accepted) if conflicts(a, b)]
        if dispatch.phase in orchestrate.READ_ONLY_PHASES:
            assert dispatch.worktree.action == orchestrate.READER_ACTION
            assert dispatch.worktree.path is None
        else:
            accepted.extend(candidate)
    assert not [(a, b) for a in new for b in new if conflicts(a, b)]
    assert not [(a, b) for a in new for b in held_live if conflicts(a, b)]
    assert not live_paths & {d.slug for d in result.dispatches}
    assert not live_paths & {b.path for b in result.blocked}
    assert {b.kind for b in result.blocked} <= orchestrate.BLOCKED_KINDS
    for blocked in result.blocked:
        if blocked.kind == "affects-overlap":
            mine = _claims(by_path[blocked.path], repo_of[blocked.path])
            assert any(conflicts(a, b) for a in mine for b in (*new, *held_live)), (seed, blocked)
        if by_path[blocked.path].phase not in CODE_WRITE_PHASES:
            assert blocked.kind != "affects-overlap", (seed, blocked)


@pytest.mark.parametrize("seed", SEEDS)
def test_finish_target_property(seed: int) -> None:
    items, contexts, repos, finish_plans, live = _finish_scenario(seed)
    result = orchestrate.plan(
        items,
        ROOT,
        dispatch_rules=RELAY,
        max_parallel=5,
        workspace="/ws",
        default_base="main",
        live=live,
        item_repos=repos,
        repo_contexts=contexts,
        finish_plans=finish_plans,
    )
    by_session, _ = orchestrate.session_index(items)
    live_paths = {by_session[key].path for key in live}
    held: dict[str, str] = {}
    for path in live_paths:
        for target in finish_plans[path].targets:
            if target.target_worktree is not None:
                held.setdefault(target.target_worktree, path)

    for dispatch in result.dispatches:
        for target in finish_plans[dispatch.slug].targets:
            if target.target_worktree is not None:
                assert target.target_worktree not in held, (seed, dispatch.slug, held)
                held[target.target_worktree] = dispatch.slug

    for blocked in result.blocked:
        if blocked.kind == "worktree-pending":
            assert any(
                target.target_worktree is not None
                and (owner := held.get(target.target_worktree)) is not None
                and owner != blocked.path
                and f"{target.target_worktree} held by {owner}" in blocked.reason
                for target in finish_plans[blocked.path].targets
            ), (seed, blocked, held)
    assert not live_paths & {blocked.path for blocked in result.blocked}


def test_claim_gate_property_is_not_vacuous() -> None:
    """D-002 deliberately admits overlapping readers; count that case."""
    multi = overlap = reader_beside_writer = finish_multi = target_overlap = 0
    for seed in SEEDS:
        items, result, live_paths, repo_of = _plan(seed)
        by_path = {item.path: item for item in items}
        writers = [
            c
            for path in (*live_paths, *(d.slug for d in result.dispatches))
            for c in _claims(by_path[path], repo_of[path])
        ]
        reader_beside_writer += any(
            dispatch.phase not in CODE_WRITE_PHASES
            and any(
                conflicts(a, b)
                for a in _claims(by_path[dispatch.slug], repo_of[dispatch.slug], "execute")
                for b in writers
            )
            for dispatch in result.dispatches
        )
        multi += len(result.dispatches) >= 2
        overlap += any(b.kind == "affects-overlap" for b in result.blocked)
        items, contexts, repos, finish_plans, live = _finish_scenario(seed)
        finish_result = orchestrate.plan(
            items,
            ROOT,
            dispatch_rules=RELAY,
            max_parallel=5,
            workspace="/ws",
            default_base="main",
            live=live,
            item_repos=repos,
            repo_contexts=contexts,
            finish_plans=finish_plans,
        )
        finish_multi += len(finish_result.dispatches) >= 2
        target_overlap += any(b.kind == "worktree-pending" for b in finish_result.blocked)
    assert multi > 0 and overlap > 0 and reader_beside_writer > 0, (multi, overlap, reader_beside_writer)
    assert finish_multi > 0 and target_overlap > 0, (finish_multi, target_overlap)
