"""Proposal decisions preserve the reviewed instant and the CLI's wire contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

import pytest
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import proposals as proposal_cli
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from httpx import Response
from mutation_helpers import make_client, serve_workspace, snapshot, write_proposal
from okf_io import load
from starlette.testclient import TestClient
from typer.testing import CliRunner

AT = datetime(2026, 9, 18, 14, 3, 7, tzinfo=UTC)
TARGET = "concepts/widget"
PLAN = "/v1/wiki/proposal/decide/plan"
APPLY = "/v1/wiki/proposal/decide/apply"
Env = tuple[WorkspaceLayout, str, TestClient, dict[str, str]]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(mutations, "now", lambda: AT)
    layout = serve_workspace(tmp_path)
    member = write_proposal(layout, "widget", target=TARGET)
    client, headers = make_client(layout)
    return layout, member, client, headers


def _apply(client: TestClient, headers: dict[str, str], params: dict[str, Any], planned: dict[str, Any]) -> Response:
    return client.post(APPLY, json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers)


@pytest.mark.parametrize(("decision", "status"), [("approve", "approved"), ("reject", "rejected")])
def test_round_trip_matches_cli_and_stamps_the_echoed_plan_instant(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, status: str
) -> None:
    monkeypatch.setattr("graph_works_core.proposals.commands.human_actor", lambda cwd=None: "human:tester")
    layout, member, client, headers = env
    params = {"target": TARGET, "decision": decision}
    before = snapshot(layout)
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200, planned.text
    assert snapshot(layout) == before
    plan = planned.json()
    assert plan["as_of"] == "2026-09-18T14:03:07Z"
    assert plan["plan"]["refusals"] == []
    assert plan["plan"]["applied"] is False
    assert plan["plan"]["rolled_back"] is False
    assert plan["plan"]["failures"] == []

    twin = serve_workspace(tmp_path / "twin")
    write_proposal(twin, "widget", target=TARGET)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return AT

    monkeypatch.setattr(proposal_cli, "datetime", FrozenDatetime)
    args = ["wiki", "proposal", decision, TARGET, "--workspace", str(twin.root), "--json"]
    cli_plan = CliRunner().invoke(app, [*args, "--dry-run"])
    assert cli_plan.exit_code == 0, cli_plan.output
    assert plan["plan"] == json.loads(cli_plan.stdout)
    cli = CliRunner().invoke(app, args)
    assert cli.exit_code == 0, cli.output

    # Apply occurs later, but verification must retain the reviewed plan's instant.
    monkeypatch.setattr(mutations, "now", lambda: AT + timedelta(minutes=2))
    applied = _apply(client, headers, params, plan)
    assert applied.status_code == 200, applied.text
    assert applied.json()["result"] == json.loads(cli.stdout)
    assert applied.json()["result"]["applied"] is True
    fm = load(layout.bundle_dir / member).fm_data()
    assert fm["page_status"] == status
    assert fm["verified"][-1] == {"by": "human:tester", "at": "2026-09-18T14:03:07+00:00"}
    assert datetime.fromisoformat(fm["verified"][-1]["at"]) == datetime.fromisoformat(plan["as_of"])
    assert snapshot(layout) == snapshot(twin)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_two_plans_at_one_instant_share_a_digest(env: Env, decision: str) -> None:
    _layout, _member, client, headers = env
    params = {"target": TARGET, "decision": decision}
    first = client.post(PLAN, json=params, headers=headers)
    second = client.post(PLAN, json=params, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


@pytest.mark.parametrize("tamper", ["decision", "target", "digest"])
def test_tampering_is_409_without_writes(env: Env, tamper: str) -> None:
    layout, _member, client, headers = env
    params = {"target": TARGET, "decision": "approve"}
    response = client.post(PLAN, json=params, headers=headers)
    assert response.status_code == 200
    planned = response.json()
    if tamper == "digest":
        planned["digest"] = "sha256:" + "0" * 64
    else:
        params[tamper] = "reject" if tamper == "decision" else "concepts/nope"
    before = snapshot(layout)
    applied = _apply(client, headers, params, planned)
    assert applied.status_code == 409
    assert applied.json()["error"]["reason"] == "stale-plan"
    assert snapshot(layout) == before


@pytest.mark.parametrize("status", ["approved", "rejected", "unknown-target"])
def test_refused_plan_is_200_and_apply_is_422_without_writes(env: Env, status: str) -> None:
    layout, _member, client, headers = env
    if status != "unknown-target":
        write_proposal(layout, "widget", target=TARGET, page_status=status)
    params = {"target": "concepts/nope" if status == "unknown-target" else TARGET, "decision": "approve"}
    before = snapshot(layout)
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200
    assert planned.json()["plan"]["refusals"]
    assert snapshot(layout) == before
    response = _apply(client, headers, params, planned.json())
    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before


@pytest.mark.parametrize("decision", ["approved", "rejected", "", "Approve", None, [], 1])
@pytest.mark.parametrize("route", [PLAN, APPLY])
def test_invalid_decision_is_400_without_writes(env: Env, decision: object, route: str) -> None:
    layout, _member, client, headers = env
    before = snapshot(layout)
    params = {"target": TARGET, "decision": decision}
    if route == APPLY:
        params.update(as_of="2026-09-18T14:03:07Z", digest="sha256:" + "0" * 64)
    response = client.post(route, json=params, headers=headers)
    assert response.status_code == 400
    assert snapshot(layout) == before
