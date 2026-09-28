"""Pure workspace-branch selection for orchestration."""

from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate.anchors import (
    AnchorRefusal,
    WorkspacePlacement,
    WorkspacePreparation,
    select_workspace,
    verify_workspace_stamp,
    workspace_chain,
    workspace_worktree_path,
)
from graph_works_core.orchestrate.commands import branch_name
from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from test_orchestrate_plan import _branch_tail_rules, _item, _plan
from work_tracker_okf.items import Stamp, WorkItem

ROOT = "work/epic-a"
CHILD = f"{ROOT}/children/feature-a"
WT = "/ws/.gw/worktrees"


def ws_context(**inventory: tuple[str, ...]) -> RepositoryContext:
    paths = {path for checkouts in inventory.values() for path in checkouts}
    return RepositoryContext(
        "git-ws",
        "/ws",
        "main",
        True,
        {"main": ("/ws",), **inventory},
        {path: True for path in paths},
        True,
        {"/ws": True, **{path: True for path in paths}},
        branches=frozenset({"main", *inventory}),
    )


def by_path(*items: WorkItem) -> dict[str, WorkItem]:
    return {item.path: item for item in items}


def test_chain_is_outermost_first() -> None:
    epic = _item(ROOT, type="Epic", phase="execute", child_paths=(CHILD,))
    child = _item(CHILD, phase="execute")
    assert [item.path for item in workspace_chain(child, by_path(epic, child))] == [ROOT, CHILD]


def test_worktree_path_uses_stable_stem() -> None:
    path = Path(workspace_worktree_path(WT, CHILD, "Feature"))
    assert path.parent == Path(WT) / "workspace"
    assert path.name == branch_name(CHILD, "Feature").split("/", 1)[1]


def test_missing_epic_anchor_is_prepared_first_from_main() -> None:
    epic = _item(ROOT, type="Epic", phase="execute", child_paths=(CHILD,))
    child = _item(CHILD, phase="execute")
    got = select_workspace(child, items=by_path(epic, child), context=ws_context(), worktrees_dir=WT)
    assert got == WorkspacePreparation(
        ROOT, "execute", workspace_worktree_path(WT, ROOT, "Epic"), branch_name(ROOT, "Epic"), "main"
    )


def test_child_is_prepared_from_verified_epic_anchor() -> None:
    anchor = workspace_worktree_path(WT, ROOT, "Epic")
    epic = _item(
        ROOT,
        type="Epic",
        phase="execute",
        child_paths=(CHILD,),
        repo_stamps={"_workspace": Stamp(anchor, branch_name(ROOT, "Epic"))},
    )
    child = _item(CHILD, phase="execute")
    context = ws_context(**{branch_name(ROOT, "Epic"): (anchor,)})
    got = select_workspace(child, items=by_path(epic, child), context=context, worktrees_dir=WT)
    assert got == WorkspacePreparation(
        CHILD,
        "execute",
        workspace_worktree_path(WT, CHILD, "Feature"),
        branch_name(CHILD, "Feature"),
        branch_name(ROOT, "Epic"),
    )


def test_fully_stamped_chain_places_item() -> None:
    anchor = workspace_worktree_path(WT, ROOT, "Epic")
    own = workspace_worktree_path(WT, CHILD, "Feature")
    epic = _item(
        ROOT,
        type="Epic",
        phase="execute",
        child_paths=(CHILD,),
        repo_stamps={"_workspace": Stamp(anchor, branch_name(ROOT, "Epic"))},
    )
    child = _item(CHILD, phase="execute", repo_stamps={"_workspace": Stamp(own, branch_name(CHILD, "Feature"))})
    context = ws_context(**{branch_name(ROOT, "Epic"): (anchor,), branch_name(CHILD, "Feature"): (own,)})
    got = select_workspace(child, items=by_path(epic, child), context=context, worktrees_dir=WT)
    assert got == WorkspacePlacement(own, branch_name(CHILD, "Feature"))


