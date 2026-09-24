"""Pure canonical-path orchestration planning."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path
from unittest import mock

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate.commands import BlockedItem
from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import Stamp, WorkItem
from work_tracker_okf.workflow import RETURN_TO_EXECUTE, RouteState, route


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
        "child_paths": (),
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


def test_worktree_refusal_kinds_are_in_the_closed_vocabulary() -> None:
    assert "worktree-unprovable" in orchestrate.BLOCKED_KINDS
    assert "worktree-ambiguous" in orchestrate.BLOCKED_KINDS

    refusal = orchestrate._Refusal(kind="worktree-ambiguous", reason="two match")
    assert refusal.kind in orchestrate.BLOCKED_KINDS
    assert refusal.reason == "two match"


def _plan(items: tuple[WorkItem, ...], root: str, **overrides: object):
    kwargs: dict[str, object] = {
        "dispatch_rules": (),
        "max_parallel": 2,
        "live": (),
        "worktree_exists": {},
        "workspace": "/ws",
        "default_base": "main",
    }
    kwargs.update(overrides)
    return orchestrate.plan(items, root, **kwargs)  # type: ignore[arg-type]


def test_distinct_repositories_share_one_budget_without_affects_collision() -> None:
    root = "work/epic-r"
    code = f"{root}/children/feature-code"
    ui = f"{root}/children/feature-ui"
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(code, ui)),
        _item(code, phase="execute", affects=("packages/a",), worktree="/wt/code", branch="feature/code"),
        _item(ui, phase="execute", affects=("packages/a",), worktree="/wt/ui", branch="feature/ui"),
    )
    repos = {
        root: ItemRepo("code", Path("/repo/code"), "frontmatter"),
        code: ItemRepo("code", Path("/repo/code"), "frontmatter"),
        ui: ItemRepo("ui", Path("/repo/ui"), "frontmatter"),
    }
    contexts = {
        "git-code": RepositoryContext(
            "git-code",
            "/repo/code",
            "main",
            True,
            {"feature/code": ("/wt/code",)},
            {"/wt/code": True},
            True,
            checkout_usable_by_path={"/wt/code": True},
        ),
        "git-ui": RepositoryContext(
            "git-ui",
            "/repo/ui",
            "main",
            True,
            {"feature/ui": ("/wt/ui",)},
            {"/wt/ui": True},
            True,
            checkout_usable_by_path={"/wt/ui": True},
        ),
    }
    items = (
        dataclasses.replace(
            items[0], worktree="/wt/code", branch="feature/code", repo_stamps={"ui": Stamp("/wt/ui", "feature/ui")}
        ),
        *items[1:],
    )
    result = _plan(items, root, item_repos=repos, repo_contexts=contexts)
    assert {d.slug for d in result.dispatches} == {code, ui}
    assert result.slots_free == 2
    assert {result.dispatch_repos[d.key].name for d in result.dispatches} == {"code", "ui"}
    limited = _plan(items, root, item_repos=repos, repo_contexts=contexts, max_parallel=1)
    assert len(limited.dispatches) == 1
    assert any(b.kind == "capacity" for b in limited.blocked)
    live = orchestrate.session_name(code, "Feature", "execute")
    occupied = _plan(items, root, item_repos=repos, repo_contexts=contexts, live=(live,), max_parallel=1)
    assert not occupied.dispatches
    assert any(b.kind == "capacity" for b in occupied.blocked)


def test_same_repository_overlap_still_blocks_and_missing_context_is_local() -> None:
    root = "work/epic-r"
    first, second, missing = (f"{root}/children/feature-{suffix}" for suffix in ("one", "two", "missing"))
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(first, second, missing)),
        _item(first, phase="execute", worktree="/wt/one", branch="feature/one"),
        _item(second, phase="execute", worktree="/wt/two", branch="feature/two"),
        _item(missing, phase="execute", affects=("packages/b",)),
    )
    repos = {path: ItemRepo("code", Path("/repo/code"), "frontmatter") for path in (root, first, second)}
    repos[missing] = ItemRepo("unknown", Path("/repo/unknown"), "frontmatter")
    context = RepositoryContext(
        "git-code",
        "/repo/code",
        "main",
        True,
        {"feature/one": ("/wt/one",), "feature/two": ("/wt/two",)},
        {"/wt/one": True, "/wt/two": True},
        True,
        checkout_usable_by_path={"/wt/one": True, "/wt/two": True},
    )
    items = (dataclasses.replace(items[0], worktree="/wt/one", branch="feature/one"), *items[1:])
    result = _plan(items, root, item_repos=repos, repo_contexts={context.identity: context})
    assert len(result.dispatches) == 1
    blocked = {b.path: b.kind for b in result.blocked}
    assert blocked[second] == "affects-overlap"
    assert blocked[missing] == "invalid"


def test_unverified_root_anchor_cannot_place_a_child() -> None:
    root, child = "work/epic-r", "work/epic-r/children/feature-a"
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(child,), worktree="/wt/foreign", branch="epic/r"),
        _item(child, phase="execute"),
    )
    selected = ItemRepo("code", Path("/repo/code"), "frontmatter")
    context = RepositoryContext("git-code", "/repo/code", "main", True, {"main": ("/repo/code",)}, {}, True)
    result = _plan(items, root, item_repos={root: selected, child: selected}, repo_contexts={context.identity: context})
    assert not result.dispatches
    assert [(b.path, b.kind) for b in result.blocked] == [(child, "worktree-unprovable")]


def test_unknown_live_repository_does_not_authorize_overlapping_candidate() -> None:
    root, live_path, ready = (
        "work/epic-r",
        "work/epic-r/children/feature-live",
        "work/epic-r/children/feature-ready",
    )
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(live_path, ready)),
        _item(live_path, phase="execute", worktree="/wt/live", branch="feature/live"),
        _item(ready, phase="execute", worktree="/wt/ready", branch="feature/ready"),
    )
    selected = ItemRepo("code", Path("/repo/code"), "frontmatter")
    context = RepositoryContext(
        "git-code", "/repo/code", "main", True, {"feature/ready": ("/wt/ready",)}, {"/wt/ready": True}, True
    )
    result = _plan(
        items,
        root,
        live=(orchestrate.session_name(live_path, "Feature", "execute"),),
        item_repos={ready: selected},
        repo_contexts={context.identity: context},
    )
    assert not result.dispatches
    assert {blocked.path: blocked.kind for blocked in result.blocked}[ready] == "worktree-unprovable"


def test_shared_git_identity_preserves_each_declared_checkout() -> None:
    root = "work/epic-r"
    first, second = (f"{root}/children/feature-{suffix}" for suffix in ("first", "second"))
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(first, second)),
        _item(first, phase="execute", affects=("packages/a",), worktree="/repo/primary", branch="main"),
        _item(second, phase="execute", affects=("packages/b",), worktree="/repo/linked", branch="feature/linked"),
    )
    selected = {
        root: ItemRepo("primary", Path("/repo/primary"), "frontmatter"),
        first: ItemRepo("primary", Path("/repo/primary"), "frontmatter"),
        second: ItemRepo("linked", Path("/repo/linked"), "frontmatter"),
    }
    context = RepositoryContext(
        "git-common",
        "/repo/primary",
        "main",
        True,
        {"main": ("/repo/primary",), "feature/linked": ("/repo/linked",)},
        {"/repo/primary": True, "/repo/linked": True},
        True,
        checkout_usable_by_path={"/repo/primary": True, "/repo/linked": True},
    )
    items = (
        dataclasses.replace(
            items[0],
            worktree="/repo/primary",
            branch="main",
            repo_stamps={"linked": Stamp("/repo/linked", "feature/linked")},
        ),
        *items[1:],
    )
    result = _plan(items, root, item_repos=selected, repo_contexts={context.identity: context})
    assert {d.slug for d in result.dispatches} == {first, second}
    assert {result.dispatch_repos[d.key].path for d in result.dispatches} == {
        Path("/repo/primary"),
        Path("/repo/linked"),
    }


def test_dirty_declared_checkout_cannot_reuse_its_scalar_stamp() -> None:
    path = "work/feature-a"
    item = _item(path, phase="execute", worktree="/repo/linked", branch="feature/linked")
    selected = ItemRepo("linked", Path("/repo/linked"), "frontmatter")
    context = RepositoryContext(
        "git-common",
        "/repo/primary",
        "main",
        True,
        {"feature/linked": ("/repo/linked",)},
        {"/repo/linked": True},
        True,
        checkout_usable_by_path={"/repo/primary": True, "/repo/linked": False},
    )
    result = _plan((item,), path, item_repos={path: selected}, repo_contexts={context.identity: context})
    assert result.dispatches == ()
    assert [(b.path, b.kind) for b in result.blocked] == [(path, "worktree-unprovable")]


def test_failed_git_identity_refuses_fresh_root_creation() -> None:
    path = "work/feature-a"
    selected = ItemRepo("code", Path("/repo/code"), "sole")
    context = RepositoryContext(
        "/repo/code", "/repo/code", "main", False, {}, {"/repo/code": True}, False, identity_known=False
    )
    result = _plan(
        (_item(path, phase="design"),), path, item_repos={path: selected}, repo_contexts={context.identity: context}
    )
    assert result.dispatches == ()
    assert [(b.path, b.kind) for b in result.blocked] == [(path, "worktree-unprovable")]


def test_lone_item_dispatch_key_and_prompt_use_full_path() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, worktree="/wt/feature-a", branch="feature/a"),), path)
    dispatch = result.dispatches[0]
    assert dispatch.key == orchestrate.session_name(path, "Feature", "plan")
    assert dispatch.slug == path
    assert dispatch.prompt.splitlines()[0] == f"Run /gw:workflow {path}."


def test_branch_name_keeps_basename_and_hashes_full_path() -> None:
    first = orchestrate.branch_name("work/epic-a/children/feature-x", "Feature")
    second = orchestrate.branch_name("work/epic-b/children/feature-x", "Feature")
    assert first.startswith("feature/x-")
    assert second.startswith("feature/x-")
    assert first != second


def test_branch_name_strips_the_canonical_type_prefix() -> None:
    branch = orchestrate.branch_name("work/feature-x", "Feature")

    assert branch.startswith("feature/x-")


def test_session_name_is_prefixed_phased_and_hash_tailed() -> None:
    name = orchestrate.session_name("work/epic-a/children/feature-x", "Feature", "design")
    assert name.startswith("gw-design-")
    stem = orchestrate.branch_name("work/epic-a/children/feature-x", "Feature").split("/", 1)[1]
    assert name == f"gw-design-{stem}"
    # The basename "feature-x" already carries the "feature" type segment, so
    # `_stable_stem` strips it -- same behavior `branch_name` has always had
    # (see test_branch_name_strips_the_canonical_type_prefix).
    assert re.fullmatch(r"gw-design-x-[0-9a-f]{8}", name)


def test_session_name_separates_paths_that_share_a_basename() -> None:
    first = orchestrate.session_name("work/epic-a/children/feature-x", "Feature", "execute")
    second = orchestrate.session_name("work/epic-b/children/feature-x", "Feature", "execute")
    assert first != second
    assert first.startswith("gw-execute-x-")
    assert second.startswith("gw-execute-x-")


def test_session_name_truncates_the_words_and_keeps_the_hash() -> None:
    path = "work/" + "feature-" + "-".join(["extraordinarily"] * 6)
    name = orchestrate.session_name(path, "Feature", "execute")
    tail = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    assert len(name) == orchestrate.SESSION_NAME_MAX
    assert name.startswith("gw-execute-")
    assert name.endswith(f"-{tail}")


def test_session_name_never_ends_on_a_separator_after_truncation() -> None:
    path = "work/feature-" + "-".join(["abcdefgh"] * 8)
    name = orchestrate.session_name(path, "Feature", "execute")
    assert "--" not in name
    assert len(name) <= orchestrate.SESSION_NAME_MAX


def test_session_index_maps_every_item_phase_pair() -> None:
    items = [_item("work/epic-a/children/feature-x", type="Feature")]
    index, warnings = orchestrate.session_index(items)
    assert warnings == ()
    assert len(index) == len(orchestrate.DISPATCH_PHASES)
    for phase in orchestrate.DISPATCH_PHASES:
        name = orchestrate.session_name("work/epic-a/children/feature-x", "Feature", phase)
        assert index[name].path == "work/epic-a/children/feature-x"


def test_session_index_drops_a_collision_and_warns() -> None:
    a = _item("work/epic-a/children/feature-x", type="Feature")
    b = _item("work/epic-b/children/feature-x", type="Feature")
    collide = "gw-design-feature-x-deadbeef"

    def fake(path: str, type_: str, phase: str) -> str:
        return collide if phase == "design" else f"gw-{phase}-{path}"

    with mock.patch.object(orchestrate, "session_name", fake):
        index, warnings = orchestrate.session_index([a, b])

    assert collide not in index
    assert warnings == (
        "session name 'gw-design-feature-x-deadbeef' is ambiguous "
        "(work/epic-a/children/feature-x, work/epic-b/children/feature-x); dropped",
    )
    assert f"gw-execute-{a.path}" in index


def test_session_index_is_empty_for_no_items() -> None:
    assert orchestrate.session_index([]) == ({}, ())


def test_the_dispatch_key_is_the_session_name() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, worktree="/wt/feature-a", branch="feature/a"),), path)
    dispatch = result.dispatches[0]
    assert dispatch.key == orchestrate.session_name(dispatch.slug, dispatch.kind, dispatch.phase)
    assert dispatch.key.startswith(f"gw-{dispatch.phase}-")
    assert "#" not in dispatch.key
    assert dispatch.slug.startswith("work/")


def test_structural_parent_frontier_dispatches_child_path() -> None:
    root = "work/epic-a"
    child = f"{root}/children/feature-a"
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(child,), affects=("packages/root",)),
        _item(child, worktree="/wt/feature-a", branch="feature/a"),
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
    live_key = orchestrate.session_name(path, "Feature", "plan")
    result = _plan((_item(path),), path, live=(live_key,))
    assert result.warnings == ()
    assert result.live == (live_key,)


def test_shared_worktree_is_not_dispatched_twice() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(first, second), affects=("packages/root",)),
        _item(first, worktree="/wt/epic", branch="epic/a", affects=("packages/a",)),
        _item(second, worktree="/wt/epic", branch="epic/a", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, root, max_parallel=3, worktree_exists={"/wt/epic": True})
    actions = {dispatch.slug: dispatch.worktree.action for dispatch in result.dispatches}
    assert actions == {first: "reuse", second: "fork-child"}


def test_plan_blocks_an_unprovable_item_without_consuming_a_slot() -> None:
    root = _item(
        "work/epic-r",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        child_paths=("work/epic-r/children/bug-x",),
    )
    child = _item("work/epic-r/children/bug-x", type="Bug", phase="execute", affects=("packages/a",))

    computed = orchestrate.plan(
        (root, child),
        "work/epic-r",
        dispatch_rules=(),
        max_parallel=2,
        workspace="/ws",
        default_base="main",
        repo_path="/repo",
        worktree_exists={"/repo": True},
        worktree_inventory={},
    )

    assert computed.dispatches == ()
    blocked = {b.path: b for b in computed.blocked}
    assert blocked["work/epic-r/children/bug-x"].kind == "worktree-unprovable"


def test_plan_dispatches_into_an_adopted_worktree() -> None:
    root = _item(
        "work/epic-r",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        child_paths=("work/epic-r/children/bug-x",),
    )
    child = _item("work/epic-r/children/bug-x", type="Bug", phase="execute", affects=("packages/a",))
    planned = orchestrate.branch_name(child.path, child.type)

    computed = orchestrate.plan(
        (root, child),
        "work/epic-r",
        dispatch_rules=(),
        max_parallel=2,
        workspace="/ws",
        default_base="main",
        repo_path="/repo",
        worktree_exists={"/repo": True},
        worktree_inventory={planned: "/wt/bug-x"},
    )

    dispatched = {d.slug: d for d in computed.dispatches}
    assert dispatched["work/epic-r/children/bug-x"].worktree.path == "/wt/bug-x"
    assert dispatched["work/epic-r/children/bug-x"].worktree.action == "reuse"


def test_plan_defaults_to_an_empty_inventory() -> None:
    child = _item("work/lone-bug", type="Bug", phase="design", affects=("packages/a",))

    computed = orchestrate.plan(
        (child,),
        "work/lone-bug",
        dispatch_rules=(),
        max_parallel=1,
        workspace="/ws",
        default_base="main",
    )

    # No `worktree_inventory=` given at all: today's behaviour, design phase
    # still dispatches.
    assert len(computed.dispatches) == 1


def test_backend_without_worktree_provisioning_blocks_a_pathless_action() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, phase="design"),), path, provisions_worktrees=False)
    assert result.dispatches == ()
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(path, "worktree-unsupported")]


def test_a_worktree_creation_without_a_known_code_repo_blocks() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, phase="design"),), path, repo_known=False)
    assert result.dispatches == ()
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(path, "worktree-unprovable")]
    assert "no code repository was resolved" in result.blocked[0].reason


def test_an_existing_worktree_dispatches_without_a_known_code_repo() -> None:
    path = "work/feature-a"
    item = _item(path, worktree="/wt/feature-a", branch="feature/a")
    result = _plan((item,), path, repo_known=False, worktree_exists={"/wt/feature-a": True})
    assert result.dispatches[0].worktree.action == "reuse"


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
        _item(root, type="Epic", phase="execute", child_paths=(first, second), affects=("packages/root",)),
        _item(first, affects=("packages/shared",)),
        _item(second, affects=("packages/shared",), opened="2026-08-02"),
    )
    result = _plan(items, root, live=(orchestrate.session_name(first, "Feature", "plan"),))
    assert any(blocked.path == second and blocked.kind == "affects-overlap" for blocked in result.blocked)


def test_capacity_blocks_only_candidates_past_the_free_slots() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", affects=("packages/root",), child_paths=(first, second)),
        _item(first, worktree="/wt/feature-a", branch="feature/a", affects=("packages/a",)),
        _item(second, affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, root, max_parallel=1)
    assert [dispatch.slug for dispatch in result.dispatches] == [first]
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(second, "capacity")]


def test_a_repo_refusal_blocks_the_candidate_and_reserves_nothing() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            affects=("packages/root",),
            child_paths=(first, second),
            worktree="/wt/epic-a",
            branch="epic/a",
        ),
        _item(first, affects=("packages/a",)),
        _item(second, affects=("packages/b",), opened="2026-08-02"),
    )
    refusal = BlockedItem(path=first, kind="cross-repo-child", reason=f"{first} resolves to 'code', not 'ui'")
    result = _plan(items, root, repo_refusals={first: refusal})
    assert refusal in result.blocked
    assert [dispatch.slug for dispatch in result.dispatches] == [second]


def test_plan_never_reuses_a_repo_refused_descendant_s_stamp_as_the_epic_anchor() -> None:
    """Review focus 2, pinned at `plan()`'s own wiring rather than only at
    `_epic_stamp` directly: `_epic_stamp` is called with
    `exclude=frozenset(repo_refusals)` (commands.py), so a refused
    descendant's own scalar `worktree`/`branch` pair can never become the
    epic anchor a sibling reuses or forks against.

    The root Epic carries no stamp of its own, so the only candidate epic
    anchor is the refused child's `/wt/foreign` / `f` pair. Without the
    exclusion, the plain-phase sibling (a `READ_ONLY_PHASES` entitlement)
    would reuse that pair outright; with it, there is no anchor left to
    inherit and the sibling cold-starts, blocking `worktree-unprovable`
    instead -- never touching the foreign path or branch anywhere in the
    plan.
    """
    root = "work/epic-a"
    refused = f"{root}/children/feature-a"
    sibling = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", affects=("packages/root",), child_paths=(refused, sibling)),
        _item(refused, worktree="/wt/foreign", branch="f", affects=("packages/a",)),
        _item(sibling, affects=("packages/b",), opened="2026-08-02"),
    )
    refusal = BlockedItem(path=refused, kind="cross-repo-child", reason=f"{refused} resolves to 'code', not 'ui'")

    result = _plan(items, root, repo_refusals={refused: refusal})

    assert refusal in result.blocked
    assert not any(
        dispatch.worktree.path == "/wt/foreign" or dispatch.worktree.branch == "f" for dispatch in result.dispatches
    )
    assert not any(advance.worktree == "/wt/foreign" or advance.branch == "f" for advance in result.advances)
    sibling_blocked = [blocked for blocked in result.blocked if blocked.path == sibling]
    assert sibling_blocked and sibling_blocked[0].kind == "worktree-unprovable"


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
        live=(orchestrate.session_name(occupier_path, "Feature", "execute"),),
        worktree_exists={"/wt/shared": True},
    )
    action = result.dispatches[0].worktree
    assert action.action == "fork-child"
    assert action.base_branch == derived
    assert action.branch == f"{derived}-plan"
    assert action.parent_path == "/wt/shared"


def test_frontier_depth_cap_blocks_a_path_instead_of_walking_forever(monkeypatch) -> None:
    monkeypatch.setattr(orchestrate, "WALK_DEPTH_CAP", 3)
    root = "work/epic-root"
    paths = [root]
    for number in range(1, 5):
        paths.append(f"{paths[-1]}/children/epic-{number}")
    items = tuple(
        _item(
            path,
            type="Epic",
            phase="execute",
            affects=(f"packages/{number}",),
            child_paths=(paths[number + 1],) if number + 1 < len(paths) else (),
        )
        for number, path in enumerate(paths)
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


def test_the_epic_stamp_fallback_skips_an_excluded_descendant() -> None:
    root = _item("work/epic", type="Epic", worktree=None, branch=None)
    foreign = _item(
        "work/epic/children/feature-a",
        worktree="/wt/foreign",
        branch="f",
    )
    items = (root, foreign)
    assert orchestrate._epic_stamp(items, root) == ("/wt/foreign", "f")
    assert orchestrate._epic_stamp(items, root, exclude=frozenset({foreign.path})) is None


def test_the_epic_stamp_reads_repo_stamps_for_a_named_repo() -> None:
    root = _item(
        "work/epic",
        type="Epic",
        worktree="/wt/own",
        branch="own",
        repo_stamps={"ui": Stamp("/wt/ui", "u")},
    )
    assert orchestrate._epic_stamp((root,), root) == ("/wt/own", "own")
    assert orchestrate._epic_stamp((root,), root, repo="ui") == ("/wt/ui", "u")
    assert orchestrate._epic_stamp((root,), root, repo="docs") is None


def test_worktree_rule_1c_adopts_when_the_stamped_directory_has_vanished() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", worktree="/gone", branch="bug/old")
    planned = orchestrate.branch_name(item.path, item.type)

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/gone": False},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={planned: "/wt/bug-x"},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse"
    assert action.path == "/wt/bug-x"
    assert action.branch == planned
    assert action.exists is True
    assert not claimed


def test_worktree_rule_1c_refuses_when_the_stamp_vanished_and_nothing_matches() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", worktree="/gone", branch="bug/old")

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/gone": False},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_worktree_rule_1c_still_reuses_when_the_stat_merely_failed() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", worktree="/unknown", branch="bug/old")

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/unknown": None},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse"


def test_worktree_rule_2_applies_the_same_check_to_a_stale_epic_anchor() -> None:
    # A read-only phase, deliberately: only a phase that still reuses the
    # epic anchor (rule 2) reaches the stale-anchor adoption check this test
    # exercises. A code-phase descendant now forks unconditionally (see the
    # `descendant_at_*` tests) and never inspects the anchor's existence.
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="plan")

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path="/gone-epic",
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/gone-epic": False},
        default_base="main",
        phase="plan",
        is_root=False,
        repo_path="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"


def test_worktree_rule_4_blocks_a_mid_pipeline_cold_start() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_worktree_rule_4_still_dispatches_a_design_phase_cold_start() -> None:
    """The design-phase exemption from the mid-pipeline refusal survives; only
    its landing changed, from the main checkout to a minted epic worktree."""
    item = _item("work/bug-x", type="Bug")

    action, claimed = _cold_start(item, "design", is_root=True)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level"
    assert claimed


def test_worktree_rule_4_exempts_a_never_advanced_testgap_first_dispatch() -> None:
    item = _item("work/gap-x", type="TestGap", phase=None)

    action, claimed = _cold_start(item, "execute", is_root=True)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level"
    assert claimed


def test_a_descendant_design_cold_start_refuses_rather_than_dispatching() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")

    action, claimed = _cold_start(item, "design", is_root=False)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_a_descendant_never_advanced_testgap_refuses_rather_than_dispatching() -> None:
    item = _item("work/epic-r/children/gap-x", type="TestGap", phase=None)

    action, claimed = _cold_start(item, "execute", is_root=False)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_worktree_rule_4_still_refuses_a_testgap_that_has_already_advanced() -> None:
    item = _item("work/epic-r/children/gap-x", type="TestGap", phase="execute")

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_worktree_rule_4_adopts_a_findable_worktree_mid_pipeline() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={planned: "/wt/bug-x"},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse"
    assert action.path == "/wt/bug-x"
    assert not claimed


def test_worktree_adoption_of_the_repo_checkout_reports_main() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={planned: "/repo"},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "main"
    assert action.path == "/repo"
    # An adopted `main` claims the epic slot for the same reason rule 4's own
    # `main` return does: a second cold-start item in this batch must block
    # as `worktree-pending` rather than land in the same checkout.
    assert claimed


def test_worktree_adoption_of_a_gone_directory_refuses_instead_of_lying() -> None:
    """Reproduction: the stamped directory is gone, and the only inventory
    match points at that same gone directory (e.g. a prunable-but-not-yet-
    pruned worktree, or a stale record pointing at a path that no longer
    resolves). Adopting it would report `exists: true` for a path proven not
    to exist -- worse than refusing, because the plan reads as verified."""
    item = _item("work/epic-r/children/bug-x", type="Bug", worktree="/wt/bug-x", branch="bug/old")

    action, claimed = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/wt/bug-x": False},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={"psprowls/unrelated": "/wt/bug-x"},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_worktree_adoption_with_a_missing_exists_entry_still_adopts() -> None:
    """A path absent from the exists-map is treated the same as the old
    hardcoded default -- adoptable, reported as existing."""
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={planned: "/wt/bug-x"},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse"
    assert action.path == "/wt/bug-x"
    assert action.exists is True


def test_worktree_adoption_with_an_unknown_exists_stat_still_adopts() -> None:
    """`None` means the stat failed -- unknown, not proven gone -- so an
    adopted path explicitly mapped to `None` stays adoptable. Only an
    explicit `False` blocks adoption; the guard is `is False`, not falsy."""
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/wt/bug-x": None},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={planned: "/wt/bug-x"},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse"
    assert action.path == "/wt/bug-x"
    assert action.exists is None


def test_worktree_adoption_forks_when_the_adopted_path_is_occupied() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={"/wt/bug-x": {"work/other"}},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={planned: "/wt/bug-x"},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "fork-child"
    assert action.base_branch == planned
    assert action.parent_path == "/wt/bug-x"


def test_worktree_ambiguity_surfaces_as_a_refusal() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    flat = orchestrate.branch_name(item.path, item.type).replace("/", "-")

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={f"alice/{flat}": "/wt/a", f"bob/{flat}": "/wt/b"},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-ambiguous"


def test_worktree_resolution_covers_main_reuse_eviction_inheritance_and_cold_start() -> None:
    path = "work/feature-a"
    common = {
        "epic_branch": "epic/root",
        "accepted_worktrees": set(),
        "worktree_exists": {"/repo": True, "/dedicated": False, "/epic": True},
        "default_base": "main",
        "phase": "plan",
        "is_root": True,
        "inventory": {},
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
    assert action is not None and action.action == "main" and not claimed and action.parent_path is None

    action, _ = orchestrate._resolve_worktree(
        main_item,
        epic_worktree_path=None,
        live_worktree_owners={"/repo": {"work/other"}},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert (
        action is not None
        and action.action == "fork-child"
        and action.base_branch == "main"
        and action.parent_path is None
    )

    dedicated = _item(path, worktree="/dedicated", branch="feature/a")
    action, _ = orchestrate._resolve_worktree(
        dedicated,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    # The stamped directory is gone and the inventory is empty: refuse.
    assert isinstance(action, orchestrate._Refusal) and action.kind == "worktree-unprovable"

    action, _ = orchestrate._resolve_worktree(
        dedicated,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **{**common, "worktree_exists": {**common["worktree_exists"], "/dedicated": True}},
    )
    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse" and action.exists is True
    assert action.parent_path is None

    action, _ = orchestrate._resolve_worktree(
        dedicated,
        epic_worktree_path=None,
        live_worktree_owners={"/dedicated": {"work/other"}},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert (
        action is not None
        and action.action == "fork-child"
        and action.base_branch == "feature/a"
        and action.parent_path == "/dedicated"
    )

    unstamped = _item(path)
    action, _ = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path="/epic",
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "reuse" and action.parent_path is None
    action, _ = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path="/repo",
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert action is not None and action.action == "main" and action.parent_path is None
    action, _ = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path="/epic",
        live_worktree_owners={"/epic": {"work/other"}},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **common,
    )
    assert (
        action is not None
        and action.action == "fork-child"
        and action.base_branch == "epic/root"
        and action.parent_path == "/epic"
    )

    action, claimed = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=True,
        repo_path=None,
        **common,
    )
    assert action is None and not claimed

    cold = {**common, "phase": "design"}
    # Both arms mint: a known repo path no longer buys an opportunistic
    # main-checkout placement (rule 4a is deleted).
    action, claimed = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path="/repo",
        **cold,
    )
    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level" and claimed
    assert action.parent_path is None
    action, claimed = orchestrate._resolve_worktree(
        unstamped,
        epic_worktree_path=None,
        live_worktree_owners={},
        epic_worktree_claimed=False,
        repo_path=None,
        **cold,
    )
    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level" and claimed
    assert action.parent_path is None


def test_a_fork_off_an_epic_anchored_in_the_main_checkout_carries_no_parent() -> None:
    """Trunk work is never a child of the repository's own checkout (design D5)."""
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="execute")

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path="/repo",
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "fork-child" and action.base_branch == "epic/root"
    assert action.parent_path is None


