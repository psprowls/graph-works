from __future__ import annotations

import asyncio
import threading

import pytest
from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_serve.hub import Changes, Hub, Resync

E1 = ChangeEvent(kind=EventKind.LOG, path="log.md", member="log.md", change=Change.MODIFIED)
E2 = ChangeEvent(kind=EventKind.PAGE, path="a.md", member="a.md", change=Change.ADDED)


async def test_publish_delivers_in_order_with_monotonic_seq() -> None:
    hub = Hub()
    a, b = hub.subscribe(), hub.subscribe()
    hub.publish([E1])
    hub.publish([E2])
    for sub in (a, b):
        assert await sub.get() == Changes(1, (E1,))
        assert await sub.get() == Changes(2, (E2,))
    assert hub.seq == 2


async def test_empty_publish_is_a_no_op() -> None:
    hub = Hub()
    sub = hub.subscribe()
    hub.publish([])
    assert hub.seq == 0 and sub.queue_size == 0


async def test_broadcast_resync_advances_seq() -> None:
    hub = Hub()
    sub = hub.subscribe()
    hub.resync("rewatch")
    assert await sub.get() == Resync(1, "rewatch")


async def test_overflow_drains_and_enqueues_exactly_one_resync() -> None:
    hub = Hub()
    slow, fast = hub.subscribe(), hub.subscribe()
    for _ in range(Hub.QUEUE_SIZE):
        hub.publish([E1])
        await fast.get()
    hub.publish([E2])  # slow's queue is full -> overflow
    assert slow.queue_size == 1
    assert await slow.get() == Resync(Hub.QUEUE_SIZE + 1, "overflow")
    assert await fast.get() == Changes(Hub.QUEUE_SIZE + 1, (E2,))
    assert hub.seq == Hub.QUEUE_SIZE + 1


async def test_close_ends_every_subscriber_and_later_subscribers() -> None:
    hub = Hub()
    full = hub.subscribe()
    for _ in range(Hub.QUEUE_SIZE):
        hub.publish([E1])
    hub.close()
    hub.close()  # idempotent
    assert hub.closed
    assert await full.get() is None
    assert await hub.subscribe().get() is None


async def test_publish_after_close_is_ignored() -> None:
    hub = Hub()
    hub.close()
    hub.publish([E1])
    hub.resync("rewatch")
    assert hub.seq == 0


async def test_unsubscribe_is_idempotent() -> None:
    hub = Hub()
    sub = hub.subscribe()
    assert hub.subscriber_count == 1
    hub.unsubscribe(sub)
    hub.unsubscribe(sub)
    assert hub.subscriber_count == 0
    hub.publish([E1])
    assert sub.queue_size == 0


async def test_close_threadsafe_from_another_thread() -> None:
    hub = Hub()
    sub = hub.subscribe()
    thread = threading.Thread(target=hub.close_threadsafe)
    thread.start()
    thread.join()
    assert await asyncio.wait_for(sub.get(), 1) is None


def test_close_threadsafe_after_loop_closed_is_a_no_op() -> None:
    async def make() -> Hub:
        return Hub()

    hub = asyncio.run(make())
    hub.close_threadsafe()  # must not raise


def test_hub_requires_a_running_loop() -> None:
    with pytest.raises(RuntimeError):
        Hub()
