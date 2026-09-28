"""Epic anchors are prepared when their owner reaches execute."""

from pathlib import Path

from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from test_orchestrate_plan import _item, _plan
from test_workspace_placement import WS_REPO, WT, ws_context

ROOT = "work/epic-a"
CHILD = f"{ROOT}/children/feature-a"


def _code_context() -> RepositoryContext:
    return RepositoryContext(
        "git-code",
        "/code",
        "trunk",
        True,
        {"trunk": ("/code",)},
        {"/code": True},
        True,
        {"/code": True},
        branch_tips={"trunk": "0" * 40},
    )


def _repos() -> dict[str, ItemRepo]:
    return {
        ROOT: ItemRepo("code", Path("/code"), "frontmatter"),
        CHILD: ItemRepo("code", Path("/code"), "inherited"),
    }


def test_epic_execute_prepares_both_anchors_before_child_execute() -> None:
    items = (_item(ROOT, type="Epic", phase="execute", child_paths=(CHILD,)), _item(CHILD, phase="design"))
    result = _plan(
        items,
        ROOT,
        item_repos=_repos(),
        repo_contexts={"git-code": _code_context()},
        workspace_repo=WS_REPO,
        workspace_context=ws_context(),
        workspace_worktrees_dir=WT,
    )
    assert [(p.owner_path, p.repo.name) for p in result.preparations] == [(ROOT, "code")]
    assert [p.owner_path for p in result.workspace_preparations] == [ROOT]


def test_epic_plan_does_not_prepare_anchors() -> None:
    items = (_item(ROOT, type="Epic", phase="plan", child_paths=(CHILD,)), _item(CHILD, phase="design"))
    result = _plan(
        items,
        ROOT,
        item_repos=_repos(),
        repo_contexts={"git-code": _code_context()},
        workspace_repo=WS_REPO,
        workspace_context=ws_context(),
        workspace_worktrees_dir=WT,
    )
    assert not result.preparations
    assert not result.workspace_preparations


def test_epic_root_design_never_mints_a_mutable_anchor() -> None:
    root = _item(ROOT, type="Epic", phase="design")
    result = _plan((root,), ROOT, item_repos=_repos(), repo_contexts={"git-code": _code_context()})
    assert [d.worktree.action for d in result.dispatches] == ["pin-detached"]
    assert not result.preparations


def test_lone_feature_reader_stays_detached() -> None:
    lone = _item("work/feature-lone", phase="design")
    result = _plan((lone,), lone.path, repo_path="/code")
    assert [d.worktree.action for d in result.dispatches] == ["pin-detached"]


def test_workspace_only_child_does_not_trigger_code_anchor() -> None:
    items = (
        _item(ROOT, type="Epic", phase="execute", child_paths=(CHILD,)),
        _item(CHILD, phase="design", affects=("gw:workspace",)),
    )
    result = _plan(
        items,
        ROOT,
        item_repos=_repos(),
        repo_contexts={"git-code": _code_context()},
        workspace_repo=WS_REPO,
        workspace_context=ws_context(),
        workspace_worktrees_dir=WT,
    )
    assert not result.preparations
    assert [p.owner_path for p in result.workspace_preparations] == [ROOT]


def test_workspace_reader_with_unused_code_repo_does_not_claim_sibling_code():
    from dataclasses import replace

    from work_tracker_okf.items import Stamp

    sibling = f"{ROOT}/children/feature-b"
    items = (
        _item(
            ROOT,
            type="Epic",
            phase="execute",
            child_paths=(CHILD, sibling),
            worktree="/code-anchor",
            branch="epic/code",
            repo_stamps={"_workspace": Stamp("/ws-anchor", "epic/ws")},
        ),
        _item(CHILD, phase="design", affects=("gw:workspace",)),
        _item(
            sibling,
            phase="execute",
            affects=("code:src",),
            worktree="/code-anchor",
            branch="epic/code",
            repo_stamps={"_workspace": Stamp("/ws-writer", "feature/writer")},
        ),
    )
    repos = {
        **_repos(),
        CHILD: ItemRepo("unused", Path("/unused"), "frontmatter"),
        sibling: ItemRepo("code", Path("/code"), "inherited"),
    }
    code = replace(
        _code_context(),
        inventory={"epic/code": ("/code-anchor",)},
        path_exists={"/code-anchor": True},
        checkout_usable_by_path={"/code-anchor": True},
    )
    unused = replace(
        _code_context(),
        identity="git-unused",
        path="/unused",
        inventory={"trunk": ("/unused",)},
        checkout_usable_by_path={"/unused": True},
    )
    ws = replace(
        ws_context(**{"epic/ws": ("/ws-anchor",), "feature/writer": ("/ws-writer",)}), branch_tips={"epic/ws": "a" * 40}
    )
    result = _plan(
        items,
        ROOT,
        item_repos=repos,
        repo_contexts={"git-code": code, "git-unused": unused},
        workspace_repo=WS_REPO,
        workspace_context=ws,
        workspace_worktrees_dir=WT,
    )
    by_path = {d.slug: d for d in result.dispatches}
    assert by_path[CHILD].worktree.action == "pin-detached"
    assert by_path[CHILD].worktree.start_sha == "a" * 40
    assert sibling in by_path, result.blocked
    assert by_path[sibling].worktree.action == "reuse"
    assert not result.preparations
