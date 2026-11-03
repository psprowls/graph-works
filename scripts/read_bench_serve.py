"""Sidecar probes for the read benchmark: six gw-serve GET routes, timed in process.

Each read sends one GET as an ASGI request to an in-process `gw_serve.app.build_app(...)` app, so
the guard, routing, handler and JSON encode/decode are timed and the socket and uvicorn are not.
No lifespan event is sent, so no watcher starts and the read generation stays fixed: warm rows
measure the sidecar's reused snapshot. Gaps in a before revision (no gw-serve, a missing route)
become explicit `LookupError` rows.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
from typing import TYPE_CHECKING
from urllib.parse import urlencode

try:
    from gw_serve.app import build_app
    from gw_serve.context import ServeContext
except ImportError:  # A checkout without gw-serve, or the old `--package gw-wiki` environment.
    build_app = None
    ServeContext = None

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bench_reads import Target

SIDECAR_UNAVAILABLE = "sidecar unavailable in this environment"
ROUTE_UNAVAILABLE = "sidecar route unavailable in this revision"
#: Nothing listens on the port; it only satisfies the guard's Host check.
BENCH_PORT = 8765
BENCH_TOKEN = "bench"


class SidecarError(RuntimeError):
    """The sidecar refused the read (a JSON error envelope), so there is no timing to report."""


_apps: dict[str, object] = {}
_runner: asyncio.Runner | None = None


def _run(coro: object) -> object:
    global _runner
    if _runner is None:
        _runner = asyncio.Runner()
        atexit.register(_runner.close)
    return _runner.run(coro)  # type: ignore[arg-type]


def _sidecar(target: Target) -> object:
    root = target.layout.root
    key = str(root)
    if key not in _apps:
        context = ServeContext(  # type: ignore[misc]
            root=root, cwd=root, port=BENCH_PORT, pid=os.getpid(), gw_version="bench"
        )
        _apps[key] = build_app(context, token=BENCH_TOKEN)  # type: ignore[misc]
    return _apps[key]


async def _request(app: object, path: str, params: Mapping[str, str] | None) -> tuple[int, bytes]:
    query = urlencode(params or {}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": [
            (b"host", f"127.0.0.1:{BENCH_PORT}".encode()),
            (b"authorization", f"Bearer {BENCH_TOKEN}".encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", BENCH_PORT),
    }
    status = 0
    chunks: list[bytes] = []
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])  # type: ignore[call-overload]
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))  # type: ignore[call-overload]

    await app(scope, receive, send)  # type: ignore[operator]
    return status, b"".join(chunks)


def get(target: Target, path: str, params: Mapping[str, str] | None = None) -> object:
    """One timed GET; the decoded JSON body of a 200."""
    if build_app is None:
        raise LookupError(SIDECAR_UNAVAILABLE)
    status, body = _run(_request(_sidecar(target), path, params))  # type: ignore[misc]
    text = body.decode("utf-8", "replace")
    if status == 200:
        return json.loads(text)
    if status == 404:
        try:
            json.loads(text)
        except ValueError:
            raise LookupError(f"{ROUTE_UNAVAILABLE}: {path}") from None
    raise SidecarError(f"GET {path} -> {status}: {text[:200]}")


def _page(target: Target) -> str:
    if target.sample_page is None:
        raise LookupError("no sample page in this target")
    return target.sample_page


SERVE_READS: dict[str, Callable[[Target], object]] = {
    "serve.work.status": lambda t: get(t, "/v1/work/status"),
    "serve.work.list": lambda t: get(t, "/v1/work/list"),
    "serve.work.item": lambda t: get(t, "/v1/work/item", {"path": t.sample_item}),
    "serve.work.queue": lambda t: get(t, "/v1/work/queue"),
    "serve.wiki.page": lambda t: get(t, "/v1/wiki/page", {"id": _page(t)}),
    "serve.wiki.tree": lambda t: get(t, "/v1/wiki/tree"),
}
