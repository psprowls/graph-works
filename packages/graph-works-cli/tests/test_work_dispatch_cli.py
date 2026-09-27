"""CLI refusal envelopes and reroute-to-dispatch behavior against real journals."""

from __future__ import annotations

import json
import runpy
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.dispatch import encode_launch_envelope
from subagents_io.backend import BackendError
from typer.testing import CliRunner
from workflow_orca._launch import decode_launch_spec

REPO = Path(__file__).resolve().parents[3]
FakeOrcaPort = runpy.run_path(str(REPO / "packages/graph-works-core/tests/orchestrate/fake_orca_port.py"))[
    "FakeOrcaPort"
]
KEY = "gw-execute-x-1a2b"
runner = CliRunner()


@pytest.fixture
def dispatch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, object]:
    repo, wt = tmp_path / "repo", tmp_path / "wt"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "--allow-empty", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feature/x", str(wt)], check=True, capture_output=True
    )
    layout = apply_init(plan_init(tmp_path / ".works", today=main._today(), topic="Dispatch")).layout
    manifest = layout.manifest_path
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "repositories: {}", f'repositories:\n  graph-works:\n    path: "{repo}"'
        ),
        encoding="utf-8",
        newline="\n",
    )
    page = layout.bundle_dir / "work/x.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: X\ndescription: d\nstatus: stable\n"
        "work_status: in-progress\nphase: execute\neffort: medium\n"
        "opened: 2026-09-26\nupdated: 2026-09-26\naffects: []\n---\n\n## Summary\nd\n",
        encoding="utf-8",
        newline="\n",
    )
    plan = {
        "path": "work/x",
        "dispatches": [
            {
                "key": KEY,
                "path": "work/x",
                "phase": "execute",
                "mode": "autonomous",
                "agent": "claude",
                "model": "opus",
                "reasoning_effort": None,
                "prompt": "Run /gw:workflow work/x.\n",
                "worktree": {
                    "action": "create-top-level",
                    "path": None,
                    "branch": "feature/x",
                    "base_branch": "main",
                    "exists": False,
                    "parent_path": None,
                },
                "repo": {"name": "graph-works", "path": str(repo), "source": "manifest"},
            }
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8", newline="\n")
    port = FakeOrcaPort(repos=[{"id": "repo1", "path": str(repo)}])
    port.worktrees["id:wt1"] = {
        "id": "wt1",
        "repo_id": "repo1",
        "path": str(wt),
        "branch": "feature/x",
        "display_name": "feature/x",
        "is_main": False,
        "parent_id": None,
    }
    monkeypatch.setattr(main, "orca_port", lambda: port)
    return layout.root, plan_path, port


def invoke(root: Path, *args: str):
    return runner.invoke(app, ["work", *args, "--workspace", str(root), "--json"])


def refused(result, *, reason: str | None = None) -> dict:
    assert result.exit_code != 0, result.output
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    if reason is not None:
        assert doc["error"]["payload"]["failure"]["reason"] == reason
        assert doc["error"]["payload"]["ok"] is False
    return doc


def test_dispatch_happy_path_emits_the_envelope(dispatch_env) -> None:
    root, plan, _port = dispatch_env
    result = invoke(root, "dispatch", KEY, "--plan", str(plan), "--run", "run_1", "--no-probe")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["status"] == "dispatched" and "error" not in doc and doc["failure"] is None


def test_dispatch_reads_saved_plan_from_stdin(dispatch_env) -> None:
    root, plan, _port = dispatch_env
    result = runner.invoke(
        app,
        ["work", "dispatch", KEY, "--plan", "-", "--run", "run_1", "--no-probe", "--workspace", str(root), "--json"],
        input=plan.read_text(encoding="utf-8"),
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "dispatched"


@pytest.mark.parametrize(
    "case,reason",
    [
        ("key-not-in-plan", "key-not-in-plan"),
        ("plan-invalid", "plan-invalid"),
        ("task-create-failed", "task-create-failed"),
        ("launch-failed", "launch-failed"),
        ("receipt-mismatch", "receipt-mismatch"),
        ("outcome-unknown", "outcome-unknown"),
        ("unsent", "unsent"),
        ("task-update-failed", "task-update-failed"),
        ("recovery-inspection", "recovery-inspection"),
        ("record-invalid", "record-invalid"),
    ],
)
def test_dispatch_step_failure_emits_one_parseable_document(dispatch_env, case: str, reason: str) -> None:
    root, plan_path, port = dispatch_env
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if case == "key-not-in-plan":
        plan["dispatches"] = []
    elif case == "plan-invalid":
        plan["dispatches"][0].pop("prompt")
    elif case == "task-create-failed":
        port.fail["task_create"] = BackendError("unavailable")
    elif case == "launch-failed":
        port.fail["worker_start"] = BackendError("unavailable")
    elif case == "receipt-mismatch":
        port.start["receipt_problem"] = "wrong model"
    elif case == "outcome-unknown":
        port.start["state"] = "outcome_unknown"
    elif case == "unsent":
        port.reads = [{"source": "transcript", "message_count": 0}]
    elif case == "task-update-failed":
        port.fail["task_update"] = BackendError("unavailable")
    elif case == "recovery-inspection":
        port.fail["task_list"] = BackendError("unavailable")
    else:
        record = root / "okf/work/x/references/orca-dispatch" / f"{KEY}.json"
        record.parent.mkdir(parents=True)
        record.write_text("garbage", encoding="utf-8", newline="\n")
    plan_path.write_text(json.dumps(plan), encoding="utf-8", newline="\n")
    refused(
        invoke(root, "dispatch", KEY, "--plan", str(plan_path), "--run", "run_1", "--settle-seconds", "0"),
        reason=reason,
    )


@pytest.mark.parametrize("case", ["workspace", "missing-plan", "invalid-json", "invalid-utf8"])
def test_dispatch_pre_core_failures_emit_one_parseable_document(dispatch_env, case: str) -> None:
    root, plan, _port = dispatch_env
    if case == "workspace":
        root = root / "missing"
    elif case == "missing-plan":
        plan = plan.with_name("missing.json")
    elif case == "invalid-json":
        plan.write_text("{", encoding="utf-8", newline="\n")
    else:
        plan.write_bytes(b"\xff")
    refused(invoke(root, "dispatch", KEY, "--plan", str(plan), "--run", "run_1"))


def test_orchestrate_missing_workspace_emits_envelope(tmp_path: Path) -> None:
    doc = refused(invoke(tmp_path / "nowhere", "orchestrate", "work/x"))
    assert doc["error"]["reason"] == "workspace"


def test_orchestrate_multi_repo_without_repo_name_emits_envelope(dispatch_env, tmp_path: Path) -> None:
    root, _plan, _port = dispatch_env
    other = tmp_path / "other"
    other.mkdir()
    manifest = root / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  graph-works:\n", f'  other:\n    path: "{other}"\n  graph-works:\n'
        ),
        encoding="utf-8",
        newline="\n",
    )
    doc = refused(invoke(root, "orchestrate", "work/x"))
    assert doc["error"]["reason"] == "workspace"


def test_orchestrate_unknown_live_key_emits_envelope(dispatch_env) -> None:
    root, _plan, _port = dispatch_env
    doc = refused(invoke(root, "orchestrate", "work/x", "--live", "gw-unknown"))
    assert doc["error"]["reason"] == "unresolved"


def test_orchestrate_unknown_path_remains_a_blocked_plan(dispatch_env) -> None:
    root, _plan, _port = dispatch_env
    result = invoke(root, "orchestrate", "work/unknown")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["blocked"][0]["kind"] == "invalid" and "error" not in doc


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory permissions")
def test_orchestrate_unreadable_bundle_root_emits_io_envelope(dispatch_env) -> None:
    root, _plan, _port = dispatch_env
    bundle_root = root / "okf"
    original_mode = stat.S_IMODE(bundle_root.stat().st_mode)
    bundle_root.chmod(0o000)
    try:
        try:
            list(bundle_root.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("directory permissions cannot be enforced on this host")
        doc = refused(invoke(root, "orchestrate", "work/x"))
        assert doc["error"]["reason"] == "io"
        assert str(bundle_root) in doc["error"]["message"]
    finally:
        bundle_root.chmod(original_mode)


def test_orchestrate_core_io_exception_maps_to_envelope(dispatch_env, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _plan, _port = dispatch_env

    def unreadable(*args: object, **kwargs: object) -> None:
        raise OSError("bundle unreadable")

    monkeypatch.setattr(main, "run_orchestrate", unreadable)
    doc = refused(invoke(root, "orchestrate", "work/x"))
    assert doc["error"]["reason"] == "io"


@pytest.mark.parametrize(
    "case,reason",
    [
        ("reason-missing", "reason-missing"),
        ("no-task", "no-task"),
        ("reroute-live", "reroute-live"),
        ("override-invalid", "override-invalid"),
    ],
)
def test_reroute_failure_emits_one_parseable_document(dispatch_env, case: str, reason: str) -> None:
    root, _plan, port = dispatch_env
    args = ["reroute", KEY, "--run", "run_1", "--reason", "stall"]
    if case != "no-task":
        port.tasks = [
            {
                "id": "task_1",
                "title": KEY,
                "display_name": "work/x · execute",
                "status": "failed",
                "spec": encode_launch_envelope(
                    key=KEY,
                    agent="claude",
                    model="opus",
                    reasoning_effort=None,
                    placement_argv=["--worktree", "path:/old"],
                    mode="autonomous",
                    worktree_path=None,
                    prompt="go\n",
                ),
            }
        ]
    if case == "reason-missing":
        args[-1] = " "
    elif case == "reroute-live":
        port.workers = [
            {
                "dispatch_id": "ctx_old",
                "task_id": "task_1",
                "state": "running",
                "dispatch_status": "dispatched",
                "worktree_id": "wt1",
            }
        ]
    elif case == "override-invalid":
        args += ["--agent", "codex", "--effort", "high"]
    refused(invoke(root, *args), reason=reason)


def test_reroute_then_dispatch_creates_the_overridden_task(dispatch_env) -> None:
    root, plan, port = dispatch_env
    port.tasks = [
        {
            "id": "task_1",
            "title": KEY,
            "display_name": "work/x · execute",
            "status": "failed",
            "spec": encode_launch_envelope(
                key=KEY,
                agent="claude",
                model="opus",
                reasoning_effort=None,
                placement_argv=["--worktree", "path:/old"],
                mode="autonomous",
                worktree_path=None,
                prompt="go\n",
            ),
        }
    ]
    port.workers = [
        {
            "dispatch_id": "ctx_old",
            "task_id": "task_1",
            "state": "failed",
            "dispatch_status": "failed",
            "worktree_id": "wt1",
        }
    ]
    port.next_task = 2
    rerouted = invoke(root, "reroute", KEY, "--run", "run_1", "--reason", "stall", "--agent", "codex")
    assert rerouted.exit_code == 0, rerouted.output
    dispatched = invoke(root, "dispatch", KEY, "--plan", str(plan), "--run", "run_1", "--no-probe")
    assert dispatched.exit_code == 0, dispatched.output
    spec = next(kwargs["spec"] for name, _args, kwargs in port.calls if name == "task_create")
    assert decode_launch_spec(spec)["agent"] == "codex"


@pytest.mark.parametrize(
    "method,payload",
    [
        ("worker_show", {"dispatch": {}}),
        ("worker_show", {"dispatch": {"lastHeartbeatAt": 0}}),
        ("worker_show", {"dispatch": {"lastHeartbeatAt": ""}}),
        ("worker_show", {"dispatch": {"lastHeartbeatAt": []}}),
        ("worker_show", {"dispatch": {"lastHeartbeatAt": None, "last_heartbeat_at": 0}}),
        ("worker_read", {"source": "transcript"}),
        ("worker_read", {"source": "transcript", "transcript": {}}),
        ("worker_read", {"source": "transcript", "transcript": {"messages": None}}),
        ("worker_read", {"source": "transcript", "transcript": {"messages": ""}}),
        ("worker_read", {"source": "transcript", "transcript": {"messages": {}}}),
        ("worker_read", {"source": "transcript", "transcript": {"messages": [None]}}),
    ],
)
def test_real_adapter_incomplete_probe_never_sends_enter(dispatch_env, monkeypatch, method, payload):
    from workflow_orca._cli import OrcaResult
    from workflow_orca.port import OrcaCliPort

    root, plan, port = dispatch_env
    adapter = OrcaCliPort(run=lambda argv: OrcaResult(0, json.dumps({"ok": True, "result": payload}), ""))
    monkeypatch.setattr(port, method, getattr(adapter, method))
    port.reads = [{"source": "transcript", "message_count": 0}]
    result = invoke(root, "dispatch", KEY, "--plan", str(plan), "--run", "run_1", "--settle-seconds", "0")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["probe"] == "inconclusive"
    assert "terminal_send_enter" not in port.names()


def test_existing_legacy_task_without_placement_has_honest_human_output(dispatch_env):
    root, plan, port = dispatch_env
    port.tasks = [
        {
            "id": "task_legacy",
            "title": KEY,
            "display_name": "work/x · execute",
            "status": "dispatched",
            "spec": "legacy",
        }
    ]
    result = runner.invoke(
        app, ["work", "dispatch", KEY, "--plan", str(plan), "--run", "run_1", "--workspace", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert "existing task_legacy" in result.stdout
    assert "placement was not read back" in result.stdout
    assert "None" not in result.stdout and "dispatched" not in result.stdout


@pytest.mark.parametrize("worktree_path", [None, "/a path"])
def test_core_v2_encoder_reaches_actual_claude_hook(worktree_path):
    guard = runpy.run_path(str(REPO / "plugins/gw/tests/hooks/test-dispatch-prompt-guard.py"))
    case = guard["GuardTest"]()
    case.setUp()
    try:
        full = guard["PREAMBLE"] + encode_launch_envelope(
            key=KEY,
            agent="claude",
            model="opus",
            reasoning_effort=None,
            placement_argv=["--worktree", "path:/a path"],
            mode="attend",
            worktree_path=worktree_path,
            prompt="Return café and the exact result.\n",
        )
        case.data["result"]["preamble"] = full
        assert case.run_guard(full) is None
        case.assert_recovery(case.run_guard(full[:1016]), full)
        case.assert_recovery(case.run_guard(full[:-10]), full)
        case.assert_calls(3)
    finally:
        case.doCleanups()
