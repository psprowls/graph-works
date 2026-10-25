"""Pure canonical-path orchestration planning."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from gw_kernel.workspace import pipeline
from gw_kernel.workspace.repos import ItemRepo
from gw_orchestrate import commands as orchestrate
from gw_orchestrate.commands import BlockedItem
from gw_orchestrate.placing import compute
from gw_pipeline.finish import FinishPlan, FinishTarget
from gw_pipeline.placement import branch_name
from gw_pipeline.repo_context import RepositoryContext
from subagents_io.dispatch import WorktreePlacement
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import LegacyPlacement, Stamp, WorkItem
from work_tracker_okf.pipeline import PACKAGED_DEFINITION, ArtifactSpec
from work_tracker_okf.workflow import RETURN_TO_EXECUTE, RouteState, route

_KEEP: Any = object()


def _restamp(
    item: WorkItem, *, worktree: Any = _KEEP, branch: Any = _KEEP, repo_stamps: Any = None, **rest: Any
) -> WorkItem:
    """`dataclasses.replace` for the retired stored placement, which lives on `legacy_placement`."""
    old = item.legacy_placement or LegacyPlacement(None, None, {})
    wt = old.worktree if worktree is _KEEP else worktree
    br = old.branch if branch is _KEEP else branch
    stamps = dict(old.repos) if repo_stamps is None else repo_stamps
    legacy = LegacyPlacement(wt, br, stamps) if (wt or br or stamps) else None
    return dataclasses.replace(item, legacy_placement=legacy, **rest)


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
    worktree, branch = base.pop("worktree", None), base.pop("branch", None)
    stamps = dict(base.pop("repo_stamps", {}))  # type: ignore[call-overload]
    if "legacy_placement" not in overrides and (worktree or branch or stamps):
        base["legacy_placement"] = LegacyPlacement(worktree, branch, stamps)  # type: ignore[arg-type]
    return WorkItem(**base)  # type: ignore[arg-type]


WORKTREES = "/wt"


def _plan(items: tuple[WorkItem, ...], root: str, **overrides: object):
    kwargs: dict[str, object] = {
        "dispatch_rules": (),
        "max_parallel": 2,
        "live": (),
        "workspace": "/ws",
        "default_base": "main",
        "worktrees_dir": WORKTREES,
    }
    kwargs.update(overrides)
    return orchestrate.plan(items, root, **kwargs)  # type: ignore[arg-type]


REPO = ItemRepo("code", Path("/repo"), "frontmatter")


def _code_context() -> RepositoryContext:
    return RepositoryContext("git-code", "/repo", "main", True, {}, {}, True, checkout_usable_by_path={"/repo": True})


def _code_plan(items: tuple[WorkItem, ...], root: str, **overrides: object):
    """`_plan` with every item in the one declared `code` repository at `/repo`."""
    kwargs: dict[str, object] = {
        "item_repos": {item.path: REPO for item in items},
        "repo_contexts": {"git-code": _code_context()},
    }
    kwargs.update(overrides)
    return _plan(items, root, **kwargs)


def _computed(item: WorkItem, items: tuple[WorkItem, ...], repo: ItemRepo = REPO):
    placement = compute(item, {i.path: i for i in items}, repo, worktrees_dir=Path(WORKTREES), default_base="main")
    assert placement is not None
    return placement


def _live_finish_holding(worktree: str) -> tuple[WorkItem, str, dict[str, FinishPlan]]:
    """An unrelated live finish whose merge target checkout is *worktree*."""
    outside = _item("work/feature-outside", phase="finish", work_status="in-progress", affects=("packages/other",))
    target = FinishTarget(REPO, "/wt/code/feature/outside", "feature/outside", "main", worktree)
    return (
        outside,
        orchestrate.session_name(outside.path, "Feature", "finish"),
        {outside.path: FinishPlan((target,), ())},
    )


def _epic_with_children(*children: WorkItem, root: str = "work/epic-p") -> tuple[WorkItem, ...]:
    epic = _item(
        root,
        type="Epic",
        phase="execute",
        work_status="in-progress",
        child_paths=tuple(child.path for child in children),
        affects=("packages/root",),
    )
    return (epic, *children)


def test_worktree_unsupported_left_the_closed_vocabulary() -> None:
    assert "worktree-unsupported" not in orchestrate.BLOCKED_KINDS
    # D-007: the other three keep their surviving non-placement emitters.
    assert {"worktree-pending", "worktree-unprovable", "worktree-ambiguous"} <= orchestrate.BLOCKED_KINDS


def test_every_code_dispatch_carries_its_computed_placement() -> None:
    root = "work/epic-p"
    children = (
        _item(f"{root}/children/feature-d", phase="design", has_design_artifact=False, affects=("packages/d",)),
        _item(f"{root}/children/feature-p", phase="plan", affects=("packages/p",)),
        _execute_item(f"{root}/children/feature-e", worktree=None, branch=None, affects=("packages/e",)),
        _item(
            f"{root}/children/feature-f",
            phase="finish",
            work_status="in-progress",
            owner="pat",
            worktree="/legacy/f",
            branch="psprowls/feature-f",
            affects=("packages/f",),
        ),
    )
    items = _epic_with_children(*children, root=root)
    result = _code_plan(items, root, max_parallel=4, dispatch_rules=_branch_tail_rules())
    assert {d.phase for d in result.dispatches} == {"design", "plan", "execute", "finish"}, result.blocked
    by_path = {item.path: item for item in items}
    for dispatch in result.dispatches:
        expected = _computed(by_path[dispatch.slug], items)
        assert result.placements[dispatch.key] == expected
        assert dispatch.worktree == WorktreePlacement(
            str(expected.worktree), expected.branch, str(expected.parent_worktree)
        )
        assert expected.worktree == Path(WORKTREES) / "code" / branch_name(dispatch.slug, "Feature")


def test_child_placement_parents_on_the_epic() -> None:
    root = "work/epic-p"
    child = _execute_item(f"{root}/children/feature-c", worktree=None, branch=None)
    items = _epic_with_children(child, root=root)
    result = _code_plan(items, root)
    [dispatch] = result.dispatches
    placement = result.placements[dispatch.key]
    assert placement.parent_branch == branch_name(root, "Epic")
    assert placement.parent_worktree == _computed(items[0], items).worktree
    assert placement.owner_path == root
    assert dispatch.merge_target == branch_name(root, "Epic")
    lone = _execute_item("work/feature-lone", worktree=None, branch=None)
    [solo] = _code_plan((lone,), lone.path).dispatches
    assert (solo.worktree.parent_path, solo.merge_target) == ("/repo", "main")


def test_finish_dispatch_places_in_the_items_own_worktree() -> None:
    root = "work/epic-p"
    child = _item(f"{root}/children/feature-f", phase="finish", work_status="in-progress", owner="pat")
    items = _epic_with_children(child, root=root)
    epic_worktree = str(_computed(items[0], items).worktree)
    target = FinishTarget(REPO, "/legacy/source", "psprowls/feature-f", branch_name(root, "Epic"), epic_worktree)
    result = _code_plan(
        items, root, dispatch_rules=_branch_tail_rules(), finish_plans={child.path: FinishPlan((target,), ())}
    )
    [dispatch] = result.dispatches
    assert dispatch.worktree.path == str(_computed(child, items).worktree) != "/legacy/source"
    assert result.finish_targets[dispatch.key] == (target,)
    assert result.dispatch_repos[dispatch.key] == REPO


def test_workspace_only_execute_dispatches_at_workspace_root() -> None:
    """A `gw:workspace` item at execute launches on the primary checkout: no blocker, no worktree claim."""
    path = "work/feature-ws"
    item = _execute_item(
        path, worktree=None, branch=None, affects=("gw:workspace",), repo_stamps={"_workspace": Stamp("/ws-wt", "b")}
    )
    # A live finish whose merge target is the workspace root: a `WorktreeScope("/ws")` claim would block.
    outside, live_key, finish_plans = _live_finish_holding("/ws")
    result = _code_plan((outside, item), path, live=(live_key,), finish_plans=finish_plans)
    [dispatch] = result.dispatches
    assert result.blocked == ()
    assert dispatch.key not in result.placements
    assert dispatch.worktree == WorktreePlacement("/ws", None, None)
    assert result.dispatch_repos[dispatch.key] == REPO
    assert "workspace-pending" not in orchestrate.BLOCKED_KINDS
    fields = {field.name for field in dataclasses.fields(orchestrate.OrchestratePlan)}
    assert not fields & {"workspace_preparations", "workspace_placements"}
    assert "content root" not in dispatch.prompt


def test_workspace_only_design_dispatch_has_null_placement() -> None:
    path = "work/feature-ws"
    item = _item(path, phase="design", has_design_artifact=False, affects=("gw:workspace",))
    result = _code_plan((item,), path)
    [dispatch] = result.dispatches
    assert result.placements == {}
    assert dispatch.worktree == WorktreePlacement("/ws", None, None)
    assert dispatch.merge_target == "main"


def test_an_item_with_no_repository_evidence_has_null_placement() -> None:
    item = _execute_item("work/feature-a", worktree=None, branch=None)
    result = _plan((item,), item.path)
    [dispatch] = result.dispatches
    assert result.placements == {}
    assert dispatch.worktree == WorktreePlacement("/ws", None, None)


def test_no_preparations_and_no_root_advance_pair() -> None:
    root = "work/epic-r"
    done = f"{root}/children/feature-done"
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(done,), worktree="/epic", branch="epic/r"),
        _item(done, phase="done", work_status="resolved", worktree="/wt/done", branch="feature/done"),
    )
    result = _code_plan(items, root)
    assert not hasattr(result, "preparations")
    assert result.advances, result.blocked
    for advance in result.advances:
        assert not hasattr(advance, "worktree") and not hasattr(advance, "branch")
    assert {field.name for field in dataclasses.fields(orchestrate.PlannedAdvance)} == {"path", "reason", "mode"}


@pytest.mark.parametrize(
    "stamp",
    [
        {},
        {"worktree": "/repo", "branch": "main"},
        {"worktree": "/wt/psprowls/feature-x", "branch": "psprowls/feature-x-1a2b3c4d"},
        {"worktree": "/gone", "branch": "feature/x"},
        {"worktree": "/wt/half", "branch": None},
    ],
    ids=["unstamped", "main-checkout", "legacy-named", "missing", "half-stamp"],
)
@pytest.mark.parametrize("phase", ["design", "plan", "execute", "finish"])
@pytest.mark.parametrize("nested", [False, True])
def test_no_placement_blocker_kinds(stamp: dict[str, object], phase: str, nested: bool) -> None:
    path = "work/epic-x/children/feature-x" if nested else "work/feature-x"
    item = _item(
        path,
        phase=phase,
        work_status="in-progress" if phase in ("execute", "finish") else "open",
        owner="pat",
        has_plan_artifact=True,
        **stamp,
    )
    items = _epic_with_children(item, root="work/epic-x") if nested else (item,)
    result = _code_plan(items, items[0].path, dispatch_rules=_branch_tail_rules())
    assert [d.slug for d in result.dispatches] == [path], result.blocked
    assert not any(b.kind == "worktree-unsupported" for b in result.blocked)
    for blocked in result.blocked:
        if blocked.kind.startswith("worktree-"):
            assert not re.search(r"anchor|adopt|stamp", blocked.reason), blocked
    assert result.dispatches[0].worktree.path == str(_computed(item, items).worktree)


def test_live_occupancy_charges_computed_worktrees() -> None:
    owner = "work/feature-a"
    child = f"{owner}/children/bug-c"
    stale = "/wt/stale-stamp"
    feature = _execute_item(owner, child_paths=(child,), worktree=stale, branch="psprowls/feature-a")
    bug = _item(child, type="Bug", phase="design", has_design_artifact=False, affects=("packages/c",))
    finisher = _item("work/feature-f", phase="finish", work_status="in-progress", owner="pat", affects=("packages/f",))
    items = (feature, bug, finisher)
    live = (orchestrate.session_name(owner, "Feature", "execute"),)
    target = FinishTarget(REPO, "/wt/code/feature/f", "feature/f", "main", stale)

    # The stamp is no longer charged: a finish whose only target is that path dispatches.
    finish = _code_plan(
        items,
        finisher.path,
        live=live,
        dispatch_rules=_branch_tail_rules(),
        finish_plans={finisher.path: FinishPlan((target,), ())},
    )
    assert [d.slug for d in finish.dispatches] == [finisher.path], finish.blocked

    # The live owner's computed worktree is charged and lies in its child's chain.
    design = _code_plan(items, owner, live=live)
    assert design.dispatches == ()
    [blocked] = design.blocked
    assert (blocked.path, blocked.kind) == (child, "worktree-pending")
    assert owner in blocked.reason and f"held by {owner}" in blocked.reason
    assert str(_computed(feature, items).worktree) in blocked.reason


@pytest.mark.parametrize("live_phase", ["design", "plan"])
def test_live_readers_charge_no_worktree(live_phase: str) -> None:
    reader = _item("work/feature-r", phase=live_phase, affects=("packages/r",))
    finisher = _item("work/feature-f", phase="finish", work_status="in-progress", owner="pat", affects=("packages/f",))
    items = (reader, finisher)
    target = FinishTarget(REPO, "/wt/code/feature/f", "feature/f", "main", str(_computed(reader, items).worktree))
    result = _code_plan(
        items,
        finisher.path,
        live=(orchestrate.session_name(reader.path, "Feature", live_phase),),
        dispatch_rules=_branch_tail_rules(),
        finish_plans={finisher.path: FinishPlan((target,), ())},
    )
    assert [d.slug for d in result.dispatches] == [finisher.path], result.blocked


def test_chain_owner_held_by_live_finish_blocks_pending() -> None:
    root = "work/epic-s"
    first = _item(f"{root}/children/feature-c1", phase="finish", work_status="in-progress", owner="pat")
    second = _item(f"{root}/children/feature-c2", phase="design", has_design_artifact=False, affects=("packages/c2",))
    items = _epic_with_children(first, second, root=root)
    epic_worktree = str(_computed(items[0], items).worktree)
    target = FinishTarget(
        REPO,
        str(_computed(first, items).worktree),
        branch_name(first.path, "Feature"),
        branch_name(root, "Epic"),
        epic_worktree,
    )
    result = _code_plan(
        items,
        root,
        live=(orchestrate.session_name(first.path, "Feature", "finish"),),
        dispatch_rules=_branch_tail_rules(),
        finish_plans={first.path: FinishPlan((target,), ())},
    )
    assert result.dispatches == ()
    [blocked] = result.blocked
    assert (blocked.path, blocked.kind) == (second.path, "worktree-pending")
    assert root in blocked.reason and f"held by {first.path}" in blocked.reason
    assert result.dispatch_resolutions == {}


def test_chain_owner_held_by_an_accepted_finish_blocks_pending() -> None:
    root = "work/epic-s"
    first = _item(f"{root}/children/feature-c1", phase="finish", work_status="in-progress", owner="pat")
    second = _execute_item(f"{root}/children/feature-c2", worktree=None, branch=None, affects=("packages/c2",))
    items = _epic_with_children(first, second, root=root)
    target = FinishTarget(
        REPO,
        str(_computed(first, items).worktree),
        branch_name(first.path, "Feature"),
        branch_name(root, "Epic"),
        str(_computed(items[0], items).worktree),
    )
    result = _code_plan(
        items, root, dispatch_rules=_branch_tail_rules(), finish_plans={first.path: FinishPlan((target,), ())}
    )
    assert [d.slug for d in result.dispatches] == [first.path]
    assert [(b.path, b.kind) for b in result.blocked] == [(second.path, "worktree-pending")]


def _execute_item(slug: str, **overrides: object) -> WorkItem:
    base: dict[str, object] = {
        "phase": "execute",
        "work_status": "in-progress",
        "owner": "pat",
        "has_plan_artifact": True,
        "worktree": "/repo",
        "branch": "main",
    }
    base.update(overrides)
    return _item(slug, **base)


def test_execute_dispatches_carry_their_item_checkpoints_and_others_carry_none() -> None:
    slug = "work/feature-cp"
    declared = orchestrate.HumanCheckpoints("declared", ("Task 3: human skims the diff",))
    result = _plan(
        (_execute_item(slug),),
        slug,
        checkpoints={slug: declared},
    )
    [dispatch] = result.dispatches
    assert dispatch.phase == "execute"
    assert result.human_checkpoints == {dispatch.key: declared}

    planned = _plan((_item(slug, phase="plan"),), slug, checkpoints={slug: declared})
    [plan_dispatch] = planned.dispatches
    assert plan_dispatch.key not in planned.human_checkpoints


def test_plan_without_checkpoints_reports_none() -> None:
    slug = "work/feature-cp"
    result = _plan((_execute_item(slug),), slug)
    assert result.human_checkpoints == {}


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("none", ("no-plan", ())),
        ("absent-file", ("unreadable", ())),
        ("binary", ("unreadable", ())),
        ("missing", ("missing", ())),
        ("malformed", ("malformed", ())),
        ("declared", ("declared", ("Task 2: skim",))),
        ("none-dot", ("declared", ())),
    ],
)
def test_read_human_checkpoints_covers_every_status(
    tmp_path: Path, setup: str, expected: tuple[str, tuple[str, ...]]
) -> None:
    from okf_io import Source

    slug = "work/feature-cp"
    plan_file = tmp_path / slug / "references" / "02-plan.md"
    plan_file.parent.mkdir(parents=True)
    body = {
        "missing": "# Plan\n",
        "malformed": "# Plan\n\n## Human checkpoints\n\nsome prose\n",
        "declared": "# Plan\n\n## Human checkpoints\n\n- Task 2: skim\n",
        "none-dot": "# Plan\n\n## Human checkpoints\n\nNone.\n",
    }
    if setup in body:
        plan_file.write_text(body[setup], encoding="utf-8", newline="")
    elif setup == "binary":
        plan_file.write_bytes(b"\xff\xfe\x00bad")
    sources = () if setup == "none" else (Source(id="plan", resource=f"/{slug}/references/02-plan.md"),)
    item = _execute_item(slug, sources=sources)
    assert orchestrate.read_human_checkpoints(tmp_path, item) == orchestrate.HumanCheckpoints(*expected)


@pytest.mark.parametrize("code_affects", [("packages/a",), ()])
def test_distinct_repositories_share_one_budget_without_affects_collision(code_affects: tuple[str, ...]) -> None:
    root = "work/epic-r"
    code = f"{root}/children/feature-code"
    ui = f"{root}/children/feature-ui"
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(code, ui)),
        _item(code, phase="execute", affects=code_affects, worktree="/wt/code", branch="feature/code"),
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
        _restamp(
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


def test_live_empty_affects_finish_claims_its_foreign_repository() -> None:
    root = "work/epic-r"
    finishing = f"{root}/children/feature-finishing"
    ready = f"{root}/children/feature-ready"
    code_repo = ItemRepo("code", Path("/repo/code"), "frontmatter")
    ui_repo = ItemRepo("ui", Path("/repo/ui"), "frontmatter")
    items = (
        _item(
            root,
            type="Epic",
            phase="execute",
            child_paths=(finishing, ready),
            worktree="/wt/code-epic",
            branch="epic/r",
            repo_stamps={"ui": Stamp("/wt/ui-epic", "epic/r")},
        ),
        _item(
            finishing,
            phase="finish",
            work_status="in-progress",
            affects=(),
            worktree="/wt/code-finish",
            branch="feature/finishing",
        ),
        _item(ready, phase="execute", affects=("packages/z",), worktree="/wt/ui-ready", branch="feature/ready"),
    )
    contexts = {
        "git-code": RepositoryContext(
            "git-code",
            "/repo/code",
            "main",
            True,
            {"epic/r": ("/wt/code-epic",), "feature/finishing": ("/wt/code-finish",)},
            {"/wt/code-epic": True, "/wt/code-finish": True},
            True,
            checkout_usable_by_path={"/wt/code-epic": True, "/wt/code-finish": True},
        ),
        "git-ui": RepositoryContext(
            "git-ui",
            "/repo/ui",
            "main",
            True,
            {"epic/r": ("/wt/ui-epic",), "feature/ready": ("/wt/ui-ready",)},
            {"/wt/ui-epic": True, "/wt/ui-ready": True},
            True,
            checkout_usable_by_path={"/repo/ui": True, "/wt/ui-epic": True, "/wt/ui-ready": True},
        ),
    }
    finish_plans = {
        finishing: FinishPlan(
            (FinishTarget(ui_repo, "/wt/ui-finish", "feature/finishing", "epic/r", "/wt/ui-epic"),), ()
        ),
    }
    result = _plan(
        items,
        root,
        live=(orchestrate.session_name(finishing, "Feature", "finish"),),
        item_repos={root: code_repo, finishing: code_repo, ready: ui_repo},
        repo_contexts=contexts,
        finish_plans=finish_plans,
    )
    assert result.dispatches == ()
    assert _blocked_kinds(result) == {(ready, "affects-overlap")}
    assert "git-ui (whole repository)" in result.blocked[0].reason


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
    items = (_restamp(items[0], worktree="/wt/one", branch="feature/one"), *items[1:])
    result = _plan(items, root, item_repos=repos, repo_contexts={context.identity: context})
    assert len(result.dispatches) == 1
    blocked = {b.path: b.kind for b in result.blocked}
    assert blocked[second] == "affects-overlap"
    assert blocked[missing] == "invalid"


@pytest.mark.parametrize(
    ("live_affects", "ready_affects"),
    [
        (("packages/a",), ("packages/a",)),
        (("packages/a",), ("packages/a/src",)),
        ((), ("packages/zzz",)),
    ],
)
def test_unknown_live_repository_does_not_authorize_overlapping_candidate(
    live_affects: tuple[str, ...], ready_affects: tuple[str, ...]
) -> None:
    root, live_path, ready = (
        "work/epic-r",
        "work/epic-r/children/feature-live",
        "work/epic-r/children/feature-ready",
    )
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(live_path, ready)),
        _item(live_path, phase="execute", worktree="/wt/live", branch="feature/live", affects=live_affects),
        _item(ready, phase="execute", worktree="/wt/ready", branch="feature/ready", affects=ready_affects),
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
    refusal = next(blocked for blocked in result.blocked if blocked.path == ready)
    assert refusal.kind == "worktree-unprovable"
    assert refusal.reason == "live item's repository is unavailable for affects check"


_UNAVAILABLE = "live item's repository is unavailable for affects check"


def _unknown_live_repo_items(live_phase: str, ready_phase: str) -> tuple[tuple[WorkItem, ...], str, str, str]:
    root, live_path, ready = (
        "work/epic-r",
        "work/epic-r/children/feature-live",
        "work/epic-r/children/feature-ready",
    )
    ready_kwargs: dict[str, object] = (
        {"worktree": "/wt/ready", "branch": "feature/ready", "work_status": "accepted", "has_plan_artifact": True}
        if ready_phase == "execute"
        else {}
    )
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(live_path, ready), worktree="/wt/epic", branch="epic/r"),
        _item(live_path, phase=live_phase, affects=("packages/a",)),
        _item(ready, phase=ready_phase, affects=("packages/a",), **ready_kwargs),
    )
    return items, root, live_path, ready


def _unknown_live_repo_plan(items: tuple[WorkItem, ...], root: str, live_path: str, ready: str, live_phase: str):
    selected = ItemRepo("code", Path("/repo/code"), "frontmatter")
    context = RepositoryContext(
        "git-code",
        "/repo/code",
        "main",
        True,
        {"epic/r": ("/wt/epic",), "feature/ready": ("/wt/ready",)},
        {"/wt/epic": True, "/wt/ready": True},
        True,
        checkout_usable_by_path={"/wt/epic": True, "/wt/ready": True},
    )
    return _plan(
        items,
        root,
        live=(orchestrate.session_name(live_path, "Feature", live_phase),),
        item_repos={root: selected, ready: selected},
        repo_contexts={context.identity: context},
    )


@pytest.mark.parametrize("live_phase", ["design", "plan"])
def test_a_live_reader_with_an_unknown_repository_does_not_fail_close(live_phase: str) -> None:
    """A live reader holds no code claim, even with unknown repository evidence."""
    items, root, live_path, ready = _unknown_live_repo_items(live_phase, "execute")
    result = _unknown_live_repo_plan(items, root, live_path, ready, live_phase)
    assert [dispatch.slug for dispatch in result.dispatches] == [ready], result.blocked
    assert not [b for b in result.blocked if b.path == ready and b.reason == _UNAVAILABLE]
    assert (ready, "affects-overlap") not in _blocked_kinds(result)


def test_a_reader_candidate_skips_the_unknown_live_repository_refusal() -> None:
    """A design candidate holds no claim against an unknown live writer repository."""
    items, root, live_path, ready = _unknown_live_repo_items("execute", "design")
    result = _unknown_live_repo_plan(items, root, live_path, ready, "execute")
    assert [dispatch.slug for dispatch in result.dispatches] == [ready], result.blocked
    assert not [b for b in result.blocked if b.path == ready and b.reason == _UNAVAILABLE]
    assert (ready, "affects-overlap") not in _blocked_kinds(result)


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
        _restamp(
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


def test_lone_item_dispatch_key_and_prompt_use_full_path() -> None:
    path = "work/feature-a"
    result = _plan((_item(path),), path)
    dispatch = result.dispatches[0]
    assert dispatch.key == orchestrate.session_name(path, "Feature", "plan")
    assert dispatch.slug == path
    assert dispatch.prompt.splitlines()[0] == f"Run /gw:workflow {path}."


def test_branch_name_keeps_basename_and_hashes_full_path() -> None:
    first = branch_name("work/epic-a/children/feature-x", "Feature")
    second = branch_name("work/epic-b/children/feature-x", "Feature")
    assert first.startswith("feature/x-")
    assert second.startswith("feature/x-")
    assert first != second


def test_branch_name_strips_the_canonical_type_prefix() -> None:
    branch = branch_name("work/feature-x", "Feature")

    assert branch.startswith("feature/x-")


def test_session_name_is_prefixed_phased_and_hash_tailed() -> None:
    name = orchestrate.session_name("work/epic-a/children/feature-x", "Feature", "design")
    assert name.startswith("gw-design-")
    stem = branch_name("work/epic-a/children/feature-x", "Feature").split("/", 1)[1]
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
    assert len(index) == len(orchestrate.DISPATCH_PHASES) + len(orchestrate.SESSION_LABELS)
    for phase in orchestrate.DISPATCH_PHASES:
        name = orchestrate.session_name("work/epic-a/children/feature-x", "Feature", phase)
        assert index[name].path == "work/epic-a/children/feature-x"


def test_session_index_drops_a_collision_and_warns() -> None:
    a = _item("work/epic-a/children/feature-x", type="Feature")
    b = _item("work/epic-b/children/feature-x", type="Feature")
    collide = "gw-design-feature-x-deadbeef"

    def fake(path: str, type_: str, phase: str, *, label: str | None = None) -> str:
        return collide if phase == "design" else f"gw-{label or phase}-{path}"

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
    result = _plan((_item(path),), path)
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
        _item(child, worktree="/wt/feature-a", branch="feature/a", phase="execute", work_status="accepted"),
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


@pytest.mark.parametrize(
    ("live_phase", "candidate_phase", "overlaps"),
    [("execute", "execute", True), ("plan", "execute", False), ("execute", "plan", False)],
)
def test_affects_overlap_with_a_live_canonical_path_blocks_dispatch(
    live_phase: str, candidate_phase: str, overlaps: bool
) -> None:
    """A live writer blocks an overlapping writer; readers hold no affects claim (D-002)."""
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    writer: dict[str, object] = {"work_status": "accepted", "has_plan_artifact": True}
    items = (
        _item(root, type="Epic", phase="execute", child_paths=(first, second), affects=("packages/root",)),
        _item(first, affects=("packages/shared",), phase=live_phase, **(writer if live_phase == "execute" else {})),
        _item(
            second,
            affects=("packages/shared",),
            opened="2026-08-02",
            phase=candidate_phase,
            **(writer if candidate_phase == "execute" else {}),
        ),
    )
    result = _plan(items, root, live=(orchestrate.session_name(first, "Feature", live_phase),))
    assert any(b.path == second and b.kind == "affects-overlap" for b in result.blocked) is overlaps


def test_capacity_blocks_only_candidates_past_the_free_slots() -> None:
    root = "work/epic-a"
    first = f"{root}/children/feature-a"
    second = f"{root}/children/feature-b"
    items = (
        _item(root, type="Epic", phase="execute", affects=("packages/root",), child_paths=(first, second)),
        _item(
            first,
            worktree="/wt/feature-a",
            branch="feature/a",
            affects=("packages/a",),
            phase="execute",
            work_status="accepted",
        ),
        _item(second, affects=("packages/b",), opened="2026-08-02", phase="execute", work_status="accepted"),
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
        _item(first, affects=("packages/a",), phase="execute", work_status="accepted"),
        _item(second, affects=("packages/b",), opened="2026-08-02", phase="execute", work_status="accepted"),
    )
    refusal = BlockedItem(path=first, kind="cross-repo-child", reason=f"{first} resolves to 'code', not 'ui'")
    result = _plan(items, root, repo_refusals={first: refusal})
    assert refusal in result.blocked
    assert [dispatch.slug for dispatch in result.dispatches] == [second]


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
    assert orchestrate._classify("effort-required") == "effort-required"
    assert orchestrate._classify("hold") == "decisions"
    assert orchestrate._classify("terminal") == "human"
    assert orchestrate._classify("attribute-required") == "human"
    assert orchestrate._classify(None) == "invalid"

    root = _item("work/epic", type="Epic")
    child = _item("work/epic/children/feature")
    cycle = dataclasses.replace(root, parent_path=child.path)
    assert [item.path for item in orchestrate._descendants((cycle, child), root.path)] == [child.path]


@pytest.mark.parametrize("effort", ["small", "medium"])
@pytest.mark.parametrize("filename", ["coverage.md", "03-execute-coverage.md"])
def test_execute_tail_names_the_definitions_coverage_file(effort: str, filename: str) -> None:
    definition = dataclasses.replace(
        PACKAGED_DEFINITION,
        artifacts={
            **PACKAGED_DEFINITION.artifacts,
            "execute": ArtifactSpec("execute", filename, "execute-coverage", False),
        },
    )
    item = _execute_item("work/feature-x", effort=effort)
    result = _plan((item,), item.path, definition=definition)
    [dispatch] = result.dispatches
    assert f"/ws/okf/work/feature-x/references/{filename}" in dispatch.prompt
    assert "{execute_artifact}" not in dispatch.prompt
    if filename != "03-execute-coverage.md":
        assert "03-execute-coverage.md" not in dispatch.prompt


def test_prompt_substitutes_tail() -> None:
    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#execute",
        phase="execute",
        workspace="/ws",
        merge_target="main",
        tail="{path} {key} {phase} {workspace} {merge_target} {literal}",
        execute_artifact="coverage.md",
        mode="autonomous",
    )
    assert "work/feature-a work/feature-a#execute execute /ws main {literal}" in prompt
    assert orchestrate.WORKER_PLACEMENT_LINE in prompt
    assert prompt.endswith(
        "work/feature-a work/feature-a#execute execute /ws main {literal}\n"
        + orchestrate.NO_SUB_DISPATCH_LINE
        + "\n"
        + orchestrate.WORKER_PLACEMENT_LINE
    )


@pytest.mark.parametrize("phase", ["design", "plan", "execute", "finish"])
@pytest.mark.parametrize("mode", ["attend", "autonomous", "relay"])
def test_every_supervised_prompt_forbids_orca_sub_dispatch_just_before_placement(phase: str, mode: str) -> None:
    prompt = orchestrate._prompt(
        path="work/feature-a",
        key=f"work/feature-a#{phase}",
        phase=phase,
        workspace="/ws",
        merge_target="main",
        tail="authored tail",
        execute_artifact="coverage.md",
        mode=mode,
    )
    assert prompt.splitlines()[-2:] == [orchestrate.NO_SUB_DISPATCH_LINE, orchestrate.WORKER_PLACEMENT_LINE]


def test_the_sub_dispatch_rule_names_orca_run_create_and_native_subagents() -> None:
    line = orchestrate.NO_SUB_DISPATCH_LINE
    for verb in ("run-create", "run-use", "task-create", "worker-start"):
        assert f"`{verb}`" in line
    assert "native in-process subagents" in line
    assert "spawn_agent" in line


def test_an_execute_dispatch_prompt_carries_the_coverage_obligation() -> None:
    from gw_kernel.workspace.pipeline import EXECUTE_TAIL, NOTIFY_LINE, WAIT_LINE

    prompt = orchestrate._prompt(
        path="work/feature-a",
        key="work/feature-a#execute",
        phase="execute",
        workspace="/ws",
        merge_target="main",
        tail=EXECUTE_TAIL,
        execute_artifact="03-execute-coverage.md",
        mode="autonomous",
    )
    assert "/ws/okf/work/feature-a/references/03-execute-coverage.md" in prompt
    assert "{workspace}" not in prompt and "{path}" not in prompt
    assert NOTIFY_LINE.replace("{path}", "work/feature-a") in prompt
    assert WAIT_LINE.replace("{path}", "work/feature-a") in prompt


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

    def fake(path: str, type_: str, phase: str, *, label: str | None = None) -> str:
        return collision if phase == "design" else f"gw-{label or phase}-{path}"

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
def test_known_prior_phase_live_key_is_never_redispatched_and_still_reserves(owner_path: str) -> None:
    """A prior-phase live key prevents redispatch in both arms. Since D-002,
    the foreign owner uses an execute key against its finish page: a design
    reader cannot reserve code, while the execute writer still blocks overlap.
    """
    path = "work/feature-a"
    candidate = _item(path, phase="execute", has_plan_artifact=True)
    items = (candidate,) if owner_path == path else (candidate, _item(owner_path, phase="finish"))
    # The self case keeps an attended reader key to suppress redispatch. A
    # foreign owner needs a non-attend execute key to reserve code (D-002).
    live = orchestrate.session_name(owner_path, "Feature", "design" if owner_path == path else "execute")
    result = _plan(items, path, live=(live,))
    assert result.slots_free == (2 if owner_path == path else 1)
    assert result.attend_slots_free == (0 if owner_path == path else 1)
    assert result.dispatches == ()
    blocked = _blocked_kinds(result)
    if owner_path == path:
        # The live item is running: no duplicate dispatch, no self-report.
        assert all(entry_path != path for entry_path, _ in blocked)
    else:
        # A different live owner still reserves its claims.
        assert (path, "affects-overlap") in blocked
    assert result.warnings == ()


def test_sole_live_item_is_neither_redispatched_nor_blocked_by_itself() -> None:
    path = "work/feature-a"
    item = _item(path, phase="execute", has_plan_artifact=True)
    live = orchestrate.session_name(path, "Feature", "execute")
    result = _plan((item,), path, live=(live,))
    assert result.live == (live,)
    assert result.dispatches == ()
    assert result.blocked == ()


def test_an_empty_affects_candidate_dispatches_when_nothing_else_writes() -> None:
    """scan-layout #43: empty affects, `live: []`, free slots -- it must run."""
    items, (only,) = _overlapping_children("feature-a")
    items = (items[0], dataclasses.replace(items[1], affects=()))
    result = _plan(items, _RESERVE_ROOT)
    assert [dispatch.slug for dispatch in result.dispatches] == [only]
    assert _blocked_kinds(result) == set()