def test_unverified_or_dirty_stamp_refuses() -> None:
    own = workspace_worktree_path(WT, CHILD, "Feature")
    child = _item("work/feature-lone", phase="execute", repo_stamps={"_workspace": Stamp(own, "feature/x")})
    missing = select_workspace(child, items=by_path(child), context=ws_context(), worktrees_dir=WT)
    assert isinstance(missing, AnchorRefusal) and missing.kind == "worktree-unprovable"
    dirty_context = replace(ws_context(**{"feature/x": (own,)}), checkout_usable_by_path={"/ws": True, own: False})
    dirty = select_workspace(child, items=by_path(child), context=dirty_context, worktrees_dir=WT)
    assert isinstance(dirty, AnchorRefusal) and "dirty" in dirty.reason


def test_ambiguous_workspace_branch_refuses() -> None:
    own = workspace_worktree_path(WT, CHILD, "Feature")
    child = _item("work/feature-lone", repo_stamps={"_workspace": Stamp(own, "feature/x")})
    context = ws_context(**{"feature/x": (own, "/other")})
    got = verify_workspace_stamp(child, context)
    assert isinstance(got, AnchorRefusal) and got.kind == "worktree-ambiguous"


def test_malformed_repo_stamps_refuses_even_without_workspace_stamp() -> None:
    child = _item("work/feature-lone", invalid_optional_fields=frozenset({"repo_stamps"}))
    got = verify_workspace_stamp(child, ws_context())
    assert isinstance(got, AnchorRefusal) and got.kind == "worktree-unprovable"


WS_REPO = ItemRepo("_workspace", Path("/ws"), "workspace")


def _ws_plan(items, root, ctx, **kw):
    return _plan(items, root, workspace_repo=WS_REPO, workspace_context=ctx, workspace_worktrees_dir=WT, **kw)


def test_execute_without_stamp_is_workspace_pending_and_emits_a_preparation():
    lone = _item("work/feature-lone", phase="execute", work_status="accepted")
    result = _ws_plan((lone,), lone.path, ws_context())
    assert not result.dispatches
    assert [b.kind for b in result.blocked] == ["workspace-pending"]
    (prep,) = result.workspace_preparations
    assert (prep.owner_path, prep.base_branch) == (lone.path, "main")


def test_stamped_execute_dispatches_with_the_content_root_line():
    own = workspace_worktree_path(WT, "work/feature-lone", "Feature")
    lone = _item(
        "work/feature-lone",
        phase="execute",
        work_status="accepted",
        repo_stamps={"_workspace": Stamp(own, "feature/lone-x")},
    )
    result = _ws_plan((lone,), lone.path, ws_context(**{"feature/lone-x": (own,)}))
    (dispatch,) = result.dispatches
    assert result.workspace_placements[dispatch.key] == WorkspacePlacement(own, "feature/lone-x")
    assert f"Workspace content root: {own} (branch feature/lone-x)" in dispatch.prompt
    assert f"content-producing commands with GRAPH_WORKS_DIR={own}" in dispatch.prompt
    assert "Run every gw work verb against GRAPH_WORKS_DIR=/ws." in dispatch.prompt
    assert dispatch.prompt.rstrip().endswith(orchestrate.WORKER_PLACEMENT_LINE)
    assert dispatch.worktree.action == "create-top-level"
    wrapped = orchestrate.OrchestrateResult(result)
    assert wrapped.workspace_placements == result.workspace_placements
    assert wrapped.workspace_preparations == result.workspace_preparations


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_readers_get_no_workspace(phase):
    lone = _item("work/feature-lone", phase=phase, affects=("gw:workspace",))
    result = _ws_plan((lone,), lone.path, ws_context())
    assert not result.workspace_preparations and not result.workspace_placements
    (dispatch,) = result.dispatches
    assert "Workspace content root" not in dispatch.prompt
    assert dispatch.worktree.action == "pin-detached"


