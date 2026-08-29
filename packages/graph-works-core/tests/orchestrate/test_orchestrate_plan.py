"""Pure canonical-path orchestration planning."""

from __future__ import annotations

import dataclasses

from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.pipeline import PACKAGED_PIPELINE
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import WorkItem


def _item(path: str, **overrides: object) -> WorkItem:
    parent_path = path.rsplit("/children/", 1)[0] if "/children/" in path else None
    base: dict[str, object] = {
        "path": path,
        "page_path": f"{path}.md",
        "basename": path.rsplit("/", 1)[-1],
        "archived": False,
        "type": "Feature",
        "title": path,
        "description": "d",
        "status": "stable",
        "work_status": "open",
        "phase": "plan",
        "effort": "medium",
        "blast_radius": None,
        "target": None,
        "opened": "2026-08-01",
        "updated": "2026-08-01",
        "affects": ("packages/a",),
        "parent_path": parent_path,
        "ancestor_paths": (parent_path,) if parent_path else (),
        "active_child_paths": (),
        "archived_child_paths": (),
        "dependency_edges": (),
        "dependency_issues": (),
        "owner": None,
        "resolved_in": None,
        "worktree": None,
        "branch": None,
        "superseded_by": None,
        "tags": (),
        "sources": (),
        "has_design_artifact": True,
        "has_plan_artifact": False,
        "version": None,
        "target_date": None,
        "released_at": None,
    }
    base.update(overrides)
    return WorkItem(**base)  # type: ignore[arg-type]


def _plan(items: tuple[WorkItem, ...], root: str, **overrides: object):
    kwargs: dict[str, object] = {
        "pipeline": PACKAGED_PIPELINE,
        "auto_drive": {},
        "max_parallel": 2,
        "permission_mode": "bypassPermissions",
        "live": (),
        "worktree_exists": {},
        "workspace": "/ws",
        "default_base": "main",
    }
    kwargs.update(overrides)
    return orchestrate.plan(items, root, **kwargs)  # type: ignore[arg-type]


def test_lone_item_dispatch_key_and_prompt_use_full_path() -> None:
    path = "work/feature-a"
    result = _plan((_item(path),), path)
    dispatch = result.dispatches[0]
    assert dispatch.key == f"{path}#plan"
    assert dispatch.slug == path
    assert dispatch.prompt.splitlines()[0] == f"Run /graph-works:workflow {path}."


def test_branch_name_keeps_basename_and_hashes_full_path() -> None:
    first = orchestrate.branch_name("work/epic-a/children/feature-x", "Feature")
    second = orchestrate.branch_name("work/epic-b/children/feature-x", "Feature")
    assert first.startswith("feature/x-")
    assert second.startswith("feature/x-")
    assert first != second


def test_branch_name_strips_the_canonical_type_prefix() -> None:
    branch = orchestrate.branch_name("work/feature-x", "Feature")

    assert branch.startswith("feature/x-")


def test_structural_parent_frontier_dispatches_child_path() -> None:
    root = "work/epic-a"
    child = f"{root}/children/feature-a"
    items = (
        _item(root, type="Epic", phase="execute", active_child_paths=(child,), affects=("packages/root",)),
        _item(child),
    )
    result = _plan(items, root)
    assert [dispatch.slug for dispatch in result.dispatches] == [child]


def test_terminal_root_short_circuits_by_work_status() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, work_status="resolved"),), path)
    assert result.terminal is True
    assert result.dispatches == ()


def test_unknown_root_blocker_names_full_path() -> None:
    result = _plan((_item("work/feature-a"),), "work/missing")
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [("work/missing", "invalid")]


def test_live_tokens_are_matched_as_canonical_path_phase_pairs() -> None:
    path = "work/feature-a"
    result = _plan((_item(path),), path, live=(f"{path}#plan",))
    assert result.warnings == ()
    assert result.live == (f"{path}#plan",)


def test_shared_worktree_is_not_dispatched_twice() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", active_child_paths=(first, second), affects=("packages/root",)),
        _item(first, worktree="/wt/epic", branch="epic/a", affects=("packages/a",)),
        _item(second, worktree="/wt/epic", branch="epic/a", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, root, max_parallel=3, worktree_exists={"/wt/epic": True})
    actions = {dispatch.slug: dispatch.worktree.action for dispatch in result.dispatches}
    assert actions == {first: "reuse", second: "fork-child"}


