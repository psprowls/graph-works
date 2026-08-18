"""`plan()` takes plain data and returns plain data, so every rule gets a fixture."""

from __future__ import annotations

import dataclasses

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.pipeline import PACKAGED_PIPELINE
from work_tracker_okf.hierarchy import descend
from work_tracker_okf.items import WorkItem


def _item(slug: str, **overrides) -> WorkItem:
    """A minimally valid `WorkItem`. Every field is named so a projection change
    fails here loudly rather than silently defaulting."""
    base = dict(
        slug=slug,
        path=f"work/{slug}.md",
        archived=False,
        type="Feature",
        title=slug,
        description="d",
        status="stable",
        workflow_status="open",
        phase="plan",
        effort="medium",
        opened="2026-08-01",
        updated="2026-08-01",
        affects=("packages/a",),
        parent=None,
        depends_on=(),
        children=(),
        owner=None,
        resolved_in=None,
        worktree=None,
        branch=None,
        superseded_by=None,
        tags=(),
        sources=(),
        has_spec_doc=True,
        has_plan_doc=False,
    )
    base.update(overrides)
    return WorkItem(**base)  # type: ignore[arg-type]


def _plan(items, root, **overrides):
    kwargs = dict(
        pipeline=PACKAGED_PIPELINE,
        auto_drive={},
        max_parallel=2,
        permission_mode="bypassPermissions",
        live=(),
        worktree_exists={},
        workspace="/ws",
        default_base="main",
    )
    kwargs.update(overrides)
    return orchestrate.plan(items, root, **kwargs)


def test_a_lone_item_at_plan_dispatches_writing_plans():
    result = _plan((_item("a"),), "a")
    assert len(result.dispatches) == 1
    dispatch = result.dispatches[0]
    assert dispatch.key == "a#plan"
    assert dispatch.skill == "writing-plans"
    assert dispatch.mode == "autonomous"
    assert dispatch.model is None
    assert dispatch.merge_target == "main"
    assert dispatch.prompt.splitlines() == [
        "Run /graph-works:next a.",
        "GRAPH_WORKS_DIR=/ws",
        "Dispatch key: a#plan",
        "Send worker_done when the stage artifact is written and the item advanced.",
    ]


def test_the_prompt_constants_are_the_values_it_emits():
    result = _plan((_item("a"),), "a")
    prompt = result.dispatches[0].prompt
    assert prompt.splitlines()[0] == f"Run {orchestrate.DISPATCH_COMMAND} a."
    assert prompt.splitlines()[1] == f"{orchestrate.WORKSPACE_VAR}=/ws"


def test_plan_is_pure():
    items = (_item("a"),)
    assert _plan(items, "a") == _plan(items, "a")


def test_a_terminal_root_short_circuits():
    result = _plan((_item("a", workflow_status="resolved"),), "a")
    assert result.terminal is True
    assert result.dispatches == () and result.advances == () and result.blocked == ()


def test_a_done_root_short_circuits():
    assert _plan((_item("a", phase="done"),), "a").terminal is True


def test_an_unknown_root_is_an_invalid_blocker():
    result = _plan((_item("a"),), "nope")
    assert [(b.slug, b.kind) for b in result.blocked] == [("nope", "invalid")]


def test_an_unknown_live_key_warns_rather_than_blocks():
    result = _plan((_item("a"),), "a", live=("ghost#plan",))
    assert result.warnings == ("live key 'ghost#plan' matches no known item",)
    assert result.slots_free == 1


def test_an_item_with_no_affects_is_blocked():
    result = _plan((_item("a", affects=()),), "a")
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "affects-overlap")]


def test_affects_overlap_with_a_live_item_blocks():
    items = (_item("a"), _item("b", affects=("packages/b",), opened="2026-08-02"))
    result = _plan(items, "a", live=("b#plan",))
    assert result.blocked == ()  # b is live, not in a's subtree, and its affects don't overlap
    # now put both under one root so both are candidates
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (root, _item("a", parent="root"), _item("b", parent="root", opened="2026-08-02"))
    result = _plan(items, "root")
    assert [d.slug for d in result.dispatches] == ["a"]
    assert [(b.slug, b.kind) for b in result.blocked] == [("b", "affects-overlap")]


