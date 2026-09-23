"""Repository evidence is collected from the checkout that owns each item."""

from __future__ import annotations

import subprocess
from pathlib import Path

from graph_works_core.workspace.repo_context import _inventory, observe_repository


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
    assert code_context.path == str(code.resolve())
    assert code_context.path_exists[str(code.resolve())] is True
    assert code_context.inventory_known and ui_context.inventory_known
    assert code_context.inventory["main"] == (str(code.resolve()),)


def test_missing_git_evidence_is_marked_unknown(tmp_path: Path) -> None:
    context = observe_repository(tmp_path)
    assert not context.inventory_known
    assert not context.checkout_usable
    assert context.inventory == {}


def test_duplicate_branch_observations_remain_visible_to_the_planner(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    observed = _inventory(
        f"worktree {first}\nbranch refs/heads/feature/a\n\nworktree {second}\nbranch refs/heads/feature/a\n\n"
    )
    assert observed["feature/a"] == (str(first.resolve()), str(second.resolve()))