def test_backend_without_worktree_provisioning_blocks_a_pathless_action() -> None:
    path = "work/feature-a"
    result = _plan((_item(path),), path, provisions_worktrees=False)
    assert result.dispatches == ()
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(path, "worktree-unsupported")]


def test_existing_worktree_reuse_does_not_require_backend_provisioning() -> None:
    path = "work/feature-a"
    item = _item(path, worktree="/wt/feature-a", branch="feature/a")
    result = _plan(
        (item,),
        path,
        provisions_worktrees=False,
        worktree_exists={"/wt/feature-a": True},
    )
    assert result.dispatches[0].worktree.action == "reuse"


def test_affects_overlap_with_a_live_canonical_path_blocks_dispatch() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", active_child_paths=(first, second), affects=("packages/root",)),
        _item(first, affects=("packages/shared",)),
        _item(second, affects=("packages/shared",), opened="2026-08-02"),
    )
    result = _plan(items, root, live=(f"{first}#plan",))
    assert any(blocked.path == second and blocked.kind == "affects-overlap" for blocked in result.blocked)


def test_capacity_blocks_only_candidates_past_the_free_slots() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", affects=("packages/root",)),
        _item(first, affects=("packages/a",)),
        _item(second, affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, root, max_parallel=1)
    assert [dispatch.slug for dispatch in result.dispatches] == [first]
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(second, "capacity")]


def test_dependency_blocker_keeps_its_path_native_classification() -> None:
    path = "work/feature-a"
    dependency = "work/feature-b"
    items = (
        _item(path, dependency_edges=(DependencyEdge(dependency, blocks="plan", needs="resolved"),)),
        _item(dependency, opened="2026-08-02"),
    )
    result = _plan(items, path)
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(path, "deps")]
    assert dependency in result.blocked[0].reason


def test_shared_stamp_never_forks_a_branch_from_itself() -> None:
    path = "work/feature-a"
    occupier_path = "work/feature-b"
    derived = orchestrate.branch_name(path, "Feature")
    items = (
        _item(path, worktree="/wt/shared", branch=derived),
        _item(occupier_path, worktree="/wt/shared", branch="feature/other", affects=("packages/b",)),
    )
    result = _plan(
        items,
        path,
        live=(f"{occupier_path}#execute",),
        worktree_exists={"/wt/shared": True},
    )
    action = result.dispatches[0].worktree
    assert action.action == "fork-child"
    assert action.base_branch == derived
    assert action.branch == f"{derived}-plan"


def test_frontier_depth_cap_blocks_a_path_instead_of_walking_forever(monkeypatch) -> None:
    monkeypatch.setattr(orchestrate, "WALK_DEPTH_CAP", 3)
    root = "work/epic-root"
    paths = [root]
    for number in range(1, 5):
        paths.append(f"{paths[-1]}/children/epic-{number}")
    items = tuple(
        _item(path, type="Epic", phase="execute", affects=(f"packages/{number}",)) for number, path in enumerate(paths)
    )
    result = _plan(items, root)
    assert any(blocked.kind == "invalid" and "depth cap" in blocked.reason for blocked in result.blocked)


def test_result_facade_forwards_every_plain_plan_field() -> None:
    path = "work/feature-a"
    plan = _plan((_item(path),), path, max_parallel=3)
    result = orchestrate.OrchestrateResult(plan=plan)
    for field in dataclasses.fields(orchestrate.OrchestratePlan):
        if field.name == "warnings":
            continue
        assert getattr(result, field.name) == getattr(plan, field.name), field.name


def test_blocker_classification_and_descendant_walk_cover_every_fallback() -> None:
    assert orchestrate._classify("effort required before execute") == "effort-required"
    assert orchestrate._classify("open decision D-001") == "decisions"
    assert orchestrate._classify("this type never dispatches") == "human"
    assert orchestrate._classify("human-owned transition") == "human"
    assert orchestrate._classify("unexpected") == "invalid"

    root = _item("work/epic", type="Epic")
    child = _item("work/epic/children/feature")
    cycle = dataclasses.replace(root, parent_path=child.path)
    assert [item.path for item in orchestrate._descendants((cycle, child), root.path)] == [child.path]


