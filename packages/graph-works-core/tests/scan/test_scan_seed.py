"""The scan seed is copied, never shared: each copy stands alone."""

from __future__ import annotations

from pathlib import Path

from _seed_copy import files_containing
from ruamel.yaml import YAML
from scan_helpers import REPO_NAME, ScanSeed, copy_repo, git, make_workspace


def _manifest_repo_path(layout) -> str:
    with layout.manifest_path.open(encoding="utf-8") as handle:
        return YAML().load(handle)["repositories"][REPO_NAME]["path"]


def test_copy_lives_under_tmp_path_and_points_at_its_own_repo(tmp_path: Path, scan_seed: ScanSeed) -> None:
    layout, repo = make_workspace(tmp_path, scan_seed)
    assert repo == tmp_path / "repo"
    assert layout.root == (tmp_path / ".works").resolve()
    assert layout.bundle_dir.is_dir() and layout.manifest_path.is_file()
    assert _manifest_repo_path(layout) == str(repo)
    assert git(repo, "log", "-1", "--format=%s") == "first"


def test_copy_contains_no_seed_path(tmp_path: Path, scan_seed: ScanSeed) -> None:
    make_workspace(tmp_path, scan_seed)
    for needle in {str(scan_seed.root), str(scan_seed.root.resolve())}:
        assert files_containing(tmp_path, needle) == []


def test_copies_are_independent(tmp_path: Path, scan_seed: ScanSeed) -> None:
    _, first = make_workspace(tmp_path / "a", scan_seed)
    _, second = make_workspace(tmp_path / "b", scan_seed)
    (first / "README.md").write_text("changed\n", encoding="utf-8", newline="\n")
    git(first, "commit", "-qam", "change")
    assert git(second, "log", "-1", "--format=%s") == "first"
    assert git(scan_seed.repo, "log", "-1", "--format=%s") == "first"


def test_copy_repo(tmp_path: Path, repo_seed: Path) -> None:
    repo = copy_repo(repo_seed, tmp_path / "x")
    assert repo == tmp_path / "x" / "repo"
    assert git(repo, "status", "--porcelain") == ""