def test_a_fork_off_a_dirty_main_checkout_still_carries_no_parent() -> None:
    """`repo_path` is withheld (`None`) whenever the checkout is dirty
    (`run_orchestrate`'s `_checkout_is_dirty` guard), but the resolved
    repository itself -- `code_repo` -- is still known and still un-withheld.
    A fork off that repository is trunk work regardless of whether its
    checkout happened to be dirty at plan time, so it must still carry no
    parent (design D5) -- comparing against a withheld `repo_path` alone lets
    this slip through and silently link trunk work beneath the main checkout.
    """
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="execute")

    action, _ = orchestrate._resolve_worktree(
        item,
        epic_worktree_path="/repo",
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/repo": True},
        default_base="main",
        phase="execute",
        is_root=False,
        repo_path=None,
        code_repo="/repo",
        inventory={},
    )

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "fork-child" and action.base_branch == "epic/root"
    assert action.parent_path is None


def test_a_planned_fork_carries_its_parent_through_plan() -> None:
    root = "work/epic-r"
    child = f"{root}/children/bug-x"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            worktree="/epic",
            branch="epic/root",
            affects=("packages/r",),
            child_paths=(child,),
        ),
        _item(child, type="Bug", phase="execute", affects=("packages/x",)),
    )
    result = _plan(items, root, worktree_exists={"/epic": True}, repo_path="/repo")
    forks = [d for d in result.dispatches if d.slug == child]
    assert forks and forks[0].worktree.action == "fork-child"
    assert forks[0].worktree.parent_path == "/epic"


