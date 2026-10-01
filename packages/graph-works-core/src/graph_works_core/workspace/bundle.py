"""The one place graph-works loads an OKF bundle.

Every load appends the repositories lane's clone glob and prunes the clone directory,
so the walk never lists inside a materialized clone (okf-io `prune=`).
`tests/test_bundle_load_guard.py` fails if any module in graph-works-core, graph-works-cli or
graph-works-serve imports `okf_io.load_bundle` instead of calling these.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import repositories_okf
from okf_io import Bundle, load_bundle

if TYPE_CHECKING:
    from graph_works_core.workspace.layout import WorkspaceLayout

CLONE_IGNORE: tuple[str, ...] = (repositories_okf.CLONE_GLOB,)
CLONE_PRUNE: tuple[str, ...] = (repositories_okf.PRUNE_GLOB,)


def with_clone_ignore(ignore: Sequence[str] = ()) -> tuple[str, ...]:
    """*ignore* with the clone glob appended, once."""
    patterns = tuple(ignore)
    return patterns if repositories_okf.CLONE_GLOB in patterns else (*patterns, repositories_okf.CLONE_GLOB)


def load_bundle_at(root: Path, *, ignore: Sequence[str] = ()) -> Bundle:
    """Load the bundle at *root* -- a lane root or a staged validation root -- hiding clones."""
    return load_bundle(root, ignore=with_clone_ignore(ignore), prune=CLONE_PRUNE)


def load_workspace_bundle(layout: WorkspaceLayout, *, ignore: Sequence[str] = ()) -> Bundle:
    """Load *layout*'s bundle, hiding clones."""
    return load_bundle_at(layout.bundle_dir, ignore=ignore)


__all__ = ["CLONE_IGNORE", "CLONE_PRUNE", "load_bundle_at", "load_workspace_bundle", "with_clone_ignore"]
