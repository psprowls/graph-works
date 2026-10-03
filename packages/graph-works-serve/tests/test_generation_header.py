from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from conftest import PORT, TOKEN
from graph_works_core.read_session import ReadSession, SnapshotSession, materialize
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from graph_works_serve.app import build_app
from graph_works_serve.context import Reply, ServeContext
from graph_works_serve.readstate import ReadState
from graph_works_serve.routes import ROUTES, RouteSpec
from starlette.testclient import TestClient
from test_mutation_section import PAGE, TEXT


def test_every_json_get_and_handler_refusal_carries_generation(client: TestClient, context: ServeContext) -> None:
    assert client.get("/v1/work/status").headers["X-GW-Generation"] == "1"
    refusal = client.get("/v1/work/next")
    assert refusal.status_code == 400 and refusal.headers["X-GW-Generation"] == "1"
    context.read_state.advance("resync")
    assert client.get("/v1/work/status").headers["X-GW-Generation"] == "2"


def memo_spec() -> RouteSpec:
    def read(context: ServeContext, args: Mapping[str, object]) -> Reply:
        with context.read_state.session(context.layout()) as (generation, session):
            row = session.member(f"{PAGE}.md")
            return Reply(
                200,
                {
                    "n": len(session.members()),
                    "sha": row.sha256 if row else None,
                    "updated": row.fm["updated"] if row and row.fm else None,
                },
                generation,
            )

    return RouteSpec("GET", "/v1/test-memo", "Test memo convention", (), read)


def test_memo_route_builds_once_and_reports_snapshot_generation(context: ServeContext) -> None:
    builds = []

    def counting(session: ReadSession) -> SnapshotSession:
        builds.append(1)
        return materialize(session)

    object.__setattr__(context, "read_state", ReadState(materializer=counting))
    # No events route/lifespan watcher: reads and write-through alone provide freshness.
    client = TestClient(
        build_app(context, token=TOKEN, routes=(memo_spec(),)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    first = client.get("/v1/test-memo")
    second = client.get("/v1/test-memo")
    assert first.json() == second.json()
    assert first.headers["X-GW-Generation"] == second.headers["X-GW-Generation"] == "1"
    assert len(builds) == 1


def test_apply_then_immediate_http_memo_get_sees_write(
    context: ServeContext,
    workspace: WorkspaceLayout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mutations, "now", lambda: datetime(2026, 10, 2, 14, 3, 7, tzinfo=UTC))
    page = workspace.bundle_dir / f"{PAGE}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(TEXT, encoding="utf-8", newline="\n")
    client = TestClient(
        build_app(context, token=TOKEN, routes=(*ROUTES, memo_spec())),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    before = client.get("/v1/test-memo")
    params = {"id": PAGE, "heading": "Context", "body": "applied edit"}
    planned = client.post("/v1/wiki/section/plan", json=params)
    assert planned.status_code == 200 and "X-GW-Generation" not in planned.headers
    body = {**params, "as_of": planned.json()["as_of"], "digest": planned.json()["digest"]}
    refused = client.post("/v1/wiki/section/apply", json={**body, "digest": "wrong"})
    assert refused.status_code == 409 and "X-GW-Generation" not in refused.headers
    assert context.read_state.bumps == []
    applied = client.post("/v1/wiki/section/apply", json=body)
    assert applied.status_code == 200, applied.text
    assert int(applied.headers["X-GW-Generation"]) >= 2
    after = client.get("/v1/test-memo")
    assert int(after.headers["X-GW-Generation"]) >= int(applied.headers["X-GW-Generation"])
    assert before.json()["updated"] == "2026-01-01"
    assert after.json()["updated"] == "2026-10-02"
    assert context.read_state.bumps[-1][1] == "write-through"


@pytest.mark.parametrize("memo", [False, True])
def test_header_reports_the_read_generation_when_state_advances_during_handler(
    context: ServeContext,
    memo: bool,
) -> None:
    def read(context: ServeContext, args: Mapping[str, object]) -> Reply:
        if memo:
            with context.read_state.session(context.layout()) as (generation, session):
                count = len(session.members())
                context.read_state.advance("resync")
                return Reply(200, {"n": count}, generation)
        context.read_state.advance("resync")
        return Reply(200, {})

    spec = RouteSpec("GET", "/test", "Test concurrent advance", (), read)
    client = TestClient(
        build_app(context, token=TOKEN, routes=(spec,)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert client.get("/test").headers["X-GW-Generation"] == "1"
    assert context.read_state.generation == 2