def test_prompt_substitutes_tail() -> None:
    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#execute",
        phase="execute",
        workspace="/ws",
        merge_target="main",
        tail="{path} {key} {phase} {workspace} {merge_target} {literal}",
    )
    assert "work/feature-a work/feature-a#execute execute /ws main {literal}" in prompt
    assert orchestrate.WORKER_PLACEMENT_LINE in prompt
    assert prompt.endswith(
        "work/feature-a work/feature-a#execute execute /ws main {literal}\n" + orchestrate.WORKER_PLACEMENT_LINE
    )


def test_an_execute_dispatch_prompt_carries_the_coverage_obligation() -> None:
    from graph_works_core.workspace.pipeline import EXECUTE_TAIL

    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#execute",
        phase="execute",
        workspace="/ws",
        merge_target="main",
        tail=EXECUTE_TAIL,
    )
    assert "/ws/okf/work/feature-a/references/03-execute-coverage.md" in prompt
    assert "{workspace}" not in prompt and "{path}" not in prompt


@pytest.mark.parametrize("unknown", ["work/feature-a#plan", "gw-plan-a-00000000"])
@pytest.mark.parametrize("work_status", ["open", "resolved"])
def test_unknown_live_key_refuses_even_a_terminal_plan(unknown: str, work_status: str) -> None:
    path = "work/feature-a"
    with pytest.raises(ValueError, match="unknown live dispatch key") as caught:
        _plan((_item(path, work_status=work_status),), path, live=(unknown,))
    assert unknown in str(caught.value)


