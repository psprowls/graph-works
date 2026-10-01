"""The integrate and accept-integration surfaces delegate to real temporary Git operations."""

import json
import subprocess
from datetime import date

from graph_works_cli.cli import app
from graph_works_core import apply_init, plan_init
from typer.testing import CliRunner

runner = CliRunner()
OWNER = "work/feature-x"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def setup(tmp_path):
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    for key, value in (("user.name", "Test"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    (repo / "a.txt").write_text("base\n", encoding="utf-8", newline="\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "base")
    source = tmp_path / "code-source"
    git(repo, "worktree", "add", "-b", "feature", str(source))
    (source / "b.txt").write_text("feature\n", encoding="utf-8", newline="\n")
    git(source, "add", "b.txt")
    git(source, "commit", "-m", "source")
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 28), topic="Integrate")).layout
    layout.manifest_path.write_text(
        f"version: 1\nworkflow: {{dispatch_rules: dispatch.yaml}}\nrepositories:\n  code:\n    path: {repo}\n",
        encoding="utf-8",
        newline="\n",
    )
    page = layout.bundle_dir / f"{OWNER}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: Example feature\ndescription: d\nstatus: stable\nwork_status: in-progress\n"
        "phase: finish\neffort: medium\nopened: 2026-09-28\nupdated: 2026-09-28\n"
        f"repo: code\nworktree: {source}\nbranch: feature\naffects: []\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    return layout, repo


def test_integrate_plan_apply_replay_and_refusals(tmp_path):
    layout, repo = setup(tmp_path)
    args = ["work", "integrate", OWNER, "--repo", "code", "--workspace", str(layout.root)]

    planned = runner.invoke(app, [*args, "--json"])
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.stdout)
    assert (plan["applied"], plan["outcome"], plan["strategy"], plan["strategy_source"]) == (
        False,
        "planned",
        "squash",
        "default",
    )
    human = runner.invoke(app, args)
    assert human.exit_code == 0 and "would integrate feature into main in code (squash, from default)" in human.stdout

    applied = runner.invoke(app, [*args, "--strategy", "merge", "--apply", "--json"])
    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.stdout)
    assert payload["outcome"] == "integrated" and payload["result_commit"] == git(repo, "rev-parse", "HEAD")
    assert payload["receipt_path"] and payload["conflicts"] == []

    replay = runner.invoke(app, [*args, "--apply"])
    assert replay.exit_code == 0 and "already integrated" in replay.stdout

    bad = runner.invoke(app, [*args, "--strategy", "rebase"])
    assert bad.exit_code != 0 and "squash, merge or ff" in bad.output

    refused = runner.invoke(
        app, ["work", "integrate", OWNER, "--repo", "_workspace", "--workspace", str(layout.root), "--json"]
    )
    assert refused.exit_code != 0
    error = json.loads(refused.stdout)["error"]
    assert (error["reason"], error["payload"]["refusal"]["reason"]) == ("refused", "workspace-target")


def test_integrate_reports_workspace_and_io_errors(tmp_path, monkeypatch):
    from graph_works_cli.work_cli import main as work_main
    from graph_works_core.workspace.errors import WorkspaceError

    layout, _repo = setup(tmp_path)
    args = ["work", "integrate", OWNER, "--repo", "code", "--workspace", str(layout.root)]
    for exc, reason in ((WorkspaceError("boom"), "workspace"), (OSError("disk"), "io")):

        def raiser(*_a, _exc=exc, **_k):
            raise _exc

        monkeypatch.setattr(work_main, "run_integrate", raiser)
        result = runner.invoke(app, [*args, "--json"])
        assert result.exit_code != 0
        assert json.loads(result.stdout)["error"]["reason"] == reason


def test_accept_integration_plan_apply_and_refusal(tmp_path):
    layout, repo = setup(tmp_path)
    git(repo, "merge", "--squash", "feature")
    (repo / "b.txt").write_text("edited in review\n", encoding="utf-8", newline="\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-m", "squash with review edits")
    sha = git(repo, "rev-parse", "HEAD")
    args = ["work", "accept-integration", OWNER, "--repo", "code", "--evidence", sha, "--workspace", str(layout.root)]

    missing = runner.invoke(app, [*args, "--json"])
    assert missing.exit_code != 0
    assert json.loads(missing.stdout)["error"]["payload"]["refusal"]["reason"] == "missing-attribution"

    attributed = [*args, "--reason", "squashed with review edits", "--by", "pat"]
    planned = runner.invoke(app, attributed)
    assert planned.exit_code == 0 and "would accept" in planned.stdout

    applied = runner.invoke(app, [*attributed, "--apply", "--json"])
    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.stdout)
    assert payload["applied"] and payload["evidence"] == sha and payload["decision_id"] and payload["receipt_path"]


def test_accept_integration_reports_workspace_and_io_errors(tmp_path, monkeypatch):
    from graph_works_cli.work_cli import main as work_main
    from graph_works_core.workspace.errors import WorkspaceError

    layout, _repo = setup(tmp_path)
    args = ["work", "accept-integration", OWNER, "--repo", "code", "--evidence", "abc", "--workspace", str(layout.root)]
    for exc, reason in ((WorkspaceError("boom"), "workspace"), (OSError("disk"), "io")):

        def raiser(*_a, _exc=exc, **_k):
            raise _exc

        monkeypatch.setattr(work_main, "run_accept_integration", raiser)
        result = runner.invoke(app, [*args, "--json"])
        assert result.exit_code != 0
        assert json.loads(result.stdout)["error"]["reason"] == reason
