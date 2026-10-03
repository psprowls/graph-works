"""The one place graph-works loads an OKF bundle.

Every load appends the repositories lane's clone glob and prunes the clone directory,
so the walk never lists inside a materialized clone (okf-io `prune=`).
`tests/test_bundle_load_guard.py` fails if any module in graph-works-core, graph-works-cli or
graph-works-serve imports `okf_io.load_bundle` instead of calling these.

`work_scope` defines the work partition. Projection readers use `load_work_bundle`
to prune every other top-level directory without listing it; lint uses
`BundleScope.as_ignore` to retain exact-case, NFC-insensitive member identity.
`tests/test_work_scope_allowlist.py` pins which functions may use the scope.
"""

from __future__ import annotations

import glob
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

import repositories_okf
import work_tracker_okf
from okf_io import Bundle, load_bundle

if TYPE_CHECKING:
    from graph_works_core.workspace.layout import WorkspaceLayout

CLONE_IGNORE: tuple[str, ...] = (repositories_okf.CLONE_GLOB,)
CLONE_PRUNE: tuple[str, ...] = (repositories_okf.PRUNE_GLOB,)


@dataclass(frozen=True, slots=True)
class BundleScope:
    """The `ignore=` and `prune=` patterns that narrow a load to one lane."""

    ignore: tuple[str, ...]
    prune: tuple[str, ...]

    def as_ignore(self) -> tuple[str, ...]:
        """Retain the partition while recording cross-lane member spellings.

        Scope roots are already glob-escaped. Lint uses this fallback because
        okf-io's pruned probes inherit filesystem case/normalization behavior,
        unlike ignored members' exact-case, NFC-insensitive matching. That
        resolver issue is separate; projection readers still use pruning.
        """
        return (*self.ignore, *(f"{root}/*" for root in self.prune))


def with_clone_ignore(ignore: Sequence[str] = ()) -> tuple[str, ...]:
    """*ignore* with the clone glob appended, once."""
    patterns = tuple(ignore)
    return patterns if repositories_okf.CLONE_GLOB in patterns else (*patterns, repositories_okf.CLONE_GLOB)


def ignored_by(member_id: str, ignore: Sequence[str]) -> bool:
    """Match a bundle-relative member with the loader's case-sensitive glob policy."""
    return any(fnmatchcase(member_id, p) for p in ignore)


def with_clone_prune(prune: Sequence[str] = ()) -> tuple[str, ...]:
    """*prune* with the clone directory glob appended, once."""
    patterns = tuple(prune)
    return patterns if repositories_okf.PRUNE_GLOB in patterns else (*patterns, repositories_okf.PRUNE_GLOB)


def load_bundle_at(root: Path, *, ignore: Sequence[str] = (), prune: Sequence[str] = ()) -> Bundle:
    """Load the bundle at *root* -- a lane root or a staged validation root -- hiding clones."""
    return load_bundle(root, ignore=with_clone_ignore(ignore), prune=with_clone_prune(prune))


def load_workspace_bundle(layout: WorkspaceLayout, *, ignore: Sequence[str] = (), prune: Sequence[str] = ()) -> Bundle:
    """Load *layout*'s bundle, hiding clones."""
    return load_bundle_at(layout.bundle_dir, ignore=ignore, prune=prune)


def work_scope(layout: WorkspaceLayout) -> BundleScope:
    """Every top-level directory except `work/` pruned; every root-level file ignored.

    `prune=` matches directories only, so root-level files (`index.md`, `log.md`,
    `work-index.json`) are ignored by name instead: they belong to the wiki lane,
    whose core-catalog index and log rules should fire once, there. Names are
    glob-escaped so a directory called `docs[old]` prunes only itself. Pruned
    membership probes depend on filesystem case/normalization behavior;
    lint must use `BundleScope.as_ignore` until that separate okf-io resolver
    issue is resolved. Projection readers do not validate cross-lane links.

    Reads the bundle directory once; a missing one raises `OSError`.
    """
    prune: list[str] = []
    ignore: list[str] = []
    for entry in sorted(layout.bundle_dir.iterdir(), key=lambda path: path.name):
        if entry.name == work_tracker_okf.WORK_DIR:
            continue
        (prune if entry.is_dir() else ignore).append(glob.escape(entry.name))
    return BundleScope(ignore=(*ignore, *work_tracker_okf.IGNORE), prune=tuple(prune))


def load_work_bundle(layout: WorkspaceLayout) -> Bundle:
    """*layout*'s bundle narrowed to the work lane -- see `work_scope`."""
    scope = work_scope(layout)
    return load_workspace_bundle(layout, ignore=scope.ignore, prune=scope.prune)


__all__ = [
    "CLONE_IGNORE",
    "CLONE_PRUNE",
    "BundleScope",
    "ignored_by",
    "load_bundle_at",
    "load_work_bundle",
    "load_workspace_bundle",
    "with_clone_ignore",
    "with_clone_prune",
    "work_scope",
]
