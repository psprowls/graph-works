"""Phase-1 reads: each body is the wire projection of its core read; parity where a twin exists."""

from __future__ import annotations

from typing import Any

from conftest import cli_json, write_item
from graph_works_core.proposals import run_proposals_read
from graph_works_core.wiki_page.commands import run_wiki_tree
from graph_works_core.work import commands as work
from graph_works_core.workspace.dispatch_config import run_dispatch_rules
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.schema_read import run_schema_read
from graph_works_wire import config as wire_config
from graph_works_wire import wiki as wire_wiki
from graph_works_wire import work as wire_work
from starlette.testclient import TestClient


def _dispatch(workspace: WorkspaceLayout, *, shared: str, local: str = "") -> None:
    (workspace.root / "dispatch.yaml").write_text(f"version: 1\npipeline:\n  rules:\n{shared}", encoding="utf-8")
    if local:
        (workspace.root / "dispatch.local.yaml").write_text(
            f"version: 1\npipeline:\n  rules:\n{local}", encoding="utf-8"
        )


def test_work_list_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    write_item(workspace, "feature-b")
    write_item(workspace, "feature-a", phase="plan")

    response = client.get("/v1/work/list")

    assert response.status_code == 200
    assert response.json() == wire_work.work_list_payload(work.run_work_list(workspace))
    assert [row["path"] for row in response.json()["items"]] == ["work/feature-a", "work/feature-b"]


def _file_proposal(workspace: WorkspaceLayout) -> str:
    filed = cli_json(
        workspace,
        "wiki",
        "proposal",
        "file",
        "--lane",
        "explanation",
        "--title",
        "Typed CLI",
        "--id",
        "s1",
        "--resource",
        "/sources/one.md",
    )
    assert isinstance(filed, dict) and filed["ok"], filed
    return str(filed["target"])


def test_proposals_is_the_cli_twin(client: TestClient, workspace: WorkspaceLayout) -> None:
    _file_proposal(workspace)

    response = client.get("/v1/wiki/proposals")

    assert response.status_code == 200
    assert response.json() == cli_json(workspace, "wiki", "proposals")
    assert [entry["mode"] for entry in response.json()] == ["create"]


def test_proposals_page_status_selects_approved_notes(client: TestClient, workspace: WorkspaceLayout) -> None:
    target = _file_proposal(workspace)
    approved = cli_json(workspace, "wiki", "proposal", "approve", target)
    assert isinstance(approved, dict) and approved["ok"], approved

    body = client.get("/v1/wiki/proposals?page_status=approved").json()

    assert body == wire_wiki.proposals_payload(run_proposals_read(workspace, "approved"))
    assert [entry["page_status"] for entry in body] == ["approved"]
    assert client.get("/v1/wiki/proposals").json() == []


def test_proposals_unknown_page_status_is_400(client: TestClient) -> None:
    response = client.get("/v1/wiki/proposals?page_status=maybe")

    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"


def _retype(workspace: WorkspaceLayout, path: str, old: str, new: str) -> None:
    page = workspace.bundle_dir / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    assert old in text
    page.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def test_queue_rows_equal_next_for_every_item(client: TestClient, workspace: WorkspaceLayout) -> None:
    ready = write_item(workspace, "feature-ready", phase="plan")
    spec = workspace.bundle_dir / ready / "references" / "01-design.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Design\n", encoding="utf-8")
    attend = write_item(workspace, "feature-design", phase="design")
    unsized = write_item(workspace, "testgap-unsized", phase="design")
    _retype(workspace, unsized, "type: Feature\n", "type: TestGap\n")
    _retype(workspace, unsized, "phase: design\neffort: medium\n", "")
    epic = write_item(workspace, "epic-e", phase="execute")
    _retype(workspace, epic, "type: Feature\n", "type: Epic\n")
    child = workspace.bundle_dir / "work" / "epic-e" / "children" / "feature-c.md"
    child.parent.mkdir(parents=True)
    child.write_text(
        (workspace.bundle_dir / f"{attend}.md").read_text(encoding="utf-8").replace("phase: design", "phase: plan"),
        encoding="utf-8",
        newline="\n",
    )
    archived = workspace.bundle_dir / "work" / "_archive" / "feature-old.md"
    archived.parent.mkdir(parents=True)
    archived.write_text((workspace.bundle_dir / f"{attend}.md").read_text(encoding="utf-8"), encoding="utf-8")

    response = client.get("/v1/work/queue")

    assert response.status_code == 200
    body = response.json()
    assert body == wire_work.work_queue_payload(work.run_work_queue(workspace))
    rows = {row["path"]: row for row in body["items"]}
    assert set(rows) == {ready, attend, unsized, epic, "work/epic-e/children/feature-c"}
    for path, row in rows.items():
        nexted = client.get(f"/v1/work/next?path={path}").json()
        assert row["skill"] == (None if nexted["action"] is None else nexted["action"]["skill"])
        assert row["mode"] == (None if nexted["dispatch"] is None else nexted["dispatch"]["profile"]["mode"])
        assert row["blockers"] == nexted["blockers"]
    assert rows[attend]["mode"] == "attend"
    assert rows[ready]["skill"] is not None and rows[ready]["blockers"] == []
    assert rows[epic]["skill"] is None and rows[epic]["blockers"]
    assert any("effort required" in blocker for blocker in rows[unsized]["blockers"])


