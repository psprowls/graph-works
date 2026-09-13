"""Real workspace/CLI profiles through the backend with synthetic Orca receipts."""

import json
import runpy
from dataclasses import fields
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from okf_io import load
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from typer.testing import CliRunner
from workflow_orca import OrcaBackend
from workflow_orca._launch import decode_launch_spec

runner = CliRunner()
REPO = Path(__file__).resolve().parents[3]
FakeOrcaCLI = runpy.run_path(str(REPO / "packages/workflow-orca/tests/fake_orca_cli.py"))["FakeOrcaCLI"]


def invoke(workspace, *args):
    return runner.invoke(app, [*args, "--workspace", str(workspace), "--json"])


def item(workspace, stage):
    result = invoke(
        workspace,
        "work",
        "file",
        "--title",
        "Integration " + stage,
        "--kind",
        "Feature",
        "--summary",
        "Dispatch integration",
    )
    assert result.exit_code == 0, result.output
    path = json.loads(result.stdout)["path"]
    page = workspace / "okf" / (path + ".md")
    document = load(page)
    document.set("affects", ["packages/example"])
    document.set("effort", "small")
    if stage == "execute":
        (workspace / "checkout").mkdir()
        document.set("phase", "execute")
        document.set("work_status", "accepted")
        document.set("spec_doc", path + "/references/01-design.md")
        document.set("plan_doc", path + "/references/02-plan.md")
        document.set("worktree", str(workspace / "checkout"))
        document.set("branch", "feature/integration")
    page.write_text(document.serialize(), encoding="utf-8")
    return path


def rules(workspace):
    manifest = workspace / "workspace.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "workflow:", "workflow:\n  auto_drive: {max_parallel: 3, supervise_merges: false}"
        ),
        encoding="utf-8",
    )
    with manifest.open("a", encoding="utf-8", newline="") as stream:
        stream.write("\nroles: {research: {model_id: preserved-model}}\ncustom: {keep: true}\n")
    (workspace / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n  - name: shared-model\n    match: {}\n"
        "    agent: claude\n    model: provider model/id\n    reasoning_effort: high\n",
        encoding="utf-8",
    )
    (workspace / "dispatch.local.yaml").write_text(
        "pipeline:\n  rules:\n  - name: local-execute\n    match: {stage: execute}\n    agent: codex\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "stage,agent,model,effort",
    [
        ("design", "claude", "provider model/id", "high"),
        ("execute", "codex", None, None),
    ],
)
def test_synced_workspace_cli_profile_reaches_orca_and_durable_receipt(workspace, stage, agent, model, effort):
    rules(workspace)
    path = item(workspace, stage)
    authored = {
        name: (workspace / name).read_bytes() for name in ("workspace.yaml", "dispatch.yaml", "dispatch.local.yaml")
    }
    sync = invoke(workspace, "config", "sync")
    assert sync.exit_code == 0, sync.output
    projection = json.loads((workspace / ".gw/cache/config.json").read_text())
    assert projection["roles"]["research"]["model_id"] == "preserved-model"
    assert projection["custom"] == {"keep": True}
    assert projection["workflow"]["auto_drive"]["max_parallel"] == 3
    assert projection["workflow"]["auto_drive"]["supervise_merges"] is False
    next_result = invoke(workspace, "work", "next", path)
    plan_result = invoke(workspace, "work", "orchestrate", path)
    assert next_result.exit_code == plan_result.exit_code == 0, (next_result.output, plan_result.output)
    next_payload, plan = json.loads(next_result.stdout), json.loads(plan_result.stdout)
    assert plan["dispatches"], json.dumps(plan)
    planned = plan["dispatches"][0]
    profile = next_payload["dispatch"]["profile"]
    for field in ("skill", "mode", "agent", "model", "reasoning_effort"):
        assert profile[field] == planned[field]
    assert (planned["agent"], planned["model"], planned["reasoning_effort"]) == (agent, model, effort)
    assert planned["provenance"] == next_payload["dispatch"]["provenance"]
    assert planned["mode"] == ("attend" if stage == "design" else "autonomous")
    raw = {field.name: planned["path" if field.name == "slug" else field.name] for field in fields(PlannedDispatch)}
    raw["worktree"] = WorktreeAction(**raw["worktree"])
    dispatch = PlannedDispatch(**raw)
    cli = FakeOrcaCLI()
    session = OrcaBackend(run=cli, repo_selector="path:" + str(workspace)).open_session("integration-" + stage)
    record = session.launch(dispatch)
    task = next(iter(cli._tasks.values()))
    request = decode_launch_spec(task["spec"])
    assert request["agent"] == agent and request["model"] == model
    assert request["reasoning_effort"] == effort
    assert task["spec"].split("\n", 1)[1] == planned["prompt"]
    restored = (
        OrcaBackend(run=cli, repo_selector="path:" + str(workspace))
        .open_session("integration-" + stage)
        .describe(dispatch.key)
    )
    assert restored.handle == record.handle
    assert "unverified" not in (restored.detail or "")
    # Durable effective evidence, rather than the initial start result, controls accounting.
    cli._workers[record.handle]["launch"]["effective"]["agent"] = "wrong"
    restored = session.describe(dispatch.key)
    assert "launch.effective.agent" in restored.detail
    assert record.handle in restored.detail and next(iter(cli._tasks)) in restored.detail
    assert {name: (workspace / name).read_bytes() for name in authored} == authored


def test_local_effort_after_agent_switch_blocks_before_any_task(workspace):
    rules(workspace)
    path = item(workspace, "execute")
    with (workspace / "dispatch.local.yaml").open("a", encoding="utf-8", newline="") as stream:
        stream.write("    reasoning_effort: high\n")
    result = invoke(workspace, "work", "orchestrate", path)
    plan = json.loads(result.stdout)
    assert plan["dispatches"] == []
    assert "Set a model or clear reasoning_effort." in result.stdout
    cli = FakeOrcaCLI()
    session = OrcaBackend(run=cli).open_session("blocked")
    for dispatch in plan["dispatches"]:
        session.launch(dispatch)
    assert not any("task-create" in call for call in cli.calls)


def test_legacy_shared_keys_hidden_by_local_nulls_fail_with_shared_filename(workspace):
    manifest = workspace / "workspace.yaml"
    text = manifest.read_text().replace("workflow:", "workflow:\n  pipeline: {}")
    manifest.write_text(text, encoding="utf-8")
    (workspace / "workspace.local.yaml").write_text("workflow: {pipeline: null}\n", encoding="utf-8")
    before = manifest.read_bytes()
    result = invoke(workspace, "config", "sync")
    assert result.exit_code != 0
    assert str(manifest) in result.output and "retired key" in result.output
    assert manifest.read_bytes() == before
