"""Display reads are served from the ReadState snapshot memo (AGENTS.md, "memo convention")."""

from __future__ import annotations

import pytest
from conftest import PORT, TOKEN, write_item
from graph_works_core.read_session import ReadSession, SnapshotSession, materialize
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.app import build_app
from graph_works_serve.context import ServeContext
from graph_works_serve.readstate import ReadState
from starlette.testclient import TestClient

MEMO_ROUTES = (
    "/v1/work/status",
    "/v1/work/list",
    "/v1/work/item?path=work/a",
    "/v1/work/queue",
    "/v1/work/decisions/open",
    "/v1/wiki/page?id=concepts/a",
    "/v1/wiki/tree",
    "/v1/wiki/citations?id=concepts/a",
)


@pytest.fixture
def builds(context: ServeContext, workspace: WorkspaceLayout) -> list[int]:
    write_item(workspace, "a")
    concepts = workspace.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    page = "---\ntype: Concept\ntitle: A\n---\n\nSee `a.py:1`.\n"
    (concepts / "a.md").write_text(page, encoding="utf-8", newline="\n")
    counted: list[int] = []

    def counting(session: ReadSession) -> SnapshotSession:
        counted.append(1)
        return materialize(session)

    object.__setattr__(context, "read_state", ReadState(materializer=counting))
    return counted


def _client(context: ServeContext) -> TestClient:
    # No lifespan: no watcher bumps the generation behind the test's back.
    return TestClient(
        build_app(context, token=TOKEN),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


@pytest.mark.parametrize("url", MEMO_ROUTES)
def test_display_read_builds_the_memo_once_per_generation(context: ServeContext, builds: list[int], url: str) -> None:
    client = _client(context)
    first, second = client.get(url), client.get(url)
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    assert len(builds) == 1
    assert first.headers["X-GW-Generation"] == second.headers["X-GW-Generation"] == "1"


@pytest.mark.parametrize("url", MEMO_ROUTES)
def test_display_read_rebuilds_after_a_generation_bump(context: ServeContext, builds: list[int], url: str) -> None:
    client = _client(context)
    client.get(url)
    context.read_state.advance("resync")
    after = client.get(url)
    assert len(builds) == 2
    assert after.headers["X-GW-Generation"] == "2"