def test_open_decisions_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    lone = write_item(workspace, "feature-lone")
    ledger = workspace.bundle_dir / lone / "references" / "00-decisions.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f"## D-001 — q\nstatus: open\naffects: [{lone}, packages/a]\n", encoding="utf-8")

    body = client.get("/v1/work/decisions/open").json()

    assert body == wire_work.open_decisions_payload(work.run_open_decisions(workspace))
    assert [(row["owner_path"], row["held"], row["entry"]["id"]) for row in body["decisions"]] == [
        (lone, [lone], "D-001")
    ]


def test_schema_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    body = client.get("/v1/schema").json()

    assert body == wire_config.schema_read_payload(run_schema_read(workspace))
    assert set(body) == {"schemas", "sections"}


_INDEX = """---
okf_version: 0.2
---

# Index

## Repositories

- [pkg-a](/code-graph/r/entities/packages/pkg-a.md) — the a package

### Extra

- [pkg-b](code-graph/r/entities/packages/pkg-b.md)
- [gone](/code-graph/r/entities/packages/gone.md)
"""


def test_wiki_tree_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    (workspace.bundle_dir / "index.md").write_text(_INDEX, encoding="utf-8", newline="\n")
    packages = workspace.bundle_dir / "code-graph" / "r" / "entities" / "packages"
    packages.mkdir(parents=True)
    for slug in ("pkg-a", "pkg-b"):
        (packages / f"{slug}.md").write_text(
            f"---\ntype: Package\ntitle: {slug}\ndescription: d\n---\n\n# {slug}\n", encoding="utf-8"
        )

    response = client.get("/v1/wiki/tree")

    assert response.status_code == 200
    body = response.json()
    assert body == wire_wiki.wiki_tree_payload(run_wiki_tree(workspace))
    (section,) = body["sections"]
    assert (section["heading"], section["level"], section["generated"]) == ("Repositories", 2, True)
    assert section["pages"] == [{"id": "code-graph/r/entities/packages/pkg-a", "title": "pkg-a", "type": "Package"}]
    (child,) = section["sections"]
    assert (child["heading"], child["level"], child["generated"], child["sections"]) == ("Extra", 3, True, [])
    assert child["pages"] == [{"id": "code-graph/r/entities/packages/pkg-b", "title": "pkg-b", "type": "Package"}]


def test_dispatch_rules_is_the_projection(client: TestClient, workspace: WorkspaceLayout) -> None:
    _dispatch(workspace, shared="  - match: {stage: [plan]}\n    model: opus\n")

    body = client.get("/v1/dispatch/rules").json()

    assert body == wire_config.dispatch_rules_payload(run_dispatch_rules(workspace))
    assert body["rules"][0]["match"] == {"stage": ["plan"]}


