"""The change-stream watcher: the only `watchfiles` importer in graph-works-serve.

Two `awatch` loops -- `okf/**` recursive, and the config files' parent
directories non-recursive with an exact-path filter (never `.gw/worktrees`
or `.gw/cache/traces`) -- feed `coalesce` -> `events.classify` ->
`Hub.publish`. `watchfiles`' own `step`/`debounce` are the debounce window.
A manifest event re-derives the watch sets; a crashed loop publishes
`resync(watcher-error)` and restarts with backoff. Nothing raises into the
stream, and nothing here logs a query string or the token.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePath

import watchfiles
from config_io import PROJECTION_FILENAME
from graph_works_core.events import Change, ChangeEvent, EventKind, classify
from graph_works_core.workspace.discovery import resolve
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_serve.coalesce import build_batch, net_changes
from graph_works_serve.hub import Hub

STEP_MS = 300
DEBOUNCE_MS = 800
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
_REWATCH_TOKENS = frozenset({"manifest", "manifest-local"})

RawBatch = set[tuple[watchfiles.Change, str]]
AwatchFn = Callable[..., AsyncIterator[RawBatch]]


def _log(message: str) -> None:
    print(f"gw-serve: {message}", file=sys.stderr)


@dataclass(frozen=True)
class WatchSets:
    bundle_dir: Path
    config_dirs: tuple[Path, ...]
    config_paths: frozenset[Path]
    dispatch_documents: tuple[Path, Path] | None


def watch_sets(layout: WorkspaceLayout, dispatch_documents: tuple[Path, Path] | None) -> WatchSets:
    paths = {layout.cache_dir / PROJECTION_FILENAME, layout.manifest_path, layout.local_manifest_path}
    if dispatch_documents is not None:
        paths.update(dispatch_documents)
    return WatchSets(
        bundle_dir=layout.bundle_dir,
        config_dirs=tuple(sorted({path.parent for path in paths})),
        config_paths=frozenset(paths),
        dispatch_documents=dispatch_documents,
    )


def load_state(root: Path) -> tuple[WorkspaceLayout, WatchSets]:
    layout = resolve(workspace=root)
    dispatch = load_dispatch_config(layout)
    return layout, watch_sets(layout, (dispatch.shared_path, dispatch.local_path))


def bundle_filter(bundle_dir: Path) -> Callable[[watchfiles.Change, str], bool]:
    def keep(change: watchfiles.Change, path: str) -> bool:
        try:
            parts = PurePath(path).relative_to(bundle_dir).parts
        except ValueError:
            return False
        return not any(part.startswith(".") for part in parts)

    return keep


def config_filter(paths: frozenset[Path]) -> Callable[[watchfiles.Change, str], bool]:
    def keep(change: watchfiles.Change, path: str) -> bool:
        return Path(path) in paths

    return keep


def path_exists(path: str) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return False


def _classify(layout: WorkspaceLayout, sets: WatchSets, path: PurePath, change: Change) -> ChangeEvent | None:
    return classify(layout, path, change, dispatch_documents=sets.dispatch_documents)


def flush(
    raw: RawBatch,
    layout: WorkspaceLayout,
    sets: WatchSets,
    hub: Hub,
    *,
    exists: Callable[[str], bool] = path_exists,
) -> tuple[ChangeEvent, ...]:
    net = net_changes(((change.name, path) for change, path in raw), exists)
    batch = build_batch(net, partial(_classify, layout, sets))
    hub.publish(batch)
    return batch


def needs_rewatch(batch: Sequence[ChangeEvent]) -> bool:
    return any(event.kind is EventKind.CONFIG and event.path in _REWATCH_TOKENS for event in batch)


@dataclass
class _State:
    layout: WorkspaceLayout
    sets: WatchSets
    backoff: float = BACKOFF_START


async def _sleep_or_stop(stop: asyncio.Event, seconds: float, sleep: Callable[[float], Awaitable[None]] | None) -> None:
    if sleep is not None:
        await sleep(seconds)
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def watch_workspace(
    root: Path,
    hub: Hub,
    stop: asyncio.Event,
    *,
    awatch_fn: AwatchFn = watchfiles.awatch,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    try:
        layout, sets = load_state(root)
    except WorkspaceError as exc:
        _log(f"dispatch documents not watched: {exc}")
        layout = resolve(workspace=root)
        sets = watch_sets(layout, None)
    state = _State(layout, sets)
    while not stop.is_set():
        rewatch = asyncio.Event()
        inner = asyncio.Event()

        async def loop(
            *paths: Path,
            keep: Callable[[watchfiles.Change, str], bool],
            recursive: bool,
            inner: asyncio.Event = inner,
            rewatch: asyncio.Event = rewatch,
        ) -> None:
            async for raw in awatch_fn(
                *paths, watch_filter=keep, step=STEP_MS, debounce=DEBOUNCE_MS, stop_event=inner, recursive=recursive
            ):
                batch = flush(raw, state.layout, state.sets, hub)
                state.backoff = BACKOFF_START
                if needs_rewatch(batch):
                    try:
                        new_layout, new_sets = load_state(root)
                    except (WorkspaceError, OSError) as exc:
                        _log(f"manifest did not load; keeping the current watch: {exc}")
                        continue
                    state.layout = new_layout
                    if new_sets != state.sets:
                        state.sets = new_sets
                        rewatch.set()

        async def relay(inner: asyncio.Event = inner, rewatch: asyncio.Event = rewatch) -> None:
            waiters = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(rewatch.wait())]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
                inner.set()

        config_dirs = [d for d in state.sets.config_dirs if d.is_dir() or _missing(d)]
        relay_task = asyncio.create_task(relay())
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(
                    loop(state.sets.bundle_dir, keep=bundle_filter(state.sets.bundle_dir), recursive=True)
                )
                if config_dirs:
                    group.create_task(loop(*config_dirs, keep=config_filter(state.sets.config_paths), recursive=False))
                await inner.wait()
        except* Exception as group_error:
            first = group_error.exceptions[0]
            _log(f"watcher error: {type(first).__name__}: {first}; restarting in {state.backoff:g}s")
            hub.resync("watcher-error")
            await _sleep_or_stop(stop, state.backoff, sleep)
            state.backoff = min(state.backoff * 2, BACKOFF_MAX)
        finally:
            relay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await relay_task
        if rewatch.is_set() and not stop.is_set():
            hub.resync("rewatch")


def _missing(directory: Path) -> bool:
    """Log a config directory that does not exist; always False so the filter drops it."""
    _log(f"not watching {directory}: no such directory")
    return False


__all__ = [
    "BACKOFF_MAX",
    "BACKOFF_START",
    "DEBOUNCE_MS",
    "STEP_MS",
    "WatchSets",
    "bundle_filter",
    "config_filter",
    "flush",
    "load_state",
    "needs_rewatch",
    "path_exists",
    "watch_sets",
    "watch_workspace",
]