def test_capacity_blocks_the_survivors_past_the_free_slots():
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (
        root,
        _item("a", parent="root", affects=("packages/a",)),
        _item("b", parent="root", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", max_parallel=1)
    assert [d.slug for d in result.dispatches] == ["a"]
    assert [(b.slug, b.kind) for b in result.blocked] == [("b", "capacity")]


def test_worktree_rule_1_reuses_the_items_own_stamp():
    items = (_item("a", worktree="/wt/a", branch="feature/a"),)
    action = _plan(items, "a", worktree_exists={"/wt/a": True}).dispatches[0].worktree
    assert (action.action, action.path, action.branch, action.exists) == (
        "reuse",
        "/wt/a",
        "feature/a",
        True,
    )


def test_two_items_stamped_with_the_same_path_do_not_both_reuse_it():
    # A shared epic worktree that provenance auto-stamped onto two siblings
    # (e.g. each ran a session with cwd inside it) must not dispatch both as
    # "reuse" -- that puts two concurrent workers in one directory. The
    # second must fork off its own branch instead.
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (
        root,
        _item("a", parent="root", worktree="/wt/epic", branch="epic/root", affects=("packages/a",)),
        _item(
            "b",
            parent="root",
            worktree="/wt/epic",
            branch="epic/root",
            affects=("packages/b",),
            opened="2026-08-02",
        ),
    )
    result = _plan(items, "root", max_parallel=3)
    assert result.blocked == ()
    dispatched = {d.slug: d.worktree for d in result.dispatches}
    assert dispatched["a"].action == "reuse"
    assert dispatched["a"].path == "/wt/epic"
    assert dispatched["b"].action == "fork-child"
    assert dispatched["b"].path is None
    assert dispatched["b"].base_branch == "epic/root"
    assert dispatched["b"].branch == "feature/b"


def test_a_live_dispatch_at_the_items_own_path_also_forks_rather_than_reuses():
    # Same hazard, but the occupier is a `--live` dispatch from another
    # invocation rather than a sibling in this same plan. "a" is the live
    # item (its worktree feeds `live_worktrees`); "b" is a different item
    # stamped with the identical path and must not also reuse it.
    live_item = _item("a", worktree="/wt/a", branch="feature/a", affects=("packages/live-a",))
    other = _item("b", worktree="/wt/a", branch="feature/a", affects=("packages/b",), opened="2026-08-02")
    result = _plan((live_item, other), "b", live=("a#execute",), worktree_exists={"/wt/a": True})
    action = result.dispatches[0].worktree
    assert action.action == "fork-child"
    assert action.base_branch == "feature/a"


def test_rule_1_does_not_fork_a_branch_onto_itself():
    # `_item("a")` derives `feature/a`. When the item's *stamped* branch is
    # already that name and its path is claimed elsewhere, the unguarded fork
    # hands the backend a create-branch identical to its own base.
    dispatching = _item("a", worktree="/wt/shared", branch="feature/a")
    occupier = _item("b", worktree="/wt/shared", branch="feature/b", affects=("packages/b",))
    result = _plan((dispatching, occupier), "a", live=("b#execute",), worktree_exists={"/wt/shared": True})
    action = result.dispatches[0].worktree
    assert action.action == "fork-child"
    assert action.base_branch == "feature/a"
    assert action.branch == "feature/a-plan"


def test_rule_1_leaves_a_non_colliding_fork_unsuffixed():
    dispatching = _item("a", worktree="/wt/shared", branch="epic/parent")
    occupier = _item("b", worktree="/wt/shared", branch="feature/b", affects=("packages/b",))
    result = _plan((dispatching, occupier), "a", live=("b#execute",), worktree_exists={"/wt/shared": True})
    action = result.dispatches[0].worktree
    assert (action.base_branch, action.branch) == ("epic/parent", "feature/a")


def test_rule_3_does_not_fork_a_branch_onto_itself_either():
    # `_epic_stamp` falls back to the first stamped descendant in pick order,
    # so `epic_branch` can already be some item's derived name. Guarding rule 1
    # alone would leave the same defect behind a rarer door.
    root = _item(
        "root",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="feature/a",
    )
    child = _item("a", parent="root")
    occupier = _item("live", worktree="/wt/epic", branch="feature/live", affects=("packages/live",))
    result = _plan((root, child, occupier), "root", live=("live#execute",), worktree_exists={"/wt/epic": True})
    action = result.dispatches[0].worktree
    assert action.action == "fork-child"
    assert action.base_branch == "feature/a"
    assert action.branch == "feature/a-plan"


def test_no_planned_dispatch_forks_a_branch_off_itself():
    """The assertion that would have caught this. It sits beside the
    `--descend`/auto-drive agreement property for the same reason: a fifth
    worktree rule added later is checked by construction, not by remembering."""
    occupier = _item("b", worktree="/wt/shared", branch="feature/b", affects=("packages/b",))
    epic_occupier = _item("live", worktree="/wt/epic", branch="feature/live", affects=("packages/live",))
    cases = (
        ((_item("a"),), "a", {}),
        (
            (_item("a", worktree="/wt/shared", branch="feature/a"), occupier),
            "a",
            {"live": ("b#execute",), "worktree_exists": {"/wt/shared": True}},
        ),
        (
            (_item("a", worktree="/wt/shared", branch="epic/parent"), occupier),
            "a",
            {"live": ("b#execute",), "worktree_exists": {"/wt/shared": True}},
        ),
        (
            (
                _item(
                    "root",
                    type="Epic",
                    phase="execute",
                    affects=("packages/root",),
                    worktree="/wt/epic",
                    branch="feature/a",
                ),
                _item("a", parent="root"),
                epic_occupier,
            ),
            "root",
            {"live": ("live#execute",), "worktree_exists": {"/wt/epic": True}},
        ),
        (
            (
                _item("root", type="Epic", phase="execute", affects=("packages/root",)),
                _item("a", parent="root"),
            ),
            "root",
            {},
        ),
    )
    for items, root, kwargs in cases:
        result = _plan(items, root, **kwargs)
        assert result.dispatches, (root, kwargs)
        for dispatch in result.dispatches:
            action = dispatch.worktree
            assert action.branch != action.base_branch, (root, dispatch.slug, action)


def test_worktree_rule_2_reuses_an_unoccupied_epic_worktree():
    root = _item(
        "root",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="epic/root",
    )
    items = (root, _item("a", parent="root"))
    action = _plan(items, "root").dispatches[0].worktree
    assert (action.action, action.path, action.branch) == ("reuse", "/wt/epic", "epic/root")


def test_worktree_rule_3_forks_a_child_branch_when_the_epic_worktree_is_live():
    root = _item(
        "root",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="epic/root",
    )
    items = (
        root,
        _item("a", parent="root", worktree="/wt/epic", branch="epic/root", affects=("packages/a",)),
        _item("2026-08-02-epic-feature-b", parent="root", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", live=("a#plan",), max_parallel=3)
    action = result.dispatches[0].worktree
    assert (action.action, action.path, action.branch, action.base_branch) == (
        "fork-child",
        None,
        "feature/b",
        "epic/root",
    )


def test_worktree_rule_4_creates_the_epic_worktree_top_level():
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (root, _item("a", parent="root"))
    action = _plan(items, "root").dispatches[0].worktree
    assert (action.action, action.path, action.base_branch) == ("create-top-level", None, "main")
    assert action.branch == "epic/root"


def test_a_pathless_worktree_action_dispatches_by_default():
    """`provisions_worktrees` defaults to `True`: every existing caller gets
    today's behaviour (a `create-top-level`/`fork-child` action with no path,
    left for the backend to provision) unless it opts into the check."""
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (root, _item("a", parent="root"))
    result = _plan(items, "root")
    assert len(result.dispatches) == 1


def test_a_pathless_worktree_action_is_blocked_when_the_backend_cannot_provision_one():
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (root, _item("a", parent="root"))
    result = _plan(items, "root", provisions_worktrees=False)
    assert result.dispatches == ()
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "worktree-unsupported")]


def test_a_fork_child_action_is_also_blocked_without_worktree_provisioning():
    root = _item(
        "root",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="epic/root",
    )
    items = (
        root,
        _item("a", parent="root", worktree="/wt/epic", branch="epic/root", affects=("packages/a",)),
        _item("2026-08-02-epic-feature-b", parent="root", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", live=("a#plan",), max_parallel=3, provisions_worktrees=False)
    assert result.dispatches == ()
    # "a" is blocked separately (affects-overlap with its own live dispatch);
    # what this test cares about is "b", the fork-child candidate.
    by_slug = {b.slug: b.kind for b in result.blocked}
    assert by_slug["2026-08-02-epic-feature-b"] == "worktree-unsupported"


def test_a_reused_worktree_action_is_unaffected_by_the_provisioning_flag():
    """Rule 1/2 reuse -- always a concrete path -- never trips the check."""
    items = (_item("a", worktree="/wt/a", branch="feature/a"),)
    result = _plan(items, "a", worktree_exists={"/wt/a": True}, provisions_worktrees=False)
    assert len(result.dispatches) == 1


def test_the_epic_worktree_falls_back_to_the_first_stamped_descendant():
    # The root itself carries no stamp; "a" does. `_epic_stamp`'s descendant
    # fallback must find it and use it as "the epic worktree" for "b" -- the
    # discriminator is `base_branch`: without the fallback, "b" would get
    # `create-top-level` off `default_base`, not `fork-child` off "a"'s branch.
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (
        root,
        _item("a", parent="root", worktree="/wt/a", branch="feature/a", affects=("packages/a",)),
        _item("b", parent="root", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", max_parallel=3)
    dispatched = {d.slug: d.worktree for d in result.dispatches}
    assert dispatched["a"].action == "reuse"
    assert dispatched["b"].action == "fork-child"
    assert dispatched["b"].base_branch == "feature/a"


def test_the_second_dispatch_in_the_same_plan_is_worktree_pending():
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (
        root,
        _item("a", parent="root", affects=("packages/a",)),
        _item("b", parent="root", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", max_parallel=2)
    assert [d.slug for d in result.dispatches] == ["a"]
    assert [(b.slug, b.kind) for b in result.blocked] == [("b", "worktree-pending")]


def test_an_epic_with_all_children_terminal_is_a_planned_advance():
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (root, _item("a", parent="root", workflow_status="resolved"))
    result = _plan(items, "root")
    assert [(a.slug, a.reason) for a in result.advances] == [("root", "epic children complete")]


def test_a_planned_advance_carries_the_epic_worktree():
    root = _item(
        "root",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="epic/root",
    )
    items = (root, _item("a", parent="root", workflow_status="resolved"))
    advance = _plan(items, "root").advances[0]
    assert (advance.worktree, advance.branch) == ("/wt/epic", "epic/root")


def test_a_dependency_blocker_classifies_as_deps():
    items = (_item("a", depends_on=("b",)), _item("b", opened="2026-08-02"))
    result = _plan(items, "a")
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "deps")]


def test_a_mitigated_item_classifies_as_human():
    result = _plan((_item("a", workflow_status="mitigated"),), "a")
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "human")]


def test_an_effort_required_blocker_classifies_as_effort_required():
    # A TestGap with no phase and no effort forks at entry: `route()` refuses
    # with a blocker starting "effort required", which `_classify` maps here.
    item = _item("a", type="TestGap", phase=None, effort=None)
    result = _plan((item,), "a")
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "effort-required")]


def test_a_parent_cycle_is_blocked_as_invalid_rather_than_looping_forever():
    # x and y are each other's parent, and both are gated (Epic, phase
    # execute, a non-terminal child) -- the walk must detect the revisit
    # rather than recursing forever.
    items = (
        _item("x", type="Epic", phase="execute", parent="y", affects=("packages/x",)),
        _item("y", type="Epic", phase="execute", parent="x", affects=("packages/y",)),
    )
    result = _plan(items, "x")
    assert [(b.slug, b.kind) for b in result.blocked] == [("y", "invalid")]
    assert "cycle" in result.blocked[0].reason


def test_the_frontier_depth_cap_blocks_rather_than_walking_forever():
    chain = [_item("root", type="Epic", phase="execute", affects=("packages/root",))]
    for i in range(1, 34):
        chain.append(_item(f"n{i}", type="Epic", phase="execute", parent=chain[-1].slug, affects=(f"packages/n{i}",)))
    result = _plan(tuple(chain), "root")
    assert any(b.kind == "invalid" and "depth cap" in b.reason for b in result.blocked)


def test_an_item_held_by_an_open_decision_is_blocked_not_redispatched():
    """The gate that stops the infinite-redispatch loop: a design-stage item
    named in an open decision's `affects` must not receive another dispatch
    until a human answers it."""
    item = _item("a", phase="design")
    result = _plan((item,), "a", held_decisions=frozenset({"a"}))
    assert result.dispatches == ()
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "decisions")]


