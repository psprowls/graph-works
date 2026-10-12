"""An external pytest consumer exercises installed transaction support."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONSUMER = """\
import stat
import sys

import pytest
import gw_kernel.testing as testing
from gw_kernel.testing.transactions import (
    workspace, snapshot, plan, init_git, git,
    representable_mode, assert_workspace_commit,
)
from okf_io import load_bundle


def test_fixture_contract_and_collection_isolation(request):
    assert testing.__all__ == [
        "code_layout", "declare_repos", "empty_graph_dir", "git_repo",
        "graph_dir", "stale_graph_dir",
    ]
    with pytest.raises(pytest.FixtureLookupError):
        request.getfixturevalue("graph_dir")
    assert not any(
        name == "conftest" and "gw-kernel/tests" in str(getattr(module, "__file__", ""))
        for name, module in sys.modules.items()
    )


def test_read_plan_and_real_git(tmp_path):
    layout = workspace(tmp_path)
    mutation = plan(layout)
    assert mutation.root == layout.bundle_dir
    assert mutation.operation == "reparent"
    before = snapshot(layout.bundle_dir)
    assert load_bundle(layout.bundle_dir).root == layout.bundle_dir
    assert snapshot(layout.bundle_dir) == before
    assert init_git(layout.root) == layout.root
    assert git(layout.root, "branch", "--show-current").strip() == "main"
    assert_workspace_commit(layout.root, "workspace: seed")


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("mode", [0o444, 0o640, 0o750])
def test_representable_mode(tmp_path, monkeypatch, directory, mode):
    path = tmp_path / "entry"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"seed")
    path.chmod(mode)
    try:
        assert stat.S_IMODE(path.stat().st_mode) == representable_mode(mode, directory=directory)
    finally:
        path.chmod(0o777 if directory else 0o666)
    monkeypatch.setattr(sys, "platform", "win32")
    expected = (0o777 if mode & stat.S_IWRITE else 0o555) if directory else (
        0o666 if mode & stat.S_IWRITE else 0o444
    )
    assert representable_mode(mode, directory=directory) == expected
"""


def test_independent_consumer_resolves_transaction_support(tmp_path: Path) -> None:
    (tmp_path / "test_consumer.py").write_text(CONSUMER, encoding="utf-8", newline="\n")
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
