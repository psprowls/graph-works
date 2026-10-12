"""Independent consumers catch broken fixture exports, seeds and dependency wiring."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONSUMER = """\
import sqlite3
import subprocess
from pathlib import Path

from gw_kernel.testing import (
    graph_dir as graph_dir,
    empty_graph_dir as empty_graph_dir,
    stale_graph_dir as stale_graph_dir,
    git_repo as git_repo,
    declare_repos as declare_repos,
    code_layout as code_layout,
)


def test_graphs(graph_dir, empty_graph_dir, stale_graph_dir):
    assert not (empty_graph_dir / "code.db").exists()
    with sqlite3.connect(graph_dir / "code.db") as conn:
        assert conn.execute("SELECT count(*) FROM nodes").fetchone() == (17,)
        assert conn.execute("SELECT count(*) FROM edges").fetchone() == (18,)
        assert conn.execute("SELECT kind, name, uri, repo FROM nodes WHERE id = 1").fetchone() == (
            "repository", "demo", "repo:acme/demo", "repo:acme/demo"
        )
        assert conn.execute("SELECT attrs_json FROM nodes WHERE id = 16").fetchone() == ('{"language": "typescript"}',)
    with sqlite3.connect(stale_graph_dir / "code.db") as conn:
        assert conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone() == ("1",)
        assert conn.execute("SELECT count(*) FROM nodes").fetchone() == (17,)


def test_git(git_repo, tmp_path):
    repo = git_repo(
        tmp_path / "repo", {"nested/a.txt": "café\\n", "b.bin": b"\\x00\\xff"},
        untracked={"u.txt": "later\\n"},
    )
    assert (repo / "nested/a.txt").read_bytes() == b"caf\\xc3\\xa9\\n"
    assert (repo / "b.bin").read_bytes() == b"\\x00\\xff"
    assert (repo / "u.txt").read_bytes() == b"later\\n"
    assert subprocess.check_output(["git", "ls-files"], cwd=repo, text=True).splitlines() == ["b.bin", "nested/a.txt"]
    assert subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo, capture_output=True).returncode != 0


def test_layout(code_layout, declare_repos, tmp_path):
    manifest = code_layout.manifest_path.read_text(encoding="utf-8")
    assert "2026-09-19" in manifest
    assert "Code" in manifest
    assert '  "code":' in manifest
    code = tmp_path / "code"
    assert (code / "src/a.py").read_text(encoding="utf-8").splitlines() == [f"a{n}" for n in range(1, 21)]
    prefix = manifest.split("repositories:")[0].rstrip("\\n")
    declare_repos(code_layout, {"other": (code, ["private/**"])}, ["global/**"])
    changed = code_layout.manifest_path.read_text(encoding="utf-8")
    assert changed == (prefix + '\\nrepositories:\\n  "other":\\n    path: "'
        + code.as_posix() + '"\\n    ignore:\\n      - "private/**"\\nignore:\\n  - "global/**"\\n')
    declare_repos(code_layout, {})
    assert code_layout.manifest_path.read_text(encoding="utf-8") == prefix + "\\nrepositories:\\n"
"""

OVERRIDE = """\
import pytest
from gw_kernel.testing import code_layout as code_layout, git_repo as git_repo

@pytest.fixture
def declare_repos():
    def declare(layout, repos):
        layout.manifest_path.write_text("overridden:" + next(iter(repos)), encoding="utf-8", newline="\\n")
    return declare

def test_override(code_layout):
    assert code_layout.manifest_path.read_text(encoding="utf-8") == "overridden:code"
"""


def test_independent_consumer_resolves_shared_fixtures(tmp_path: Path) -> None:
    (tmp_path / "test_consumer.py").write_text(CONSUMER, encoding="utf-8", newline="\n")
    (tmp_path / "test_override.py").write_text(OVERRIDE, encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--confcutdir", str(tmp_path), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_testing_package_preserves_fixture_exports():
    import gw_kernel.testing as support
    from gw_kernel.testing.seed_copy import copy_tree_into, files_containing, rebase_layout
    from gw_kernel.testing.transactions import assert_workspace_commit

    assert set(support.__all__) == {
        "graph_dir",
        "empty_graph_dir",
        "stale_graph_dir",
        "git_repo",
        "declare_repos",
        "code_layout",
    }
    assert all(callable(getattr(support, name)) for name in support.__all__)
    assert all(
        callable(helper)
        for helper in (
            copy_tree_into,
            files_containing,
            rebase_layout,
            assert_workspace_commit,
        )
    )
