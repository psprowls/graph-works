"""Build the guarded Starlette app from the declarative route table."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from graph_works_serve import sse
from graph_works_serve.context import ServeContext
from graph_works_serve.guard import Guard
from graph_works_serve.hub import ChangeSource, Hub
from graph_works_serve.routes import ROUTES, RouteSpec, handle, handle_mutation
from graph_works_serve.watch import watch_workspace

SHUTDOWN_TIMEOUT = 5.0


def _lifespan(root: Path, source: ChangeSource) -> Callable[[Starlette], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        hub = Hub()
        stop = asyncio.Event()
        app.state.hub = hub
        task = asyncio.ensure_future(source(root, hub, stop))
        try:
            yield
        finally:
            stop.set()
            try:
                await asyncio.wait_for(task, SHUTDOWN_TIMEOUT)
            except TimeoutError:
                pass  # wait_for cancelled the task
            except Exception as exc:
                print(f"gw-serve: change source stopped: {exc}", file=sys.stderr)
            hub.close()

    return lifespan


async def _events_endpoint(request: Request) -> StreamingResponse:
    return StreamingResponse(
        sse.event_stream(request.app.state.hub, request.is_disconnected),
        media_type=sse.MEDIA_TYPE,
        headers=sse.HEADERS,
    )


def _endpoint(spec: RouteSpec, context: ServeContext) -> Callable[[Request], Response]:
    """Adapt one framework-independent route handler to a Starlette endpoint."""

    def endpoint(request: Request) -> Response:
        reply = handle(spec, context, request.query_params.multi_items())
        return Response(json.dumps(reply.body), status_code=reply.status, media_type="application/json")

    return endpoint


def _mutation_endpoint(spec: RouteSpec, context: ServeContext) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        body = await request.body()
        reply = await run_in_threadpool(handle_mutation, spec, context, body, request.headers.get("content-type"))
        return Response(json.dumps(reply.body), status_code=reply.status, media_type="application/json")

    return endpoint


def build_app(
    context: ServeContext,
    *,
    token: str,
    routes: Sequence[RouteSpec] = ROUTES,
    change_source: ChangeSource | None = None,
) -> Starlette:
    """Mount *routes* behind the loopback token and Host guard."""
    return Starlette(
        routes=[
            Route(
                spec.path,
                _mutation_endpoint(spec, context)
                if spec.method == "POST"
                else _events_endpoint
                if spec.response == "event-stream"
                else _endpoint(spec, context),
                methods=[spec.method],
            )
            for spec in routes
        ],
        lifespan=_lifespan(context.root, change_source or watch_workspace),
        middleware=[Middleware(Guard, token=token, port=context.port)],
    )
