"""Design/plan stages read a pinned, detached commit -- never the mutable epic worktree."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from test_orchestrate_plan import _item, _plan
from work_tracker_okf.items import Stamp

ROOT = "work/epic-r"
A, B, C = (f"{ROOT}/children/feature-{n}" for n in "abc")
TIP_EPIC, TIP_MAIN = "e" * 40, "0" * 40


def _context(*, dirty_epic: bool = False, tips=True) -> RepositoryContext:
    return RepositoryContext(
        "git-code",
        "/repo",
        "main",
        True,
        {"epic/r": ("/epic",), "main": ("/repo",), "feature/c": ("/wt/c",)},
        {"/epic": True, "/repo": True, "/wt/c": True},
        True,
        checkout_usable_by_path={"/epic": not dirty_epic, "/repo": True, "/wt/c": True},
        branches=frozenset({"epic/r", "main", "feature/c"}),
        branch_tips={"epic/r": TIP_EPIC, "main": TIP_MAIN, "feature/c": "c" * 40} if tips else {},
        branch_tips_known=tips,
    )


def _repos(*paths):
    return {p: ItemRepo("code", Path("/repo"), "frontmatter") for p in paths}


def _items(root_worktree="/epic"):
    return (
        _item(
            ROOT,
            type="Epic",
            phase="execute",
            child_paths=(A, B, C),
            worktree=root_worktree,
            branch="epic/r" if root_worktree else None,
        ),
        _item(A, phase="design", affects=("packages/a",)),
        _item(B, phase="plan", affects=("packages/b",)),
        _item(
            C, phase="execute", work_status="in-progress", affects=("packages/c",), worktree="/wt/c", branch="feature/c"
        ),
    )


def test_descendant_readers_pin_the_epic_tip_and_never_the_epic_path() -> None:
    result = _plan(
        _items(), ROOT, max_parallel=5, item_repos=_repos(ROOT, A, B, C), repo_contexts={"git-code": _context()}
    )
    by_slug = {d.slug: d for d in result.dispatches}
    for reader in (A, B):
        action = by_slug[reader].worktree
        assert action.action == orchestrate.READER_ACTION
        assert (action.path, action.branch, action.base_branch, action.start_sha) == (None, None, "epic/r", TIP_EPIC)
        assert action.parent_path == "/epic"
        assert f"at {TIP_EPIC}" in by_slug[reader].prompt
    assert by_slug[C].worktree.path == "/wt/c"  # the writer is unaffected by readers


def test_readers_reserve_nothing() -> None:
    items = (
        *_items()[:3],
        _item(
            C, phase="execute", work_status="in-progress", affects=("packages/c",), worktree="/epic", branch="epic/r"
        ),
    )
    result = _plan(
        items, ROOT, max_parallel=5, item_repos=_repos(ROOT, A, B, C), repo_contexts={"git-code": _context()}
    )
    assert {d.slug for d in result.dispatches} >= {A, B}


def test_reader_is_never_placed_in_a_dirty_anchor() -> None:
    result = _plan(
        _items(),
        ROOT,
        max_parallel=5,
        item_repos=_repos(ROOT, A, B, C),
        repo_contexts={"git-code": _context(dirty_epic=True)},
    )
    for d in result.dispatches:
        if d.phase in orchestrate.READ_ONLY_PHASES:
            assert d.worktree.action == orchestrate.READER_ACTION and d.worktree.path is None
    assert all(b.kind == "worktree-unprovable" for b in result.blocked if b.path in (A, B))


def test_missing_tip_evidence_refuses_readers() -> None:
    result = _plan(
        _items(),
        ROOT,
        max_parallel=5,
        item_repos=_repos(ROOT, A, B, C),
        repo_contexts={"git-code": _context(tips=False)},
    )
    assert {(b.path, b.kind) for b in result.blocked} >= {(A, "worktree-unprovable"), (B, "worktree-unprovable")}
    assert all(d.phase not in orchestrate.READ_ONLY_PHASES for d in result.dispatches)


@pytest.mark.parametrize("phase", sorted(orchestrate.READ_ONLY_PHASES))
def test_root_reader_with_own_stamp_pins_its_branch_tip(phase: str) -> None:
    items = (_item(ROOT, type="Epic", phase=phase, worktree="/epic", branch="epic/r"),)
    result = _plan(items, ROOT, item_repos=_repos(ROOT), repo_contexts={"git-code": _context()})
    (dispatch,) = result.dispatches
    assert (dispatch.worktree.action, dispatch.worktree.start_sha, dispatch.worktree.base_branch) == (
        orchestrate.READER_ACTION,
        TIP_EPIC,
        "epic/r",
    )


@pytest.mark.parametrize("phase", sorted(orchestrate.READ_ONLY_PHASES))
def test_unanchored_root_reader_pins_default_base_and_mints_nothing(phase: str) -> None:
    items = (_item(ROOT, type="Epic", phase=phase),)
    result = _plan(items, ROOT, item_repos=_repos(ROOT), repo_contexts={"git-code": _context()})
    (dispatch,) = result.dispatches
    assert (dispatch.worktree.action, dispatch.worktree.base_branch, dispatch.worktree.start_sha) == (
        orchestrate.READER_ACTION,
        "main",
        TIP_MAIN,
    )
    assert dispatch.worktree.parent_path is None  # trunk checkout: no lineage
    assert result.preparations == ()


def test_descendant_with_old_own_stamp_still_pins_the_anchor() -> None:
    items = list(_items())
    items[1] = _item(A, phase="design", affects=("packages/a",), worktree="/epic", branch="epic/r")
    result = _plan(
        tuple(items), ROOT, max_parallel=5, item_repos=_repos(ROOT, A, B, C), repo_contexts={"git-code": _context()}
    )
    action = next(d for d in result.dispatches if d.slug == A).worktree
    assert action.action == orchestrate.READER_ACTION and action.start_sha == TIP_EPIC


def test_reader_needs_a_provisioning_backend() -> None:
    result = _plan(
        _items(),
        ROOT,
        max_parallel=5,
        provisions_worktrees=False,
        item_repos=_repos(ROOT, A, B, C),
        repo_contexts={"git-code": _context()},
    )
    assert {(b.path, b.kind) for b in result.blocked} >= {(A, "worktree-unsupported"), (B, "worktree-unsupported")}


def test_legacy_path_uses_branch_tips_argument() -> None:
    items = _items()
    pinned = _plan(
        items,
        ROOT,
        max_parallel=5,
        branch_tips={"epic/r": TIP_EPIC},
        worktree_inventory={"epic/r": "/epic"},
        worktree_exists={"/epic": True},
    )
    assert next(d for d in pinned.dispatches if d.slug == A).worktree.start_sha == TIP_EPIC
    refused = _plan(items, ROOT, max_parallel=5, branch_tips=None)
    assert (A, "worktree-unprovable") in {(b.path, b.kind) for b in refused.blocked}


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("sha", [None, "", "e" * 7, "g" * 40, "e" * 39, "e" * 41, "e" * 63, "e" * 65, "e" * 40 + "\n"])
def test_reader_refuses_missing_or_non_full_commit_ids(legacy, sha):
    tips = {} if sha is None else {"epic/r": sha}
    kwargs = (
        dict(branch_tips=tips, worktree_inventory={"epic/r": "/epic"}, worktree_exists={"/epic": True})
        if legacy
        else dict(item_repos=_repos(ROOT, A, B, C), repo_contexts={"git-code": replace(_context(), branch_tips=tips)})
    )
    result = _plan(_items(), ROOT, max_parallel=5, **kwargs)
    assert {(b.path, b.kind) for b in result.blocked} >= {(A, "worktree-unprovable"), (B, "worktree-unprovable")}


@pytest.mark.parametrize("sha", ["a" * 40, "a" * 64])
def test_full_git_object_id_lengths_are_supported(sha):
    result = _plan(
        _items(),
        ROOT,
        max_parallel=5,
        item_repos=_repos(ROOT, A, B, C),
        repo_contexts={"git-code": replace(_context(), branch_tips={"epic/r": sha})},
    )
    assert next(d for d in result.dispatches if d.slug == A).worktree.start_sha == sha


@pytest.mark.parametrize("legacy", [False, True])
def test_descendant_never_falls_back_to_default_or_its_own_old_stamp(legacy):
    items = list(_items(root_worktree=None))
    items[1] = replace(items[1], worktree="/wt/c", branch="feature/c")
    kwargs = (
        dict(
            branch_tips={"main": TIP_MAIN, "feature/c": "c" * 40},
            worktree_inventory={"main": "/repo", "feature/c": "/wt/c"},
            worktree_exists={"/repo": True, "/wt/c": True},
        )
        if legacy
        else dict(item_repos=_repos(ROOT, A, B, C), repo_contexts={"git-code": _context()})
    )
    result = _plan(tuple(items), ROOT, max_parallel=5, **kwargs)
    assert not {A, B} & {d.slug for d in result.dispatches}
    assert {(b.path, b.kind) for b in result.blocked} >= {(A, "worktree-unprovable"), (B, "worktree-unprovable")}


@pytest.mark.parametrize(
    "change",
    [
        {"identity_known": False},
        {"inventory_known": False},
        {"inventory": {"epic/r": ("/epic", "/duplicate")}},
        {"inventory": {"epic/r": ("/other",)}},
        {"path_exists": {}},
    ],
)
def test_unprovable_or_ambiguous_anchor_evidence_refuses(change):
    result = _plan(
        _items(),
        ROOT,
        max_parallel=5,
        item_repos=_repos(ROOT, A, B, C),
        repo_contexts={"git-code": replace(_context(), **change)},
    )
    assert not {A, B} & {d.slug for d in result.dispatches}
    assert {b.path for b in result.blocked if b.kind in {"worktree-unprovable", "worktree-ambiguous"}} >= {A, B}


@pytest.mark.parametrize("legacy", [False, True])
def test_nested_reader_uses_nearest_owner_not_root_or_own_stamp(legacy):
    leaf = f"{A}/children/task-leaf"
    items = (
        _items()[0],
        replace(_items()[1], phase="execute", child_paths=(leaf,), worktree="/nested", branch="feature/a"),
        _item(leaf, phase="design", worktree="/old", branch="old"),
    )
    tips = {"epic/r": TIP_EPIC, "feature/a": "a" * 40}
    kwargs = (
        dict(
            branch_tips=tips,
            worktree_inventory={"epic/r": "/epic", "feature/a": "/nested"},
            worktree_exists={"/epic": True, "/nested": True},
        )
        if legacy
        else dict(
            item_repos=_repos(ROOT, A, leaf),
            repo_contexts={
                "git-code": replace(
                    _context(),
                    inventory={"epic/r": ("/epic",), "feature/a": ("/nested",)},
                    path_exists={"/epic": True, "/nested": True},
                    branch_tips=tips,
                )
            },
        )
    )
    result = _plan(items, ROOT, **kwargs)
    (dispatch,) = result.dispatches
    assert (dispatch.worktree.base_branch, dispatch.worktree.start_sha, dispatch.worktree.parent_path) == (
        "feature/a",
        "a" * 40,
        "/nested",
    )


def test_foreign_reader_uses_repository_stamp_even_when_anchor_dirty():
    items = list(_items())
    items[0] = replace(items[0], repo_stamps={"foreign": Stamp("/foreign-anchor", "epic/foreign")})
    repos = _repos(ROOT, A, B, C)
    repos[A] = ItemRepo("foreign", Path("/foreign"), "frontmatter")
    foreign = RepositoryContext(
        "git-foreign",
        "/foreign",
        "trunk",
        False,
        {"epic/foreign": ("/foreign-anchor",)},
        {"/foreign-anchor": True},
        True,
        branch_tips={"epic/foreign": "f" * 40},
    )
    result = _plan(
        tuple(items),
        ROOT,
        max_parallel=5,
        item_repos=repos,
        repo_contexts={"git-code": _context(), "git-foreign": foreign},
    )
    action = next(d for d in result.dispatches if d.slug == A).worktree
    assert (action.base_branch, action.start_sha, action.parent_path) == ("epic/foreign", "f" * 40, "/foreign-anchor")


def test_readers_do_not_add_accepted_code_claims_and_prompt_cites_baseline():
    items = list(_items())
    items[2] = replace(items[2], affects=items[1].affects)
    items[3] = replace(items[3], affects=items[1].affects, work_status="open")
    result = _plan(
        tuple(items), ROOT, max_parallel=5, item_repos=_repos(ROOT, A, B, C), repo_contexts={"git-code": _context()}
    )
    assert {d.slug for d in result.dispatches} == {A, B, C}
    for dispatch in result.dispatches[:2]:
        assert dispatch.prompt.splitlines()[-3] == (
            f"Reader baseline: this stage reads a detached checkout of epic/r at {TIP_EPIC}; "
            "cite that commit in the stage artifact and do not commit in this checkout."
        )
        assert dispatch.prompt.splitlines()[-1] == orchestrate.WORKER_PLACEMENT_LINE
        assert not dispatch.auto_merge


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "stamp",
    [
        {"worktree": "/epic"},
        {"branch": "epic/r"},
        {"invalid_optional_fields": ("worktree", "branch")},
    ],
)
def test_root_with_invalid_stamp_cannot_fall_back_to_default(legacy, stamp):
    item = _item(ROOT, type="Epic", phase="design", **stamp)
    kwargs = (
        dict(branch_tips={"main": TIP_MAIN})
        if legacy
        else dict(item_repos=_repos(ROOT), repo_contexts={"git-code": _context()})
    )
    result = _plan((item,), ROOT, **kwargs)
    assert not result.dispatches
    assert {(b.path, b.kind) for b in result.blocked} == {(ROOT, "worktree-unprovable")}


@pytest.mark.parametrize("phase", ["design", "plan"])
@pytest.mark.parametrize("stamped", [False, True])
def test_legacy_root_pins_baseline_and_omits_trunk_lineage(phase, stamped):
    item = _item(ROOT, type="Epic", phase=phase, **({"worktree": "/repo", "branch": "main"} if stamped else {}))
    result = _plan(
        (item,),
        ROOT,
        branch_tips={"main": TIP_MAIN},
        code_repo="/repo",
        worktree_inventory={"main": "/repo"},
        worktree_exists={"/repo": True},
    )
    (dispatch,) = result.dispatches
    assert (
        dispatch.worktree.action,
        dispatch.worktree.base_branch,
        dispatch.worktree.start_sha,
        dispatch.worktree.parent_path,
    ) == ("pin-detached", "main", TIP_MAIN, None)
    assert not result.preparations


def test_foreign_reader_cannot_use_owners_scalar_stamp_or_default_base():
    repos = _repos(ROOT, A, B, C)
    repos[A] = ItemRepo("foreign", Path("/foreign"), "frontmatter")
    foreign = replace(_context(), identity="git-foreign", path="/foreign")
    result = _plan(
        _items(), ROOT, max_parallel=5, item_repos=repos, repo_contexts={"git-code": _context(), "git-foreign": foreign}
    )
    assert A not in {d.slug for d in result.dispatches}
    assert (A, "worktree-unprovable") in {(b.path, b.kind) for b in result.blocked}


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_nearest_anchor_cannot_fall_back_to_outer_anchor(legacy):
    leaf = f"{A}/children/task-leaf"
    items = (_items()[0], replace(_items()[1], phase="execute", child_paths=(leaf,)), _item(leaf, phase="design"))
    kwargs = (
        dict(
            branch_tips={"epic/r": TIP_EPIC, "main": TIP_MAIN},
            worktree_inventory={"epic/r": "/epic"},
            worktree_exists={"/epic": True},
        )
        if legacy
        else dict(item_repos=_repos(ROOT, A, leaf), repo_contexts={"git-code": _context()})
    )
    result = _plan(items, ROOT, **kwargs)
    assert not result.dispatches
    assert (leaf, "worktree-unprovable") in {(b.path, b.kind) for b in result.blocked}


@pytest.mark.parametrize("live_phase", ["design", "plan", "execute", "finish"])
@pytest.mark.parametrize("reader_path", [ROOT, A], ids=["root", "descendant"])
@pytest.mark.parametrize("foreign_stamp", [False, True], ids=["scalar", "foreign"])
@pytest.mark.parametrize("candidate_phase", ["execute", "finish"])
def test_live_dispatch_phase_controls_historical_stamp_occupancy(
    live_phase, reader_path, foreign_stamp, candidate_phase
):
    from graph_works_core.workspace.finish import FinishPlan, FinishTarget
    from test_orchestrate_plan import _branch_tail_rules

    # The item has advanced already; only the live key describes its running worker.
    root = replace(_items()[0], child_paths=(A, C), affects=("packages/root",))
    reader = _item(reader_path, phase="execute", affects=("packages/reader",)) if reader_path == A else root
    repos = _repos(ROOT, A, C)
    contexts = {"git-code": _context()}
    if foreign_stamp:
        reader = replace(
            reader,
            worktree="/foreign-epic",
            branch="epic/foreign",
            repo_stamps={"code": Stamp("/epic", "epic/r")},
        )
        repos[reader_path] = ItemRepo("foreign", Path("/foreign"), "frontmatter")
        contexts["git-foreign"] = RepositoryContext(
            "git-foreign",
            "/foreign",
            "main",
            True,
            {"epic/foreign": ("/foreign-epic",)},
            {"/foreign-epic": True},
            True,
            checkout_usable_by_path={"/foreign-epic": True},
        )
    else:
        reader = replace(reader, worktree="/epic", branch="epic/r")
    candidate = _item(
        C,
        phase=candidate_phase,
        work_status="in-progress",
        affects=("packages/c",),
        worktree="/epic" if candidate_phase == "execute" else "/wt/c",
        branch="epic/r" if candidate_phase == "execute" else "feature/c",
    )
    items = (reader, candidate) if reader_path == ROOT else (root, reader, candidate)
    # Even a newly computed finish plan must not turn a still-live reader into a writer.
    plans = {
        reader_path: FinishPlan((FinishTarget(repos[C], "/epic", "epic/r", "main", "/repo"),), ()),
    }
    if candidate_phase == "finish":
        plans[C] = FinishPlan((FinishTarget(repos[C], "/wt/c", "feature/c", "epic/r", "/epic"),), ())
    result = _plan(
        items,
        ROOT,
        live=(orchestrate.session_name(reader_path, reader.type, live_phase),),
        item_repos=repos,
        repo_contexts=contexts,
        finish_plans=plans,
        dispatch_rules=_branch_tail_rules(),
        max_parallel=3,
    )
    if live_phase in ("design", "plan"):
        assert len(result.dispatches) == 1, result.blocked
        (dispatch,) = result.dispatches
        assert dispatch.slug == C
        assert (dispatch.worktree.action, dispatch.worktree.path) == (
            "reuse",
            "/epic" if candidate_phase == "execute" else "/wt/c",
        )
        assert not result.blocked
    elif candidate_phase == "finish":
        assert not result.dispatches
        assert [(b.path, b.kind) for b in result.blocked] == [(C, "worktree-pending")]
        assert f"/epic held by {reader_path}" in result.blocked[0].reason
    else:
        (dispatch,) = result.dispatches
        assert dispatch.slug == C
        assert dispatch.worktree.action == "fork-child"
        assert dispatch.worktree.path is None


@pytest.mark.parametrize("live_phase", ["design", "plan", "execute"])
@pytest.mark.parametrize("stamp_kind", ["none", "matched", "unmatched"])
def test_live_key_controls_code_affects_claims(live_phase, stamp_kind):
    """The live key owns phase: readers hold no affects claims despite an advanced
    page or foreign stamp, while execute claims or fails closed on an unknown stamp (D-002)."""
    reader = replace(_items()[1], phase="execute", worktree="/epic", branch="epic/r")
    repos = _repos(ROOT, A, C)
    if stamp_kind != "none":
        stamp_path = "/unmatched-epic" if stamp_kind == "unmatched" else "/epic"
        reader = replace(reader, worktree="/foreign", branch="main", repo_stamps={"code": Stamp(stamp_path, "epic/r")})
        repos[A] = ItemRepo("foreign", Path("/foreign"), "frontmatter")
    contexts = {
        "git-code": _context(),
        "git-foreign": RepositoryContext("git-foreign", "/foreign", "main", True, {}, {}, True),
    }
    result = _plan(
        (_items()[0], reader, replace(_items()[3], affects=reader.affects)),
        ROOT,
        live=(orchestrate.session_name(A, "Feature", live_phase),),
        item_repos=repos,
        repo_contexts=contexts,
    )
    if live_phase == "execute":
        assert not result.dispatches
        kind = "worktree-unprovable" if stamp_kind == "unmatched" else "affects-overlap"
        assert (C, kind) in {(b.path, b.kind) for b in result.blocked}
    else:
        assert (C, "affects-overlap") not in {(b.path, b.kind) for b in result.blocked}
        assert [dispatch.slug for dispatch in result.dispatches] == [C]
