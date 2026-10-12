"""Polling watcher backend and filesystem writes observable at its second resolution."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import watchfiles
from gw_serve.watch import RawBatch


class PollingBackend:
    """Expose completed idle waits of watchfiles' polling backend.

    The gate runs many suites at once; native FSEvents delivery stalls for
    seconds under that load (see the report on this item), while polling is
    deterministic. The native path stays covered by the explicit-native
    smoke run in a quiet environment, not by the gate.
    """

    def __init__(self) -> None:
        self._idle: asyncio.Queue[bool] = asyncio.Queue()

    async def awatch(
        self,
        *paths: Path,
        watch_filter: Callable[[watchfiles.Change, str], bool],
        step: int,
        debounce: int,
        stop_event: asyncio.Event,
        recursive: bool,
    ) -> AsyncIterator[RawBatch]:
        reported = False
        async for batch in watchfiles.awatch(
            *paths,
            watch_filter=watch_filter,
            step=step,
            debounce=debounce,
            stop_event=stop_event,
            recursive=recursive,
            yield_on_timeout=True,
            force_polling=True,
        ):
            if batch:
                yield batch
            elif not reported:
                # awatch constructs RustNotify before waiting. An empty batch
                # proves a completed backend timeout, rather than subscriber
                # silence while native events are still being delivered.
                self._idle.put_nowait(recursive)
                reported = True

    async def wait_ready(self) -> None:
        seen: set[bool] = set()
        while seen != {True, False}:
            seen.add(await self._idle.get())


def advance_mtime(path: Path, previous_ns: int) -> None:
    stat = path.stat()
    # notify's polling backend compares whole-second modification times.
    os.utime(path, ns=(stat.st_atime_ns, max(stat.st_mtime_ns, previous_ns + 1_000_000_000)))


def write(path: Path, text: str) -> None:
    previous_ns = path.stat().st_mtime_ns if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    advance_mtime(path, previous_ns)
