"""`--allow-origin`: Host first, then token-free preflight, then the token; ACAO on every allowed-origin response."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from conftest import PORT, TOKEN
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.app import build_app
from graph_works_serve.context import ServeContext
from starlette.testclient import TestClient
from test_events_route import scripted

ORIGIN = "http://127.0.0.1:4780"
OTHER = "http://evil.example"
PREFLIGHT = {
    "Origin": ORIGIN,
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "authorization",
}


@pytest.fixture
def cors(context: ServeContext) -> Iterator[TestClient]:
    app = build_app(context, token=TOKEN, allow_origins=frozenset({ORIGIN, "http://localhost:5173"}))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        yield client


def _access_control(response: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k.lower().startswith("access-control-")}


def test_an_allowed_preflight_is_204_without_a_token(cors: TestClient) -> None:
    response = cors.options("/v1/work/status", headers=PREFLIGHT)

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-methods"] == "GET, POST"
    assert response.headers["access-control-allow-headers"] == "Authorization, Content-Type"
    assert response.headers["access-control-max-age"] == "600"
    assert response.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in response.headers


def test_a_disallowed_preflight_is_403_and_never_echoes_the_origin(cors: TestClient) -> None:
    response = cors.options("/v1/work/status", headers={**PREFLIGHT, "Origin": OTHER})

    assert response.status_code == 403
    assert response.json()["error"]["reason"] == "refused"
    assert "origin not allowed" in response.json()["error"]["message"]
    assert OTHER not in response.text
    assert _access_control(response) == {}


def test_a_foreign_host_is_refused_before_the_preflight_path(cors: TestClient) -> None:
    response = cors.options("/v1/work/status", headers={**PREFLIGHT, "Host": f"evil.example:{PORT}"})

    assert response.status_code == 403
    assert "Host header" in response.json()["error"]["message"]
    assert _access_control(response) == {}


def test_an_allowed_origin_gets_acao_on_success_and_on_refusal(cors: TestClient) -> None:
    ok = cors.get("/v1/health", headers={"Origin": ORIGIN, "Authorization": f"Bearer {TOKEN}"})
    refused = cors.get("/v1/health", headers={"Origin": ORIGIN})

    assert ok.status_code == 200 and refused.status_code == 401
    for response in (ok, refused):
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["vary"] == "Origin"


def test_a_disallowed_origin_gets_no_acao(cors: TestClient) -> None:
    response = cors.get("/v1/health", headers={"Origin": OTHER, "Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert _access_control(response) == {}


def test_the_event_stream_carries_acao(workspace: WorkspaceLayout) -> None:
    app = build_app(
        ServeContext(workspace.root, workspace.root, PORT, 4242, "test"),
        token=TOKEN,
        change_source=scripted(),
        allow_origins=frozenset({ORIGIN}),
    )
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as client:
        response = client.get(f"/v1/events?token={TOKEN}", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_without_the_flag_no_response_has_any_cors_header(client: TestClient) -> None:
    preflight = client.options("/v1/work/status", headers=PREFLIGHT)
    get = client.get("/v1/health", headers={"Origin": ORIGIN})

    assert _access_control(preflight) == {} and _access_control(get) == {}
    assert "vary" not in get.headers
    assert preflight.status_code == 405
