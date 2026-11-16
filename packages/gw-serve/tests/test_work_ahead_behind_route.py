"""`GET /v1/work/ahead-behind`: live, uncached counts against each placed item's merge target."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import write_item
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_pipeline.placement import branch_name
from gw_wire import work as wire_work
from gw_work import commands as work
from gw_work.ahead_behind import run_work_ahead_behind
from starlette.testclient import TestClient


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(root: Path, name: str) -> None:
    (root / f"{name}.txt").write_text(name, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", name)


@pytest.fixture
def code_repo(workspace: WorkspaceLayout, tmp_path: Path) -> Path:
    code = tmp_path / "code"
    code.mkdir()
    _git(code, "init", "-b", "main")
    _commit(code, "base")
    head = workspace.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    workspace.manifest_path.write_text(
        f'{head}repositories:\n  "code":\n    path: "{code.as_posix()}"\n', encoding="utf-8", newline="\n"
    )
    for slug in ("a", "b"):
        write_item(workspace, slug, extra_fm="repo: code\n")
    _git(code, "checkout", "-b", branch_name("work/a", "Feature"))
    _commit(code, "a1")
    _git(code, "checkout", "main")
    return code


def test_route_is_the_projection(client: TestClient, workspace: WorkspaceLayout, code_repo: Path) -> None:
    body = client.get("/v1/work/ahead-behind").json()
    assert body == wire_work.work_ahead_behind_payload(run_work_ahead_behind(workspace, work.run_work_list(workspace)))
    by_path = {row["path"]: row for row in body["items"]}
    assert (by_path["work/a"]["ahead"], by_path["work/a"]["behind"], by_path["work/a"]["status"]) == (1, 0, "ok")
    assert by_path["work/b"]["status"] == "branch-missing"
    assert by_path["work/b"]["merge_target"] == "main"


def test_path_param_narrows(client: TestClient, code_repo: Path) -> None:
    assert [r["path"] for r in client.get("/v1/work/ahead-behind?path=work/a").json()["items"]] == ["work/a"]
    response = client.get("/v1/work/ahead-behind?path=work/nope")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_counts_are_live_across_requests(client: TestClient, code_repo: Path) -> None:
    first = client.get("/v1/work/ahead-behind?path=work/a")
    _git(code_repo, "checkout", branch_name("work/a", "Feature"))
    _commit(code_repo, "a2")
    _git(code_repo, "checkout", "main")
    second = client.get("/v1/work/ahead-behind?path=work/a")
    assert second.json()["items"][0]["ahead"] == first.json()["items"][0]["ahead"] + 1
    assert second.headers["X-GW-Generation"] == first.headers["X-GW-Generation"]
