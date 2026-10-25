"""Repository evidence is collected from the checkout that owns each item."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from gw_pipeline import repo_context
from gw_pipeline.repo_context import _inventory, observe_branch_tips, observe_repository


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_observation_distinguishes_repositories_and_canonicalizes_aliases(tmp_path: Path) -> None:
    paths = []
    for name in ("code", "ui"):
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "T")
        (repo / "a.txt").write_text(name, encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "first")
        paths.append(repo)
    code, ui = paths
    alias = tmp_path / "code-alias"
    alias.symlink_to(code, target_is_directory=True)
    code_context = observe_repository(code, paths=(alias,))
    alias_context = observe_repository(alias)
    ui_context = observe_repository(ui)
    assert code_context.identity == alias_context.identity
    assert code_context.identity != ui_context.identity
    assert code_context.identity_known and ui_context.identity_known
    assert code_context.path == str(code.resolve())
    assert code_context.path_exists[str(code.resolve())] is True
    assert code_context.branches_known and code_context.branches == frozenset({"main"})
    assert code_context.inventory_known and ui_context.inventory_known
    assert code_context.inventory["main"] == (str(code.resolve()),)


def test_missing_git_evidence_is_marked_unknown(tmp_path: Path) -> None:
    context = observe_repository(tmp_path)
    assert not context.identity_known
    assert not context.branches_known
    assert not context.branches
    assert not context.inventory_known
    assert not context.checkout_usable
    assert context.inventory == {}


@pytest.mark.parametrize("shadow_tag", [False, True])
def test_branch_tips_are_committed_refs_not_worktree_state(tmp_path: Path, shadow_tag: bool) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "f").write_text("a", encoding="utf-8", newline="\n")
    _git(repo, "add", "f")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "a")
    tip = _git(repo, "rev-parse", "HEAD")
    if shadow_tag:
        _git(repo, "tag", "main", tip)
    (repo / "f").write_text("dirty", encoding="utf-8", newline="\n")
    context = observe_repository(repo)
    assert context.branch_tips == {"main": tip}
    assert context.branch_tips_known is True
    assert context.branches == frozenset({"main"})
    assert observe_branch_tips(repo) == {"main": tip}
    with pytest.raises(TypeError):
        context.branch_tips["main"] = "different"


def test_branch_tips_unknown_outside_git(tmp_path: Path) -> None:
    context = observe_repository(tmp_path)
    assert context.branch_tips == {} and context.branch_tips_known is False
    assert observe_branch_tips(tmp_path) is None


def test_linked_checkout_has_own_dirty_state_under_shared_identity(tmp_path: Path) -> None:
    repo = tmp_path / "code"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "first")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "feature/linked", str(linked))
    (linked / "dirty.txt").write_text("local\n", encoding="utf-8")
    context = observe_repository(repo)  # Inventory paths are checked even without a manifest declaration.
    assert context.checkout_usable_by_path[str(repo.resolve())] is True
    assert context.checkout_usable_by_path[str(linked.resolve())] is False


def test_duplicate_branch_observations_remain_visible_to_the_planner(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    observed = _inventory(
        f"worktree {first}\nbranch refs/heads/feature/a\n\nworktree {second}\nbranch refs/heads/feature/a\n\n"
    )
    assert observed["feature/a"] == (str(first.resolve()), str(second.resolve()))


def test_inventory_observation_uses_resolved_git_without_status_scans(place_env, monkeypatch):
    env = place_env
    linked = env.repo.parent / "linked"
    env.git("worktree", "add", "-b", "feature/linked", str(linked), "main")
    original = repo_context.probe_git
    commands = []
    git = env.ctx().git

    def probe(cwd, *args, **kwargs):
        commands.append((args, kwargs))
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(repo_context, "probe_git", probe)

    inventory = repo_context.observe_inventory(env.repo, git=git)

    assert inventory == {"main": (str(env.repo.resolve()),), "feature/linked": (str(linked.resolve()),)}
    assert commands == [(("worktree", "list", "--porcelain"), {"executable": git.path})]
    with pytest.raises(TypeError):
        inventory["main"] = ()


def test_inventory_observation_unknown_outside_git(tmp_path):
    assert repo_context.observe_inventory(tmp_path) is None
