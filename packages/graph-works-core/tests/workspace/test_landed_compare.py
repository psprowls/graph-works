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
