"""The merge-workspace surface delegates to real temporary Git operations."""

import json
import subprocess
from datetime import date

from graph_works_cli.cli import app
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from typer.testing import CliRunner

runner = CliRunner()


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_merge_workspace_plan_apply_replay_and_refusal(tmp_path):
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 27), topic="Merge")).layout
    path = "work/feature-a"
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: A\ndescription: d\nstatus: stable\nwork_status: in-progress\n"
        "phase: finish\neffort: medium\nowner: pat\nopened: 2026-09-27\nupdated: 2026-09-27\naffects: []\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    git(layout.root, "init", "-b", "main")
    git(layout.root, "config", "user.name", "Test")
    git(layout.root, "config", "user.email", "test@example.invalid")
    git(layout.root, "config", "commit.gpgsign", "false")
    git(layout.root, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    git(layout.root, "add", ".")
    git(layout.root, "commit", "-m", "seed")
    prepared = run_prepare_workspace(layout, path, today=date(2026, 9, 27), apply=True)
    assert prepared.refusal is None
    git(prepared.steps[-1].worktree, "commit", "--allow-empty", "-m", "source")
    args = ["work", "merge-workspace", path, "--workspace", str(layout.root)]
    planned = runner.invoke(app, [*args, "--json"])
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.stdout)["applied"] is False
    human = runner.invoke(app, args)
    assert human.exit_code == 0 and "would merge" in human.stdout
    applied = runner.invoke(app, [*args, "--apply", "--json"])
    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.stdout)
    assert payload["applied"] and payload["merge_commit"] and payload["receipt_path"]
    replay = runner.invoke(app, [*args, "--apply"])
    assert replay.exit_code == 0 and "merged" in replay.stdout
    git(layout.root, "checkout", "-b", "elsewhere")
    refusal = runner.invoke(app, [*args, "--apply", "--json"])
    assert refusal.exit_code != 0
    error = json.loads(refusal.stdout)["error"]
    assert error["reason"] == "refused"
    assert error["payload"]["refusal"]["reason"] == "wrong-checkout"
