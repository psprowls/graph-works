"""Generic mutation routes over the guarded HTTP boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations, routes
from graph_works_serve.catalog import describe
from graph_works_serve.context import ServeContext
from graph_works_serve.mutations import BeforeApply, MutationSpec, Params
from graph_works_serve.params import Param
from mutation_helpers import TOKEN, make_client, serve_workspace, snapshot
from starlette.testclient import TestClient


def _echo(
    _layout: WorkspaceLayout, params: Params, _as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
) -> object:
    if not dry_run and before_apply is not None:
        before_apply({"name": params["name"], "dry_run": True})
    return {"name": params["name"], "dry_run": dry_run}


def _project(result: object, _params: Params, _dry_run: bool) -> dict[str, object]:
    assert isinstance(result, Mapping)
    return dict(result)


ECHO = MutationSpec(
    route="/v1/test/echo",
    command="test echo",
    summary="Echo",
    params=(Param("name", "str", required=True, location="body"),),
    run=_echo,
    project=_project,
    refused=lambda _plan, _params: None,
)


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutations, "now", lambda: datetime(2026, 9, 18, 14, 3, 7, tzinfo=UTC))


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    return make_client(serve_workspace(tmp_path), routes=(*routes.ROUTES, *routes.mutation_routes(ECHO)))


def test_post_plan_then_apply_over_http(client: tuple[TestClient, dict[str, str]]) -> None:
    http, headers = client
    planned = http.post("/v1/test/echo/plan", json={"name": "n"}, headers=headers)
    assert planned.status_code == 200
    body = planned.json()
    assert body["plan"] == {"name": "n", "dry_run": True}
    applied = http.post(
        "/v1/test/echo/apply", json={"name": "n", "as_of": body["as_of"], "digest": body["digest"]}, headers=headers
    )
    assert applied.status_code == 200
    assert applied.json()["result"] == {"name": "n", "dry_run": False}
    assert applied.headers["content-type"] == "application/json"
    assert http.get("/v1/health", headers=headers).status_code == 200


@pytest.mark.parametrize("suffix", ["plan", "apply"])
@pytest.mark.parametrize("content_type", [None, "text/plain"])
def test_non_json_content_type_is_415(
    client: tuple[TestClient, dict[str, str]], suffix: str, content_type: str | None
) -> None:
    http, headers = client
    if content_type is not None:
        headers = {**headers, "Content-Type": content_type}
    response = http.post(f"/v1/test/echo/{suffix}", content=b"name=n", headers=headers)
    assert response.status_code == 415
    assert response.json()["error"]["reason"] == "usage"


def test_a_stale_apply_is_a_409_json_response(client: tuple[TestClient, dict[str, str]]) -> None:
    http, headers = client
    response = http.post(
        "/v1/test/echo/apply",
        json={"name": "n", "as_of": "2026-09-18T14:03:07Z", "digest": "sha256:00"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["payload"]["digest"].startswith("sha256:")


@pytest.mark.parametrize("suffix", ["plan", "apply"])
def test_get_on_a_mutation_route_is_405(client: tuple[TestClient, dict[str, str]], suffix: str) -> None:
    http, headers = client
    assert http.get(f"/v1/test/echo/{suffix}", headers=headers).status_code == 405


@pytest.mark.parametrize("suffix", ["plan", "apply"])
def test_mutations_require_the_bearer_header(client: tuple[TestClient, dict[str, str]], suffix: str) -> None:
    http, _headers = client
    assert http.post(f"/v1/test/echo/{suffix}", json={"name": "n"}).status_code == 401
    assert http.post(f"/v1/test/echo/{suffix}?token={TOKEN}", json={"name": "n"}).status_code == 401


@pytest.mark.parametrize("suffix", ["plan", "apply"])
def test_malformed_json_is_a_usage_envelope(client: tuple[TestClient, dict[str, str]], suffix: str) -> None:
    http, headers = client
    response = http.post(
        f"/v1/test/echo/{suffix}", content=b"{", headers={**headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"


def test_pair_preserves_body_metadata_and_catalog() -> None:
    plan, apply = routes.mutation_routes(ECHO)
    assert (plan.method, plan.path, plan.params) == ("POST", "/v1/test/echo/plan", ECHO.params)
    assert (apply.method, apply.path, apply.params) == (
        "POST",
        "/v1/test/echo/apply",
        (*ECHO.params, *mutations.APPLY_FIELDS),
    )
    assert plan.response == apply.response == "json"
    catalog = describe((plan, apply), "test")
    assert len(catalog["routes"]) == 2
    assert all(param.location == "body" for param in apply.params)


@pytest.mark.parametrize("suffix", ["plan", "apply"])
def test_layout_and_core_run_off_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    layout = serve_workspace(tmp_path)
    resolved = ServeContext.layout
    calls: list[str] = []

    def resolve(context: ServeContext) -> WorkspaceLayout:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        calls.append("layout")
        return resolved(context)

    def run(
        layout: WorkspaceLayout, params: Params, as_of: datetime, dry_run: bool, before_apply: BeforeApply | None = None
    ) -> object:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        calls.append("plan" if dry_run else "apply")
        return _echo(layout, params, as_of, dry_run, before_apply)

    monkeypatch.setattr(ServeContext, "layout", resolve)
    http, headers = make_client(layout, routes=routes.mutation_routes(replace(ECHO, run=run)))
    before = snapshot(layout)
    planned = http.post("/v1/test/echo/plan", json={"name": "n"}, headers=headers).json()
    assert snapshot(layout) == before
    calls.clear()
    response = http.post(
        f"/v1/test/echo/{suffix}",
        json={"name": "n", **({"as_of": planned["as_of"], "digest": planned["digest"]} if suffix == "apply" else {})},
        headers=headers,
    )
    assert response.status_code == 200
    assert calls == (["layout", "plan", "apply"] if suffix == "apply" else ["layout", "plan"])


@pytest.mark.parametrize("suffix", ["plan", "apply"])
def test_layout_is_resolved_again_and_invalid_workspace_uses_existing_envelope(tmp_path: Path, suffix: str) -> None:
    layout = serve_workspace(tmp_path)
    http, headers = make_client(layout, routes=routes.mutation_routes(ECHO))
    planned = http.post("/v1/test/echo/plan", json={"name": "n"}, headers=headers).json()
    layout.manifest_path.unlink()
    response = http.post(
        f"/v1/test/echo/{suffix}",
        json={"name": "n", **({"as_of": planned["as_of"], "digest": planned["digest"]} if suffix == "apply" else {})},
        headers=headers,
    )
    assert response.status_code == 500
    assert response.json()["error"]["reason"] == "workspace"
    assert response.json()["error"]["exit_code"] == 4


MUTATION_PATHS = {
    "/v1/work/advance/plan",
    "/v1/work/advance/apply",
    "/v1/work/archive/plan",
    "/v1/work/archive/apply",
    "/v1/wiki/proposal/decide/plan",
    "/v1/wiki/proposal/decide/apply",
}


def test_all_six_mutation_routes_are_in_the_table() -> None:
    posts = {route.path for route in routes.ROUTES if route.method == "POST"}
    assert posts >= MUTATION_PATHS


@pytest.mark.parametrize("path", sorted(MUTATION_PATHS))
def test_every_mutation_route_refuses_a_query_token(tmp_path: Path, path: str) -> None:
    http, _headers = make_client(serve_workspace(tmp_path))
    assert http.post(f"{path}?token={TOKEN}", json={}).status_code == 401


@pytest.mark.parametrize("path", sorted(MUTATION_PATHS))
def test_every_mutation_route_requires_json(tmp_path: Path, path: str) -> None:
    http, headers = make_client(serve_workspace(tmp_path))
    response = http.post(path, content=b"{}", headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 415


def test_an_incomplete_apply_over_http_is_500(tmp_path: Path) -> None:
    """Exercise the HTTP error mapping with a portable faulty apply projection."""
    from graph_works_serve import mutation_specs
    from mutation_helpers import write_item

    real = mutation_specs.ADVANCE.project

    def faulty(result: object, params: Params, dry_run: bool) -> dict[str, object]:
        payload = real(result, params, dry_run)
        if not dry_run:
            payload = {**payload, "applied": True, "rolled_back": True, "failures": ["injected"]}
        return payload

    spec = replace(mutation_specs.ADVANCE, route="/v1/test/faulty", project=faulty)
    layout = serve_workspace(tmp_path)
    write_item(layout, "work/feature-scratch", work_status="open", phase="design")
    http, headers = make_client(layout, routes=(*routes.ROUTES, *routes.mutation_routes(spec)))
    planned = http.post("/v1/test/faulty/plan", json={"path": "work/feature-scratch"}, headers=headers)
    assert planned.status_code == 200
    body = planned.json()
    response = http.post(
        "/v1/test/faulty/apply",
        json={"path": "work/feature-scratch", "as_of": body["as_of"], "digest": body["digest"]},
        headers=headers,
    )
    assert response.status_code == 500
    assert response.json()["error"]["reason"] == "incomplete-apply"
    assert response.json()["error"]["payload"]["failures"] == ["injected"]
