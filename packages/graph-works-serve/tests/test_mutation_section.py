"""Section writes plan and apply through serve with the CLI's wire contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import section as section_cli
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from mutation_helpers import frozen_datetime, make_client, serve_workspace, snapshot
from starlette.testclient import TestClient
from typer.testing import CliRunner

AT = datetime(2026, 10, 2, 14, 3, 7, tzinfo=UTC)
PLAN = "/v1/wiki/section/plan"
APPLY = "/v1/wiki/section/apply"
PAGE = "docs/explanations/x"
TEXT = (
    "---\ntype: Explanation\ntitle: X\ndescription: d\nupdated: 2026-01-01\n# keep this comment\n---\n\n"
    "## Context\n\nold context\n\n### Detail\n\nd\n\n## Trade-offs\n\nt\n"
)
PACKAGE = "code-graph/r/entities/packages/p"
Env = tuple[WorkspaceLayout, TestClient, dict[str, str]]


def _write(layout: WorkspaceLayout, page: str, text: str) -> Path:
    path = layout.bundle_dir / f"{page}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _workspace(root: Path) -> WorkspaceLayout:
    layout = serve_workspace(root)
    _write(layout, PAGE, TEXT)
    return layout


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(mutations, "now", lambda: AT)
    layout = _workspace(tmp_path)
    client, headers = make_client(layout)
    return layout, client, headers


def test_round_trip_matches_cli(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, client, headers = env
    params = {"id": PAGE, "heading": "Context", "body": "new context"}
    before = snapshot(layout)
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200, planned.text
    assert snapshot(layout) == before
    assert planned.json()["plan"]["after"] == "\nnew context\n\n"
    twin = _workspace(tmp_path / "twin")
    body_file = tmp_path / "body.md"
    body_file.write_text("new context", encoding="utf-8", newline="")
    monkeypatch.setattr(section_cli, "datetime", frozen_datetime(AT))
    args = [
        "wiki", "section", "write", PAGE, "--heading", "Context", "--body-file", str(body_file),
        "--workspace", str(twin.root), "--json",
    ]  # fmt: skip
    cli_plan = CliRunner().invoke(app, args)
    assert cli_plan.exit_code == 0, cli_plan.output
    assert planned.json()["plan"] == json.loads(cli_plan.stdout)
    cli = CliRunner().invoke(app, [*args, "--apply"])
    assert cli.exit_code == 0, cli.output
    applied = client.post(
        APPLY, json={**params, "as_of": planned.json()["as_of"], "digest": planned.json()["digest"]}, headers=headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["result"] == json.loads(cli.stdout)
    assert applied.json()["result"]["applied"] is True
    assert snapshot(layout) == snapshot(twin)
    assert "updated: 2026-10-02\n# keep this comment\n" in (layout.bundle_dir / f"{PAGE}.md").read_text(
        encoding="utf-8"
    )


def test_concurrent_edit_of_the_section_is_409(env: Env) -> None:
    layout, client, headers = env
    params = {"id": PAGE, "heading": "Context", "body": "mine"}
    planned = client.post(PLAN, json=params, headers=headers).json()
    path = layout.bundle_dir / f"{PAGE}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("old context", "their context"), encoding="utf-8", newline=""
    )
    response = client.post(
        APPLY, json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers
    )
    assert response.status_code == 409
    assert "their context" in path.read_text(encoding="utf-8")


def test_generated_section_plan_is_200_and_apply_422(env: Env) -> None:
    layout, client, headers = env
    _write(layout, PACKAGE, "---\ntype: Package\ntitle: p\n---\n\n## Purpose\n\nx\n\n## Files\n\ngen\n")
    params = {"id": PACKAGE, "heading": "Files", "body": "mine"}
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200, planned.text
    assert planned.json()["plan"]["refusal"] == "generated-section"
    before = snapshot(layout)
    response = client.post(
        APPLY, json={**params, "as_of": planned.json()["as_of"], "digest": planned.json()["digest"]}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before


def test_missing_body_is_400(env: Env) -> None:
    _layout, client, headers = env
    response = client.post(PLAN, json={"id": PAGE, "heading": "Context"}, headers=headers)
    assert response.status_code == 400


def test_unbalanced_body_plan_is_200_and_apply_422(env: Env) -> None:
    layout, client, headers = env
    params = {"id": PAGE, "heading": "Context", "body": "```sh\ncode\n"}
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200, planned.text
    assert planned.json()["plan"]["refusal"] == "unbalanced-body"
    before = snapshot(layout)
    response = client.post(
        APPLY, json={**params, "as_of": planned.json()["as_of"], "digest": planned.json()["digest"]}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before