def test_frontier_reports_a_parent_cycle_in_a_gated_tree() -> None:
    root_path = "work/epic-root"
    child_path = "work/epic-child"
    root = _item(root_path, type="Epic", phase="execute", parent_path=child_path, child_paths=(child_path,))
    child = _item(child_path, type="Epic", phase="execute", parent_path=root_path, child_paths=(root_path,))

    _candidates, _advances, blocked = orchestrate._frontier((root, child), root_path, definition=PACKAGED_DEFINITION)

    assert blocked[0].kind == "invalid"
    assert "parent cycle" in blocked[0].reason


def test_frontier_plans_an_advance_when_the_current_artifact_is_complete() -> None:
    path = "work/epic-a"
    child_path = "work/epic-a/children/feature-done"
    item = _item(path, type="Epic", phase="execute", child_paths=(child_path,))
    child = _item(child_path, work_status="resolved")
    _candidates, advances, blocked = orchestrate._frontier((item, child), path, definition=PACKAGED_DEFINITION)
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

    _candidates, advances, blocked = orchestrate._frontier(items, root, definition=PACKAGED_DEFINITION)
    assert blocked == []
    assert [advance.path for advance in advances] == [root]
    assert advances[0].mode == "return"

    reopened = dataclasses.replace(epic, phase="execute")
    candidates2, advances2, blocked2 = orchestrate._frontier(
        (reopened, done, late), root, definition=PACKAGED_DEFINITION
    )
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

    candidates, advances, blocked = orchestrate._frontier(items, root, definition=PACKAGED_DEFINITION)
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

    candidates, _advances, _blocked = orchestrate._frontier(items, root, definition=PACKAGED_DEFINITION)
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

    _candidates, advances, blocked = orchestrate._frontier(items, root, definition=PACKAGED_DEFINITION)
    assert blocked == []
    assert [advance.path for advance in advances] == [root]
    assert advances[0].mode == "return"

    reopened = dataclasses.replace(epic, phase="execute")
    candidates2, advances2, blocked2 = orchestrate._frontier(
        (reopened, old, *children), root, definition=PACKAGED_DEFINITION
    )
    assert blocked2 == []
    assert advances2 == []
    assert sorted(node.path for node, _result in candidates2) == sorted(child_paths)


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
    dispatch = _only_dispatch(_plan(items, root, dispatch_rules=_branch_tail_rules()))
    assert (dispatch.slug, dispatch.phase, dispatch.mode) == (child, "finish", "relay")
    # The owner's computed integration branch, never its stamp.
    assert dispatch.merge_target == branch_name(root, "Epic")
    assert dispatch.auto_merge is True