@pytest.mark.parametrize("missing", ["workspace_repo", "workspace_context", "workspace_worktrees_dir", "all"])
def test_disabled_workspace_placement_changes_nothing(missing):
    lone = _item("work/feature-lone", phase="execute", work_status="accepted", affects=("gw:workspace",))
    args = dict(workspace_repo=WS_REPO, workspace_context=ws_context(), workspace_worktrees_dir=WT)
    for name in args:
        if missing in (name, "all"):
            args[name] = None
    result = _plan((lone,), lone.path, **args)
    assert result == _plan((lone,), lone.path)
    assert result.dispatches[0].worktree.action == "create-top-level"


def test_workspace_only_execute_gets_no_code_fork():
    own = workspace_worktree_path(WT, "work/feature-docs", "Feature")
    docs = _item(
        "work/feature-docs",
        phase="execute",
        work_status="accepted",
        affects=("gw:workspace",),
        repo_stamps={"_workspace": Stamp(own, "feature/docs-x")},
    )
    result = _ws_plan(
        (docs,), docs.path, ws_context(**{"feature/docs-x": (own,)}), provisions_worktrees=False, repo_known=False
    )
    (dispatch,) = result.dispatches
    assert (dispatch.worktree.action, dispatch.worktree.path, dispatch.worktree.branch) == (
        "reuse",
        own,
        "feature/docs-x",
    )
    assert result.dispatch_repos[dispatch.key] == WS_REPO
    assert not result.preparations


def test_workspace_only_without_stamp_is_pending_never_forked():
    docs = _item("work/feature-docs", phase="execute", work_status="accepted", affects=("gw:workspace",))
    result = _ws_plan((docs,), docs.path, ws_context())
    assert not result.dispatches
    assert [b.kind for b in result.blocked] == ["workspace-pending"]


def test_live_item_with_dirty_workspace_is_excluded_without_self_blocker():
    own = workspace_worktree_path(WT, "work/feature-lone", "Feature")
    lone = _item(
        "work/feature-lone",
        phase="execute",
        work_status="in-progress",
        affects=("gw:workspace",),
        repo_stamps={"_workspace": Stamp(own, "feature/lone-x")},
    )
    ctx = replace(ws_context(**{"feature/lone-x": (own,)}), checkout_usable_by_path={"/ws": True, own: False})
    key = orchestrate.session_name(lone.path, lone.type, "execute")
    result = _ws_plan((lone,), lone.path, ctx, live=(key,))
    assert result.live == (key,)
    assert not result.blocked and not result.dispatches and not result.workspace_preparations


def sibling_fixture():
    a, b = f"{ROOT}/children/feature-a", f"{ROOT}/children/feature-b"
    wa, wb, anchor = (workspace_worktree_path(WT, p, t) for p, t in ((a, "Feature"), (b, "Feature"), (ROOT, "Epic")))
    items = (
        _item(
            ROOT,
            type="Epic",
            phase="execute",
            child_paths=(a, b),
            worktree="/code-anchor",
            branch="epic/a",
            repo_stamps={"_workspace": Stamp(anchor, "epic/ws")},
        ),
        _item(
            a,
            phase="execute",
            work_status="in-progress",
            affects=("packages/a",),
            repo_stamps={"_workspace": Stamp(wa, "feature/a")},
        ),
        _item(
            b,
            phase="execute",
            work_status="accepted",
            affects=("packages/b",),
            repo_stamps={"_workspace": Stamp(wb, "feature/b")},
        ),
    )
    repos = {item.path: ItemRepo("code", Path("/code"), "frontmatter") for item in items}
    context = RepositoryContext(
        "git-code",
        "/code",
        "main",
        True,
        {"main": ("/code",), "epic/a": ("/code-anchor",)},
        {"/code": True, "/code-anchor": True},
        True,
        checkout_usable_by_path={"/code": True, "/code-anchor": True},
    )
    ws = ws_context(**{"epic/ws": (anchor,), "feature/a": (wa,), "feature/b": (wb,)})
    ws = replace(ws, checkout_usable_by_path={**ws.checkout_usable_by_path, wa: False})
    return items, repos, {context.identity: context}, ws


