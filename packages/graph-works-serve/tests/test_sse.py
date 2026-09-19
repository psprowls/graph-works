from __future__ import annotations

import asyncio
import json

import pytest
from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_serve import sse
from graph_works_serve.hub import Changes, Hub, Resync
from graph_works_wire.events import changes_payload

E = ChangeEvent(kind=EventKind.WORK_ITEM, path="work/a", member="work/a.md", change=Change.MODIFIED)


def test_frame_is_event_then_compact_data_then_blank_line() -> None:
    assert sse.frame("changes", {"a": "é", "b": [1]}) == 'event: changes\ndata: {"a":"é","b":[1]}\n\n'.encode()


def test_ready_frame_sets_retry_first() -> None:
    assert sse.ready_frame(41) == b'retry: 2000\nevent: ready\ndata: {"schema_version":1,"seq":41}\n\n'


def test_changes_frame_is_the_wire_payload_framed() -> None:
    expected = (
        "event: changes\ndata: "
        + json.dumps(changes_payload(42, (E,)), separators=(",", ":"), ensure_ascii=False)
        + "\n\n"
    )
    assert sse.message_frame(Changes(42, (E,))) == expected.encode()


def test_resync_frame() -> None:
    assert (
        sse.message_frame(Resync(57, "overflow"))
        == b'event: resync\ndata: {"schema_version":1,"seq":57,"reason":"overflow"}\n\n'
    )


def test_no_frame_carries_an_id_line() -> None:
    for blob in (
        sse.ready_frame(1),
        sse.message_frame(Changes(1, (E,))),
        sse.message_frame(Resync(1, "rewatch")),
        sse.PING,
    ):
        assert b"\nid:" not in blob and not blob.startswith(b"id:")


async def _never() -> bool:
    return False


async def test_stream_yields_ready_then_messages_and_ends_on_close() -> None:
    hub = Hub()
    stream = sse.event_stream(hub, _never, ping_interval=60)
    assert await anext(stream) == sse.ready_frame(0)
    assert hub.subscriber_count == 1
    hub.publish([E])
    assert await anext(stream) == sse.message_frame(Changes(1, (E,)))
    hub.close()
    assert [chunk async for chunk in stream] == []
    assert hub.subscriber_count == 0


async def test_stream_pings_when_idle() -> None:
    hub = Hub()
    stream = sse.event_stream(hub, _never, ping_interval=0.01)
    await anext(stream)
    assert await anext(stream) == sse.PING
    await stream.aclose()
    assert hub.subscriber_count == 0


async def test_stream_ends_when_client_is_gone_at_ping_time() -> None:
    async def gone() -> bool:
        return True

    hub = Hub()
    stream = sse.event_stream(hub, gone, ping_interval=0.01)
    await anext(stream)
    assert [chunk async for chunk in stream] == []
    assert hub.subscriber_count == 0


async def test_cancellation_unsubscribes() -> None:
    hub = Hub()
    stream = sse.event_stream(hub, _never, ping_interval=60)
    await anext(stream)
    task = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await stream.aclose()
    assert hub.subscriber_count == 0


async def test_default_ping_interval_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sse, "PING_INTERVAL", 0.01)
    hub = Hub()
    stream = sse.event_stream(hub, _never)
    await anext(stream)
    assert await anext(stream) == sse.PING
    await stream.aclose()