def test_supervised_merges_never_auto_merge() -> None:
    root, _, items = _child_at_finish()
    result = _plan(items, root, supervise_merges=True, dispatch_rules=_branch_tail_rules())
    assert _only_dispatch(result).auto_merge is False


def test_the_roots_own_finish_never_auto_merges() -> None:
    root = "work/lone-bug"
    item = _item(root, type="Bug", phase="finish", work_status="in-progress", worktree="/wt/lone", branch="bug/lone-1")
    dispatch = _only_dispatch(_plan((item,), root, dispatch_rules=_branch_tail_rules()))
    assert (dispatch.phase, dispatch.mode) == ("finish", "relay")
    assert dispatch.auto_merge is False


def test_a_stamped_epic_root_at_finish_never_auto_merges() -> None:
    root, items = _root_at_finish("Epic", worktree="/wt/epic-int", branch="epic/int-1a2b3c4d")
    dispatch = _only_dispatch(_plan(items, root, dispatch_rules=_branch_tail_rules()))
    assert dispatch.auto_merge is False


def test_a_non_root_item_without_an_integration_owner_never_auto_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    # The walk only reaches a non-root item through an owning Epic/Release/Feature,
    # so "non-root, no owner" is a guard rather than a reachable shape: force it.
    # The policy never auto-answers it, even though the item is not the plan's root.
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
    assert dispatch.auto_merge is False


