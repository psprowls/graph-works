"""Select an indexed display snapshot or a fresh full-load fallback."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from okf_ext import readindex

from graph_works_core.read_session import location
from graph_works_core.read_session.bundle_backend import BundleSession
from graph_works_core.read_session.index_backend import IndexSession
from graph_works_core.read_session.model import FallbackReason, ReadSession
from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import checked_bool


@contextmanager
def open_read_session(layout: WorkspaceLayout, *, reconcile: bool = True) -> Iterator[ReadSession]:
    """Own the index and read transaction without catching caller exceptions."""
    with ExitStack() as stack:
        yield _open(layout, reconcile=reconcile, stack=stack)


@contextmanager
def borrow_read_session(layout: WorkspaceLayout, session: ReadSession | None) -> Iterator[ReadSession]:
    """Yield the caller's *session*, or open (and reconcile) one when it is `None`.

    The seam for display `run_*` reads: the CLI passes nothing, the sidecar
    passes its memoized snapshot so a read never reconciles on its own.
    """
    if session is not None:
        yield session
        return
    with open_read_session(layout) as owned:
        yield owned


def _open(layout: WorkspaceLayout, *, reconcile: bool, stack: ExitStack) -> ReadSession:
    root = layout.bundle_dir
    if not root.is_dir():
        raise FileNotFoundError(f"bundle root not found: {root}")
    if not checked_bool(layout, location.ENABLED_KEY):
        return BundleSession(root, fallback="disabled")
    db_path = location.database_path(layout)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        index = stack.enter_context(
            readindex.open_index(
                db_path,
                root,
                ignore=CLONE_IGNORE,
                prune=CLONE_PRUNE,
                fingerprint=location.fingerprint(layout),
                busy_timeout_ms=location.BUSY_TIMEOUT_MS,
            )
        )
        if index.rebuilt_reason is not None:
            location.logger.warning("read index rebuilt (%s): %s", index.rebuilt_reason, db_path)
        if reconcile or index.rebuilt_reason is not None:
            result = readindex.reconcile(index)
            if result.unsettled:
                raise readindex.IndexUnavailable(f"unsettled members: {result.unsettled}")
        view = stack.enter_context(readindex.read(index))
        return IndexSession(index, view, root=root, db_path=db_path)
    except readindex.IndexBusy as exc:
        return _fallback(root, "busy", db_path, exc)
    except (readindex.IndexUnavailable, OSError) as exc:
        return _fallback(root, "unavailable", db_path, exc)
    except sqlite3.Error as exc:
        return _fallback(root, "error", db_path, exc)


def _fallback(root: Path, reason: FallbackReason, db_path: Path, exc: BaseException) -> BundleSession:
    location.warn_fallback(reason, db_path, exc)
    return BundleSession(root, fallback=reason)