def test_held_decisions_defaults_to_empty_and_does_not_block():
    item = _item("a", phase="design")
    result = _plan((item,), "a")
    assert len(result.dispatches) == 1


def test_held_decisions_is_read_per_slug_not_globally():
    """Being held applies to the slug the decision names, not every candidate
    in the same plan."""
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (
        root,
        _item("a", parent="root", phase="design", affects=("packages/a",)),
        _item("b", parent="root", phase="design", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", held_decisions=frozenset({"a"}))
    assert [d.slug for d in result.dispatches] == ["b"]
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "decisions")]


def test_every_blocked_kind_is_declared():
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (root, _item("a", parent="root", workflow_status="mitigated"))
    for blocked in _plan(items, "root").blocked:
        assert blocked.kind in orchestrate.BLOCKED_KINDS


def test_the_model_comes_from_the_auto_drive_block():
    rules = {"models": {"plan": "sonnet"}, "overrides": [{"match": {"kind": "Epic"}, "model": "opus"}]}
    result = _plan((_item("a"),), "a", auto_drive=rules)
    assert result.dispatches[0].model == "sonnet"
    epic = _item("a", type="Epic", phase="plan")
    assert _plan((epic,), "a", auto_drive=rules).dispatches[0].model == "opus"


def test_the_prompt_tail_substitutes_the_merge_target():
    table = dict(PACKAGED_PIPELINE)
    table["single"] = dataclasses.replace(table["single"], prompt_tail="merge into {merge_target}")
    result = _plan((_item("a"),), "a", pipeline=table)
    assert result.dispatches[0].prompt.splitlines()[-1] == "merge into main"