def test_mixed_live_keys_refuse_and_report_all_unknown_keys_once_in_input_order() -> None:
    path = "work/feature-a"
    known = orchestrate.session_name(path, "Feature", "plan")
    with pytest.raises(ValueError, match="unknown live dispatch key") as caught:
        _plan((_item(path),), path, live=("unknown-z", known, "unknown-a", "unknown-z"))
    message = str(caught.value)
    assert message.count("unknown-z") == message.count("unknown-a") == 1
    assert message.index("unknown-z") < message.index("unknown-a")
    assert known not in message


def test_collision_dropped_live_key_refuses_with_ambiguous_owners() -> None:
    first = _item("work/feature-a")
    second = _item("work/feature-b")
    collision = "gw-design-collision-deadbeef"

    def fake(path: str, type_: str, phase: str) -> str:
        return collision if phase == "design" else f"gw-{phase}-{path}"

    with mock.patch.object(orchestrate, "session_name", fake):
        with pytest.raises(ValueError, match="unknown live dispatch key") as caught:
            _plan((first, second), first.path, live=(collision,))
        ordinary = _plan((first, second), first.path)
    message = str(caught.value)
    assert collision in message
    assert "ambiguous" in message
    assert first.path in message and second.path in message
    assert any(collision in warning and "ambiguous" in warning for warning in ordinary.warnings)


@pytest.mark.parametrize("owner_path", ["work/feature-a", "work/feature-outside"])
def test_known_prior_phase_live_key_keeps_affects_reserved(owner_path: str) -> None:
    path = "work/feature-a"
    candidate = _item(path, phase="execute", has_plan_artifact=True)
    items = (candidate,) if owner_path == path else (candidate, _item(owner_path, phase="finish"))
    live = orchestrate.session_name(owner_path, "Feature", "design")
    result = _plan(items, path, live=(live,))
    assert result.slots_free == 1
    assert result.dispatches == ()
    assert any(blocked.path == path and blocked.kind == "affects-overlap" for blocked in result.blocked)
    assert result.warnings == ()


def test_plan_blocks_items_without_affects() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, affects=()),), path)
    assert any(blocked.path == path and blocked.kind == "affects-overlap" for blocked in result.blocked)


def test_frontier_reports_a_parent_cycle_in_a_gated_tree() -> None:
    root_path = "work/epic-root"
    child_path = "work/epic-child"
    root = _item(root_path, type="Epic", phase="execute", parent_path=child_path, child_paths=(child_path,))
    child = _item(child_path, type="Epic", phase="execute", parent_path=root_path, child_paths=(root_path,))

    _candidates, _advances, blocked = orchestrate._frontier((root, child), root_path)

    assert blocked[0].kind == "invalid"
    assert "parent cycle" in blocked[0].reason


def test_frontier_plans_an_advance_when_the_current_artifact_is_complete() -> None:
    path = "work/epic-a"
    child_path = "work/epic-a/children/feature-done"
    item = _item(path, type="Epic", phase="execute", child_paths=(child_path,))
    child = _item(child_path, work_status="resolved")
    _candidates, advances, blocked = orchestrate._frontier((item, child), path)
    assert blocked == []
    assert advances[0].path == path
    assert advances[0].mode == "advance"


def test_frontier_repairs_an_epic_reopened_by_a_post_finish_child() -> None:
    """Fixture 1 (design §3): epic at `finish`, one terminal child, one child
    filed afterwards -> one `advances[]` entry with `mode == "return"`, no
    bogus resolve. Applying it and replanning dispatches the new child."""
    root = "work/epic-a"
    done_child = f"{root}/children/feature-done"
    late_child = f"{root}/children/bug-late"
    epic = _item(root, type="Epic", phase="finish", child_paths=(done_child, late_child))
    done = _item(done_child, work_status="resolved")
    late = _item(late_child, type="Bug", work_status="open", phase="execute")
    items = (epic, done, late)

    _candidates, advances, blocked = orchestrate._frontier(items, root)
    assert blocked == []
    assert [advance.path for advance in advances] == [root]
    assert advances[0].mode == "return"

    reopened = dataclasses.replace(epic, phase="execute")
    candidates2, advances2, blocked2 = orchestrate._frontier((reopened, done, late), root)
    assert blocked2 == []
    assert advances2 == []
    assert [node.path for node, _result in candidates2] == [late_child]


