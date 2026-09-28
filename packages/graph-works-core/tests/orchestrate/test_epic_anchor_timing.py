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
