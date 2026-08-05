"""Tests for the directory-skip helper."""

from __future__ import annotations

from pathlib import Path

from code_graph_io import _ignore


def test_default_skip_dirs_contents() -> None:
    assert _ignore.DEFAULT_SKIP_DIRS == frozenset(
        {
            ".git",
            "node_modules",
            ".worktrees",
            ".venv",
            "venv",
            "dist",
            "build",
            "__pycache__",
            ".tox",
            ".nox",
        }
    )


def test_load_returns_defaults_when_no_graphignore(tmp_path: Path) -> None:
    assert _ignore.load_skip_dirs(tmp_path) == _ignore.DEFAULT_SKIP_DIRS


def test_load_merges_graphignore_entries(tmp_path: Path) -> None:
    (tmp_path / ".graphignore").write_text("generated\nvendor\n")
    result = _ignore.load_skip_dirs(tmp_path)
    assert "generated" in result
    assert "vendor" in result
    assert _ignore.DEFAULT_SKIP_DIRS <= result


def test_load_ignores_blanks_and_comments(tmp_path: Path) -> None:
    (tmp_path / ".graphignore").write_text("# a comment\n\ngenerated\n   \n# another\nvendor\n")
    result = _ignore.load_skip_dirs(tmp_path)
    assert "generated" in result
    assert "vendor" in result
    assert "# a comment" not in result
    assert "" not in result


def test_load_tolerates_trailing_slash(tmp_path: Path) -> None:
    (tmp_path / ".graphignore").write_text("generated/\n")
    result = _ignore.load_skip_dirs(tmp_path)
    assert "generated" in result
    assert "generated/" not in result


def test_should_skip_matches_any_path_component() -> None:
    skip = frozenset({"dist", "node_modules"})
    assert _ignore.should_skip("dist/foo.py", skip)
    assert _ignore.should_skip("packages/x/node_modules/y/z.js", skip)
    assert _ignore.should_skip("dist", skip)
    assert not _ignore.should_skip("packages/x/src/a.py", skip)
    assert not _ignore.should_skip("distance.py", skip)  # substring is NOT a match