def test_frontier_sees_an_open_grandchild_beneath_a_terminal_direct_child() -> None:
    """Fixture 2 (design §3, axis 2): a terminal direct child can still hold an
    open grandchild, and the plan must not drop it."""
    root = "work/epic-a"
    feat = f"{root}/children/feature-done"
    grandchild = f"{feat}/children/bug-gc"
    epic = _item(root, type="Epic", phase="execute", child_paths=(feat,))
    feature = _item(feat, type="Feature", phase="finish", work_status="resolved", child_paths=(grandchild,))
    gc = _item(grandchild, type="Bug", work_status="open", phase="execute")
    items = (epic, feature, gc)

    candidates, advances, blocked = orchestrate._frontier(items, root)
    assert blocked == []
    assert advances == []
    assert [node.path for node, _result in candidates] == [grandchild]


def test_descend_and_frontier_agree_on_the_widened_execute_window() -> None:
    """Fixture 4 (design §3): `descend()` and `_frontier()` must never disagree
    about the same tree -- both walk through `child_gated`, the one authority."""
    from work_tracker_okf.hierarchy import descend

    root = "work/epic-a"
    feat = f"{root}/children/feature-done"
    grandchild = f"{feat}/children/bug-gc"
    epic = _item(root, type="Epic", phase="execute", child_paths=(feat,))
    feature = _item(feat, type="Feature", phase="finish", work_status="resolved", child_paths=(grandchild,))
    gc = _item(grandchild, type="Bug", work_status="open", phase="execute")
    items = (epic, feature, gc)

    candidates, _advances, _blocked = orchestrate._frontier(items, root)
    descended = descend(items, root)

    assert descended.leaf == grandchild
    assert [node.path for node, _result in candidates] == [descended.leaf]


def test_regression_the_real_epic_reopens_then_dispatches_its_four_children() -> None:
    """Regression guard for the reported incident: the epic
    `work/epic-auto-drive-dispatch-correctness` with its four post-finish
    children must plan to a `mode: return` advance, then to four dispatches."""
    root = "work/epic-auto-drive-dispatch-correctness"
    names = (
        "tech-debt-auto-merge-child-finish",
        "tech-debt-merge-ingest-root-archive",
        "tech-debt-session-name-standardization",
        "tech-debt-worktree-anchor-cold-start",
    )
    child_paths = tuple(f"{root}/children/{name}" for name in names)
    old_child = f"{root}/children/bug-orchestrate-drops-post-finish-children"
    epic = _item(root, type="Epic", phase="finish", child_paths=(old_child, *child_paths))
    old = _item(old_child, work_status="resolved")
    children = tuple(
        _item(path, type="TestGap", work_status="open", phase="execute", effort="small") for path in child_paths
    )
    items = (epic, old, *children)

    _candidates, advances, blocked = orchestrate._frontier(items, root)
    assert blocked == []
    assert [advance.path for advance in advances] == [root]
    assert advances[0].mode == "return"

    reopened = dataclasses.replace(epic, phase="execute")
    candidates2, advances2, blocked2 = orchestrate._frontier((reopened, old, *children), root)
    assert blocked2 == []
    assert advances2 == []
    assert sorted(node.path for node, _result in candidates2) == sorted(child_paths)


def test_adopt_finds_the_planned_branch() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)

    assert orchestrate._adopt(item, inventory={planned: "/wt/bug-x"}) == ("/wt/bug-x", planned)


def test_adopt_finds_a_renamed_branch_without_knowing_the_username() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)
    renamed = "psprowls/" + planned.replace("/", "-")

    adopted = orchestrate._adopt(item, inventory={renamed: "/wt/bug-x", "main": "/repo"})

    assert adopted == ("/wt/bug-x", renamed)


def test_adopt_falls_through_to_the_directory_basename() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")

    adopted = orchestrate._adopt(item, inventory={"someone/unrelated": "/wt/bug-x"})

    assert adopted == ("/wt/bug-x", "someone/unrelated")


def test_adopt_never_takes_the_collision_decoy() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")

    # Only the empty `-2` collision directory exists: the real work is gone.
    assert orchestrate._adopt(item, inventory={"someone/unrelated": "/wt/bug-x-2"}) is None


def test_adopt_prefers_the_exact_directory_over_the_decoy() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    inventory = {"someone/a": "/wt/bug-x", "someone/b": "/wt/bug-x-2"}

    assert orchestrate._adopt(item, inventory=inventory) == ("/wt/bug-x", "someone/a")


def test_adopt_refuses_when_two_entries_match() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")
    planned = orchestrate.branch_name(item.path, item.type)
    flat = planned.replace("/", "-")
    inventory = {f"alice/{flat}": "/wt/a", f"bob/{flat}": "/wt/b"}

    refusal = orchestrate._adopt(item, inventory=inventory)

    assert isinstance(refusal, orchestrate._Refusal)
    assert refusal.kind == "worktree-ambiguous"


def test_adopt_finds_nothing_in_an_empty_inventory() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug")

    assert orchestrate._adopt(item, inventory={}) is None


def test_supervise_merges_defaults_off_and_rides_on_the_plan() -> None:
    item = _item("work/lone-bug", type="Bug", phase="design", affects=("packages/a",))
    assert _plan((item,), "work/lone-bug").supervise_merges is False
    assert _plan((item,), "work/lone-bug", supervise_merges=True).supervise_merges is True


def test_supervise_merges_rides_on_a_terminal_plan_too() -> None:
    # The terminal short-circuit builds its own `OrchestratePlan`; a field
    # threaded only through the non-terminal return would read back as a
    # default here and silently mean "not supervised".
    item = _item("work/lone-bug", type="Bug", phase="done", work_status="resolved", affects=("packages/a",))
    computed = _plan((item,), "work/lone-bug", supervise_merges=True)
    assert computed.terminal is True
    assert computed.supervise_merges is True


def _child_at_finish(**overrides: object) -> tuple[str, str, tuple[WorkItem, ...]]:
    """An epic with one stamped child sitting at `finish`."""
    root = "work/epic-am"
    child = f"{root}/children/feature-am"
    epic = _item(
        root,
        type="Epic",
        phase="execute",
        work_status="in-progress",
        child_paths=(child,),
        worktree="/wt/epic-am",
        branch="epic/am-1a2b3c4d",
        affects=("packages/root",),
    )
    leaf = _item(
        child,
        phase="finish",
        work_status="in-progress",
        worktree="/wt/feature-am",
        branch="feature/am-5e6f7a8b",
        **overrides,
    )
    return root, child, (epic, leaf)


_AM_WORKTREES = {"/wt/epic-am": True, "/wt/feature-am": True}


def _only_dispatch(result):
    [dispatch] = result.dispatches
    return dispatch


def test_a_non_root_child_at_finish_auto_merges_when_merges_are_unsupervised() -> None:
    root, child, items = _child_at_finish()
    dispatch = _only_dispatch(_plan(items, root, dispatch_rules=_branch_tail_rules(), worktree_exists=_AM_WORKTREES))
    assert (dispatch.slug, dispatch.phase, dispatch.mode) == (child, "finish", "relay")
    assert dispatch.merge_target == "epic/am-1a2b3c4d"
    assert dispatch.auto_merge is True


def test_supervised_merges_never_auto_merge() -> None:
    root, _, items = _child_at_finish()
    result = _plan(
        items, root, supervise_merges=True, dispatch_rules=_branch_tail_rules(), worktree_exists=_AM_WORKTREES
    )
    assert _only_dispatch(result).auto_merge is False


def test_the_roots_own_finish_never_auto_merges() -> None:
    root = "work/lone-bug"
    item = _item(root, type="Bug", phase="finish", work_status="in-progress", worktree="/wt/lone", branch="bug/lone-1")
    dispatch = _only_dispatch(
        _plan((item,), root, dispatch_rules=_branch_tail_rules(), worktree_exists={"/wt/lone": True})
    )
    assert (dispatch.phase, dispatch.mode) == ("finish", "relay")
    assert dispatch.auto_merge is False


def test_a_stamped_epic_root_at_finish_never_auto_merges() -> None:
    root, items = _root_at_finish("Epic", worktree="/wt/epic-int", branch="epic/int-1a2b3c4d")
    dispatch = _only_dispatch(
        _plan(items, root, dispatch_rules=_branch_tail_rules(), worktree_exists={"/wt/epic-int": True})
    )
    assert dispatch.auto_merge is False


def test_a_non_root_item_without_an_integration_owner_never_auto_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    # The walk only reaches a non-root item through an owning Epic/Release/Feature,
    # so "non-root, no owner" is a guard rather than a reachable shape: force it.
    # The merge target is then the release base, which the policy never
    # auto-answers, even though the item is not the plan's root.
    monkeypatch.setattr(orchestrate, "enclosing_owner", lambda item, items: None)
    root, child, items = _child_at_finish()
    repo = ItemRepo("code", Path("/repo/code"), "frontmatter")
    contexts = {
        "git-code": RepositoryContext(
            "git-code",
            "/repo/code",
            "main",
            True,
            {"epic/am-1a2b3c4d": ("/wt/epic-am",), "feature/am-5e6f7a8b": ("/wt/feature-am",)},
            {"/wt/epic-am": True, "/wt/feature-am": True},
            True,
            checkout_usable_by_path={"/wt/epic-am": True, "/wt/feature-am": True},
        ),
    }
    result = _plan(
        items,
        root,
        dispatch_rules=_branch_tail_rules(),
        item_repos={root: repo, child: repo},
        repo_contexts=contexts,
    )
    [dispatch] = [d for d in result.dispatches if d.slug == child]
    assert dispatch.merge_target == "main"
    assert dispatch.auto_merge is False


