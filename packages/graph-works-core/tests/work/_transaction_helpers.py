"""Shared fixtures for `test_transactions.py` and `test_windows_anchor.py`.

Not a package module: `tests/` carries no `__init__.py`, so this is imported
as a bare top-level module (`from _transaction_helpers import ...`). That
only resolves because `tests/conftest.py` puts this directory on `sys.path`
before any test module in the run is collected -- see the comment there.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.workspace import anchors, transactions
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for
from okf_ext.moves import Move
from work_tracker_okf.init import install_bundle
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan


def _workspace(tmp_path: Path) -> WorkspaceLayout:
    layout = layout_for(tmp_path / "workspace")
    layout.bundle_dir.mkdir(parents=True)
    layout.cache_dir.mkdir(parents=True)
    installed = install_bundle(layout.bundle_dir, today=date(2026, 8, 22), dry_run=False)
    assert installed.ok
    return layout


def _snapshot(root: Path) -> dict[str, tuple[str, int, bytes | str | None]]:
    snapshot: dict[str, tuple[str, int, bytes | str | None]] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir(), key=lambda entry: os.fsencode(entry.name), reverse=True):
            relative = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                snapshot[relative] = ("symlink", mode, str(path.readlink()))
            elif path.is_dir():
                snapshot[relative] = ("directory", mode, None)
                pending.append(path)
            else:
                snapshot[relative] = ("file", mode, path.read_bytes())
    return snapshot


def _plan(
    layout: WorkspaceLayout,
    *,
    moves: tuple[Move, ...] = (),
    writes: tuple[PlannedWrite, ...] = (),
    deletes: tuple[str, ...] = (),
    mkdirs: tuple[str, ...] = (),
    validate_paths: tuple[str, ...] = (),
    directory_preconditions: tuple[DirectoryPrecondition, ...] = (),
    path_mapping: dict[str, str] | None = None,
) -> WorkMutationPlan:
    return WorkMutationPlan(
        root=layout.bundle_dir,
        operation="reparent",
        path_mapping={} if path_mapping is None else path_mapping,
        move_plan=None,
        moves=moves,
        writes=writes,
        deletes=deletes,
        mkdirs=mkdirs,
        warnings=("opaque reference retained",),
        refusals=(),
        validate_paths=validate_paths,
        directory_preconditions=directory_preconditions,
    )


@contextmanager
def _forced_tier(platform_name: str) -> Iterator[None]:
    """Run the engine on a named tier.

    Patches the two module-level selector delegators in `transactions` rather
    than `anchors`' own functions, because `transactions._open_root` and
    `_open_absolute_directory` resolve those names through `transactions`'
    globals -- the same reason the `:290-305` delegator layer exists.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            transactions,
            "_open_root",
            lambda root: anchors.open_anchor(root, platform_name=platform_name),
        )
        patch.setattr(
            transactions,
            "_open_absolute_directory",
            lambda path: anchors.open_absolute_anchor(path, platform_name=platform_name),
        )
        yield