def test_a_non_finish_phase_never_auto_merges() -> None:
    root, _, items = _child_at_finish()
    items = (items[0], dataclasses.replace(items[1], phase="execute"))
    dispatch = _only_dispatch(_plan(items, root, dispatch_rules=_branch_tail_rules()))
    assert dispatch.phase == "execute"
    assert dispatch.auto_merge is False


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


def test_dispatch_profile_errors_preserve_relay_blocker_and_do_not_claim_placement():
    from gw_kernel.workspace.dispatch import parse_rules

    root = "work/epic-a"
    child = f"{root}/children/feature-a"
    rules = parse_rules(
        [{"match": {"stage": "plan"}, "mode": "relay", "prompt_tail": None}],
        source="/ws/dispatch.yaml",
        attributes=frozenset({"stage"}),
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
    from gw_kernel.workspace.dispatch import parse_rules

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
# Every fixture below is an execute-phase epic with first-sorted child A
# followed by B (and C), all sharing `packages/shared`. B's route, profile and
# placement are always dispatchable; the only thing that can stop B is A's
# reservation. A late refusal of A is a placement chain held by an unrelated
# live finish (D-010), which also consumes one worker slot.

_RESERVE_ROOT = "work/epic-reserve"
_SHARED = ("packages/shared",)


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
        _item(
            path,
            affects=_SHARED,
            **({"phase": "execute", "work_status": "accepted"} | ((first or {}) if index == 0 else {})),
        )
        for index, path in enumerate(paths)
    )
    return (epic, *children), paths