def test_a_non_finish_phase_never_auto_merges() -> None:
    root, _, items = _child_at_finish()
    items = (items[0], dataclasses.replace(items[1], phase="execute"))
    dispatch = _only_dispatch(_plan(items, root, dispatch_rules=_branch_tail_rules(), worktree_exists=_AM_WORKTREES))
    assert dispatch.phase == "execute"
    assert dispatch.auto_merge is False


def _epic_anchor(item, phase: str, *, is_root: bool):
    """Rule 2's setup: an unoccupied epic anchor at `/epic`, nothing else."""
    return orchestrate._resolve_worktree(
        item,
        epic_worktree_path="/epic",
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/epic": True, "/repo": True},
        default_base="main",
        phase=phase,
        repo_path="/repo",
        inventory={},
        is_root=is_root,
    )


def test_a_descendant_at_execute_forks_off_the_epic_branch_instead_of_reusing_it() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="execute")

    action, claimed = _epic_anchor(item, "execute", is_root=False)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "fork-child"
    assert action.base_branch == "epic/root"
    assert action.path is None
    assert not claimed
    assert action.parent_path == "/epic"


def test_a_descendant_at_finish_forks_off_the_epic_branch_too() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="finish")

    action, _ = _epic_anchor(item, "finish", is_root=False)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "fork-child"
    assert action.base_branch == "epic/root"
    assert action.parent_path == "/epic"


def test_a_descendant_at_a_read_only_phase_still_reuses_the_epic_anchor() -> None:
    """The read context with the branch's code, and it claims nothing: a
    vault-only stage is a non-exclusive occupant."""
    for phase in sorted(orchestrate.READ_ONLY_PHASES):
        item = _item("work/epic-r/children/bug-x", type="Bug", phase=phase)

        action, claimed = _epic_anchor(item, phase, is_root=False)

        assert isinstance(action, orchestrate.WorktreeAction), phase
        assert action.action == "reuse", phase
        assert action.path == "/epic", phase
        assert not claimed, phase


def test_the_subtree_root_reuses_the_epic_anchor_at_every_phase() -> None:
    """Rule 1 is untouched and the root carve-out keeps rule 2 whole for it:
    an epic's `finish` stage belongs in the epic worktree, not a fork."""
    for phase in ("design", "plan", "execute", "finish"):
        item = _item("work/epic-r", type="Epic", phase=phase)

        action, _ = _epic_anchor(item, phase, is_root=True)

        assert isinstance(action, orchestrate.WorktreeAction), phase
        assert action.action == "reuse", phase
        assert action.path == "/epic", phase


def _cold_start(
    item,
    phase: str,
    *,
    is_root: bool,
    repo_path: str | None = "/repo",
    claimed: bool = False,
    inventory: dict[str, str] | None = None,
):
    """Rule 4's setup: no stamp, no epic anchor, and (by default) nothing in the inventory."""
    return orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/root",
        live_worktree_owners={},
        accepted_worktrees=set(),
        epic_worktree_claimed=claimed,
        worktree_exists={"/repo": True},
        default_base="main",
        phase=phase,
        repo_path=repo_path,
        inventory=inventory or {},
        is_root=is_root,
    )


def test_cold_start_mints_the_epic_worktree_even_when_the_repo_path_is_known() -> None:
    """Rule 4a is gone. There is no opportunistic main-checkout placement at
    any phase: a stage that would commit straight onto trunk is exactly what
    this item exists to stop."""
    item = _item("work/epic-r", type="Epic", phase=None)

    action, claimed = _cold_start(item, "design", is_root=True)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level"
    assert action.action != "main"
    assert action.branch == "epic/root"
    assert action.base_branch == "main"
    assert action.path is None
    assert claimed


def test_a_second_cold_start_in_the_same_cycle_is_pending_not_a_second_mint() -> None:
    item = _item("work/epic-r", type="Epic", phase=None)

    action, claimed = _cold_start(item, "design", is_root=True, claimed=True)

    assert action is None
    assert not claimed


def test_a_descendant_cold_start_refuses_and_names_the_root_as_the_remedy() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", phase=None)

    action, claimed = _cold_start(item, "design", is_root=False)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert "subtree root" in action.reason
    assert not claimed


def test_a_descendant_cold_start_refuses_with_no_repo_path_either() -> None:
    """The refusal is about the missing anchor, not about the checkout."""
    item = _item("work/epic-r/children/bug-x", type="Bug", phase=None)

    action, _ = _cold_start(item, "design", is_root=False, repo_path=None)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"


def test_a_root_cold_start_at_plan_mints_when_every_prior_stage_was_vault_only() -> None:
    """The field report: design ran attended, so no worktree was ever made,
    and the plan dispatch refused with `worktree-unprovable`."""
    item = _item("work/bug-x", type="Bug", phase="plan")

    action, claimed = _cold_start(item, "plan", is_root=True)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level"
    assert action.branch == "epic/root"
    assert action.base_branch == "main"
    assert action.path is None
    assert claimed


def test_a_root_cold_start_at_execute_accepted_mints_because_execute_never_ran() -> None:
    """Same defect one stage later: design *and* plan ran attended."""
    item = _item("work/bug-x", type="Bug", phase="execute", work_status="accepted")

    action, claimed = _cold_start(item, "execute", is_root=True)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "create-top-level"
    assert claimed


def test_a_root_cold_start_at_execute_in_progress_still_refuses() -> None:
    item = _item("work/bug-x", type="Bug", phase="execute", work_status="in-progress")

    action, claimed = _cold_start(item, "execute", is_root=True)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert "say where the prior work is" in action.reason
    assert not claimed


def test_a_root_cold_start_at_finish_still_refuses() -> None:
    item = _item("work/bug-x", type="Bug", phase="finish", work_status="in-progress")

    action, claimed = _cold_start(item, "finish", is_root=True)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert not claimed


def test_a_descendant_cold_start_at_plan_still_names_the_root_as_the_remedy() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="plan")

    action, claimed = _cold_start(item, "plan", is_root=False)

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-unprovable"
    assert "subtree root" in action.reason
    assert not claimed


def test_a_root_cold_start_adopts_a_findable_worktree_at_plan() -> None:
    """A dispatched plan stage whose stamp was lost is reunited with its
    worktree rather than given a second one."""
    item = _item("work/bug-x", type="Bug", phase="plan")
    planned = orchestrate.branch_name(item.path, item.type)

    action, claimed = _cold_start(item, "plan", is_root=True, inventory={planned: "/wt/bug-x"})

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "reuse"
    assert action.path == "/wt/bug-x"
    assert action.branch == planned
    assert not claimed


def test_a_root_cold_start_at_plan_refuses_an_ambiguous_adoption() -> None:
    item = _item("work/bug-x", type="Bug", phase="plan")
    flattened = orchestrate.branch_name(item.path, item.type).replace("/", "-")

    action, claimed = _cold_start(
        item,
        "plan",
        is_root=True,
        inventory={f"alice/{flattened}": "/wt/a", f"bob/{flattened}": "/wt/b"},
    )

    assert isinstance(action, orchestrate._Refusal)
    assert action.kind == "worktree-ambiguous"
    assert not claimed


def test_accepted_at_execute_is_the_state_no_execute_stage_has_started_from() -> None:
    """`_code_work_may_exist` exempts exactly `execute`/`accepted`. That is
    sound only while plan completion is the one way in with `accepted` and
    every execute dispatch leaves it. Pin the workflow table, not just the
    resolver, so a new transition that breaks the coupling fails here."""
    planned = route(RouteState(type="Bug", work_status="open", phase="plan", effort="medium", has_spec_doc=True))
    assert planned.on_complete is not None
    assert (planned.on_complete.phase, planned.on_complete.work_status) == ("execute", "accepted")

    dispatched = route(
        RouteState(
            type="Bug",
            work_status="accepted",
            phase="execute",
            effort="medium",
            has_spec_doc=True,
            has_plan_doc=True,
        )
    )
    assert dispatched.on_dispatch is not None
    assert dispatched.on_dispatch.work_status == "in-progress"

    assert RETURN_TO_EXECUTE.work_status == "in-progress"


def test_a_child_design_dispatch_reuses_the_epic_anchor_and_records_nothing() -> None:
    """The reproduction this item was filed from, at the `plan()` level: a
    `design` dispatch of a child against a stamped epic anchor plans a reuse
    of the epic worktree, claims nothing, and is told to record nothing."""
    root = "work/epic-r"
    child = f"{root}/children/tech-debt-x"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            worktree="/epic",
            branch="epic/root",
            child_paths=(child,),
            affects=("packages/root",),
        ),
        _item(child, type="TechDebt", phase="design", affects=("packages/a",)),
    )

    result = _plan(items, root, worktree_exists={"/epic": True, "/repo": True}, repo_path="/repo")

    dispatch = next(d for d in result.dispatches if d.slug == child)
    assert dispatch.worktree.action == "reuse"
    assert dispatch.worktree.path == "/epic"
    assert "Record the worktree" not in dispatch.prompt
    assert orchestrate.WORKER_PLACEMENT_LINE in dispatch.prompt