def test_a_tail_with_stray_braces_is_left_alone():
    table = dict(PACKAGED_PIPELINE)
    table["single"] = dataclasses.replace(table["single"], prompt_tail="use {unknown} literally")
    result = _plan((_item("a"),), "a", pipeline=table)
    assert result.dispatches[0].prompt.splitlines()[-1] == "use {unknown} literally"


def test_branch_names_strip_the_date_and_the_epic_filing_marker():
    assert orchestrate.branch_name("2026-08-11-epic-graph-works-core", "Epic") == ("epic/graph-works-core")
    assert orchestrate.branch_name("2026-08-13-epic-feature-work-pipeline-x", "Feature") == ("feature/work-pipeline-x")
    assert orchestrate.branch_name("2026-08-13-bug-x", "") == "work/bug-x"


def test_the_frontier_agrees_with_descend():
    """The bond that keeps `--descend` and auto-drive from disagreeing: for any
    tree where `descend` lands on a leaf, that leaf is a node the frontier
    visited (as a candidate, an advance, or a blocker)."""
    trees = (
        (_item("a"),),
        (
            _item("root", type="Epic", phase="execute", affects=("packages/root",)),
            _item("a", parent="root"),
        ),
        (
            _item("root", type="Epic", phase="execute", affects=("packages/root",)),
            _item("mid", parent="root", type="Feature", phase="execute", affects=("packages/m",)),
            _item("leaf", parent="mid", affects=("packages/l",)),
        ),
        (
            _item("root", type="Epic", phase="execute", affects=("packages/root",)),
            _item("a", parent="root", workflow_status="resolved"),
        ),
    )
    for items in trees:
        landed = descend(items, items[0].slug)
        if landed.leaf is None:
            continue
        result = _plan(items, items[0].slug)
        visited = (
            {d.slug for d in result.dispatches} | {a.slug for a in result.advances} | {b.slug for b in result.blocked}
        )
        assert landed.leaf in visited, (items[0].slug, landed.leaf, visited)