def _blocked_kinds(result) -> set[tuple[str, str]]:
    return {(blocked.path, blocked.kind) for blocked in result.blocked}


def _refusing_first(items: tuple[WorkItem, ...], first: str) -> tuple[tuple[WorkItem, ...], dict[str, object]]:
    """Hold A's computed worktree with an unrelated live finish so A refuses `worktree-pending`."""
    held = str(_computed(next(item for item in items if item.path == first), items).worktree)
    outside, live, plans = _live_finish_holding(held)
    return (*items, outside), {"live": (live,), "finish_plans": plans, "dispatch_rules": _branch_tail_rules()}


def test_refused_placement_reserves_no_affects() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items, extra = _refusing_first(items, first)
    result = _code_plan(items, _RESERVE_ROOT, **extra)
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert (first, "worktree-pending") in _blocked_kinds(result)
    assert (second, "affects-overlap") not in _blocked_kinds(result)
    assert set(result.dispatch_resolutions) == {result.dispatches[0].key}
    assert set(result.placements) == {result.dispatches[0].key}


def test_dispatch_profile_error_reserves_no_affects() -> None:
    from gw_kernel.workspace.dispatch import parse_rules

    rules = parse_rules(
        [{"match": {"type": "Bug"}, "mode": "relay", "prompt_tail": None}],
        source="/ws/dispatch.yaml",
        attributes=frozenset({"stage", "type"}),
    )
    items, (first, second) = _overlapping_children("bug-a", "feature-b", first={"type": "Bug", "phase": "plan"})
    result = _plan(items, _RESERVE_ROOT, dispatch_rules=rules)
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert (first, "relay-untailed") in _blocked_kinds(result)
    assert set(result.dispatch_resolutions) == {result.dispatches[0].key}


