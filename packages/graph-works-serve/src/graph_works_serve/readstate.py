"""The sidecar's only read state: a generation and one single-flight snapshot.

Framework-free: no Starlette, no watchfiles. Identity and future selection are
atomic. Completion tokens fence obsolete builds and reconciles, including a
stale mark without a generation bump. Slow index operations are serialized by
_operation; lock order is operation -> state, never state -> operation. The
mutation engine may take its lock before operation; ReadState never takes it.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Literal

from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_core.read_session import (
    IndexRevision,
    ReadSession,
    SnapshotSession,
    fingerprint,
    index_revision,
    materialize,
    open_read_session,
)
from graph_works_core.workspace.layout import WorkspaceLayout

BumpReason = Literal["index", "config", "fallback", "reconcile-failed", "resync", "identity", "write-through"]
Identity = tuple[Path, Path, Path, str]
Key = tuple[Identity, int]
Token = tuple[Key, int]
Opener = Callable[..., AbstractContextManager[ReadSession]]


class ReadState:
    def __init__(
        self,
        *,
        opener: Opener | None = None,
        materializer: Callable[[ReadSession], SnapshotSession] | None = None,
    ) -> None:
        self._opener = open_read_session if opener is None else opener
        self._materializer = materialize if materializer is None else materializer
        self._lock = threading.Lock()
        self._operation = threading.Lock()
        self._generation = 1
        self._identity: Identity | None = None
        self._slot: tuple[Key, Future[SnapshotSession]] | None = None
        self._needs_reconcile = True
        self._index_revision: IndexRevision | None = None
        self._stale_epoch = 0
        self.bumps: list[tuple[int, BumpReason]] = []

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def _advance_locked(self, reason: BumpReason) -> int:
        self._generation += 1
        self._slot = None
        self.bumps.append((self._generation, reason))
        print(f"gw-serve: read generation {self._generation} ({reason})", file=sys.stderr)
        return self._generation

    def advance(self, reason: BumpReason) -> int:
        with self._lock:
            return self._advance_locked(reason)

    def _stale_locked(self) -> None:
        self._needs_reconcile = True
        self._stale_epoch += 1

    def mark_stale(self) -> None:
        with self._lock:
            self._stale_locked()

    def _adopt_locked(self, identity: Identity) -> Key:
        if self._identity is None:
            self._identity = identity
        elif self._identity != identity:
            self._identity = identity
            self._index_revision = None
            self._stale_locked()
            self._advance_locked("identity")
        return identity, self._generation

    def _current_locked(self, token: Token) -> bool:
        return token == ((self._identity, self._generation), self._stale_epoch)

    @staticmethod
    def _identity_for(layout: WorkspaceLayout) -> Identity:
        return layout.root, layout.bundle_dir, layout.cache_dir, fingerprint(layout)

    @contextmanager
    def session(self, layout: WorkspaceLayout) -> Iterator[tuple[int, ReadSession]]:
        identity = self._identity_for(layout)
        with self._lock:
            key = self._adopt_locked(identity)
            token = key, self._stale_epoch
            builder = self._slot is None or self._slot[0] != key
            if builder:
                future: Future[SnapshotSession] = Future()
                self._slot = key, future
                reconcile = self._needs_reconcile
            else:
                assert self._slot is not None
                future = self._slot[1]
                reconcile = False
        if builder:
            self._build(layout, future, token, reconcile)
        try:
            snapshot = future.result()
        except Exception as exc:
            print(f"gw-serve: snapshot build failed: {type(exc).__name__}: {exc}; using a fresh read", file=sys.stderr)
            snapshot = None
        # Yield outside exception handling: handler failures belong to the caller.
        if snapshot is not None:
            yield key[1], snapshot
        else:
            with ExitStack() as stack:
                with self._operation:
                    session = stack.enter_context(self._opener(layout))
                yield key[1], session

    def _build(self, layout: WorkspaceLayout, future: Future[SnapshotSession], token: Token, reconcile: bool) -> None:
        try:
            with self._operation:
                with self._opener(layout, reconcile=reconcile) as session:
                    snapshot = self._materializer(session)
                with self._lock:
                    if self._current_locked(token) and reconcile and snapshot.backend == "index":
                        self._needs_reconcile = False
                        self._index_revision = index_revision(snapshot)
            future.set_result(snapshot)
        except Exception as exc:
            future.set_exception(exc)
            with self._lock:
                if self._slot is not None and self._slot[1] is future:
                    self._slot = None

    def reconcile(self, layout: WorkspaceLayout, batch: Sequence[ChangeEvent]) -> int:
        return self._reconcile(layout, batch, write=False)

    def write_through(self, layout: WorkspaceLayout) -> int:
        return self._reconcile(layout, (ChangeEvent(EventKind.PAGE, "", None, Change.MODIFIED),), write=True)

    def _reconcile(self, layout: WorkspaceLayout, batch: Sequence[ChangeEvent], *, write: bool) -> int:
        config = any(event.kind is EventKind.CONFIG for event in batch)
        bundle = any(event.kind is not EventKind.CONFIG for event in batch)
        with self._operation:
            token: Token | None = None
            try:
                identity = self._identity_for(layout)
                with self._lock:
                    key = self._adopt_locked(identity)
                    if config:
                        self._stale_locked()
                    token = key, self._stale_epoch
                if bundle:
                    with self._opener(layout, reconcile=True) as session:
                        backend, revision, fallback = session.backend, index_revision(session), session.fallback
                else:
                    backend, revision, fallback = "index", None, None
            except Exception:
                # A failure predating a resync/identity transition cannot invalidate it again.
                with self._lock:
                    if token is None or self._current_locked(token):
                        self._stale_locked()
                        return self._advance_locked("reconcile-failed")
                    return self._generation
            assert token is not None
            with self._lock:
                if not self._current_locked(token):
                    return self._generation
                if bundle:
                    if fallback in {"busy", "unavailable", "error"}:
                        self._stale_locked()
                        return self._advance_locked("reconcile-failed")
                    if backend == "bundle":
                        self._stale_locked()
                        return self._advance_locked("write-through" if write else "fallback")
                    moved = revision != self._index_revision
                    self._index_revision = revision
                    self._needs_reconcile = config
                    if moved:
                        return self._advance_locked("write-through" if write else "index")
                if config:
                    return self._advance_locked("config")
                return self._generation


__all__ = ["BumpReason", "Identity", "Opener", "ReadState"]
