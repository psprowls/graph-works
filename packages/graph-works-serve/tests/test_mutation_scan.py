"""One repository's structural scan plans and applies through serve with the CLI's wire contract."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import scan as scan_cli
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from mutation_helpers import frozen_datetime, make_client, serve_workspace, snapshot
from starlette.testclient import TestClient
from typer.testing import CliRunner

AT = datetime(2026, 10, 2, 14, 3, 7, tzinfo=UTC)
PLAN = "/v1/scan/plan"
APPLY = "/v1/scan/apply"
Env = tuple[WorkspaceLayout, TestClient, dict[str, str], Path]


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=gw-test",
            "-c",
            "user.email=gw-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _code_repo(root: Path) -> Path:
    (root / "src" / "demo").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8", newline="\n")
    (root / "src" / "demo" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    return root


def _workspace(root: Path, code: Path) -> WorkspaceLayout:
    layout = serve_workspace(root)
    head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    layout.manifest_path.write_text(
        f'{head}repositories:\n  "code":\n    path: "{code.as_posix()}"\n', encoding="utf-8", newline="\n"
    )
    return layout


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(mutations, "now", lambda: AT)
    code = _code_repo(tmp_path / "code")
    layout = _workspace(tmp_path / "served", code)
    client, headers = make_client(layout)
    return layout, client, headers, code


def test_round_trip_matches_cli(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, client, headers, code = env
    before = snapshot(layout)
    planned = client.post(PLAN, json={"repo": "code"}, headers=headers)
    assert planned.status_code == 200, planned.text
    plan = planned.json()["plan"]
    assert plan["refusal"] is None and plan["applied"] is False
    assert plan["entities_created"]
    assert snapshot(layout) == before

    twin = _workspace(tmp_path / "twin", code)
    monkeypatch.setattr(scan_cli, "datetime", frozen_datetime(AT))
    args = ["scan", "--repo", "code", "--no-narrate", "--json", "--workspace", str(twin.root)]
    cli_plan = CliRunner().invoke(app, [*args, "--dry-run"])
    assert cli_plan.exit_code == 0, cli_plan.output
    assert plan == json.loads(cli_plan.stdout)

    cli = CliRunner().invoke(app, args)
    assert cli.exit_code == 0, cli.output
    applied = client.post(
        APPLY,
        json={"repo": "code", "as_of": planned.json()["as_of"], "digest": planned.json()["digest"]},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()["result"]
    assert result == json.loads(cli.stdout)
    assert result["applied"] is True and result["failures"] == []
    assert snapshot(layout) == snapshot(twin)
    assert snapshot(layout) != before


def test_unknown_repository_plans_200_and_applies_422(env: Env) -> None:
    layout, client, headers, _code = env
    planned = client.post(PLAN, json={"repo": "nope"}, headers=headers)
    assert planned.status_code == 200, planned.text
    assert planned.json()["plan"]["refusal"] == "unknown-repository"
    before = snapshot(layout)
    response = client.post(
        APPLY,
        json={"repo": "nope", "as_of": planned.json()["as_of"], "digest": planned.json()["digest"]},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before


def test_missing_repo_is_400(env: Env) -> None:
    _layout, client, headers, _code = env
    assert client.post(PLAN, json={}, headers=headers).status_code == 400