def test_the_facade_forwards_every_plan_field_to_the_result():
    """`OrchestrateResult`'s pass-through properties must track every field on
    `OrchestratePlan` -- a field added there without a matching property would
    leave the facade partial. `warnings` is deliberately excluded:
    `OrchestrateResult.warnings` combines the plan's warnings with the
    decisions ledger's, so it is not a plain pass-through.
    """
    root = _item(
        "root",
        type="Epic",
        phase="execute",
        affects=("packages/root",),
        worktree="/wt/epic",
        branch="epic/root",
    )
    items = (root, _item("a", parent="root", workflow_status="resolved"))
    plan_obj = _plan(items, "root", live=("ghost#plan",), max_parallel=3)
    result = orchestrate.OrchestrateResult(plan=plan_obj)
    for field in dataclasses.fields(orchestrate.OrchestratePlan):
        if field.name == "warnings":
            continue
        assert getattr(result, field.name) == getattr(plan_obj, field.name), field.name


# --- the relay-untailed blocker ---------------------------------------------


def test_an_untailed_relay_entry_blocks_rather_than_dispatching():
    # The `finishing-relay` skill triggers on the literal `Auto-drive context:`
    # line. Unset, a finish-stage worker falls into the interactive menu and
    # blocks forever with no human watching -- so the refusal is computed here,
    # before any worker launches.
    result = _plan((_item("a", phase="finish", workflow_status="in-progress", owner="pat"),), "a")
    assert result.dispatches == ()
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "relay-untailed")]
    assert "workflow.pipeline.branch.prompt_tail" in result.blocked[0].reason


