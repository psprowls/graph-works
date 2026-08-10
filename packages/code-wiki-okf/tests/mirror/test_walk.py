import subprocess
from pathlib import Path

from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.mirror.walk import tracked_files


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _scratch_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.lock").write_text("locked\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _config(repos: tuple[RepoConfig, ...]) -> Config:
    return Config(
        graph_dir=Path("/graph"),
        declarations_dir=Path("/declarations"),
        repos=repos,
        state_gate=StateGateConfig(enabled=False, branches=()),
    )


def test_tracked_files_one_repo(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, "one")
    config = _config((RepoConfig(name="one", path=repo, ignore=()),))
    assert tracked_files(config) == {"one": ("a.py", "b.lock")}


def test_tracked_files_honors_ignore(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, "one")
    config = _config((RepoConfig(name="one", path=repo, ignore=("*.lock",)),))
    assert tracked_files(config) == {"one": ("a.py",)}


def test_tracked_files_multiple_repos(tmp_path: Path) -> None:
    repo_a = _scratch_repo(tmp_path, "a")
    repo_b = _scratch_repo(tmp_path, "b")
    config = _config(
        (
            RepoConfig(name="a", path=repo_a, ignore=()),
            RepoConfig(name="b", path=repo_b, ignore=("*.lock",)),
        )
    )
    result = tracked_files(config)
    assert result == {"a": ("a.py", "b.lock"), "b": ("a.py",)}


def test_tracked_files_empty_for_non_git_path(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    config = _config((RepoConfig(name="plain", path=not_a_repo, ignore=()),))
    assert tracked_files(config) == {"plain": ()}
