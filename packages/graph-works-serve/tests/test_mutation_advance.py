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
from mutation_helpers import make_client, seed_plan_doc, serve_workspace, snapshot, write_item
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
    seed_plan_doc(layout, ITEM)
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


def test_multi_repository_public_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real Git evidence survives planning, placement, partial receipt and restart."""
    from graph_works_core import apply_init, plan_init
    from graph_works_core.orchestrate.finish_receipt import run_record_finish
    from okf_io import load

    monkeypatch.setattr(mutations, "now", lambda: AT)
    layout = apply_init(plan_init(tmp_path / "workspace", today=AT.date(), topic="Lifecycle")).layout
    repos = {}

    def git(repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    for name in ("code", "ui"):
        repo = tmp_path / name
        repo.mkdir()
        (repo / "packages").mkdir()
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.test")
        git(repo, "commit", "--allow-empty", "-m", "base")
        repos[name] = repo
    layout.manifest_path.write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\nrepositories:\n"
        + "".join(f"  {n}: {{path: {p}}}\n" for n, p in repos.items()),
        encoding="utf-8",
    )
    owner = "work/epic-lifecycle"
    child = owner + "/children/feature-ui"
    anchor = tmp_path / "code-anchor"
    git(repos["code"], "worktree", "add", "-b", "epic/lifecycle", str(anchor))
    owner_page = layout.bundle_dir / (owner + ".md")
    owner_page.parent.mkdir(parents=True, exist_ok=True)
    owner_page.write_text(
        "---\ntype: Epic\ntitle: Lifecycle\nwork_status: in-progress\nphase: execute\naffects: [packages]\nrepo: code\n"
        f"worktree: {anchor}\nbranch: epic/lifecycle\n---\n",
        encoding="utf-8",
    )
    child_page = layout.bundle_dir / (child + ".md")
    child_page.parent.mkdir(parents=True)
    child_page.write_text(
        "---\ntype: Feature\ntitle: UI\nwork_status: open\nphase: execute\n"
        "effort: small\naffects: [packages]\nrepo: ui\n---\n",
        encoding="utf-8",
    )
    client, headers = make_client(layout)

    def cli(*args: str) -> dict[str, Any]:
        result = CliRunner().invoke(app, [*args, "--workspace", str(layout.root), "--json"])
        payload = json.loads(result.stdout)
        return payload.get("error", {}).get("payload", payload)

    def plan() -> dict[str, Any]:
        public = cli("work", "orchestrate", owner)
        response = client.get("/v1/work/orchestrate", params={"path": owner}, headers=headers)
        assert response.status_code == 200, response.text
        assert public == response.json()
        assert public["repo"]["name"] == "code"
        return public

    initial = plan()
    assert initial["preparations"], json.dumps(initial)
    [preparation] = initial["preparations"]
    assert preparation["owner_path"] == owner
    assert preparation["owner_phase"] == "execute"
    assert preparation["repo"]["name"] == "ui"
    assert preparation["worktree"]["action"] == "create-top-level"
    foreign_anchor = tmp_path / "ui-anchor"
    branch = preparation["worktree"]["branch"]
    git(repos["ui"], "worktree", "add", "-b", branch, str(foreign_anchor), preparation["base_branch"])
    recorded = cli(
        "work",
        "record-placement",
        owner,
        "--root",
        owner,
        "--phase",
        preparation["owner_phase"],
        "--repo",
        "ui",
        "--worktree",
        str(foreign_anchor),
        "--branch",
        branch,
    )
    assert recorded["written"]
    ready = plan()
    assert ready["preparations"] == []
    [dispatch] = ready["dispatches"]
    assert dispatch["repo"]["name"] == "ui"
    assert dispatch["worktree"]["action"] == "fork-child"
    assert dispatch["worktree"]["parent_path"] == str(foreign_anchor)
    assert dispatch["worktree"]["base_branch"] == dispatch["merge_target"] == branch
    git(foreign_anchor, "commit", "--allow-empty", "-m", "child integrated")
    document = load(child_page)
    document.set("phase", "done")
    document.set("work_status", "resolved")
    document.save()
    document = load(owner_page)
    document.set("phase", "finish")
    document.save()
    finishing = plan()
    assert finishing["dispatches"], json.dumps(finishing)
    [dispatch] = finishing["dispatches"]
    assert [target["repo"]["name"] for target in dispatch["finish_targets"]] == ["code", "ui"]
    next_cli = cli("work", "next", owner)
    next_http = client.get("/v1/work/next", params={"path": owner}, headers=headers).json()
    assert next_cli["finish_targets"] == next_http["finish_targets"] == dispatch["finish_targets"]
    git(repos["code"], "merge", "epic/lifecycle")
    assert run_record_finish(layout, owner, repo_name="code", today=AT.date()).changed
    params = {"path": owner}
    partial = client.post(PLAN, json=params, headers=headers).json()
    assert partial["plan"]["changed"] is False
    assert partial["plan"]["blockers"]
    assert partial["plan"]["refusal"]["reason"] == "finish-incomplete"
    cli_partial = cli("work", "advance", owner, "--no-infer-worktree")
    assert cli_partial["blockers"] == partial["plan"]["blockers"]
    assert cli_partial["changed"] is False
    assert _apply(client, headers, params, partial).status_code == 422
    assert load(owner_page).fm_data()["phase"] == "finish"
    # A new client represents a resumed controller; proof lives in the workspace.
    client.close()
    client, headers = make_client(layout)
    git(repos["ui"], "merge", branch)
    assert run_record_finish(layout, owner, repo_name="ui", today=AT.date()).changed
    complete = client.post(PLAN, json=params, headers=headers).json()
    assert complete["plan"]["refusal"] is None
    assert _apply(client, headers, params, complete).status_code == 200
    assert load(owner_page).fm_data()["phase"] == "done"
    assert load(owner_page).fm_data()["resolved_in"] == git(repos["code"], "rev-parse", "HEAD")
    repeated = client.post(PLAN, json=params, headers=headers).json()
    assert repeated["plan"]["changed"] is False
    client.close()
