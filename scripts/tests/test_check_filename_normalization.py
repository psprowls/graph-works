"""Acceptance tests for `scripts/check_filename_normalization.py`.

Outside the repo's `testpaths` and coverage `source` list on purpose:
`scripts/` is repo tooling, not a package, so these do not move the 95% gate.
Run them with `uv run pytest scripts/tests`.

The gate exists to catch what `os.path.exists()` cannot (APFS lookup is
normalization-insensitive, so an `exists()`-based scan reports a broken tree
clean -- see the design spec for `2026-08-07-bug-moves-fixture-unicode-normalization`):
a tracked filename whose on-disk bytes differ from its git-index bytes in
Unicode normalization form alone.
"""

from __future__ import annotations

import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_filename_normalization import find_drift, main  # noqa: E402

_NFC = unicodedata.normalize("NFC", "café")
_NFD = unicodedata.normalize("NFD", "café")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    return root


def test_a_clean_tree_reports_no_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / f"{_NFC}.md").write_text("x", encoding="utf-8", newline="")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    assert find_drift(root) == []


def test_an_nfd_disk_name_for_an_nfc_tracked_name_is_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / f"{_NFC}.md").write_text("x", encoding="utf-8", newline="")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)

    # Materialize the tracked NFC name as an NFD entry on disk, matching what
    # a byte-exact-filesystem checkout of the same commit can produce.
    (root / f"{_NFC}.md").rename(root / f"{_NFD}.md")

    drift = find_drift(root)
    assert len(drift) == 1
    tracked, on_disk = drift[0]
    assert tracked == f"{_NFC}.md"
    assert on_disk == f"{_NFD}.md"


def test_drift_is_found_regardless_of_directory_depth(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    nested = root / "concepts"
    nested.mkdir()
    (nested / f"{_NFC}.md").write_text("x", encoding="utf-8", newline="")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    (nested / f"{_NFC}.md").rename(nested / f"{_NFD}.md")

    drift = find_drift(root)
    assert drift == [(f"concepts/{_NFC}.md", f"concepts/{_NFD}.md")]


def test_a_missing_file_is_not_this_gates_concern(tmp_path: Path) -> None:
    """A tracked file absent from disk entirely (deleted outside git) is a
    different problem -- `git status` already reports it. This gate only
    reports a *normalization* mismatch, never a presence mismatch."""
    root = _repo(tmp_path)
    (root / f"{_NFC}.md").write_text("x", encoding="utf-8", newline="")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    (root / f"{_NFC}.md").unlink()
    assert find_drift(root) == []


def test_main_exits_nonzero_and_reports_on_drift(tmp_path: Path, capsys: object) -> None:
    root = _repo(tmp_path)
    (root / f"{_NFC}.md").write_text("x", encoding="utf-8", newline="")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    (root / f"{_NFC}.md").rename(root / f"{_NFD}.md")

    code = main([str(root)])
    assert code == 1


def test_main_exits_zero_on_a_clean_tree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / f"{_NFC}.md").write_text("x", encoding="utf-8", newline="")
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    assert main([str(root)]) == 0
