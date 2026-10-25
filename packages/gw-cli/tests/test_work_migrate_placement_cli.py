"""`gw work migrate-placement`: plan JSON, apply, and the in-flight refusal envelope."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from gw_cli import exit_codes
from gw_cli.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _setup(workspace: Path, tmp_path: Path) -> Path:
    repo = tmp_path / "code"
    repo.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
        ("commit", "-q", "--allow-empty", "-m", "Initial"),
    ):
        _git(repo, *args)
    manifest = workspace / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {repo.as_posix()}"
        ),
        encoding="utf-8",
        newline="\n",
    )
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
    ):
        _git(workspace, *args)
    return repo


def _page(workspace: Path, name: str, extra: str, status: str) -> str:
    path = f"work/{name}"
    page = workspace / "okf" / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Feature\ntitle: {name}\ndescription: Migration\nstatus: stable\nwork_status: {status}\n"
        f"phase: {'done' if status == 'resolved' else 'execute'}\neffort: medium\nopened: 2026-10-22\n"
        f"updated: 2026-10-22\nrepo: code\naffects: [src]\n{extra}---\n\n## Summary\nTest\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", f"add {name}")
    return path


def test_migrate_placement_plan_json_golden(workspace: Path, tmp_path: Path) -> None:
    repo = _setup(workspace, tmp_path)
    path = _page(workspace, "feature-a", f"worktree: {repo}\nbranch: main\n", "resolved")
    result = runner.invoke(app, ["work", "migrate-placement", "--workspace", str(workspace), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "mode": "plan",
        "nothing_to_migrate": False,
        "refusal": None,
        "rows": [
            {
                "item": path,
                "repo": "code",
                "worktree": str(repo),
                "branch": "main",
                "action": "strip",
                "reason": "declared-checkout",
            }
        ],
        "blockers": [],
        "orphans": [],
        "workspace_stamps": [],
        "strips": [path],
        "result": None,
    }
    assert "worktree:" in (workspace / "okf" / f"{path}.md").read_text(encoding="utf-8")

    applied = runner.invoke(app, ["work", "migrate-placement", "--apply", "--workspace", str(workspace), "--json"])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["result"]["stripped"] == [path]
    assert "worktree:" not in (workspace / "okf" / f"{path}.md").read_text(encoding="utf-8")
    again = runner.invoke(app, ["work", "migrate-placement", "--workspace", str(workspace)])
    assert again.exit_code == 0 and "nothing to migrate" in again.stdout


def test_migrate_placement_refusal_exit_code(workspace: Path, tmp_path: Path) -> None:
    repo = _setup(workspace, tmp_path)
    wt = tmp_path / "wts" / "orca"
    _git(repo, "worktree", "add", "-q", "-b", "orca/reader", str(wt))
    path = _page(workspace, "feature-a", f"worktree: {wt}\nbranch: orca/reader\n", "open")
    result = runner.invoke(app, ["work", "migrate-placement", "--apply", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.GENERIC
    envelope = json.loads(result.stdout)["error"]
    assert envelope["reason"] == "refused"
    assert envelope["payload"]["blockers"][0]["item"] == path
    assert envelope["payload"]["refusal"] == "legacy-in-flight"
    assert wt.is_dir()