def test_a_tailed_relay_entry_dispatches_with_its_tail_substituted():
    table = dict(PACKAGED_PIPELINE)
    table["branch"] = dataclasses.replace(
        table["branch"], prompt_tail="Auto-drive context: merge target is {merge_target}."
    )
    items = (_item("a", phase="finish", workflow_status="in-progress", owner="pat"),)
    result = _plan(items, "a", pipeline=table)
    assert [d.slug for d in result.dispatches] == ["a"]
    assert result.dispatches[0].prompt.splitlines()[-1] == "Auto-drive context: merge target is main."


def test_the_blocker_is_keyed_on_mode_not_on_the_branch_variant():
    # A workspace may set any variant to `relay`. Checking `branch` by name
    # would miss it -- and `mode` is the property that means "no human is in
    # the room but a decision is needed".
    table = dict(PACKAGED_PIPELINE)
    table["single"] = dataclasses.replace(table["single"], mode="relay", prompt_tail=None)
    result = _plan((_item("a"),), "a", pipeline=table)
    assert result.dispatches == ()
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "relay-untailed")]
    assert "workflow.pipeline.single.prompt_tail" in result.blocked[0].reason


@pytest.mark.parametrize("tail", ["", "   "])
def test_a_blank_tail_is_no_tail(tail):
    table = dict(PACKAGED_PIPELINE)
    table["branch"] = dataclasses.replace(table["branch"], prompt_tail=tail)
    items = (_item("a", phase="finish", workflow_status="in-progress", owner="pat"),)
    result = _plan(items, "a", pipeline=table)
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "relay-untailed")]


def _dispatch_for(result, slug):
    return next(d for d in result.dispatches if d.slug == slug)


def test_cold_start_runs_in_the_main_checkout_when_a_repo_is_known():
    items = [_item("a")]
    result = _plan(items, "a", repo_path="/repo", worktree_exists={"/repo": True})
    action = _dispatch_for(result, "a").worktree
    assert action.action == "main"
    assert action.path == "/repo"
    assert action.branch == "main"  # _plan's default_base
    assert action.base_branch is None
    assert action.exists is True


def test_cold_start_without_a_repo_path_still_creates_a_top_level_worktree():
    """The regression guard on the new parameter's default: every existing
    caller passes nothing and must see exactly today's plan."""
    items = [_item("a")]
    action = _dispatch_for(_plan(items, "a"), "a").worktree
    assert action.action == "create-top-level"
    assert action.path is None
    assert action.base_branch == "main"


