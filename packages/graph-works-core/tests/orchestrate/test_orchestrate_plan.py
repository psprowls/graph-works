"""Pure canonical-path orchestration planning."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from unittest import mock

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


def test_worktree_refusal_kinds_are_in_the_closed_vocabulary() -> None:
    assert "worktree-unprovable" in orchestrate.BLOCKED_KINDS
    assert "worktree-ambiguous" in orchestrate.BLOCKED_KINDS

    refusal = orchestrate._Refusal(kind="worktree-ambiguous", reason="two match")
    assert refusal.kind in orchestrate.BLOCKED_KINDS
    assert refusal.reason == "two match"


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
        _item(root, type="Epic", phase="execute", active_child_paths=(child,), affects=("packages/root",)),
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
        _item(root, type="Epic", phase="execute", active_child_paths=(first, second), affects=("packages/root",)),
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
        active_child_paths=("work/epic-r/children/bug-x",),
    )
    child = _item("work/epic-r/children/bug-x", type="Bug", phase="execute", affects=("packages/a",))

    computed = orchestrate.plan(
        (root, child),
        "work/epic-r",
        pipeline=PACKAGED_PIPELINE,
        auto_drive={},
        max_parallel=2,
        permission_mode="bypassPermissions",
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
        active_child_paths=("work/epic-r/children/bug-x",),
    )
    child = _item("work/epic-r/children/bug-x", type="Bug", phase="execute", affects=("packages/a",))
    planned = orchestrate.branch_name(child.path, child.type)

    computed = orchestrate.plan(
        (root, child),
        "work/epic-r",
        pipeline=PACKAGED_PIPELINE,
        auto_drive={},
        max_parallel=2,
        permission_mode="bypassPermissions",
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
        pipeline=PACKAGED_PIPELINE,
        auto_drive={},
        max_parallel=1,
        permission_mode="bypassPermissions",
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
    result = _plan(items, root, live=(orchestrate.session_name(first, "Feature", "plan"),))
    assert any(blocked.path == second and blocked.kind == "affects-overlap" for blocked in result.blocked)


def test_capacity_blocks_only_candidates_past_the_free_slots() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", affects=("packages/root",), active_child_paths=(first, second)),
        _item(first, worktree="/wt/feature-a", branch="feature/a", affects=("packages/a",)),
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
        live=(orchestrate.session_name(occupier_path, "Feature", "execute"),),
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
        _item(
            path,
            type="Epic",
            phase="execute",
            affects=(f"packages/{number}",),
            active_child_paths=(paths[number + 1],) if number + 1 < len(paths) else (),
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


def test_prompt_substitutes_tail_and_explains_main_checkout() -> None:
    action = orchestrate.WorktreeAction("main", "/repo", "main", None, True)
    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#execute",
        phase="execute",
        workspace="/ws",
        merge_target="main",
        tail="{path} {key} {phase} {workspace} {merge_target} {literal}",
        worktree=action,
        is_root=False,
    )
    assert "work/feature-a work/feature-a#execute execute /ws main {literal}" in prompt
    assert "main checkout" in prompt


def test_prompt_asks_a_root_dispatch_to_record_its_worktree_for_every_action() -> None:
    reuse = orchestrate.WorktreeAction("reuse", "/wt/epic-r", "epic/root-1a2b3c4d", None, True)

    root_prompt = orchestrate._prompt(
        path="work/epic-r",
        key="work/epic-r#design",
        phase="design",
        workspace="/ws",
        merge_target="main",
        tail=None,
        worktree=reuse,
        is_root=True,
    )
    assert "Record the worktree as `/wt/epic-r`" in root_prompt

    child_prompt = orchestrate._prompt(
        path="work/epic-r/children/bug-x",
        key="work/epic-r/children/bug-x#design",
        phase="design",
        workspace="/ws",
        merge_target="epic/root-1a2b3c4d",
        tail=None,
        worktree=reuse,
        is_root=False,
    )
    assert "Record the worktree" not in child_prompt


def test_prompt_pathless_root_asks_to_record_wherever_the_worker_lands() -> None:
    """The epic-anchor freeze's primary case: an epic's first-ever dispatch
    has no stamp and `repo_path` is unresolved, so the action is a pathless
    `create-top-level`. The instruction must not render the literal `None`."""
    action = orchestrate.WorktreeAction("create-top-level", None, "epic/root-1a2b3c4d", "main", None)

    prompt = orchestrate._prompt(
        path="work/epic-r",
        key="work/epic-r#design",
        phase="design",
        workspace="/ws",
        merge_target="main",
        tail=None,
        worktree=action,
        is_root=True,
    )

    assert "None" not in prompt
    assert "Record the worktree you end up in, and the branch as `epic/root-1a2b3c4d`" in prompt


def test_an_execute_dispatch_prompt_carries_the_coverage_obligation() -> None:
    from graph_works_core.workspace.pipeline import EXECUTE_TAIL

    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#execute",
        phase="execute",
        workspace="/ws",
        merge_target="main",
        tail=EXECUTE_TAIL,
        worktree=orchestrate.WorktreeAction("create-top-level", "/wt", "feature/a", None, True),
        is_root=False,
    )
    assert "/ws/okf/work/feature-a/references/03-execute-coverage.md" in prompt
    assert "{workspace}" not in prompt and "{path}" not in prompt


def test_plan_blocks_items_without_affects_and_ignores_unknown_live_owner() -> None:
    path = "work/feature-a"
    result = _plan((_item(path, affects=()),), path, live=("work/missing#plan",))
    assert any(blocked.path == path and blocked.kind == "affects-overlap" for blocked in result.blocked)
    assert "matches no known item" in result.warnings[0]


def test_frontier_reports_a_parent_cycle_in_a_gated_tree() -> None:
    root_path = "work/epic-root"
    child_path = "work/epic-child"
    root = _item(root_path, type="Epic", phase="execute", parent_path=child_path, active_child_paths=(child_path,))
    child = _item(child_path, type="Epic", phase="execute", parent_path=root_path, active_child_paths=(root_path,))

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
    assert advances[0].mode == "advance"


def test_frontier_repairs_an_epic_reopened_by_a_post_finish_child() -> None:
    """Fixture 1 (design §3): epic at `finish`, one terminal child, one child
    filed afterwards -> one `advances[]` entry with `mode == "return"`, no
    bogus resolve. Applying it and replanning dispatches the new child."""
    root = "work/epic-a"
    done_child = f"{root}/children/feature-done"
    late_child = f"{root}/children/bug-late"
    epic = _item(root, type="Epic", phase="finish", active_child_paths=(done_child, late_child))
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
    epic = _item(root, type="Epic", phase="execute", active_child_paths=(feat,))
    feature = _item(feat, type="Feature", phase="finish", work_status="resolved", active_child_paths=(grandchild,))
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
    epic = _item(root, type="Epic", phase="execute", active_child_paths=(feat,))
    feature = _item(feat, type="Feature", phase="finish", work_status="resolved", active_child_paths=(grandchild,))
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
    epic = _item(root, type="Epic", phase="finish", active_child_paths=(old_child, *child_paths))
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


def test_a_descendant_at_finish_forks_off_the_epic_branch_too() -> None:
    item = _item("work/epic-r/children/bug-x", type="Bug", phase="finish")

    action, _ = _epic_anchor(item, "finish", is_root=False)

    assert isinstance(action, orchestrate.WorktreeAction)
    assert action.action == "fork-child"
    assert action.base_branch == "epic/root"


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


def _cold_start(item, phase: str, *, is_root: bool, repo_path: str | None = "/repo", claimed: bool = False):
    """Rule 4's setup: no stamp, no epic anchor, nothing in the inventory."""
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
        inventory={},
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


