# packages/graph-works-serve/tests/test_mutation_advance.py
from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_cli
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import mutations
from httpx import Response
from mutation_helpers import make_client, serve_workspace, snapshot, write_item
from starlette.testclient import TestClient
from typer.testing import CliRunner

Env = tuple[WorkspaceLayout, TestClient, dict[str, str]]

ITEM = "work/feature-scratch"
AT = datetime(2026, 9, 18, 14, 3, 7, tzinfo=UTC)
PLAN = "/v1/work/advance/plan"
APPLY = "/v1/work/advance/apply"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(mutations, "now", lambda: AT)
    layout = serve_workspace(tmp_path)
    write_item(layout, ITEM, work_status="open", phase="design")
    refs = layout.bundle_dir / ITEM / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "01-design.md").write_text("# Design\n", encoding="utf-8", newline="")
    (refs / "02-plan.md").write_text("# Plan\n", encoding="utf-8", newline="")
    client, headers = make_client(layout)
    return layout, client, headers


def _apply(client: TestClient, headers: dict[str, str], params: dict[str, Any], planned: dict[str, Any]) -> Response:
    return client.post(APPLY, json={**params, "as_of": planned["as_of"], "digest": planned["digest"]}, headers=headers)


def test_plan_writes_nothing_and_apply_matches_the_cli_projection(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, client, headers = env
    before = snapshot(layout)
    params = {"path": ITEM, "effort": "small"}
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200
    assert snapshot(layout) == before
    assert planned.json()["plan"]["changes"]["phase"] == ["design", "plan"]

    twin = serve_workspace(tmp_path / "twin")
    write_item(twin, ITEM, work_status="open", phase="design")
    twin_refs = twin.bundle_dir / ITEM / "references"
    twin_refs.mkdir(parents=True, exist_ok=True)
    (twin_refs / "01-design.md").write_text("# Design\n", encoding="utf-8", newline="")
    monkeypatch.setattr(work_cli, "_today", lambda: date(2026, 9, 18))
    cli = CliRunner().invoke(
        app,
        ["work", "advance", ITEM, "--effort", "small", "--no-infer-worktree", "--workspace", str(twin.root), "--json"],
    )
    assert cli.exit_code == 0, cli.output
    expected = json.loads(cli.stdout)

    applied = _apply(client, headers, params, planned.json())
    assert applied.status_code == 200, applied.text
    assert applied.json()["result"] == _relocate(expected, twin.root, layout.root)
    assert snapshot(layout) != before


def _relocate(payload: dict[str, Any], old: Path, new: Path) -> dict[str, Any]:
    """`results_path`/`pointer_path` are absolute; swap the twin's root for this one."""
    return {
        key: value.replace(str(old), str(new)) if isinstance(value, str) else value for key, value in payload.items()
    }


def test_two_plans_at_one_instant_share_a_digest(env: Env) -> None:
    _layout, client, headers = env
    first = client.post(PLAN, json={"path": ITEM}, headers=headers).json()
    second = client.post(PLAN, json={"path": ITEM, "return": False}, headers=headers).json()
    assert first == second


def test_an_outside_edit_makes_apply_409_and_the_fresh_plan_applies(env: Env) -> None:
    layout, client, headers = env
    planned = client.post(PLAN, json={"path": ITEM}, headers=headers).json()
    write_item(layout, ITEM, work_status="open", phase="plan", effort="small")
    before = snapshot(layout)
    stale = _apply(client, headers, {"path": ITEM}, planned)
    assert stale.status_code == 409
    assert stale.json()["error"]["reason"] == "stale-plan"
    assert snapshot(layout) == before
    fresh = stale.json()["error"]["payload"]
    assert _apply(client, headers, {"path": ITEM}, fresh).status_code == 200


def test_a_changed_effort_is_409(env: Env) -> None:
    layout, client, headers = env
    planned = client.post(PLAN, json={"path": ITEM, "effort": "small"}, headers=headers).json()
    before = snapshot(layout)
    assert _apply(client, headers, {"path": ITEM, "effort": "large"}, planned).status_code == 409
    assert snapshot(layout) == before


def test_a_refused_advance_plans_200_and_applies_422(env: Env) -> None:
    layout, client, headers = env
    params = {"path": ITEM, "return": True}  # --return is refused outside `finish`
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200
    assert planned.json()["plan"]["refusal"] is not None
    before = snapshot(layout)
    refused = _apply(client, headers, params, planned.json())
    assert refused.status_code == 422
    assert refused.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before


def test_an_unknown_item_is_a_refused_plan(env: Env) -> None:
    layout, client, headers = env
    params = {"path": "work/feature-nope"}
    before = snapshot(layout)
    response = client.post(PLAN, json=params, headers=headers)
    assert response.status_code == 200
    assert response.json()["plan"]["refusal"]["reason"] == "unknown-path"
    applied = _apply(client, headers, params, response.json())
    assert applied.status_code == 422
    assert applied.json()["error"]["reason"] == "refused"
    assert snapshot(layout) == before


def test_advance_never_infers_a_worktree_from_cwd(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, client, headers = env
    write_item(layout, ITEM, work_status="open", phase="design", effort="small")
    repo = layout.root.parent
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-qm",
            "init",
        ],
        check=True,
    )
    worktree = tmp_path / "linked"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-qb", "test-placement", str(worktree)], check=True)
    monkeypatch.chdir(worktree)
    # Positive control: this cwd really can supply placement when inference is enabled.
    inferred = run_stage_advance(layout, ITEM, today=AT.date(), infer_worktree=True)
    assert "worktree" in {change.key for change in inferred.outcome.plan.changes}
    planned = client.post(PLAN, json={"path": ITEM}, headers=headers).json()
    assert "worktree" not in planned["plan"]["changes"]
    assert "branch" not in planned["plan"]["changes"]
    applied = _apply(client, headers, {"path": ITEM}, planned)
    assert applied.status_code == 200, applied.text
    assert "worktree" not in applied.json()["result"]["changes"]
    assert "branch" not in applied.json()["result"]["changes"]
    assert Path.cwd() == worktree


def test_explicit_placement_and_metadata_survive_apply(env: Env, tmp_path: Path) -> None:
    layout, client, headers = env
    params = {
        "path": ITEM,
        "worktree": str(tmp_path / "explicit"),
        "branch": "feature/explicit",
        "owner": "pat",
        "effort": "small",
        "released_at": "2026-09-17",
    }
    planned = client.post(PLAN, json=params, headers=headers)
    assert planned.status_code == 200, planned.text
    changes = planned.json()["plan"]["changes"]
    assert changes["worktree"][1] == params["worktree"]
    assert changes["branch"][1] == params["branch"]
    before = snapshot(layout)
    applied = _apply(client, headers, params, planned.json())
    assert applied.status_code == 200, applied.text
    assert applied.json()["result"]["changes"] == changes
    assert snapshot(layout) != before


def test_tampered_digest_writes_nothing(env: Env) -> None:
    layout, client, headers = env
    params = {"path": ITEM}
    planned = client.post(PLAN, json=params, headers=headers).json()
    planned["digest"] = "sha256:" + "0" * 64
    before = snapshot(layout)
    response = _apply(client, headers, params, planned)
    assert response.status_code == 409
    assert response.json()["error"]["reason"] == "stale-plan"
    assert snapshot(layout) == before
