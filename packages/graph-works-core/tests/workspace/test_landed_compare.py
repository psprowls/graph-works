"""`compare_to_baseline`: the per-ref ancestor probe shared by landed_since and epic_brief."""

from __future__ import annotations

import subprocess
from pathlib import Path

from graph_works_core.workspace.landed import BaselineComparison, compare_to_baseline


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "later")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def test_an_ancestor_is_not_new(tmp_path: Path) -> None:
    repo, base, later = _repo(tmp_path)
    assert compare_to_baseline(repo, base, later) == BaselineComparison(False)


def test_a_descendant_is_new(tmp_path: Path) -> None:
    repo, base, later = _repo(tmp_path)
    assert compare_to_baseline(repo, later, base) == BaselineComparison(True)


def test_an_unknown_ref_is_missing(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    assert compare_to_baseline(repo, "f" * 40, base) == BaselineComparison(None, missing=True)


def test_a_bad_baseline_is_undetermined_with_a_cause(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    got = compare_to_baseline(repo, base, "e" * 40)
    assert got.new is None and not got.missing and got.cause is not None


def _side_commit(repo: Path, branch: str) -> str:
    """A commit on *branch*, off the first commit, leaving HEAD where it was."""
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-q", "-b", branch, "HEAD~1")
    _git(repo, "commit", "-q", "--allow-empty", "-m", branch)
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", head)
    return sha


def test_a_commit_no_ref_contains_is_unreachable(tmp_path: Path) -> None:
    repo, _, later = _repo(tmp_path)
    orphan = _side_commit(repo, "gone")
    _git(repo, "branch", "-q", "-D", "gone")
    assert compare_to_baseline(repo, orphan, later) == BaselineComparison(None, unreachable=True)


def test_a_commit_on_a_live_branch_is_new(tmp_path: Path) -> None:
    repo, _, later = _repo(tmp_path)
    side = _side_commit(repo, "epic")
    assert compare_to_baseline(repo, side, later) == BaselineComparison(True)


def test_a_commit_reachable_only_from_a_tag_is_new(tmp_path: Path) -> None:
    repo, _, later = _repo(tmp_path)
    tagged = _side_commit(repo, "tagged")
    _git(repo, "tag", "keep", tagged)
    _git(repo, "branch", "-q", "-D", "tagged")
    assert compare_to_baseline(repo, tagged, later) == BaselineComparison(True)
