"""Token and ``Host`` checks on every request, as pure ASGI middleware.

``Host`` is the DNS-rebinding defence and is checked first. The token may
arrive as ``Authorization: Bearer`` or ``?token=`` because EventSource cannot
set headers. Refusals name the failed check but never echo credentials.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any
from urllib.parse import parse_qs

from graph_works_serve.context import Reply
from graph_works_serve.errors import refusal

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_COMMAND = "guard"
_QUERY_TOKEN_ROUTE = ("GET", "/v1/events")


def _presented(headers: Mapping[str, str], query_string: bytes, *, method: str, path: str) -> str | None:
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    if (method, path) != _QUERY_TOKEN_ROUTE:
        return None
    values = parse_qs(query_string.decode("latin-1")).get("token")
    return values[0] if values else None


def check(
    headers: Mapping[str, str],
    query_string: bytes,
    *,
    token: str,
    port: int,
    method: str = "",
    path: str = "",
) -> Reply | None:
    """Return this request's refusal, or ``None`` when it may proceed."""
    if headers.get("host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return refusal(_COMMAND, "refused", "Host header is not this server's loopback address", status=403)
    presented = _presented(headers, query_string, method=method, path=path)
    if presented is None:
        return refusal(_COMMAND, "refused", "missing or invalid token", status=401)
    try:
        matches = hmac.compare_digest(presented.encode("ascii"), token.encode("ascii"))
    except UnicodeEncodeError:
        matches = False
    if not matches:
        return refusal(_COMMAND, "refused", "missing or invalid token", status=401)
    return None


class Guard:
    """Refuse HTTP requests that fail ``check``; pass all other scopes through."""

    def __init__(self, app: ASGIApp, *, token: str, port: int) -> None:
        self.app = app
        self.token = token
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        reply = check(
            headers,
            scope.get("query_string", b""),
            token=self.token,
            port=self.port,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )
        if reply is None:
            await self.app(scope, receive, send)
            return
        body = json.dumps(reply.body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": reply.status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