def _explain_equals_next(client: TestClient, path: str) -> dict[str, Any]:
    explained = client.get(f"/v1/dispatch/explain?path={path}")
    nexted = client.get(f"/v1/work/next?path={path}")
    assert explained.status_code == nexted.status_code == 200
    body: dict[str, Any] = explained.json()
    dispatch = nexted.json()["dispatch"]
    if dispatch is None:
        assert body["profile"] is None and body["provenance"] is None
    else:
        assert {"profile": body["profile"], "provenance": body["provenance"]} == dispatch
    assert body["blockers"] == nexted.json()["blockers"]
    return body


def test_explain_matches_next_when_packaged_and_local_rules_both_win(
    client: TestClient, workspace: WorkspaceLayout
) -> None:
    path = write_item(workspace, "feature-a", phase="plan")
    _dispatch(
        workspace,
        shared="  - match: {stage: design}\n    model: haiku\n",
        local="  - name: planning\n    match: {stage: plan}\n    model: opus\n",
    )

    body = _explain_equals_next(client, path)

    assert [rule["matched"] for rule in body["rules"]] == [False, True]
    assert body["provenance"]["skill"]["rule"]["source"] == "packaged"
    assert body["provenance"]["model"]["rule"]["name"] == "planning"
    assert body["packaged_rule"]["origin"]["source"] == "packaged"
    assert body["packaged_rule"]["origin"]["name"] == "single"


def test_explain_matches_next_on_an_agent_change_reset(client: TestClient, workspace: WorkspaceLayout) -> None:
    path = write_item(workspace, "feature-a", phase="plan")
    _dispatch(
        workspace,
        shared="  - match: {}\n    model: opus\n    reasoning_effort: high\n",
        local="  - match: {variant: single}\n    agent: codex\n",
    )

    body = _explain_equals_next(client, path)

    assert body["provenance"]["model"]["reason"] == "agent-change"


def test_explain_matches_next_for_a_blocked_epic(client: TestClient, workspace: WorkspaceLayout) -> None:
    epic = workspace.bundle_dir / "work" / "epic-e.md"
    epic.write_text(
        (workspace.bundle_dir / f"{write_item(workspace, 'epic-e', phase='execute')}.md")
        .read_text(encoding="utf-8")
        .replace("type: Feature", "type: Epic"),
        encoding="utf-8",
    )
    child = workspace.bundle_dir / "work" / "epic-e" / "children"
    child.mkdir(parents=True)
    (child / "feature-c.md").write_text(
        (workspace.bundle_dir / "work" / "epic-e.md")
        .read_text(encoding="utf-8")
        .replace("type: Epic", "type: Feature")
        .replace("phase: execute", "phase: plan"),
        encoding="utf-8",
    )

    body = _explain_equals_next(client, "work/epic-e")

    assert body["attributes"] is None and body["packaged_rule"] is None
    assert all(rule["matched"] is False for rule in body["rules"])
    assert body["blockers"]


def test_explain_unknown_path_is_404(client: TestClient) -> None:
    response = client.get("/v1/dispatch/explain?path=work/nope")

    assert response.status_code == 404
    assert response.json()["error"]["reason"] == "unresolved"


def test_explain_requires_path(client: TestClient) -> None:
    response = client.get("/v1/dispatch/explain")

    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"


def test_a_malformed_dispatch_file_is_a_workspace_refusal_naming_file_and_rule(
    client: TestClient, workspace: WorkspaceLayout
) -> None:
    path = write_item(workspace, "feature-a", phase="plan")
    _dispatch(workspace, shared="  - match: {}\n    model: opus\n", local="  - match: {}\n    colour: red\n")

    for url in (f"/v1/dispatch/explain?path={path}", "/v1/dispatch/rules"):
        error = client.get(url).json()["error"]
        assert error["reason"] == "workspace"
        assert "dispatch.local.yaml: rule 0" in error["message"]


def test_a_broken_schema_file_is_a_workspace_refusal(client: TestClient, workspace: WorkspaceLayout) -> None:
    (workspace.config_dir / "schema").mkdir(exist_ok=True)
    (workspace.config_dir / "schema" / "Bad.schema.json").write_text("{nope", encoding="utf-8")

    error = client.get("/v1/schema").json()["error"]

    assert error["reason"] == "workspace"
    assert "Bad.schema.json" in error["message"]
