"""Shared bundle locking and pending workspace commits over resolved kernel paths."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

from gw_kernel.workspace import anchors
from gw_kernel.workspace.anchors import Anchor, open_anchor
from gw_kernel.workspace.commits import CommitOutcome, WorkspaceCommit, commit_mode, commit_workspace
from gw_kernel.workspace.layout import WorkspaceLayout

#: A bounded bundle-lock wait expired without writing or committing.
LOCK_TIMEOUT_FAILURE = "bundle-lock-timeout"

_HELD_BUNDLE_LOCKS: ContextVar[frozenset[str]] = ContextVar("_HELD_BUNDLE_LOCKS", default=frozenset())


def bundle_lock_held(layout: WorkspaceLayout) -> bool:
    """Whether this context already holds the lock for the resolved bundle root."""
    return str(layout.bundle_dir.resolve()) in _HELD_BUNDLE_LOCKS.get()


@contextmanager
def bundle_root_lock(anchor: Anchor, *, timeout: float | None = None) -> Iterator[None]:
    """Serialize callers over the anchored bundle directory."""
    with anchor.exclusive_lock(timeout=timeout):
        yield


@contextmanager
def held_bundle_lock(layout: WorkspaceLayout) -> Iterator[None]:
    """Hold the mutation lock across Git effects and mutations in this context.

    Call only inside decision-owner ownership: lock order is owner then bundle.
    Nested callers reuse this hold; other threads and processes still wait.
    """
    key = str(layout.bundle_dir.resolve())
    if bundle_lock_held(layout):
        yield
        return
    root = open_anchor(layout.bundle_dir)
    try:
        with bundle_root_lock(root):
            token = _HELD_BUNDLE_LOCKS.set(_HELD_BUNDLE_LOCKS.get() | {key})
            try:
                yield
            finally:
                _HELD_BUNDLE_LOCKS.reset(token)
    finally:
        root.close()


def commit_pending(
    layout: WorkspaceLayout,
    commit: WorkspaceCommit,
    *,
    lock_timeout: float | None = None,
    on_committed: Callable[[], None] | None = None,
) -> CommitOutcome:
    """Commit *commit*'s items' `references/` and `extra_paths` under the bundle lock, with no plan.

    For a verb whose plan is a no-op but whose item still owns freshly written
    files (a prior stage's `orca-placement/<key>.json`), and for
    the wiki half of `gw work archive`, which applies outside `apply_mutation`.
    """
    mode = commit_mode(layout)
    root = open_anchor(layout.bundle_dir)
    try:
        try:
            with nullcontext() if bundle_lock_held(layout) else bundle_root_lock(root, timeout=lock_timeout):
                if on_committed is not None:
                    return commit_workspace(layout, commit, (), mode=mode, on_committed=on_committed)
                return commit_workspace(layout, commit, (), mode=mode)
        except anchors.LockTimeout:
            return CommitOutcome("failed", None, commit.subject, (), LOCK_TIMEOUT_FAILURE)
    finally:
        root.close()


__all__ = [
    "LOCK_TIMEOUT_FAILURE",
    "bundle_lock_held",
    "bundle_root_lock",
    "commit_pending",
    "held_bundle_lock",
]
