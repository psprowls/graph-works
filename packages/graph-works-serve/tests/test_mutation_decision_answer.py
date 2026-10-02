"""Decision answers plan and apply through serve with the CLI's wire contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from graph_works_cli.cli import app
from graph_works_cli.work_cli import decision as decision_cli
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from mutation_helpers import make_client, serve_workspace, snapshot, write_item
from starlette.testclient import TestClient
from typer.testing import CliRunner

AT = datetime(2026, 9, 18, 14, 3, 7, tzinfo=UTC)
PLAN = "/v1/work/decision/answer/plan"
APPLY = "/v1/work/decision/answer/apply"
ITEM = "work/feature-x"
Env = tuple[WorkspaceLayout, TestClient, dict[str, str]]


def _seed(layout: WorkspaceLayout) -> None:
    write_item(layout, ITEM, work_status="open", phase="design")
    added = CliRunner().invoke(
        app, ["work", "decision", "add", ITEM, "--question", "Ship it?", "--workspace", str(layout.root), "--json"]
    )
    assert added.exit_code == 0, added.output


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(mutations, "now", lambda: AT)
    monkeypatch.setattr(decision_cli, "_today", lambda: AT.date())
    layout = serve_workspace(tmp_path)
    _seed(layout)
    client, headers = make_client(layout)
    return layout, client, headers


def _relocate(payload: dict[str, Any], old: Path, new: Path) -> dict[str, Any]:
    """`ledger_path` is absolute; swap the twin's root for this one."""
    return {
        key: value.replace(str(old), str(new)) if isinstance(value, str) else value for key, value in payload.items()
    }


def _strip_commit(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "commit"}


def test_round_trip_matches_cli(env: Env, tmp_path: Path) -> None:
    layout, client, headers = env
    params = {"path": ITEM, "id": "D-001", "answer": "Yes.", "decided_by": "human:tester"}
    before = snapshot(layout)
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200, planned.text
    assert snapshot(layout) == before
    twin = serve_workspace(tmp_path / "twin")
    _seed(twin)
    args = [
        "work", "decision", "answer", ITEM, "D-001", "--answer", "Yes.",
        "--decided-by", "human:tester", "--workspace", str(twin.root), "--json",
    ]  # fmt: skip
    cli_plan = CliRunner().invoke(app, [*args, "--dry-run"])
    assert cli_plan.exit_code == 0, cli_plan.output
    assert _strip_commit(_relocate(planned.json()["plan"], layout.root, twin.root)) == _strip_commit(
        json.loads(cli_plan.stdout)
    )
    cli = CliRunner().invoke(app, args)
    assert cli.exit_code == 0, cli.output
    applied = client.post(
        APPLY, json={**params, **{k: planned.json()[k] for k in ("as_of", "digest")}}, headers=headers
    )
    assert applied.status_code == 200, applied.text
    assert _strip_commit(_relocate(applied.json()["result"], layout.root, twin.root)) == _strip_commit(
        json.loads(cli.stdout)
    )
    assert snapshot(layout) == snapshot(twin)


def test_decided_by_defaults_to_the_git_human(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("graph_works_serve.mutation_specs.human_actor", lambda cwd=None: "human:git")
    _layout, client, headers = env
    plan = client.post(PLAN, json={"path": ITEM, "id": "D-001", "answer": "Yes."}, headers=headers).json()
    assert "human:git" in json.dumps(plan["plan"]["entry"])


def test_unknown_decision_plan_is_200_refused_and_apply_422(env: Env) -> None:
    layout, client, headers = env
    params = {"path": ITEM, "id": "D-099", "answer": "x", "decided_by": "human:t"}
    planned = client.post(PLAN, json=params, headers=headers).json()
    assert planned["plan"]["refusal"] == "unknown-decision"
    before = snapshot(layout)
    response = client.post(
        APPLY, json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers
    )
    assert response.status_code == 422
    assert snapshot(layout) == before


def test_concurrent_answer_is_409(env: Env) -> None:
    layout, client, headers = env
    params = {"path": ITEM, "id": "D-001", "answer": "Yes.", "decided_by": "human:t"}
    planned = client.post(PLAN, json=params, headers=headers).json()
    outside = CliRunner().invoke(
        app, ["work", "decision", "answer", ITEM, "D-001", "--answer", "No.", "--workspace", str(layout.root)]
    )
    assert outside.exit_code == 0, outside.output
    response = client.post(
        APPLY, json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers
    )
    assert response.status_code == 409
