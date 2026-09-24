"""Repository-local integration preparation and recovery planning."""

from dataclasses import replace
from pathlib import Path

from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.repo_context import RepositoryContext
from graph_works_core.workspace.repos import ItemRepo
from test_orchestrate_plan import _item, _plan
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.items import Stamp

ROOT = "work/epic-a"
CHILD = f"{ROOT}/children/feature-a"
SIBLING = f"{ROOT}/children/feature-b"


def fixture(*, phase="execute"):
    items = (
        _item(ROOT, type="Epic", phase="execute", child_paths=(CHILD, SIBLING)),
        _item(CHILD, phase=phase),
        _item(SIBLING, phase=phase, affects=("packages/b",)),
    )
    repos = {
        ROOT: ItemRepo("ui", Path("/ui"), "frontmatter"),
        CHILD: ItemRepo("code", Path("/code"), "frontmatter"),
        SIBLING: ItemRepo("code", Path("/code"), "inherited"),
    }
    context = RepositoryContext("git-code", "/code", "trunk", True, {"trunk": ("/code",)}, {"/code": True}, True)
    return items, repos, {context.identity: context}


def test_foreign_siblings_prepare_once_and_reserve_no_workers():
    items, repos, contexts = fixture()
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    (prep,) = result.preparations
    assert (prep.owner_path, prep.owner_phase, prep.repo.name) == (ROOT, "execute", "code")
    assert prep.base_branch == "trunk"
    assert prep.worktree.action == "create-top-level"
    assert not result.dispatches
    assert {b.kind for b in result.blocked} == {"worktree-pending"}


