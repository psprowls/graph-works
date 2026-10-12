"""An external consumer resolves installed pipeline tier support without conftests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONSUMER = """\
import pytest
from gw_kernel.testing.transactions import workspace, snapshot
from gw_kernel.workspace import anchors
from gw_pipeline import transactions
from gw_pipeline.testing import forced_tier


@pytest.mark.parametrize("platform_name", ["linux", "win32"])
def test_forced_tier_preserves_content_and_restores_selectors(tmp_path, platform_name):
    layout = workspace(tmp_path)
    before = snapshot(layout.bundle_dir)
    original_root = transactions._open_root
    original_directory = transactions._open_absolute_directory
    with forced_tier(platform_name):
        anchor = transactions._open_root(layout.bundle_dir)
        absolute = transactions._open_absolute_directory(layout.bundle_dir)
        try:
            expected = anchors._WindowsAnchor if platform_name == "win32" else anchors._PosixAnchor
            assert isinstance(anchor, expected)
            assert isinstance(absolute, expected)
            with anchor.exclusive_lock():
                assert snapshot(layout.bundle_dir) == before
        finally:
            anchor.close()
            absolute.close()
    assert transactions._open_root is original_root
    assert transactions._open_absolute_directory is original_directory
    assert snapshot(layout.bundle_dir) == before


"""


def test_independent_consumer_resolves_pipeline_tier_support(tmp_path: Path) -> None:
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
