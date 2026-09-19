"""The page, log, config, and agent-config read routes."""

from __future__ import annotations

from pathlib import Path

from conftest import cli_json
from graph_works_core.workspace.layout import WorkspaceLayout
from pytest import MonkeyPatch
from starlette.testclient import TestClient


def test_page_reads_with_links(client: TestClient, workspace: WorkspaceLayout) -> None:
    """A page route must preserve the core link-neighbourhood projection."""
    concepts = workspace.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    (concepts / "a.md").write_text("---\ntype: Concept\ntitle: A\n---\n\n[B](b.md)\n", encoding="utf-8", newline="\n")
    (concepts / "b.md").write_text("---\ntype: Concept\ntitle: B\n---\n\n[A](a.md)\n", encoding="utf-8", newline="\n")

    body = client.get("/v1/wiki/page?id=concepts/a").json()

    assert body["backlinks"] == ["concepts/b"]
    assert body["outlinks"][0]["raw"] == "b.md"


def test_unknown_page_is_404(client: TestClient) -> None:
    """An absent page must remain a structured unknown-page refusal."""
    response = client.get("/v1/wiki/page?id=concepts/nope")

    assert response.status_code == 404
    assert response.json()["error"]["payload"]["refusal"] == "unknown-page"


def test_log_filters_and_defaults(client: TestClient, workspace: WorkspaceLayout) -> None:
    """The log route must apply its declared default and each read filter."""
    (workspace.bundle_dir / "log.md").write_text(
        "# Log\n\n## 2026-09-16\n\n- **scan** a\n\n## 2026-09-17\n\n- **ingest** b\n",
        encoding="utf-8",
        newline="\n",
    )

    assert [entry["op"] for entry in client.get("/v1/log").json()["entries"]] == ["ingest", "scan"]
    assert [entry["op"] for entry in client.get("/v1/log?op=scan").json()["entries"]] == ["scan"]
    assert [entry["op"] for entry in client.get("/v1/log?since=2026-09-17").json()["entries"]] == ["ingest"]
    assert client.get("/v1/log?last=1").json()["entries"][0]["op"] == "ingest"
    assert client.get("/v1/log?last=-1").status_code == 400
    assert client.get("/v1/log?since=yesterday").status_code == 400


def test_config_list_and_key_are_cli_twins(client: TestClient, workspace: WorkspaceLayout) -> None:
    """Config list and key reads must use the same wire projection as the CLI."""
    assert client.get("/v1/config").json() == cli_json(workspace, "config", "list")
    listed = client.get("/v1/config").json()
    key = listed[0]["key"]

    assert isinstance(key, str)
    assert client.get(f"/v1/config?key={key}").json() == cli_json(workspace, "config", "get", key)


def test_config_unknown_key_is_404(client: TestClient) -> None:
    """A config catalog miss must be an unresolved 404, not an internal error."""
    response = client.get("/v1/config?key=no.such.key")

    assert response.status_code == 404
    assert response.json()["error"]["reason"] == "unresolved"


def test_agent_config_is_the_cli_twin(
    client: TestClient, workspace: WorkspaceLayout, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """A selected agent report must match the real CLI's JSON output."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    assert client.get("/v1/agent-config?agent=claude").json() == cli_json(
        workspace, "agent-config", "show", "--agent", "claude"
    )


def test_agent_config_project_and_bad_agent(client: TestClient, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Explicit projects work without a layout while unknown agents are rejected."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    assert client.get(f"/v1/agent-config?project={project}").status_code == 200
    assert client.get("/v1/agent-config?agent=emacs").status_code == 400