def test_dependencies_prevent_preparation():
    items, repos, contexts = fixture()
    items = (
        items[0],
        *(
            replace(i, dependency_edges=(DependencyEdge("work/feature-wait", blocks="execute", needs="resolved"),))
            for i in items[1:]
        ),
        _item("work/feature-wait"),
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations
    assert not result.dispatches


def test_read_only_foreign_children_need_no_integration_stamp():
    items, repos, contexts = fixture(phase="plan")
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations
    assert len(result.dispatches) == 2
    assert all(d.worktree.path == "/code" for d in result.dispatches)
    assert not items[0].repo_stamps


def test_stamp_then_replan_uses_the_exact_foreign_anchor():
    items, repos, contexts = fixture()
    items = (replace(items[0], repo_stamps={"code": Stamp("/code-anchor", "epic/a")}), *items[1:])
    contexts["git-code"] = replace(
        contexts["git-code"],
        inventory={"epic/a": ("/code-anchor",)},
        path_exists={"/code-anchor": True},
        checkout_usable_by_path={"/code-anchor": True},
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations
    assert len(result.dispatches) == 2
    for dispatch in result.dispatches:
        assert dispatch.worktree.parent_path == "/code-anchor"
        assert dispatch.worktree.base_branch == dispatch.merge_target == "epic/a"


def test_nested_feature_prepares_outer_then_inner_from_outer_branch():
    items, repos, contexts = fixture()
    nested = f"{CHILD}/children/task-one"
    items = (items[0], replace(items[1], child_paths=(nested,)), _item(nested, phase="execute"))
    repos[nested] = repos[CHILD]
    first = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert [p.owner_path for p in first.preparations] == [ROOT]
    items = (replace(items[0], repo_stamps={"code": Stamp("/outer", "epic/outer")}), *items[1:])
    contexts["git-code"] = replace(
        contexts["git-code"],
        inventory={"epic/outer": ("/outer",)},
        path_exists={"/outer": True},
        checkout_usable_by_path={"/outer": True},
    )
    second = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    (prep,) = second.preparations
    assert prep.owner_path == CHILD
    assert prep.base_branch == "epic/outer"
    assert prep.worktree.parent_path == "/outer"
    items = (items[0], replace(items[1], worktree="/inner", branch="feature/inner"), items[2])
    contexts["git-code"] = replace(
        contexts["git-code"],
        inventory={"epic/outer": ("/outer",), "feature/inner": ("/inner",)},
        path_exists={"/outer": True, "/inner": True},
        checkout_usable_by_path={"/outer": True, "/inner": True},
    )
    third = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    (dispatch,) = third.dispatches
    assert dispatch.worktree.parent_path == "/inner"
    assert dispatch.worktree.base_branch == dispatch.merge_target == "feature/inner"
    # Requesting the nested subtree preserves its enclosing integration chain.
    narrowed = _plan(items, CHILD, item_repos=repos, repo_contexts=contexts)
    assert narrowed.dispatches[0].merge_target == "feature/inner"


def test_invalid_stamp_is_never_hidden_by_a_preparation():
    items, repos, contexts = fixture()
    items = (replace(items[0], repo_stamps={"code": Stamp("/wrong", "epic/a")}), *items[1:])
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations and not result.dispatches
    assert all(b.kind == "worktree-unprovable" and "repair" in b.reason for b in result.blocked)


def test_existing_deterministic_worktree_requires_stamp_before_dispatch():
    items, repos, contexts = fixture()
    branch = orchestrate.integration_branch(ROOT, "Epic")
    contexts["git-code"] = replace(
        contexts["git-code"],
        inventory={branch: ("/anchor",)},
        path_exists={"/anchor": True},
        checkout_usable_by_path={"/anchor": True},
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    (prep,) = result.preparations
    assert prep.worktree.action == "reuse" and prep.worktree.path == "/anchor"
    assert not result.dispatches


def test_branch_without_checkout_refuses_creation():
    items, repos, contexts = fixture()
    contexts["git-code"] = replace(
        contexts["git-code"], branches=frozenset({orchestrate.integration_branch(ROOT, "Epic")})
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations and not result.dispatches
    assert all(b.kind == "worktree-unprovable" for b in result.blocked)


def test_ambiguous_deterministic_worktrees_refuse_adoption():
    items, repos, contexts = fixture()
    contexts["git-code"] = replace(
        contexts["git-code"], inventory={orchestrate.integration_branch(ROOT, "Epic"): ("/one", "/two")}
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations and not result.dispatches
    assert all(b.kind == "worktree-ambiguous" for b in result.blocked)


def test_preparation_does_not_reserve_affects_or_capacity_in_other_repo():
    items, repos, contexts = fixture()
    items = (
        replace(items[0], worktree="/ui-anchor", branch="epic/ui"),
        items[1],
        replace(items[2], affects=items[1].affects),
    )
    repos[SIBLING] = repos[ROOT]
    contexts["git-ui"] = RepositoryContext(
        "git-ui",
        "/ui",
        "main",
        True,
        {"epic/ui": ("/ui-anchor",)},
        {"/ui-anchor": True},
        True,
        checkout_usable_by_path={"/ui-anchor": True},
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts, max_parallel=1)
    assert len(result.preparations) == 1
    assert [d.slug for d in result.dispatches] == [SIBLING]


def test_missing_branch_observation_refuses_creation():
    items, repos, contexts = fixture()
    contexts["git-code"] = replace(contexts["git-code"], branches_known=False)
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations and not result.dispatches


def test_dirty_deterministic_checkout_cannot_be_adopted():
    items, repos, contexts = fixture()
    branch = orchestrate.integration_branch(ROOT, "Epic")
    contexts["git-code"] = replace(
        contexts["git-code"],
        inventory={branch: ("/code",)},
        checkout_usable=False,
        checkout_usable_by_path={"/code": False},
    )
    result = _plan(items, ROOT, item_repos=repos, repo_contexts=contexts)
    assert not result.preparations and not result.dispatches
    assert all(b.kind == "worktree-unprovable" for b in result.blocked)


def test_missing_cleanliness_evidence_refuses_adoption_and_stamped_anchor():
    items, repos, contexts = fixture()
    branch = orchestrate.integration_branch(ROOT, "Epic")
    contexts["git-code"] = replace(
        contexts["git-code"],
        inventory={branch: ("/anchor",)},
        path_exists={"/anchor": True},
        checkout_usable_by_path={},
    )
    for owner in (items[0], replace(items[0], repo_stamps={"code": Stamp("/anchor", branch)})):
        result = _plan((owner, *items[1:]), ROOT, item_repos=repos, repo_contexts=contexts)
        assert not result.preparations and not result.dispatches
        assert all(b.kind == "worktree-unprovable" for b in result.blocked)
