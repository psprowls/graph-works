"""Citation and excerpt reads keep core projections and refusal payloads intact."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import PORT, TOKEN
from graph_works_core.code_read import run_code_excerpt
from graph_works_core.wiki_page.citations import run_wiki_citations
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.app import build_app
from graph_works_serve.context import ServeContext
from graph_works_wire import code as wire_code
from graph_works_wire import wiki as wire_wiki
from starlette.testclient import TestClient


@pytest.fixture
def code_repo(workspace: WorkspaceLayout, tmp_path: Path) -> Path:
    root = tmp_path / "code"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("".join(f"a{n}\n" for n in range(1, 21)), encoding="utf-8", newline="\n")
    (root / "b.py").write_text("b\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    (root / "untracked.py").write_text("u\n", encoding="utf-8", newline="\n")
    head = workspace.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    workspace.manifest_path.write_text(
        f'{head}repositories:\n  "code":\n    path: "{root.as_posix()}"\n', encoding="utf-8", newline="\n"
    )
    concepts = workspace.bundle_dir / "concepts"
    concepts.mkdir(exist_ok=True)
    (concepts / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\n\nSee `src/a.py:3-4`, `a.py:9` and `nope.py:1`.\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_citations_is_the_projection(client: TestClient, workspace: WorkspaceLayout, code_repo: Path) -> None:
    """Removing the route or corrupting its output must fail this core-projection boundary."""
    response = client.get("/v1/wiki/citations?id=concepts/a")

    assert response.status_code == 200
    body = response.json()
    assert body == wire_wiki.citations_payload(run_wiki_citations(workspace, "concepts/a"))
    assert [(c["status"], c["path"], c["line"]) for c in body["citations"]] == [
        ("resolved", "src/a.py", 1),
        ("resolved", "src/a.py", 1),
        ("missing", None, 1),
    ]


def test_citations_unknown_page_is_404_with_payload(client: TestClient) -> None:
    """An absent page must retain its citation refusal payload in the 404 envelope."""
    response = client.get("/v1/wiki/citations?id=concepts/nope")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"] == {"id": "concepts/nope", "citations": [], "refusal": "unknown-page"}


def test_excerpt_is_the_projection(client: TestClient, workspace: WorkspaceLayout, code_repo: Path) -> None:
    """Excerpt output must be the wire projection of the core read, including its context."""
    response = client.get("/v1/code/excerpt?repo=code&path=src/a.py&start=8&end=9")

    assert response.status_code == 200
    body = response.json()
    assert body == wire_code.excerpt_payload(run_code_excerpt(workspace, "code", "src/a.py", 8, 9))
    assert (body["first"], body["last"], body["language"]) == (3, 14, "python")


@pytest.mark.parametrize(
    ("query", "status", "reason", "refusal"),
    [
        ("repo=nope&path=b.py&start=1", 404, "unresolved", "unknown-repository"),
        ("repo=code&path=untracked.py&start=1", 404, "unresolved", "unknown-file"),
        ("repo=code&path=../x.py&start=1", 403, "refused", "outside-repository"),
    ],
)
def test_excerpt_refusals_carry_status_and_payload(
    client: TestClient, code_repo: Path, query: str, status: int, reason: str, refusal: str
) -> None:
    """Known core refusal variants must remain distinguishable HTTP failures."""
    response = client.get(f"/v1/code/excerpt?{query}")

    assert response.status_code == status
    error = response.json()["error"]
    assert error["reason"] == reason
    assert error["payload"]["refusal"] == refusal
    assert error["message"].startswith(f"{refusal}: ")


def test_excerpt_out_of_range_is_a_200_body(client: TestClient, code_repo: Path) -> None:
    """An in-range request shape with no matching lines is a useful successful read."""
    response = client.get("/v1/code/excerpt?repo=code&path=b.py&start=5")

    assert response.status_code == 200
    body = response.json()
    assert (body["refusal"], body["total_lines"], body["lines"], body["first"], body["last"]) == (
        "out-of-range",
        1,
        [],
        None,
        None,
    )


@pytest.mark.parametrize(
    "query",
    ["repo=code&path=b.py&start=0", "repo=code&path=b.py&start=3&end=2", "repo=code&path=b.py", "path=b.py&start=1"],
)
def test_excerpt_bad_bounds_and_missing_params_are_usage_400(client: TestClient, code_repo: Path, query: str) -> None:
    """Malformed excerpt bounds and missing identity fields must stop at parameter parsing."""
    response = client.get(f"/v1/code/excerpt?{query}")

    assert response.status_code == 400
    assert response.json()["error"]["reason"] == "usage"


@pytest.mark.parametrize("url", ["/v1/wiki/citations?id=concepts/a", "/v1/code/excerpt?repo=code&path=b.py&start=1"])
def test_both_routes_are_guarded(context: ServeContext, url: str) -> None:
    """New read routes must remain behind the same loopback token and host guard."""
    bare = TestClient(build_app(context, token=TOKEN), base_url=f"http://127.0.0.1:{PORT}")
    assert bare.get(url).status_code == 401
    assert bare.get(url, headers={"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"}).status_code == 403


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("parent_alias", [False, True], ids=["file-alias", "parent-alias"])
@pytest.mark.parametrize("policy", ["allowed", "untracked", "ignored", "ignored-alias", "outside"])
def test_excerpt_alias_obeys_file_policy(
    client: TestClient, workspace: WorkspaceLayout, code_repo: Path, parent_alias: bool, policy: str
) -> None:
    """A tracked alias must never serve excluded targets, even through a replaced parent."""
    target = code_repo / "target" / "value.py"
    target.parent.mkdir()
    target.write_text("SYNTHETIC_TARGET\n", encoding="utf-8", newline="\n")
    alias = "alias/value.py" if parent_alias else "alias.py"
    if parent_alias:
        (code_repo / "alias").mkdir()
        (code_repo / alias).write_text("original\n", encoding="utf-8", newline="\n")
    else:
        (code_repo / alias).symlink_to("target/value.py")
    subprocess.run(["git", "add", "--", "alias" if parent_alias else alias], cwd=code_repo, check=True)
    if policy != "untracked":
        subprocess.run(["git", "add", "--", "target"], cwd=code_repo, check=True)
    if policy == "outside":
        outside = code_repo.parent / "outside"
        target.parent.rename(outside)
        target = outside / "value.py"
    if parent_alias:
        (code_repo / alias).unlink()
        (code_repo / "alias").rmdir()
        (code_repo / "alias").symlink_to(target.parent, target_is_directory=True)
    elif policy == "outside":
        (code_repo / alias).unlink()
        (code_repo / alias).symlink_to(target)
    if policy in {"ignored", "ignored-alias", "outside"}:
        ignored = "target/**" if policy == "ignored" else alias
        with workspace.manifest_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f'    ignore: ["{ignored}"]\n')

    response = client.get("/v1/code/excerpt", params={"repo": "code", "path": alias, "start": 1})

    if policy == "allowed":
        assert response.status_code == 200
        assert response.json()["lines"] == ["SYNTHETIC_TARGET"]
        assert response.json()["refusal"] is None
    else:
        assert response.status_code == (403 if policy == "outside" else 404)
        error = response.json()["error"]
        assert error["reason"] == ("refused" if policy == "outside" else "unresolved")
        assert error["payload"]["refusal"] == ("outside-repository" if policy == "outside" else "unknown-file")
        assert error["payload"]["lines"] == []
        assert "SYNTHETIC_TARGET" not in response.text
