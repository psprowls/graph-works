"""Token and ``Host`` checks on every request, as pure ASGI middleware.

``Host`` is the DNS-rebinding defence and is checked first. The token may
arrive as ``Authorization: Bearer`` anywhere, or as ``?token=`` on
``GET /v1/events`` alone, because EventSource cannot set headers. Refusals
name the failed check but never echo credentials.

With an allow-list of origins, the order per request is: the ``Host`` check,
then a token-free answer to an allowed origin's CORS preflight (a disallowed
origin's preflight is refused), then the token check. Every response to an
allowed origin -- refusals and the event stream included -- carries
``Access-Control-Allow-Origin`` and ``Vary: Origin``. With no allow-list,
none of that runs and no CORS header is ever sent.
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


def check_host(headers: Mapping[str, str], *, port: int) -> Reply | None:
    """Refuse a request whose ``Host`` is not this server's loopback address."""
    if headers.get("host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return refusal(_COMMAND, "refused", "Host header is not this server's loopback address", status=403)
    return None


def check_token(
    headers: Mapping[str, str],
    query_string: bytes,
    *,
    token: str,
    method: str = "",
    path: str = "",
) -> Reply | None:
    """Refuse a request that does not present *token*."""
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
    return check_host(headers, port=port) or check_token(headers, query_string, token=token, method=method, path=path)


_PREFLIGHT_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"access-control-allow-methods", b"GET, POST"),
    (b"access-control-allow-headers", b"Authorization, Content-Type"),
    (b"access-control-max-age", b"600"),
)


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [(b"access-control-allow-origin", origin.encode("latin-1")), (b"vary", b"Origin")]


def _with_cors(send: Send, origin: str) -> Send:
    """Add the allow-origin headers to whatever response starts through *send*."""
    extra = _cors_headers(origin)

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), *extra]}
        await send(message)

    return wrapped


async def _send_reply(send: Send, reply: Reply) -> None:
    body = json.dumps(reply.body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": reply.status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class Guard:
    """Refuse HTTP requests that fail the Host or token check; answer allowed
    CORS preflights; pass all other scopes through."""

    def __init__(self, app: ASGIApp, *, token: str, port: int, allow_origins: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.token = token
        self.port = port
        self.allow_origins = allow_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        refused = check_host(headers, port=self.port)
        if refused is not None:
            await _send_reply(send, refused)
            return
        origin = headers.get("origin")
        allowed = origin if origin is not None and origin in self.allow_origins else None
        preflight = (
            scope.get("method") == "OPTIONS" and "origin" in headers and "access-control-request-method" in headers
        )
        if self.allow_origins and preflight:
            if allowed is None:
                await _send_reply(send, refusal(_COMMAND, "refused", "origin not allowed", status=403))
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [*_cors_headers(allowed), *_PREFLIGHT_HEADERS],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        if allowed is not None:
            send = _with_cors(send, allowed)
        refused = check_token(
            headers,
            scope.get("query_string", b""),
            token=self.token,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )
        if refused is not None:
            await _send_reply(send, refused)
            return
        await self.app(scope, receive, send)
