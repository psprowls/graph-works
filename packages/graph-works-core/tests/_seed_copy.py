"""Copying a session-built seed into one test's `tmp_path`.

A seed is built once per session (per xdist worker) and never handed to a test
directly; every test works on its own copy. A copy is only safe when nothing
inside it still names the seed's absolute path -- `files_containing` is the
check.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from graph_works_core.workspace.layout import WorkspaceLayout


def rebase_layout(layout: WorkspaceLayout, old_root: Path, new_root: Path) -> WorkspaceLayout:
    old, new = old_root.resolve(), new_root.resolve()

    def move(path: Path) -> Path:
        return new / path.relative_to(old)

    return replace(
        layout,
        root=move(layout.root),
        config_dir=move(layout.config_dir),
        cache_dir=move(layout.cache_dir),
        bundle_dir=move(layout.bundle_dir),
        worktrees_dir=move(layout.worktrees_dir),
        repo_root=None if layout.repo_root is None else move(layout.repo_root),
    )


def copy_tree_into(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, symlinks=True)
    return dst


def files_containing(root: Path, needle: str) -> list[Path]:
    encoded = needle.encode("utf-8")
    return [p for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink() and encoded in p.read_bytes()]
