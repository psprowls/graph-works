"""The workspace placement surface is gone: no source names a workspace branch, preparation or merge.

Workspace writes land on the primary checkout (feature-workspace-writes-primary-checkout).
Two legacy readers keep reading old `repo_stamps._workspace` until `gw work migrate-placement`
has run (own D-002): the reservation in `gw_kernel/workspace/config.py` and
`gw_work/migrate_placement.py`. `tech-debt-delete-the-legacy-placement-read-path` deletes them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PATTERN = (
    r'"_workspace"|WORKSPACE_REPO|workspace_repo\(|prepare.workspace|merge.workspace|workspace_preparations'
    r"|workspace_placements|WorkspacePreparation|select_workspace|workspace-pending|WORKSPACE_CONTENT_LINE"
    r"|Workspace content root|resolve_destination|ReturnDestination|workspace_placement|workspace_branch"
    r"|plan_workspace_placement"
)

#: `packages/*/src` and the shipped plugin tree, as git pathspecs.
SCOPE = (":(glob)packages/*/src/**", ":(glob)plugins/gw/**")

LEGACY_READERS = (
    "packages/gw-kernel/src/gw_kernel/workspace/config.py",
    "packages/gw-work/src/gw_work/migrate_placement.py",
    # The inverted plugin guard names the removed surface in order to forbid it.
    "plugins/gw/tests/test-placement-docs.sh",
)


def _repo_root() -> Path:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not on PATH")
    out = subprocess.run(
        [git, "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if out.returncode != 0:
        pytest.skip("not inside a git checkout")
    return Path(out.stdout.strip())


def test_no_source_names_the_workspace_stamp_key() -> None:
    """A bare `_workspace` word in `packages/*/src` is the retired stamp key leaking back."""
    root = _repo_root()
    result = subprocess.run(
        [
            "git",
            "grep",
            "--untracked",
            "-nE",
            r"(^|[^A-Za-z0-9_])_workspace([^A-Za-z0-9_]|$)",
            "--",
            ":(glob)packages/*/src/**",
            *(f":(exclude){path}" for path in LEGACY_READERS),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    assert result.stdout == ""


def test_done_grep_is_empty() -> None:
    root = _repo_root()
    result = subprocess.run(
        [
            "git",
            "grep",
            "--untracked",
            "-nE",
            PATTERN,
            "--",
            *SCOPE,
            *(f":(exclude){path}" for path in LEGACY_READERS),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    assert result.stdout == ""
