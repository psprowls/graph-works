"""gw-orca read routes: wiki lint and later reads."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import write_item
from graph_works_core.code_read import run_code_graph_neighborhood, run_code_graph_search, run_code_graph_tree
from graph_works_core.lint_drift.lint import run_mechanical
from graph_works_core.proposals import run_proposal_checks, run_proposal_preview
from graph_works_core.work.affecting import run_work_affecting
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_serve import mutations
from graph_works_wire import code as wire_code
from graph_works_wire import wiki as wire_wiki
from graph_works_wire import work as wire_work
from mutation_helpers import write_proposal
from starlette.testclient import TestClient


def test_lint_is_mechanical_and_matches_core(
    client: TestClient, workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mutations, "now", lambda: datetime(2026, 9, 18, tzinfo=UTC))
    body = client.get("/v1/wiki/lint").json()
    expected = wire_wiki.wiki_lint_payload(
        run_mechanical(
            workspace, load_workspace_config(workspace), today=date(2026, 9, 18), repo_roots=resolve_repos(workspace)
        )
    )
    assert body == expected
    assert body["semantic"] is None and set(body["counts"]) == {"errors", "warnings", "by_code"}


@pytest.fixture
def code_graph(workspace: WorkspaceLayout, tmp_path: Path) -> None:
    head = workspace.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    workspace.manifest_path.write_text(
        f'{head}repositories:\n  "demo":\n    path: "{(tmp_path / "demo").as_posix()}"\n',
        encoding="utf-8",
        newline="\n",
    )
    for page_id, type_name, title in (
        ("code-graph/demo", "Repository", "demo"),
        ("code-graph/demo/entities/packages/core", "Package", "core"),
        ("code-graph/demo/file-system/src/app.py", "File", "app.py"),
    ):
        path = workspace.bundle_dir / f"{page_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: {type_name}\ntitle: {title}\n---\n\nx\n", encoding="utf-8", newline="\n")
    (workspace.bundle_dir / "code-graph/demo/file-system/src/index.md").write_text(
        "---\ntitle: src\n---\n\n# src\n", encoding="utf-8", newline="\n"
    )


@pytest.mark.usefixtures("code_graph")
def test_code_graph_tree_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    response = client.get("/v1/code-graph/tree?repo=demo")
    assert response.status_code == 200
    assert response.json() == wire_code.code_graph_tree_payload(run_code_graph_tree(workspace, "demo"))
    assert any(node["is_index"] for node in response.json()["nodes"])


def test_code_graph_tree_unknown_repository_is_404_with_payload(client: TestClient) -> None:
    response = client.get("/v1/code-graph/tree?repo=nope")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"] == {"repo": "nope", "nodes": [], "refusal": "unknown-repository"}


@pytest.mark.usefixtures("code_graph")
def test_code_graph_search_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    response = client.get("/v1/code-graph/search?q=CORE&repo=demo&limit=5")
    assert response.status_code == 200
    assert response.json() == wire_code.code_graph_search_payload(
        run_code_graph_search(workspace, "CORE", repo="demo", limit=5)
    )
    assert [hit["title"] for hit in response.json()["hits"]] == ["core"]


def test_code_graph_search_unknown_repository_is_404_with_payload(client: TestClient) -> None:
    response = client.get("/v1/code-graph/search?q=x&repo=nope")
    assert response.status_code == 404
    assert response.json()["error"]["payload"]["refusal"] == "unknown-repository"


def test_code_graph_search_requires_q(client: TestClient) -> None:
    assert client.get("/v1/code-graph/search").status_code == 400


@pytest.fixture
def resource_pages(workspace: WorkspaceLayout) -> None:
    for page_id, resource in (
        ("code-graph/demo/entities/packages/core", "pkg:acme/demo/core"),
        ("code-graph/demo/bare", None),
    ):
        path = workspace.bundle_dir / f"{page_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        extra = f"resource: {resource}\n" if resource else ""
        path.write_text(f"---\ntype: Package\ntitle: x\n{extra}---\n\nx\n", encoding="utf-8", newline="\n")


@pytest.mark.usefixtures("resource_pages")
def test_code_graph_neighborhood_without_a_graph_is_422_with_payload(
    client: TestClient, workspace: WorkspaceLayout
) -> None:
    page = "code-graph/demo/entities/packages/core"
    response = client.get(f"/v1/code-graph/neighborhood?id={page}")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["reason"] == "refused"
    assert error["payload"] == wire_code.code_graph_neighborhood_payload(
        run_code_graph_neighborhood(workspace, page, 1)
    )
    assert error["payload"]["refusal"] == "no-graph"


@pytest.mark.usefixtures("resource_pages")
def test_code_graph_neighborhood_page_without_resource_is_422(client: TestClient) -> None:
    response = client.get("/v1/code-graph/neighborhood?id=code-graph/demo/bare")
    assert response.status_code == 422
    assert response.json()["error"]["payload"]["refusal"] == "no-resource"


def test_code_graph_neighborhood_unknown_page_is_404_with_payload(client: TestClient) -> None:
    response = client.get("/v1/code-graph/neighborhood?id=nope/x")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"]["refusal"] == "unknown-page" and error["payload"]["nodes"] == []


@pytest.mark.parametrize("depth", ["4", "0"])
def test_code_graph_neighborhood_depth_out_of_range_is_400(client: TestClient, depth: str) -> None:
    assert client.get(f"/v1/code-graph/neighborhood?id=nope/x&depth={depth}").status_code == 400


@pytest.fixture
def two_repo_items(workspace: WorkspaceLayout, tmp_path: Path) -> None:
    head = workspace.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    workspace.manifest_path.write_text(
        f'{head}repositories:\n  "code":\n    path: "{(tmp_path / "code").as_posix()}"\n'
        f'  "ui":\n    path: "{(tmp_path / "ui").as_posix()}"\n',
        encoding="utf-8",
        newline="\n",
    )
    write_item(workspace, "in-code", extra_fm="repo: code\n")
    write_item(workspace, "in-ui", extra_fm="repo: ui\n")
    write_item(workspace, "no-repo")


@pytest.mark.usefixtures("two_repo_items")
def test_work_affecting_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    response = client.get("/v1/work/affecting?repo=code&path=packages/a/src")
    assert response.status_code == 200
    assert response.json() == wire_work.work_affecting_payload(run_work_affecting(workspace, "code", "packages/a/src"))
    assert [item["path"] for item in response.json()["items"]] == ["work/in-code"]
    assert response.json()["items"][0]["affects"] == ["packages/a"]


def test_work_affecting_unknown_repository_is_404_with_payload(client: TestClient) -> None:
    response = client.get("/v1/work/affecting?repo=nope&path=x")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"] == {"repo": "nope", "path": "x", "items": [], "refusal": "unknown-repository"}


def test_work_affecting_requires_both_params(client: TestClient) -> None:
    assert client.get("/v1/work/affecting?repo=code").status_code == 400
    assert client.get("/v1/work/affecting?path=x").status_code == 400


def test_proposal_checks_is_the_projection(
    client: TestClient, workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mutations, "now", lambda: datetime(2026, 9, 18, tzinfo=UTC))
    target = "docs/explanations/typed.md"
    write_proposal(workspace, "typed", target=target)
    response = client.get(f"/v1/wiki/proposal/checks?target={target}")
    assert response.status_code == 200
    assert response.json() == wire_wiki.proposal_checks_payload(
        run_proposal_checks(workspace, target, today=date(2026, 9, 18))
    )
    assert [check["id"] for check in response.json()["checks"]] == ["schema", "citations", "code-drift", "related-adrs"]


def test_proposal_checks_without_a_proposal_is_404_with_payload(client: TestClient) -> None:
    response = client.get("/v1/wiki/proposal/checks?target=docs/nope.md")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"] == {"target": "docs/nope.md", "proposal": None, "checks": [], "refusal": "no-proposal"}


def test_proposal_checks_requires_target(client: TestClient) -> None:
    assert client.get("/v1/wiki/proposal/checks").status_code == 400


def test_proposal_preview_is_the_projection(
    client: TestClient, workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mutations, "now", lambda: datetime(2026, 9, 18, tzinfo=UTC))
    target = "docs/explanations/typed.md"
    write_proposal(workspace, "typed", target=target)
    response = client.get(f"/v1/wiki/proposal/preview?target={target}")
    assert response.status_code == 200
    assert response.json() == wire_wiki.proposal_preview_payload(
        run_proposal_preview(workspace, target, today=date(2026, 9, 18))
    )
    assert response.json()["mode"] == "create"


def test_proposal_preview_without_a_proposal_is_404_with_payload(client: TestClient) -> None:
    response = client.get("/v1/wiki/proposal/preview?target=docs/nope.md")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"]["refusal"] == "no-proposal" and error["payload"]["proposal"] is None


def test_proposal_preview_requires_target(client: TestClient) -> None:
    assert client.get("/v1/wiki/proposal/preview").status_code == 400
