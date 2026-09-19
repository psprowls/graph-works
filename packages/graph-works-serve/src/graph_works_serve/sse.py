"""Server-Sent Events framing and the per-connection stream for `GET /v1/events`.

Framework-free: `app.py` wraps `event_stream` in Starlette's
`StreamingResponse`. No frame carries `id:` -- there is no replay, so a
reconnect gets a fresh `ready`, which means "re-fetch everything".
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from graph_works_wire.events import SCHEMA_VERSION, changes_payload

from graph_works_serve.hub import Changes, Hub, Message

PING_INTERVAL = 15.0
RETRY_MS = 2000
MEDIA_TYPE = "text/event-stream; charset=utf-8"
HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
PING = b": ping\n\n"


def encode_data(obj: Mapping[str, object]) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def frame(event: str, data: Mapping[str, object]) -> bytes:
    return f"event: {event}\ndata: {encode_data(data)}\n\n".encode()


def ready_frame(seq: int) -> bytes:
    return f"retry: {RETRY_MS}\n".encode() + frame("ready", {"schema_version": SCHEMA_VERSION, "seq": seq})


def message_frame(message: Message) -> bytes:
    if isinstance(message, Changes):
        return frame("changes", changes_payload(message.seq, message.events))
    return frame("resync", {"schema_version": SCHEMA_VERSION, "seq": message.seq, "reason": message.reason})


async def event_stream(
    hub: Hub,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    ping_interval: float | None = None,
) -> AsyncIterator[bytes]:
    interval = PING_INTERVAL if ping_interval is None else ping_interval
    sub = hub.subscribe()
    try:
        yield ready_frame(hub.seq)
        while True:
            try:
                message = await asyncio.wait_for(sub.get(), timeout=interval)
            except TimeoutError:
                if await is_disconnected():
                    return
                yield PING
                continue
            if message is None:
                return
            yield message_frame(message)
    finally:
        hub.unsubscribe(sub)


__all__ = [
    "HEADERS",
    "MEDIA_TYPE",
    "PING",
    "PING_INTERVAL",
    "RETRY_MS",
    "encode_data",
    "event_stream",
    "frame",
    "message_frame",
    "ready_frame",
]