def test_live_workspace_stamp_does_not_make_sibling_affects_uncertain():
    items, repos, contexts, ws = sibling_fixture()
    # Matching affects in distinct code repositories must remain independent.
    # Treating the live workspace stamp as unknown code evidence poisons this
    # admission even though both code repository identities are proven.
    items = (
        replace(items[0], repo_stamps={**items[0].repo_stamps, "other": Stamp("/other-anchor", "epic/other")}),
        items[1],
        replace(items[2], affects=items[1].affects),
    )
    repos[items[2].path] = ItemRepo("other", Path("/other"), "frontmatter")
    contexts["git-other"] = RepositoryContext(
        "git-other",
        "/other",
        "main",
        True,
        {"main": ("/other",), "epic/other": ("/other-anchor",)},
        {"/other": True, "/other-anchor": True},
        True,
        checkout_usable_by_path={"/other": True, "/other-anchor": True},
    )
    key = orchestrate.session_name(items[1].path, "Feature", "execute")
    result = _ws_plan(items, ROOT, ws, item_repos=repos, repo_contexts=contexts, live=(key,))
    assert [d.slug for d in result.dispatches] == [items[2].path]
    assert not result.blocked


@pytest.mark.parametrize("live", [False, True])
def test_real_workspace_claim_blocks_sibling_before_workspace_selection(live):
    items, repos, contexts, ws = sibling_fixture()
    items = (
        items[0],
        replace(items[1], affects=("gw:workspace",)),
        replace(items[2], affects=("gw:workspace",), repo_stamps={}),
    )
    # The first stamp is clean when it needs fresh admission.
    ws = replace(ws, checkout_usable_by_path={path: True for path in ws.checkout_usable_by_path})
    keys = (orchestrate.session_name(items[1].path, "Feature", "execute"),) if live else ()
    result = _ws_plan(items, ROOT, ws, item_repos=repos, repo_contexts=contexts, live=keys)
    assert [(b.path, b.kind) for b in result.blocked] == [(items[2].path, "affects-overlap")]
    assert not result.workspace_preparations
    assert len(result.dispatches) == (0 if live else 1)


def test_capacity_precedes_workspace_preparation():
    lone = _item("work/feature-lone", phase="execute", work_status="accepted")
    result = _ws_plan((lone,), lone.path, ws_context(), max_parallel=0)
    assert [b.kind for b in result.blocked] == ["capacity"]
    assert not result.workspace_preparations


def test_workspace_preparation_does_not_reserve_claims_or_capacity():
    items, repos, contexts, ws = sibling_fixture()
    items = (
        items[0],
        replace(items[1], affects=("gw:workspace",), repo_stamps={}),
        replace(items[2], affects=("gw:workspace",)),
    )
    result = _ws_plan(items, ROOT, ws, item_repos=repos, repo_contexts=contexts, max_parallel=1)
    assert [(b.path, b.kind) for b in result.blocked] == [(items[1].path, "workspace-pending")]
    assert [d.slug for d in result.dispatches] == [items[2].path]
    assert [p.owner_path for p in result.workspace_preparations] == [items[1].path]


def test_dirty_workspace_refusal_is_data():
    items, _, _, ws = sibling_fixture()
    lone = replace(items[1], path="work/feature-lone", parent_path=None, ancestor_paths=())
    result = _ws_plan((lone,), lone.path, ws)
    assert [b.kind for b in result.blocked] == ["worktree-unprovable"]
    assert not result.dispatches and not result.workspace_preparations


def test_finish_without_workspace_stamp_never_prepares():
    lone = _item(
        "work/feature-lone", phase="finish", work_status="in-progress", worktree="/code-own", branch="feature/lone"
    )
    result = _ws_plan(
        (lone,), lone.path, ws_context(), worktree_exists={"/code-own": True}, dispatch_rules=_branch_tail_rules()
    )
    assert len(result.dispatches) == 1
    assert not result.workspace_preparations and not result.workspace_placements