def test_zero_capacity_candidates_do_not_overlap_each_other() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    result = _plan(items, _RESERVE_ROOT, max_parallel=0)
    assert result.dispatches == ()
    assert _blocked_kinds(result) == {(first, "capacity"), (second, "capacity")}


def test_capacity_dropped_candidates_reserve_nothing_when_a_slot_is_filled_by_unrelated_work() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    outside = _item("work/feature-outside", phase="finish", affects=("packages/other",))
    live = orchestrate.session_name(outside.path, "Feature", "execute")
    result = _plan((*items, outside), _RESERVE_ROOT, max_parallel=1, live=(live,))
    assert result.dispatches == ()
    assert _blocked_kinds(result) == {(first, "capacity"), (second, "capacity")}


def test_accepted_dispatch_still_serializes_an_overlapping_sibling() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    result = _plan(items, _RESERVE_ROOT)
    assert [dispatch.slug for dispatch in result.dispatches] == [first]
    assert _blocked_kinds(result) == {(second, "affects-overlap")}


@pytest.mark.parametrize("reverse_input", [False, True])
def test_a_sibling_accepted_after_a_refusal_reserves_for_later_overlaps(reverse_input: bool) -> None:
    items, (first, second, third) = _overlapping_children("feature-a", "feature-b", "feature-c")
    items, extra = _refusing_first(items, first)
    if reverse_input:
        items = tuple(reversed(items))
    result = _code_plan(items, _RESERVE_ROOT, max_parallel=4, **extra)
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert _blocked_kinds(result) == {(first, "worktree-pending"), (third, "affects-overlap")}
    assert "packages/shared" in next(blocked.reason for blocked in result.blocked if blocked.path == third)


