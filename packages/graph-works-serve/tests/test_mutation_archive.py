# packages/graph-works-serve/tests/test_mutation_archive.py
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_cli
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from httpx import Response
from mutation_helpers import make_client, serve_workspace, snapshot, write_item, write_proposal
from starlette.testclient import TestClient
from typer.testing import CliRunner

Env = tuple[WorkspaceLayout, TestClient, dict[str, str]]

DONE = "work/feature-done"
OPEN = "work/feature-open"
AT = datetime(2026, 9, 18, 14, 3, 7, tzinfo=UTC)
PLAN = "/v1/work/archive/plan"
APPLY = "/v1/work/archive/apply"
CHILD_PARENT = "work/epic-done"
CHILD = f"{CHILD_PARENT}/children/bug-done"


def _seed(layout: WorkspaceLayout) -> None:
    write_item(layout, DONE, work_status="resolved", phase="done", status="stable", effort="medium")
    write_item(layout, OPEN, work_status="open", phase="execute", status="stable", effort="medium")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(mutations, "now", lambda: AT)
    layout = serve_workspace(tmp_path)
    _seed(layout)
    client, headers = make_client(layout)
    return layout, client, headers


def _apply(client: TestClient, headers: dict[str, str], params: dict[str, Any], planned: dict[str, Any]) -> Response:
    return client.post(APPLY, json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers)


@pytest.mark.parametrize("params", [{"paths": [DONE]}, {}, {"paths": None}])
def test_round_trip_matches_the_cli_projection(
    env: Env, tmp_path: Path, params: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, client, headers = env
    before = snapshot(layout)
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200
    assert snapshot(layout) == before
    assert list(planned.json()["plan"]["path_mapping"]) == [DONE]

    twin = serve_workspace(tmp_path / "twin")
    _seed(twin)
    monkeypatch.setattr(work_cli, "_today", lambda: date(2026, 9, 18))
    cli = CliRunner().invoke(
        app, ["work", "archive", *(params.get("paths") or []), "--workspace", str(twin.root), "--json"]
    )
    assert cli.exit_code == 0, cli.output
    expected = json.loads(cli.stdout)

    applied = _apply(client, headers, params, planned.json())
    assert applied.status_code == 200, applied.text
    assert applied.json()["result"] == expected
    assert not (layout.bundle_dir / f"{DONE}.md").exists()


def test_omitted_and_null_paths_hash_the_same(env: Env) -> None:
    _layout, client, headers = env
    assert (
        client.post(PLAN, json={}, headers=headers).json()
        == client.post(PLAN, json={"paths": None}, headers=headers).json()
    )


def test_an_empty_paths_list_is_400(env: Env) -> None:
    _layout, client, headers = env
    response = client.post(PLAN, json={"paths": []}, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"


def test_a_different_paths_list_is_409(env: Env) -> None:
    layout, client, headers = env
    planned = client.post(PLAN, json={"paths": [DONE]}, headers=headers).json()
    before = snapshot(layout)
    assert _apply(client, headers, {"paths": [DONE, OPEN]}, planned).status_code == 409
    assert snapshot(layout) == before


def test_archiving_an_open_item_plans_200_and_applies_422(env: Env) -> None:
    layout, client, headers = env
    planned = client.post(PLAN, json={"paths": [OPEN]}, headers=headers)
    assert planned.status_code == 200
    assert planned.json()["plan"]["refusals"][0]["kind"] == "not-terminal"
    before = snapshot(layout)
    response = _apply(client, headers, {"paths": [OPEN]}, planned.json())
    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before


def test_an_outside_edit_is_409(env: Env) -> None:
    layout, client, headers = env
    planned = client.post(PLAN, json={}, headers=headers).json()
    write_item(layout, "work/feature-done-too", work_status="resolved", phase="done", status="stable", effort="medium")
    before = snapshot(layout)
    assert _apply(client, headers, {}, planned).status_code == 409
    assert snapshot(layout) == before


def test_the_wiki_lane_is_never_touched(env: Env) -> None:
    layout, client, headers = env
    write_proposal(layout, "p", target="concepts/x", page_status="rejected")
    planned = client.post(PLAN, json={}, headers=headers).json()
    applied = _apply(client, headers, {}, planned)
    assert applied.status_code == 200
    assert (layout.bundle_dir / "proposals/p.md").exists()


def test_tampered_digest_writes_nothing(env: Env) -> None:
    layout, client, headers = env
    planned = client.post(PLAN, json={}, headers=headers).json()
    planned["digest"] = "sha256:" + "0" * 64
    before = snapshot(layout)
    response = _apply(client, headers, {}, planned)
    assert response.status_code == 409
    assert response.json()["error"]["reason"] == "stale-plan"
    assert snapshot(layout) == before


def test_repeated_plans_are_deterministic(env: Env) -> None:
    layout, client, headers = env
    before = snapshot(layout)
    first = client.post(PLAN, json={"paths": [DONE]}, headers=headers)
    second = client.post(PLAN, json={"paths": [DONE]}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert snapshot(layout) == before


def test_archiving_a_child_plans_200_refused_not_top_level_and_applies_422(env: Env) -> None:
    layout, client, headers = env
    write_item(layout, CHILD_PARENT, work_status="resolved", phase="done", status="stable", effort="medium")
    write_item(layout, CHILD, work_status="resolved", phase="done", status="stable", effort="small")
    (layout.bundle_dir / CHILD_PARENT / "children").mkdir(parents=True, exist_ok=True)

    planned = client.post(PLAN, json={"paths": [CHILD]}, headers=headers)
    assert planned.status_code == 200
    assert [refusal["kind"] for refusal in planned.json()["plan"]["refusals"]] == ["not-top-level"]
    before = snapshot(layout)
    response = _apply(client, headers, {"paths": [CHILD]}, planned.json())
    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before
