"""The guarded app builds each route from the declarative table."""

from __future__ import annotations

from conftest import PORT, TOKEN
from graph_works_serve.app import build_app
from graph_works_serve.context import Reply, ServeContext
from graph_works_serve.params import Param
from graph_works_serve.routes import ROUTES, RouteSpec
from starlette.routing import Route
from starlette.testclient import TestClient


def test_health_answers_through_the_guard(client: TestClient, context: ServeContext) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "gw_version": "0.0.0-test",
        "workspace": str(context.root),
        "pid": 4242,
    }
    assert "access-control-allow-origin" not in response.headers


def test_health_is_guarded(context: ServeContext) -> None:
    bare = TestClient(build_app(context, token=TOKEN), base_url=f"http://127.0.0.1:{PORT}")
    assert bare.get("/v1/health").status_code == 401
    assert bare.get(f"/v1/health?token={TOKEN}").status_code == 401
    assert (
        bare.get("/v1/health", headers={"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"}).status_code == 403
    )
    assert (
        bare.get("/v1/health", headers={"Host": f"localhost:{PORT}", "Authorization": f"Bearer {TOKEN}"}).status_code
        == 200
    )


def test_an_unknown_parameter_is_a_usage_400(client: TestClient) -> None:
    response = client.get("/v1/health?bogus=1")
    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"
    assert response.json()["error"]["command"] == "/v1/health"


def test_every_spec_is_mounted_and_every_mount_is_a_spec(context: ServeContext) -> None:
    app = build_app(context, token=TOKEN)
    mounted = {
        (route.path, method)
        for route in app.routes
        if isinstance(route, Route)
        for method in (route.methods or ())
        if method != "HEAD"
    }
    assert mounted == {(spec.path, spec.method) for spec in ROUTES}


def test_a_custom_table_mounts_as_given(context: ServeContext) -> None:
    spec = RouteSpec("GET", "/v1/echo", "echo", (Param("x", "int"),), lambda _ctx, args: Reply(200, dict(args)))
    with TestClient(
        build_app(context, token=TOKEN, routes=(spec,)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        assert client.get("/v1/echo?x=2").json() == {"x": 2}