def test_a_live_item_reuses_its_own_stamp_instead_of_forking_off_itself():
    """Self-exclusion. `_frontier` never filters the live set out of the
    candidates, so an item is routinely re-proposed while still live. Without
    subtracting its own slug from the worktree owner map, its own stamp reads
    as held-by-another and it forks off itself.

    Exercised directly against `_resolve_worktree` rather than through the
    full `plan()` pipeline: `plan()`'s affects-overlap gate (pass 1) has its
    own, pre-existing self-collision in exactly this scenario -- a live
    item's own affects always land in `live_affects`, so it never survives to
    `_resolve_worktree` at all -- and that gate's behaviour is pinned as-is by
    `test_worktree_rule_3_forks_a_child_branch_when_the_epic_worktree_is_live`,
    out of scope for this task.
    """
    item = _item("a", worktree="/wt/a", branch="psprowls/a")
    action, claims_epic_slot = orchestrate._resolve_worktree(
        item,
        epic_worktree_path=None,
        epic_branch="epic/a",
        live_worktree_owners={"/wt/a": {"a"}},
        accepted_worktrees=set(),
        epic_worktree_claimed=False,
        worktree_exists={"/wt/a": True},
        default_base="main",
        phase="plan",
        repo_path=None,
    )
    assert action is not None
    assert action.action == "reuse"
    assert action.path == "/wt/a"
    assert action.exists is True
    assert claims_epic_slot is False


def test_an_items_own_stamp_on_the_repo_root_yields_main_when_unoccupied():
    """Rule 1's first branch: the item's own stamp is the repo root itself,
    and nothing else holds it -- `"main"`, not `"reuse"`, and with a real
    `exists` rather than `None`."""
    item = _item("a", worktree="/repo", branch="main")
    result = _plan([item], "a", repo_path="/repo", worktree_exists={"/repo": True})
    action = _dispatch_for(result, "a").worktree
    assert action.action == "main"
    assert action.path == "/repo"
    assert action.branch == "main"
    assert action.base_branch is None
    assert action.exists is True


def test_a_main_mode_item_is_evicted_once_another_slug_holds_the_checkout():
    items = [
        _item("a", worktree="/repo", branch="main"),
        _item("b", worktree="/repo", branch="main", affects=("packages/b",)),
    ]
    result = _plan(items, "a", live=("b#plan",), repo_path="/repo", max_parallel=3)
    action = _dispatch_for(result, "a").worktree
    assert action.action == "fork-child"
    assert action.base_branch == "main"
    assert action.path is None


def test_an_epic_stamp_on_the_repo_root_resolves_to_main_not_reuse():
    # `phase="execute"` is required for the epic to gate through to its child
    # (see `child_gated_node`): an Epic still at plan/design is its own leaf
    # and would be the candidate itself, never descending to "a".
    items = [
        _item(
            "epic",
            type="Epic",
            phase="execute",
            worktree="/repo",
            branch="main",
            affects=("packages/e",),
            children=("a",),
        ),
        _item("a", parent="epic"),
    ]
    result = _plan(items, "epic", repo_path="/repo", worktree_exists={"/repo": True})
    action = _dispatch_for(result, "a").worktree
    assert action.action == "main"
    assert action.path == "/repo"


def test_a_main_dispatch_tells_the_worker_to_record_its_location():
    """The worker cannot detect this for itself -- git reports no worktree for
    the main checkout -- so the prompt has to say it."""
    result = _plan([_item("a")], "a", repo_path="/repo")
    prompt = _dispatch_for(result, "a").prompt
    assert "main checkout" in prompt
    assert "/repo" in prompt
    # Names no flag: this package ships no CLI, so a flag name here would be
    # an invention that goes stale the moment one is written.
    assert "--worktree" not in prompt
    assert "--branch" not in prompt


def test_a_non_main_dispatch_says_nothing_about_the_main_checkout():
    prompt = _dispatch_for(_plan([_item("a")], "a"), "a").prompt
    assert "main checkout" not in prompt


def test_a_relay_untailed_item_does_not_claim_the_epic_worktree_slot():
    # An item that cannot dispatch should not consume the epic worktree on its
    # way out. Without the reorder, `_resolve_worktree` runs first, sets
    # `epic_worktree_claimed`, and the next candidate gets `worktree-pending`.
    root = _item("root", type="Epic", phase="execute", affects=("packages/root",))
    items = (
        root,
        _item(
            "a",
            parent="root",
            phase="finish",
            workflow_status="in-progress",
            owner="pat",
            affects=("packages/a",),
        ),
        _item("b", parent="root", affects=("packages/b",), opened="2026-08-02"),
    )
    result = _plan(items, "root", max_parallel=3)
    by_slug = {b.slug: b.kind for b in result.blocked}
    assert by_slug["a"] == "relay-untailed"
    # "b" still gets the top-level create, not `worktree-pending`.
    dispatched = {d.slug: d.worktree for d in result.dispatches}
    assert dispatched["b"].action == "create-top-level"