def test_a_read_only_dispatch_does_not_occupy_the_slot_for_a_later_dispatch() -> None:
    """Two `design` children of the same epic, sharing a stamped epic anchor,
    both resolve to `reuse` -- neither one's `reuse` action evicts the other
    into a needless fork -- and a third, code-phase child that owns its own
    stamp of that same directory (having committed there before) is not
    blocked or forked off it by either read-only occupant having claimed the
    slot first."""
    root = "work/epic-r"
    first_child = f"{root}/children/tech-debt-x"
    second_child = f"{root}/children/tech-debt-y"
    code_child = f"{root}/children/tech-debt-z"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            worktree="/epic",
            branch="epic/root",
            child_paths=(first_child, second_child, code_child),
            affects=("packages/root",),
        ),
        _item(first_child, type="TechDebt", phase="design", affects=("packages/a",)),
        _item(second_child, type="TechDebt", phase="design", affects=("packages/b",)),
        _item(
            code_child,
            type="TechDebt",
            phase="execute",
            worktree="/epic",
            branch="epic/root",
            affects=("packages/c",),
        ),
    )

    result = _plan(
        items,
        root,
        worktree_exists={"/epic": True, "/repo": True},
        repo_path="/repo",
        max_parallel=3,
    )

    by_slug = {d.slug: d for d in result.dispatches}
    assert not result.blocked, result.blocked
    assert by_slug[first_child].worktree.action == "reuse"
    assert by_slug[first_child].worktree.path == "/epic"
    assert by_slug[second_child].worktree.action == "reuse"
    assert by_slug[second_child].worktree.path == "/epic"
    assert by_slug[code_child].worktree.action == "reuse"
    assert by_slug[code_child].worktree.path == "/epic"


def test_dispatch_profile_errors_preserve_relay_blocker_and_do_not_claim_placement():
    from graph_works_core.workspace.dispatch import parse_rules

    root = "work/epic-a"
    child = f"{root}/children/feature-a"
    rules = parse_rules(
        [{"match": {"variant": "single"}, "mode": "relay"}],
        source="/ws/dispatch.yaml",
        attributes=frozenset({"variant"}),
    )
    result = _plan(
        (_item(root, type="Epic", phase="execute", child_paths=(child,)), _item(child)),
        root,
        dispatch_rules=rules,
    )
    assert result.dispatches == ()
    assert result.dispatch_resolutions == {}
    assert any(block.kind == "relay-untailed" for block in result.blocked), result.blocked
    assert "dispatch" in next(block.reason for block in result.blocked if block.kind == "relay-untailed")


def test_resolved_tail_formats_known_placeholders_and_preserves_literal_braces():
    from graph_works_core.workspace.dispatch import parse_rules

    path = "work/feature-a"
    rules = parse_rules(
        [{"match": {}, "prompt_tail": "{workspace} {path} {merge_target} {literal}", "agent": "codex"}],
        source="/ws/dispatch.yaml",
        attributes=frozenset(),
    )
    result = _plan((_item(path, phase="design"),), path, dispatch_rules=rules)
    planned = result.dispatches[0]
    assert "/ws work/feature-a main {literal}" in planned.prompt
    assert planned.agent == "codex"


# --- affects are reserved only by emitted dispatches ------------------------
#
# Every fixture below is an execute-phase epic with a stamped, existing anchor
# and first-sorted child A followed by B (and C), all sharing
# `packages/shared`. A plan-phase child with no stamp of its own reuses the
# epic anchor, so B's route, profile and placement are always dispatchable;
# the only thing that can stop B is A's reservation.

_RESERVE_ROOT = "work/epic-reserve"
_SHARED = ("packages/shared",)
_GONE_STAMP: dict[str, object] = {
    "phase": "execute",
    "has_plan_artifact": True,
    "worktree": "/wt/gone",
    "branch": "feature/a",
}


def _overlapping_children(*names: str, first: dict[str, object] | None = None) -> tuple[tuple, tuple[str, ...]]:
    paths = tuple(f"{_RESERVE_ROOT}/children/{name}" for name in names)
    epic = _item(
        _RESERVE_ROOT,
        type="Epic",
        phase="execute",
        child_paths=paths,
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="epic/reserve",
    )
    children = tuple(
        _item(path, affects=_SHARED, **((first or {}) if index == 0 else {})) for index, path in enumerate(paths)
    )
    return (epic, *children), paths


def _blocked_kinds(result) -> set[tuple[str, str]]:
    return {(blocked.path, blocked.kind) for blocked in result.blocked}


@pytest.mark.parametrize(
    ("claimants", "kind"),
    [((), "worktree-unprovable"), (("alice", "bob"), "worktree-ambiguous")],
)
def test_refused_placement_reserves_no_affects(claimants: tuple[str, ...], kind: str) -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b", first=_GONE_STAMP)
    flat = orchestrate.branch_name(first, "Feature").replace("/", "-")
    result = _plan(
        items,
        _RESERVE_ROOT,
        repo_path="/repo",
        worktree_exists={"/wt/gone": False, "/wt/epic": True, "/wt/alice": True, "/wt/bob": True},
        worktree_inventory={f"{who}/{flat}": f"/wt/{who}" for who in claimants},
    )
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert (first, kind) in _blocked_kinds(result)
    assert (second, "affects-overlap") not in _blocked_kinds(result)
    assert set(result.dispatch_resolutions) == {result.dispatches[0].key}


def test_dispatch_profile_error_reserves_no_affects() -> None:
    from graph_works_core.workspace.dispatch import parse_rules

    rules = parse_rules(
        [{"match": {"type": "Bug"}, "mode": "relay"}],
        source="/ws/dispatch.yaml",
        attributes=frozenset({"variant", "type"}),
    )
    items, (first, second) = _overlapping_children("bug-a", "feature-b", first={"type": "Bug"})
    result = _plan(items, _RESERVE_ROOT, dispatch_rules=rules, worktree_exists={"/wt/epic": True})
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert (first, "relay-untailed") in _blocked_kinds(result)
    assert set(result.dispatch_resolutions) == {result.dispatches[0].key}


def test_unsupported_provisioning_reserves_no_affects() -> None:
    # A is an execute-phase descendant: it forks a child branch, a pathless
    # action this backend cannot provision. B reuses the existing anchor.
    items, (first, second) = _overlapping_children(
        "feature-a", "feature-b", first={"phase": "execute", "has_plan_artifact": True}
    )
    result = _plan(items, _RESERVE_ROOT, provisions_worktrees=False, worktree_exists={"/wt/epic": True})
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert (first, "worktree-unsupported") in _blocked_kinds(result)


def test_pending_placement_reserves_no_affects_and_no_slot() -> None:
    # A controlled resolver return isolates the planner's acceptance contract;
    # the real placement rules are exercised for B, unchanged.
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    real = orchestrate._resolve_worktree
    resolved: list[str] = []

    def resolve(item, **kwargs):
        resolved.append(item.path)
        return (None, False) if item.path == first else real(item, **kwargs)

    with mock.patch.object(orchestrate, "_resolve_worktree", side_effect=resolve):
        result = _plan(items, _RESERVE_ROOT, max_parallel=1, worktree_exists={"/wt/epic": True})
    assert resolved == [first, second]
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert _blocked_kinds(result) == {(first, "worktree-pending")}


def test_zero_capacity_candidates_do_not_overlap_each_other() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    result = _plan(items, _RESERVE_ROOT, max_parallel=0, worktree_exists={"/wt/epic": True})
    assert result.dispatches == ()
    assert _blocked_kinds(result) == {(first, "capacity"), (second, "capacity")}


def test_capacity_dropped_candidates_reserve_nothing_when_a_slot_is_filled_by_unrelated_work() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    outside = _item("work/feature-outside", phase="finish", affects=("packages/other",))
    live = orchestrate.session_name(outside.path, "Feature", "execute")
    result = _plan((*items, outside), _RESERVE_ROOT, max_parallel=1, live=(live,), worktree_exists={"/wt/epic": True})
    assert result.dispatches == ()
    assert _blocked_kinds(result) == {(first, "capacity"), (second, "capacity")}


def test_accepted_dispatch_still_serializes_an_overlapping_sibling() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    result = _plan(items, _RESERVE_ROOT, worktree_exists={"/wt/epic": True})
    assert [dispatch.slug for dispatch in result.dispatches] == [first]
    assert _blocked_kinds(result) == {(second, "affects-overlap")}


@pytest.mark.parametrize("reverse_input", [False, True])
def test_a_sibling_accepted_after_a_refusal_reserves_for_later_overlaps(reverse_input: bool) -> None:
    items, (first, second, third) = _overlapping_children("feature-a", "feature-b", "feature-c", first=_GONE_STAMP)
    if reverse_input:
        items = tuple(reversed(items))
    result = _plan(items, _RESERVE_ROOT, max_parallel=3, worktree_exists={"/wt/gone": False, "/wt/epic": True})
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert _blocked_kinds(result) == {(first, "worktree-unprovable"), (third, "affects-overlap")}
    assert "packages/shared" in next(blocked.reason for blocked in result.blocked if blocked.path == third)


