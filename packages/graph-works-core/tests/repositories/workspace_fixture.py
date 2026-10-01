"""A bootstrapped workspace that is its own git repository, so `commit_pending` commits (mode `auto`)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gitrepo import git
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout

NOW = datetime(2026, 9, 29, 20, 40, tzinfo=UTC)


def make_workspace(tmp_path: Path) -> WorkspaceLayout:
    root = tmp_path / "ws"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    layout = apply_init(plan_init(root, today=NOW.date(), topic="Repos")).layout
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return layout


def porcelain(layout: WorkspaceLayout) -> str:
    return git(layout.root, "status", "--porcelain", "--untracked-files=all")


def bundle_bytes(layout: WorkspaceLayout) -> dict[str, bytes]:
    """Every bundle file's bytes, except inside clones: a fetch rewrites the clone's own `.git/FETCH_HEAD`,
    which is not a bundle write."""
    files: dict[str, bytes] = {}
    for path in layout.bundle_dir.rglob("*"):
        rel = path.relative_to(layout.bundle_dir).as_posix()
        if path.is_file() and "/references/git/" not in f"/{rel}/":
            files[rel] = path.read_bytes()
    return files