def test_epic_stamp_prefers_root_then_sorted_stamped_descendant() -> None:
    root = _item("work/epic", type="Epic", worktree="/root", branch="epic/root")
    assert orchestrate._epic_stamp((root,), root) == ("/root", "epic/root")

    unstamped = dataclasses.replace(root, worktree=None, branch=None)
    later = _item(
        "work/epic/children/feature-later",
        worktree="/later",
        branch="feature/later",
        opened="2026-08-03",
    )
    first = _item(
        "work/epic/children/feature-first",
        worktree="/first",
        branch="feature/first",
        opened="2026-08-02",
    )
    assert orchestrate._epic_stamp((unstamped, later, first), unstamped) == ("/first", "feature/first")
    assert orchestrate._epic_stamp((unstamped,), unstamped) is None


def test_worktree_resolution_covers_main_reuse_eviction_inheritance_and_cold_start() -> None:
    path = "work/feature-a"
    common = {
        "epic_branch": "epic/root",
        "accepted_worktrees": set(),
        "worktree_exists": {"/repo": True, "/dedicated": False, "/epic": True},
        "default_base": "main",
        "phase": "plan",
    }

    main_item = _item(path, worktree="/repo", branch="main")
    action, claimed = orchestrate._resolve_worktree(
        main_item,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "main" and not claimed

    action, _ = orchestrate._resolve_worktree(
        main_item,
        epic_worktree_path=None,
        live_worktree_owners={"/repo": {"work/other"}},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "fork-child" and action.base_branch == "main"

    dedicated = _item(path, worktree="/dedicated", branch="feature/a")
    action, _ = orchestrate._resolve_worktree(
        dedicated,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "reuse" and action.exists is False
    action, _ = orchestrate._resolve_worktree(
        dedicated,
        epic_worktree_path=None,
        live_worktree_owners={"/dedicated": {"work/other"}},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "fork-child" and action.base_branch == "feature/a"

    unstamped = _item(path)
    action, _ = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path="/epic",
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "reuse"
    action, _ = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path="/repo",
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "main"
    action, _ = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path="/epic",
        live_worktree_owners={"/epic": {"work/other"}},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "fork-child" and action.base_branch == "epic/root"

    action, claimed = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=True,
        repo_path=None,
        **common,
    )
    assert action is None and not claimed
    action, claimed = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "main" and claimed
    action, claimed = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path=None,
        **common,
    )
    assert action is not None and action.action == "create-top-level" and claimed


def test_prompt_substitutes_tail_and_explains_main_checkout() -> None:
    action = orchestrate.WorktreeAction("main", "/repo", "main", None, True)
    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#plan",
        phase="plan",
        workspace="/ws",
        merge_target="main",
        tail="{path} {key} {phase} {workspace} {merge_target} {literal}",
        worktree=action,
    )
    assert "work/feature-a work/feature-a#plan plan /ws main {literal}" in prompt
    assert "main checkout" in prompt


def test_plan_blocks_items_without_affects_and_ignores_unknown_live_owner() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, affects=()),), path, live=("work/missing#plan",))
    assert any(blocked.path == path and blocked.kind == "affects-overlap" for blocked in result.blocked)
    assert "matches no known item" in result.warnings[0]


def test_frontier_reports_a_parent_cycle_in_a_gated_tree() -> None:
    root_path = "work/epic-root"
    child_path = "work/epic-child"
    root = _item(root_path, type="Epic", phase="execute", parent_path=child_path)
    child = _item(child_path, type="Epic", phase="execute", parent_path=root_path)

    _candidates, _advances, blocked = orchestrate._frontier((root, child), root_path)

    assert blocked[0].kind == "invalid"
    assert "parent cycle" in blocked[0].reason


def test_frontier_plans_an_advance_when_the_current_artifact_is_complete() -> None:
    path = "work/epic-a"
    child_path = "work/epic-a/children/feature-done"
    item = _item(path, type="Epic", phase="execute", active_child_paths=(child_path,))
    child = _item(child_path, work_status="resolved")
    _candidates, advances, blocked = orchestrate._frontier((item, child), path)
    assert blocked == []
    assert advances[0].path == path
