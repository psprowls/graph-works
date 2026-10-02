"""`/v1/query/brief`: the retrieval brief, page pinning, and the lexical fallback (D-001, D-002)."""

from __future__ import annotations

import pytest
from conftest import cli_json
from graph_works_core.workspace.layout import WorkspaceLayout
from models_io import ProviderNotInstalled
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def no_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credentials on this machine: the embedder never resolves, and nothing reaches Bedrock."""

    def raising_models_io_error() -> object:
        raise ProviderNotInstalled("bedrock")

    monkeypatch.setattr("graph_works_core.query.commands.default_embedder", raising_models_io_error)


@pytest.fixture
def linked(workspace: WorkspaceLayout) -> WorkspaceLayout:
    concepts = workspace.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    pages = {
        "auth": "Token exchange and refresh rotation.",
        "storage": "Blob storage, token buckets, retention.",
        "session": "Cookie lifetimes; see [Auth](/concepts/auth.md).",
    }
    for name, body in pages.items():
        (concepts / f"{name}.md").write_text(
            f"---\ntype: Concept\ntitle: {name.title()}\n---\n\n{body}\n", encoding="utf-8", newline="\n"
        )
    return workspace


def test_brief_is_lexical_and_matches_cli(client: TestClient, linked: WorkspaceLayout) -> None:
    response = client.get("/v1/query/brief?q=token&page=concepts/session")

    assert response.status_code == 200
    body = response.json()
    assert body == cli_json(
        linked, "query", "--query", "token", "--page", "concepts/session", "--backend", "claude_code"
    )
    assert body["retrieval"] == "lexical"
    assert body["page"] == "concepts/session" and body["refusal"] is None
    assert [page["path"] for page in body["top_pages"]] == ["concepts/session", "concepts/auth", "concepts/storage"]
    assert not (linked.cache_dir / "search" / "search.db").exists()


def test_embedding_failure_at_call_time_is_a_lexical_brief_not_a_500(
    client: TestClient, linked: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review Focus #1: the embedder builds, but its credentials are missing when it is called."""

    class NoCredentials:
        model_id = "stub-embed"

        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("no credentials")

    monkeypatch.setattr("graph_works_serve.routes.brief_embedder", NoCredentials)

    response = client.get("/v1/query/brief?q=token")

    assert response.status_code == 200
    body = response.json()
    assert body["retrieval"] == "lexical"
    assert body["warnings"] == ["embedding unavailable: RuntimeError: no credentials"]
    assert body["top_pages"]


def test_unknown_page_is_404_with_payload(client: TestClient, linked: WorkspaceLayout) -> None:
    response = client.get("/v1/query/brief?q=token&page=concepts/nope")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"] == {
        "query": "token",
        "top_pages": [],
        "retrieval": "lexical",
        "page": "concepts/nope",
        "refusal": "unknown-page",
        "warnings": [],
    }


def test_limit_above_ten_is_400(client: TestClient, linked: WorkspaceLayout) -> None:
    response = client.get("/v1/query/brief?q=token&limit=11")

    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"


def test_limit_below_three_is_400(client: TestClient, linked: WorkspaceLayout) -> None:
    assert client.get("/v1/query/brief?q=token&limit=2").status_code == 400


def test_missing_question_is_400(client: TestClient, linked: WorkspaceLayout) -> None:
    assert client.get("/v1/query/brief").status_code == 400


def test_a_concept_free_bundle_is_422_refused_not_a_workspace_error(client: TestClient) -> None:
    """QueryError subclasses WorkspaceError; the route must report it as the query refusal it is."""
    response = client.get("/v1/query/brief?q=token")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["reason"] == "refused"
    assert "no concepts to search" in error["message"]
