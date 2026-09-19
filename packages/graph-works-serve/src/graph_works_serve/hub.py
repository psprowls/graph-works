"""In-process fan-out for the change stream: one hub per app, one bounded queue per subscriber.

Knows nothing about files or HTTP -- only batches and subscribers. A slow
subscriber never blocks the watcher or its neighbours: its full queue is
drained and replaced by one `resync(overflow)`, which a client recovers from
by re-fetching. `None` on a queue means the hub closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from graph_works_core.events import ChangeEvent

ResyncReason = Literal["overflow", "rewatch", "watcher-error"]


@dataclass(frozen=True, slots=True)
class Changes:
    seq: int
    events: tuple[ChangeEvent, ...]


@dataclass(frozen=True, slots=True)
class Resync:
    seq: int
    reason: ResyncReason


Message = Changes | Resync


class Subscription:
    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue[Message | None] = asyncio.Queue(maxsize=maxsize)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    async def get(self) -> Message | None:
        return await self._queue.get()

    def _drain(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()

    def _offer(self, item: Message | None, overflow: Resync) -> None:
        if self._queue.full():
            self._drain()
            if item is not None:
                item = overflow
        self._queue.put_nowait(item)


class Hub:
    QUEUE_SIZE = 64

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._subscribers: list[Subscription] = []
        self._seq = 0
        self._closed = False

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self) -> Subscription:
        sub = Subscription(self.QUEUE_SIZE)
        if self._closed:
            sub._offer(None, Resync(self._seq, "overflow"))
        else:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subscribers:
            self._subscribers.remove(sub)

    def publish(self, events: Sequence[ChangeEvent]) -> None:
        if self._closed or not events:
            return
        self._seq += 1
        self._broadcast(Changes(self._seq, tuple(events)))

    def resync(self, reason: ResyncReason) -> None:
        if self._closed:
            return
        self._seq += 1
        self._broadcast(Resync(self._seq, reason))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._broadcast(None)

    def close_threadsafe(self) -> None:
        """Schedule `close` on the hub's loop; safe from a signal handler or another thread."""
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self.close)

    def _broadcast(self, item: Message | None) -> None:
        overflow = Resync(self._seq, "overflow")
        for sub in self._subscribers:
            sub._offer(item, overflow)


ChangeSource = Callable[[Path, Hub, asyncio.Event], Awaitable[None]]
"""Feeds a hub until `stop` is set: `watch.watch_workspace` in production, a script in tests."""


__all__ = ["ChangeSource", "Changes", "Hub", "Message", "Resync", "ResyncReason", "Subscription"]