def test_a_refused_candidate_never_lets_capacity_be_exceeded() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    third = f"{_RESERVE_ROOT}/children/feature-c"
    epic = dataclasses.replace(items[0], child_paths=(first, second, third))
    items, extra = _refusing_first((epic, *items[1:], _item(third, affects=("packages/elsewhere",))), first)
    result = _code_plan(items, _RESERVE_ROOT, max_parallel=2, **extra)
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert _blocked_kinds(result) == {(first, "worktree-pending"), (third, "capacity")}


def test_two_empty_affects_candidates_in_one_repository_serialize() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (items[0], *(dataclasses.replace(child, affects=()) for child in items[1:]))
    result = _plan(items, _RESERVE_ROOT)
    assert len(result.dispatches) == 1
    (blocked,) = result.blocked
    assert blocked.kind == "affects-overlap"
    assert blocked.path in {first, second} and blocked.path != result.dispatches[0].slug
    assert "(whole repository)" in blocked.reason


def test_an_empty_affects_candidate_is_blocked_by_any_write_in_its_repository() -> None:
    items, _ = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=()),
        dataclasses.replace(items[2], affects=("packages/elsewhere",)),
    )
    result = _plan(items, _RESERVE_ROOT)
    assert len(result.dispatches) == 1
    assert {kind for _, kind in _blocked_kinds(result)} == {"affects-overlap"}


def test_a_live_empty_affects_item_blocks_a_candidate_in_its_repository() -> None:
    """A live execute with empty affects claims its whole repository. Since D-002,
    a live reader holds none; see test_a_live_reader_never_blocks_an_overlapping_execute."""
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=()),
        dataclasses.replace(items[2], affects=("packages/b",)),
    )
    live = (orchestrate.session_name(first, "Feature", "execute"),)
    result = _plan(items, _RESERVE_ROOT, live=live)
    assert result.dispatches == ()
    assert _blocked_kinds(result) == {(second, "affects-overlap")}
    reason = next(blocked.reason for blocked in result.blocked if blocked.path == second)
    assert f"held by {first}" in reason and "(whole repository)" in reason


@pytest.mark.parametrize("live_phase", ["design", "plan"])
@pytest.mark.parametrize("live_affects", [(), ("packages/shared",), ("packages/shared/src",)])
def test_a_live_reader_never_blocks_an_overlapping_execute(live_phase: str, live_affects: tuple[str, ...]) -> None:
    """A live reader holds no claim for equal, nested, or whole-repository affects."""
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=live_affects, phase=live_phase, work_status="open"),
        items[2],
    )
    live = (orchestrate.session_name(first, "Feature", live_phase),)
    result = _plan(items, _RESERVE_ROOT, live=live)
    assert [dispatch.slug for dispatch in result.dispatches] == [second]
    assert result.blocked == ()


def test_workspace_only_and_code_affects_dispatch_together() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=("gw:workspace",)),
        dataclasses.replace(items[2], affects=("packages/b",)),
    )
    result = _plan(items, _RESERVE_ROOT)
    assert sorted(dispatch.slug for dispatch in result.dispatches) == sorted((first, second))
    assert result.blocked == ()


def test_two_workspace_only_items_serialize() -> None:
    items, _ = _overlapping_children("feature-a", "feature-b")
    items = (items[0], *(dataclasses.replace(child, affects=("gw:workspace",)) for child in items[1:]))
    result = _plan(items, _RESERVE_ROOT)
    assert len(result.dispatches) == 1
    (blocked,) = result.blocked
    assert blocked.kind == "affects-overlap"
    assert "workspace (held by" in blocked.reason


@pytest.mark.parametrize(
    ("first_affects", "second_affects"),
    [
        (("packages/gw-wiki",), ("packages/gw-wiki/src/gw_wiki/lint_drift",)),
        (("packages/gw-wiki/src/gw_wiki/lint_drift",), ("packages/gw-wiki",)),
    ],
)
def test_nested_affects_in_one_plan_serialize(first_affects: tuple[str, ...], second_affects: tuple[str, ...]) -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=first_affects),
        dataclasses.replace(items[2], affects=second_affects),
    )
    result = _plan(items, _RESERVE_ROOT)
    assert [dispatch.slug for dispatch in result.dispatches] == [first]
    assert _blocked_kinds(result) == {(second, "affects-overlap")}
    assert first in next(blocked.reason for blocked in result.blocked if blocked.path == second)


def test_string_prefix_siblings_do_not_serialize() -> None:
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=("packages/a",)),
        dataclasses.replace(items[2], affects=("packages/ab",)),
    )
    result = _plan(items, _RESERVE_ROOT)
    assert [dispatch.slug for dispatch in result.dispatches] == [first, second]


def test_nested_affects_against_a_live_item_blocks_the_candidate() -> None:
    """Nested affects conflict by containment for a live execute. A live plan
    holds no affects claim since epic D-002."""
    items, (first, second) = _overlapping_children("feature-a", "feature-b")
    items = (
        items[0],
        dataclasses.replace(items[1], affects=("packages/gw-wiki",)),
        dataclasses.replace(items[2], affects=("packages/gw-wiki/src/gw_wiki/lint_drift",)),
    )
    live = orchestrate.session_name(first, "Feature", "execute")
    result = _plan(items, _RESERVE_ROOT, live=(live,))
    assert result.dispatches == ()
    assert (second, "affects-overlap") in _blocked_kinds(result)
    assert first in next(blocked.reason for blocked in result.blocked if blocked.path == second)


# --- a stamped root finishes its integration branch (D-002) ---------------


def _branch_tail_rules():
    from gw_kernel.workspace.dispatch import parse_rules

    return parse_rules(
        [{"match": {"stage": "finish"}, "prompt_tail": "Auto-drive context: merge target is `{merge_target}`."}],
        source="/ws/dispatch.yaml",
        attributes=frozenset({"stage"}),
    )


