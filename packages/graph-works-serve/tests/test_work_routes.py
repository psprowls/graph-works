"""Each work route returns its CLI twin's `--json` body; `next` writes nothing."""

from __future__ import annotations

from conftest import cli_json, write_item
from graph_works_core.workspace.layout import WorkspaceLayout
from starlette.testclient import TestClient


def test_status_is_the_cli_twin(client: TestClient, workspace: WorkspaceLayout) -> None:
    write_item(workspace, "feature-a")

    response = client.get("/v1/work/status")

    assert response.status_code == 200
    assert response.json() == cli_json(workspace, "work", "status")


def test_next_is_the_cli_twin_with_null_normalized(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a", phase="plan")

    response = client.get(f"/v1/work/next?path={path}")

    assert response.status_code == 200
    body = response.json()
    assert body["normalized"] is None
    twin = cli_json(workspace, "work", "next", path)
    assert isinstance(twin, dict)
    assert body == {**twin, "normalized": None}


def test_next_writes_nothing_when_a_design_source_needs_repair(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a", phase="design")
    design = workspace.bundle_dir / path / "references" / "01-design.md"
    design.parent.mkdir(parents=True)
    design.write_text("---\ntitle: D\n---\n\n# D\n", encoding="utf-8", newline="\n")
    item = workspace.bundle_dir / f"{path}.md"
    before = item.read_bytes()

    served = client.get(f"/v1/work/next?path={path}").json()

    assert item.read_bytes() == before
    assert served["normalized"] is None
    twin = cli_json(workspace, "work", "next", path)
    assert isinstance(twin, dict)
    assert served["action"] == twin["action"]
    assert item.read_bytes() != before


def test_next_with_blockers_is_still_200(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a", phase="plan")
    item = workspace.bundle_dir / f"{path}.md"
    item.write_text(
        item.read_text(encoding="utf-8").replace("effort: medium\n", "effort: oversized\n"),
        encoding="utf-8",
        newline="\n",
    )

    response = client.get(f"/v1/work/next?path={path}")

    assert response.status_code == 200
    assert response.json()["blockers"]


def test_next_unknown_path_is_404_unresolved(client: TestClient) -> None:
    response = client.get("/v1/work/next?path=work/nope")

    assert response.status_code == 404
    assert response.json()["error"]["reason"] == "unresolved"
    assert response.json()["error"]["exit_code"] == 7


def test_next_requires_path_and_a_real_bool(client: TestClient) -> None:
    assert client.get("/v1/work/next").status_code == 400
    assert client.get("/v1/work/next?path=work/a&descend=maybe").status_code == 400


def test_orchestrate_is_the_cli_twin(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a")

    response = client.get(f"/v1/work/orchestrate?path={path}&live=gw-execute-a,gw-plan-b")
    twin = cli_json(workspace, "work", "orchestrate", path, "--live", "gw-execute-a,gw-plan-b")
    body = response.json()

    assert isinstance(twin, dict)
    assert "error" in twin
    assert {**body["error"], "command": None} == {**twin["error"], "command": None}
    assert response.status_code == 404
    assert body["error"]["reason"] == "unresolved"


def test_orchestrate_with_declared_repository_is_the_cli_twin(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a")
    code = workspace.root.parent / "code"
    code.mkdir()
    manifest = workspace.root / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("repositories: {}", f'repositories:\n  code:\n    path: "{code}"'),
        encoding="utf-8",
        newline="\n",
    )

    response = client.get(f"/v1/work/orchestrate?path={path}")
    twin = cli_json(workspace, "work", "orchestrate", path)

    assert response.status_code == 200
    assert response.json() == twin


def test_decisions_is_the_cli_twin(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a")

    response = client.get(f"/v1/work/decisions?path={path}&status=open")

    assert response.status_code == 200
    assert response.json() == cli_json(workspace, "work", "decision", "list", path, "--status", "open")


def test_decisions_unknown_path_is_404(client: TestClient) -> None:
    assert client.get("/v1/work/decisions?path=work/nope").status_code == 404


def test_item_reads_whole(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a")

    body = client.get(f"/v1/work/item?path={path}").json()

    assert body["path"] == path
    assert body["refusal"] is None
    assert body["frontmatter"]["title"] == "feature-a"


def test_item_unknown_is_404_carrying_the_payload(client: TestClient) -> None:
    response = client.get("/v1/work/item?path=work/nope")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"]["refusal"] == "unknown-item"