def _main_prompt(phase: str, *, is_root: bool) -> str:
    return orchestrate._prompt(
        path="work/epic-r/children/bug-x" if not is_root else "work/epic-r",
        key="gw-k",
        phase=phase,
        workspace="/ws",
        merge_target="main",
        tail=None,
        worktree=orchestrate.WorktreeAction("main", "/repo", "main", None, True),
        is_root=is_root,
    )


def test_a_read_only_descendant_in_the_main_checkout_is_not_told_to_record_it() -> None:
    """Suppressing the stamp at the planner is half the fix; the other half is
    not instructing the worker to write it by hand."""
    for phase in sorted(orchestrate.READ_ONLY_PHASES):
        assert "Record the worktree" not in _main_prompt(phase, is_root=False), phase


def test_a_code_stage_descendant_in_the_main_checkout_is_still_told_to_record_it() -> None:
    for phase in ("execute", "finish"):
        prompt = _main_prompt(phase, is_root=False)
        assert "Record the worktree as `/repo`" in prompt, phase
        assert "cannot be detected from where you are" in prompt, phase


def test_the_subtree_root_is_told_to_record_its_placement_at_every_phase() -> None:
    for phase in ("design", "plan", "execute", "finish"):
        assert "Record the worktree as `/repo`" in _main_prompt(phase, is_root=True), phase


def test_the_subtree_root_records_a_pathless_mint_at_a_read_only_phase() -> None:
    """The cold-start mint is a `design`-phase root dispatch with no path yet;
    this is the line that gets the epic anchor written at all."""
    prompt = orchestrate._prompt(
        path="work/epic-r",
        key="gw-k",
        phase="design",
        workspace="/ws",
        merge_target="main",
        tail=None,
        worktree=orchestrate.WorktreeAction("create-top-level", None, "epic/root-1a2b3c4d", "main", None),
        is_root=True,
    )

    assert "Record the worktree you end up in" in prompt
    assert "epic/root-1a2b3c4d" in prompt


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
            active_child_paths=(child,),
            affects=("packages/root",),
        ),
        _item(child, type="TechDebt", phase="design", affects=("packages/a",)),
    )

    result = _plan(items, root, worktree_exists={"/epic": True, "/repo": True}, repo_path="/repo")

    dispatch = next(d for d in result.dispatches if d.slug == child)
    assert dispatch.worktree.action == "reuse"
    assert dispatch.worktree.path == "/epic"
    assert "Record the worktree" not in dispatch.prompt


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
            active_child_paths=(first_child, second_child, code_child),
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