def test_a_refused_candidate_never_lets_capacity_be_exceeded() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b", first=_GONE_STAMP)
    third = f"{_RESERVE_ROOT}/children/feature-c"
    epic = dataclasses.replace(items[0], child_paths=(first, second, third))
    items = (epic, *items[1:], _item(third, affects=("packages/other",)))
    result = _plan(items, _RESERVE_ROOT, max_parallel=1, worktree_exists={"/wt/gone": False, "/wt/epic": True})
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert _blocked_kinds(result) == {(first, "worktree-unprovable"), (third, "capacity")}


def test_empty_affects_candidate_reserves_nothing() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (items[0], dataclasses.replace(items[1], affects=()), items[2])
    result = _plan(items, _RESERVE_ROOT, worktree_exists={"/wt/epic": True})
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert _blocked_kinds(result) == {(first, "affects-overlap")}


# --- a stamped root finishes its integration branch (D-002) ---------------


def _branch_tail_rules():
    from graph_works_core.workspace.dispatch import parse_rules

    return parse_rules(
        [{"match": {"variant": "branch"}, "prompt_tail": "Auto-drive context: merge target is `{merge_target}`."}],
        source="/ws/dispatch.yaml",
        attributes=frozenset({"variant"}),
    )


def _root_at_finish(type_: str, **overrides: object) -> tuple[str, tuple[WorkItem, ...]]:
    root = "work/epic-int"
    child = f"{root}/children/feature-done"
    parent = _item(
        root,
        type=type_,
        phase="finish",
        work_status="in-progress",
        child_paths=(child,),
        affects=("packages/root",),
        **overrides,
    )
    done = _item(child, phase="done", work_status="resolved", worktree="/wt/child", branch="feature/done-1a2b3c4d")
    return root, (parent, done)


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_regression_a_stamped_root_at_finish_dispatches_its_integration_instead_of_resolving(type_: str) -> None:
    """The reported run: every child merged into the epic branch, the root
    reached `finish`, and the planner handed the coordinator a bare done/resolved
    advance. A stamped root must instead get a relay finish in its own worktree,
    merging into the default base."""
    root, items = _root_at_finish(type_, worktree="/wt/epic-int", branch="epic/int-1a2b3c4d")
    result = _plan(items, root, dispatch_rules=_branch_tail_rules(), worktree_exists={"/wt/epic-int": True})
    assert result.advances == ()
    assert result.blocked == ()
    [dispatch] = result.dispatches
    assert (dispatch.slug, dispatch.phase, dispatch.skill, dispatch.mode) == (
        root,
        "finish",
        "superpowers:finishing-a-development-branch",
        "relay",
    )
    assert (dispatch.worktree.action, dispatch.worktree.path) == ("reuse", "/wt/epic-int")
    assert dispatch.merge_target == "main"
    assert "merge target is `main`" in dispatch.prompt


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_an_unstamped_root_at_finish_keeps_its_planned_advance(type_: str) -> None:
    root, items = _root_at_finish(type_)
    result = _plan(items, root, dispatch_rules=_branch_tail_rules())
    assert result.dispatches == ()
    assert [(advance.path, advance.reason, advance.mode) for advance in result.advances] == [
        (root, f"{type_.lower()} at finish stage", "advance")
    ]


def test_a_stamped_root_whose_placement_cannot_be_proved_blocks_rather_than_resolving() -> None:
    root, items = _root_at_finish("Epic", worktree="/wt/epic-int", branch="epic/int-1a2b3c4d")
    result = _plan(
        items,
        root,
        dispatch_rules=_branch_tail_rules(),
        worktree_exists={"/wt/epic-int": False},
        worktree_inventory={},
    )
    assert result.dispatches == ()
    assert result.advances == ()
    assert [(blocked.path, blocked.kind) for blocked in result.blocked] == [(root, "worktree-unprovable")]


def test_a_nested_stamped_epic_finishes_into_its_parent_integration_branch() -> None:
    root = "work/epic-int"
    mid = f"{root}/children/epic-mid"
    leaf = f"{mid}/children/feature-leaf"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            work_status="in-progress",
            child_paths=(mid,),
            worktree="/wt/epic-int",
            branch="epic/int-1a2b3c4d",
            affects=("packages/root",),
        ),
        _item(
            mid,
            type="Epic",
            phase="finish",
            work_status="in-progress",
            child_paths=(leaf,),
            worktree="/wt/epic-mid",
            branch="epic/mid-5e6f7a8b",
            affects=("packages/mid",),
        ),
        _item(leaf, phase="done", work_status="resolved"),
    )
    result = _plan(
        items,
        root,
        dispatch_rules=_branch_tail_rules(),
        worktree_exists={"/wt/epic-int": True, "/wt/epic-mid": True},
    )
    assert result.advances == ()
    assert [(d.slug, d.worktree.action, d.worktree.path, d.merge_target) for d in result.dispatches] == [
        (mid, "reuse", "/wt/epic-mid", "epic/int-1a2b3c4d")
    ]


def test_only_the_root_advance_carries_the_epic_pair() -> None:
    """D-006: a descendant's coordinator-applied advance must not acquire the
    shared epic anchor, or the planner later reuses it instead of forking.
    The middle item is an Epic so execute routes to an automatic advance."""
    root = "work/epic-r"
    mid = f"{root}/children/epic-mid"
    leaf = f"{mid}/children/bug-leaf"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            work_status="in-progress",
            worktree="/epic",
            branch="epic/r",
            child_paths=(mid,),
            affects=("packages/root",),
        ),
        _item(
            mid,
            type="Epic",
            phase="execute",
            work_status="in-progress",
            child_paths=(leaf,),
            ancestor_paths=(root,),
        ),
        _item(leaf, type="Bug", phase="done", work_status="resolved", parent_path=mid, ancestor_paths=(mid, root)),
    )

    result = _plan(items, root, worktree_exists={"/epic": True})

    by_path = {advance.path: advance for advance in result.advances}
    assert mid in by_path, (result.advances, result.blocked)
    assert (by_path[mid].worktree, by_path[mid].branch) == (None, None)


@pytest.mark.parametrize("root", ["work/epic-r", "work/epic-outer/children/epic-r"])
def test_the_root_advance_still_carries_the_epic_pair(root: str) -> None:
    """Root means the explicit orchestration root, including a nested subtree."""
    child = f"{root}/children/bug-done"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            work_status="in-progress",
            worktree="/epic",
            branch="epic/r",
            child_paths=(child,),
            affects=("packages/root",),
        ),
        _item(child, type="Bug", phase="done", work_status="resolved"),
    )

    result = _plan(items, root, worktree_exists={"/epic": True})

    [advance] = [a for a in result.advances if a.path == root]
    assert (advance.worktree, advance.branch) == ("/epic", "epic/r")


def test_a_nested_return_advance_carries_no_epic_pair() -> None:
    """A nested Epic at finish repairs to execute when a later child opens."""
    root = "work/epic-r"
    mid = f"{root}/children/epic-mid"
    leaf = f"{mid}/children/bug-late"
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            work_status="in-progress",
            worktree="/epic",
            branch="epic/r",
            child_paths=(mid,),
            affects=("packages/root",),
        ),
        _item(
            mid,
            type="Epic",
            phase="finish",
            work_status="in-progress",
            child_paths=(leaf,),
            ancestor_paths=(root,),
        ),
        _item(
            leaf,
            type="Bug",
            phase=None,
            work_status="open",
            parent_path=mid,
            ancestor_paths=(mid, root),
            affects=("packages/leaf",),
        ),
    )

    result = _plan(items, root, worktree_exists={"/epic": True})

    returns = [a for a in result.advances if a.mode == "return"]
    assert [a.path for a in returns] == [mid], (result.advances, result.blocked)
    assert (returns[0].worktree, returns[0].branch) == (None, None)


@pytest.mark.parametrize("phase", ["design", "plan", "execute", "finish"])
@pytest.mark.parametrize("is_root", [True, False])
def test_every_dispatch_prompt_hands_placement_to_the_coordinator(phase: str, is_root: bool) -> None:
    """Root: a lone Feature dispatching itself. Descendant: a Bug under a
    stamped Epic at `execute`. Either way the worker is told to stay out of
    placement, and nobody is told to record one. The workspace relay tail
    enables finish routing, preserving all four phases for both placements."""
    if is_root:
        slug = "work/feature-solo"
        items: tuple[WorkItem, ...] = (
            _item(
                slug,
                phase=phase,
                work_status="in-progress",
                owner="pat",
                worktree="/repo",
                branch="main",
                has_plan_artifact=True,
            ),
        )
        root = slug
    else:
        root = "work/epic-r"
        slug = f"{root}/children/bug-x"
        items = (
            _item(
                root,
                type="Epic",
                phase="execute",
                work_status="in-progress",
                worktree="/epic",
                branch="epic/r",
                child_paths=(slug,),
                affects=("packages/root",),
            ),
            _item(slug, type="Bug", phase=phase, work_status="in-progress", owner="pat", has_plan_artifact=True),
        )

    result = _plan(
        items,
        root,
        worktree_exists={"/epic": True, "/repo": True},
        repo_path="/repo",
        dispatch_rules=_branch_tail_rules(),
    )

    matches = [d for d in result.dispatches if d.slug == slug]
    assert len(matches) == 1, (result.dispatches, result.blocked)
    [dispatch] = matches
    assert orchestrate.WORKER_PLACEMENT_LINE in dispatch.prompt
    assert "Record the worktree" not in dispatch.prompt
    assert "--worktree`/`--branch` explicitly" not in dispatch.prompt