def _root_at_finish(
    type_: str, affects: tuple[str, ...] = ("packages/root",), **overrides: object
) -> tuple[str, tuple[WorkItem, ...]]:
    root = "work/epic-int"
    child = f"{root}/children/feature-done"
    parent = _item(
        root,
        type=type_,
        phase="finish",
        work_status="in-progress",
        child_paths=(child,),
        affects=affects,
        **overrides,
    )
    done = _item(child, phase="done", work_status="resolved", worktree="/wt/child", branch="feature/done-1a2b3c4d")
    return root, (parent, done)


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_regression_a_stamped_root_at_finish_dispatches_its_integration_instead_of_resolving(type_: str) -> None:
    """The reported run: every child merged into the epic branch, the root
    reached `finish`, and the planner handed the coordinator a bare done/resolved
    advance. A stamped root must instead get a relay finish in its own computed
    worktree, merging into the default base."""
    root, items = _root_at_finish(type_, worktree="/wt/epic-int", branch="epic/int-1a2b3c4d")
    result = _code_plan(items, root, dispatch_rules=_branch_tail_rules())
    assert result.advances == ()
    assert result.blocked == ()
    [dispatch] = result.dispatches
    assert (dispatch.slug, dispatch.phase, dispatch.skill, dispatch.mode) == (
        root,
        "finish",
        "superpowers:finishing-a-development-branch",
        "relay",
    )
    assert dispatch.worktree.path == str(_computed(items[0], items).worktree)
    assert dispatch.merge_target == "main"
    assert "merge target is `main`" in dispatch.prompt


@pytest.mark.parametrize("type_", ["Epic", "Release"])
def test_an_unstamped_root_at_finish_keeps_its_planned_advance(type_: str) -> None:
    root, items = _root_at_finish(type_, affects=("gw:workspace",))
    result = _plan(items, root, dispatch_rules=_branch_tail_rules())
    assert result.dispatches == ()
    assert [(advance.path, advance.reason, advance.mode) for advance in result.advances] == [
        (root, f"{type_.lower()} at finish stage", "advance")
    ]


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
    result = _code_plan(items, root, dispatch_rules=_branch_tail_rules())
    assert result.advances == ()
    assert [(d.slug, d.worktree.path, d.merge_target) for d in result.dispatches] == [
        (mid, str(_computed(items[1], items).worktree), branch_name(root, "Epic"))
    ]


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
        dispatch_rules=_branch_tail_rules(),
    )

    matches = [d for d in result.dispatches if d.slug == slug]
    assert len(matches) == 1, (result.dispatches, result.blocked)
    [dispatch] = matches
    assert orchestrate.WORKER_PLACEMENT_LINE in dispatch.prompt
    assert "Record the worktree" not in dispatch.prompt
    assert "--worktree`/`--branch` explicitly" not in dispatch.prompt


@pytest.mark.parametrize("stage", ["plan", "design", "finish"])
def test_planned_dispatch_reports_findings_with_or_without_a_tail(stage: str) -> None:
    slug = "work/feature-findings"
    item = _item(
        slug,
        phase=stage,
        has_design_artifact=stage != "design",
        work_status="in-progress" if stage == "finish" else "open",
        owner="pat" if stage == "finish" else None,
        worktree="/repo" if stage == "finish" else None,
        branch="main" if stage == "finish" else None,
    )
    dispatch = _only_dispatch(_plan((item,), slug, dispatch_rules=_branch_tail_rules()))
    lines = dispatch.prompt.splitlines()
    done = lines.index("Send worker_done when the stage artifact is written and the item advanced.")
    assert "outside this item's scope" in lines[done + 1]
    assert lines[done + 1] == pipeline.FINDINGS_LINE
    assert lines.count(pipeline.FINDINGS_LINE) == 1
    if stage == "plan":
        assert (
            next(rule for rule in pipeline.PACKAGED_PIPELINE if rule.name == "plan").prompt_tail
            == pipeline.WORKSPACE_COMMIT_TAIL
        )
    elif stage == "design":
        attend = pipeline.ATTEND_TAIL.replace("{path}", slug).replace("{phase}", "design").replace("{workspace}", "/ws")
        assert attend in dispatch.prompt
        assert f'--body "{slug} design: waiting' in dispatch.prompt
    else:
        assert "Auto-drive context:" in dispatch.prompt


@pytest.mark.parametrize(("stage", "expected"), [("plan", 1), ("design", 0), ("finish", 1)])
def test_the_ask_line_follows_findings_on_every_non_attend_dispatch(stage: str, expected: int) -> None:
    slug = "work/feature-asks"
    item = _item(
        slug,
        phase=stage,
        has_design_artifact=stage != "design",
        work_status="in-progress" if stage == "finish" else "open",
        owner="pat" if stage == "finish" else None,
        worktree="/repo" if stage == "finish" else None,
        branch="main" if stage == "finish" else None,
    )
    dispatch = _only_dispatch(_plan((item,), slug, dispatch_rules=_branch_tail_rules()))
    lines = dispatch.prompt.splitlines()
    assert lines.count(pipeline.ASK_LINE) == expected
    if expected:
        assert lines[lines.index(pipeline.FINDINGS_LINE) + 1] == pipeline.ASK_LINE
    assert dispatch.mode == {"plan": "autonomous", "design": "attend", "finish": "relay"}[stage]


def test_session_name_label_replaces_the_phase_head() -> None:
    path = "work/epic-a/children/feature-x"
    assert orchestrate.session_name(path, "Feature", "plan", label="reconcile").startswith("gw-reconcile-x-")
    assert orchestrate.session_name(path, "Feature", "plan").startswith("gw-plan-x-")


def test_session_index_resolves_a_reconcile_key() -> None:
    item = _item("work/feature-x")
    index, warnings = orchestrate.session_index((item,))
    key = orchestrate.session_name(item.path, item.type, "plan", label="reconcile")
    assert index[key] == item
    assert warnings == ()


def test_a_stale_plan_item_is_emitted_under_the_reconcile_key() -> None:
    item = _item("work/feature-x")
    computed = _plan((item,), item.path, stale={item.path: ("work/feature-y",)})
    key = orchestrate.session_name(item.path, item.type, "plan", label="reconcile")
    assert computed.dispatch_resolutions[key].profile.skill == "gw:reconciling-spec"
    assert computed.dispatches[0].key == key


def test_a_live_reconcile_key_resolves_to_its_item_at_plan() -> None:
    item = _item("work/feature-x")
    key = orchestrate.session_name(item.path, item.type, "plan", label="reconcile")
    computed = _plan((item,), item.path, live=(key,), stale={item.path: ("work/feature-y",)})
    assert key not in computed.dispatch_resolutions
    assert not computed.blocked
    assert not computed.dispatches


def test_off_path_repair_is_planned_as_an_advance_not_a_return() -> None:
    item = _item("work/bug-x", type="Bug", effort="small", phase="plan")
    result = _plan((item,), item.path)
    assert result.blocked == ()
    [repair] = result.advances
    assert repair.path == item.path
    assert repair.mode == "repair"
    assert result.dispatches == ()


def test_plan_stage_reconcile_keeps_its_session_label() -> None:
    item = _item("work/feature-x", phase="plan")
    result = _plan((item,), item.path, stale={item.path: ("work/landed",)})
    [dispatch] = result.dispatches
    assert dispatch.key == orchestrate.session_name(item.path, item.type, "plan", label="reconcile")


def test_stale_testgap_entry_keeps_plain_plan_session_label() -> None:
    item = _item("work/gap-entry", type="TestGap", phase=None, effort="large")
    result = _plan((item,), item.path, stale={item.path: ("work/landed",)})
    [dispatch] = result.dispatches
    assert dispatch.key == orchestrate.session_name(item.path, item.type, "plan")
