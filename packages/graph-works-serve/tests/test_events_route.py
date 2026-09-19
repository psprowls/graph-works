"""`GET /v1/events` over Starlette's TestClient with a scripted change source."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from conftest import TOKEN  # S6
from graph_works_core.events import Change, ChangeEvent, EventKind
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import sse
from graph_works_serve.app import build_app
from graph_works_serve.context import ServeContext
from graph_works_serve.hub import Changes, Hub, Resync
from starlette.testclient import TestClient

E = ChangeEvent(kind=EventKind.WORK_ITEM, path="work/a", member="work/a.md", change=Change.MODIFIED)
HOST = {"Host": "127.0.0.1:8123"}  # the port build_app is given below


async def _until_subscribed(hub: Hub, count: int = 1) -> None:
    while hub.subscriber_count < count:
        await asyncio.sleep(0.005)


def scripted(
    *steps: Callable[[Hub], None], close: bool = True
) -> Callable[[Path, Hub, asyncio.Event], Awaitable[None]]:
    async def source(root: Path, hub: Hub, stop: asyncio.Event) -> None:
        await _until_subscribed(hub)
        for step in steps:
            step(hub)
        if close:
            hub.close()
        await stop.wait()

    return source


def _client(workspace: WorkspaceLayout, source: Callable[[Path, Hub, asyncio.Event], Awaitable[None]]) -> TestClient:
    return TestClient(
        build_app(ServeContext(workspace.root, workspace.root, 8123, 4242, "test"), token=TOKEN, change_source=source)
    )


def test_stream_sends_ready_then_changes_then_resync(workspace: WorkspaceLayout) -> None:
    source = scripted(lambda hub: hub.publish([E]), lambda hub: hub.resync("rewatch"))
    with _client(workspace, source) as client:
        response = client.get(f"/v1/events?token={TOKEN}", headers=HOST)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.content == (
        sse.ready_frame(0) + sse.message_frame(Changes(1, (E,))) + sse.message_frame(Resync(2, "rewatch"))
    )


def test_bearer_header_also_works(workspace: WorkspaceLayout) -> None:
    with _client(workspace, scripted()) as client:
        response = client.get("/v1/events", headers={**HOST, "Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.content == sse.ready_frame(0)


@pytest.mark.parametrize(
    ("url", "headers", "status"),
    [
        ("/v1/events", HOST, 401),
        ("/v1/events?token=wrong", HOST, 401),
        (f"/v1/events?token={TOKEN}", {"Host": "evil.example:8123"}, 403),
    ],
)
def test_guard_refuses_before_the_stream_starts(
    workspace: WorkspaceLayout, url: str, headers: dict[str, str], status: int
) -> None:
    with _client(workspace, scripted(close=False)) as client:
        response = client.get(url, headers=headers)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["reason"] == "refused"
    assert TOKEN not in response.text


def test_last_event_id_is_ignored(workspace: WorkspaceLayout) -> None:
    with _client(workspace, scripted()) as client:
        response = client.get(f"/v1/events?token={TOKEN}", headers={**HOST, "Last-Event-ID": "12"})
    assert response.content == sse.ready_frame(0)


def test_heartbeat_when_idle(workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sse, "PING_INTERVAL", 0.02)

    async def source(root: Path, hub: Hub, stop: asyncio.Event) -> None:
        await _until_subscribed(hub)
        await asyncio.sleep(0.1)
        hub.close()
        await stop.wait()

    with _client(workspace, source) as client:
        response = client.get(f"/v1/events?token={TOKEN}", headers=HOST)
    assert response.content.startswith(sse.ready_frame(0))
    assert sse.PING in response.content


def test_lifespan_stops_the_source_and_closes_the_hub(workspace: WorkspaceLayout) -> None:
    seen: dict[str, object] = {}

    async def source(root: Path, hub: Hub, stop: asyncio.Event) -> None:
        seen["root"] = root
        await stop.wait()
        seen["stopped"] = True

    app = build_app(ServeContext(workspace.root, workspace.root, 8123, 4242, "test"), token=TOKEN, change_source=source)
    with TestClient(app):
        hub = app.state.hub
        assert isinstance(hub, Hub) and not hub.closed
    assert seen == {"root": workspace.root, "stopped": True}
    assert hub.closed


def test_a_crashing_source_is_logged_not_raised(workspace: WorkspaceLayout, capsys: pytest.CaptureFixture[str]) -> None:
    async def source(root: Path, hub: Hub, stop: asyncio.Event) -> None:
        raise RuntimeError("boom")

    with TestClient(
        build_app(ServeContext(workspace.root, workspace.root, 8123, 4242, "test"), token=TOKEN, change_source=source)
    ):
        pass
    assert "boom" in capsys.readouterr().err


def test_a_source_that_ignores_stop_is_cancelled(workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    from graph_works_serve import app as app_module

    monkeypatch.setattr(app_module, "SHUTDOWN_TIMEOUT", 0.05)
    cancelled: list[bool] = []

    async def source(root: Path, hub: Hub, stop: asyncio.Event) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with TestClient(
        build_app(ServeContext(workspace.root, workspace.root, 8123, 4242, "test"), token=TOKEN, change_source=source)
    ):
        pass
    assert cancelled == [True]
