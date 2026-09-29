"""`gw work gate run | wait | check`: routing, exit codes, refusal envelope, boundary."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import gate as gate_cli
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.gate import GateWaitResult
from graph_works_core.workspace.errors import WorkspaceError
from typer.testing import CliRunner

runner = CliRunner()
ITEM = "work/feature-a"
MANIFEST = "version: 1\nrepositories:\n  code:\n    path: ../code\n    gate:\n      full: 'true'\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@dataclass
class Env:
    root: Path
    repo: Path
    path: str


@pytest.fixture
def env(tmp_path: Path) -> Env:
    root = tmp_path / "workspace"
    root.mkdir()
    layout = apply_init(plan_init(root, today=date(2026, 9, 28), topic="Gate")).layout
    repo = tmp_path / "code"
    (repo / "packages/a").mkdir(parents=True)
    (repo / "packages/a/x.py").write_text("x", encoding="utf-8", newline="\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (root / "workspace.yaml").write_text(MANIFEST, encoding="utf-8", newline="\n")
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    page = (
        "---\ntype: Feature\ntitle: A\nwork_status: in-progress\nphase: execute\nowner: someone\nrepo: code\n"
        f"worktree: {repo}\naffects:\n  - packages/a\n---\n\nBody.\n"
    )
    (layout.bundle_dir / f"{ITEM}.md").write_text(page, encoding="utf-8", newline="\n")
    return Env(root, repo, ITEM)


def invoke(args: list[str]):
    return runner.invoke(app, args)


def wait_result(status: str, exit_value: int | None) -> GateWaitResult:
    return GateWaitResult(status, None, "", "rid", exit_value, True, "/log", "tail", None)  # type: ignore[arg-type]


def test_gate_run_json_started(env, monkeypatch):
    monkeypatch.setattr(gate_cli, "spawn_runner", lambda record: None)
    out = invoke(["work", "gate", "run", env.path, "--workspace", str(env.root), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout)["status"] == "started"


def test_gate_run_text_started(env, monkeypatch):
    monkeypatch.setattr(gate_cli, "spawn_runner", lambda record: None)
    out = invoke(["work", "gate", "run", env.path, "--workspace", str(env.root)])
    assert out.exit_code == 0 and out.stdout.startswith("started ") and ": true (log: " in out.stdout


def test_gate_run_refusal_exits_3_with_envelope(env):
    (env.repo / "packages/a/dirt.py").write_text("x", encoding="utf-8", newline="\n")
    out = invoke(["work", "gate", "run", env.path, "--workspace", str(env.root), "--json"])
    assert out.exit_code == 3
    error = json.loads(out.stdout)["error"]
    assert error["reason"] == "refused" and error["payload"]["refusal"]["reason"] == "dirty-tree"


def test_gate_run_rejects_unknown_scope(env):
    out = invoke(["work", "gate", "run", env.path, "--scope", "bogus", "--workspace", str(env.root)])
    assert out.exit_code != 0


@pytest.mark.parametrize(
    ("status", "exit_value", "code"),
    [("finished", 0, 0), ("finished", 1, 1), ("running", None, 2), ("orphaned", None, 3)],
)
def test_gate_wait_exit_codes(env, monkeypatch, status, exit_value, code):
    monkeypatch.setattr(gate_cli, "run_gate_wait", lambda *a, **k: wait_result(status, exit_value))
    out = invoke(["work", "gate", "wait", env.path, "--workspace", str(env.root), "--json"])
    assert out.exit_code == code, out.output
    assert json.loads(out.stdout)["status"] == status


def test_gate_wait_red_text_prints_tail(env, monkeypatch):
    monkeypatch.setattr(gate_cli, "run_gate_wait", lambda *a, **k: wait_result("finished", 1))
    out = invoke(["work", "gate", "wait", env.path, "--workspace", str(env.root)])
    assert "finished exit 1 (recorded) log: /log" in out.stdout and "tail" in out.stdout


def test_gate_wait_without_a_run_refuses_3(env):
    out = invoke(["work", "gate", "wait", env.path, "--workspace", str(env.root), "--json"])
    assert out.exit_code == 3 and json.loads(out.stdout)["error"]["reason"] == "refused"


def test_gate_wait_default_timeout_is_540(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(gate_cli, "run_gate_wait", lambda *a, **k: seen.update(k) or wait_result("running", None))
    invoke(["work", "gate", "wait", env.path, "--workspace", str(env.root)])
    assert seen["timeout"] == 540 and seen["run_id"] is None


def test_gate_wait_passes_run_and_timeout(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(gate_cli, "run_gate_wait", lambda *a, **k: seen.update(k) or wait_result("running", None))
    invoke(["work", "gate", "wait", env.path, "--run", "r1", "--timeout", "5", "--workspace", str(env.root)])
    assert seen["timeout"] == 5 and seen["run_id"] == "r1"


def test_gate_check_unsatisfied_exits_1(env):
    out = invoke(["work", "gate", "check", env.path, "--workspace", str(env.root), "--json"])
    assert out.exit_code == 1 and json.loads(out.stdout)["reason"] == "no receipt"
    text = invoke(["work", "gate", "check", env.path, "--workspace", str(env.root)])
    assert text.stdout.startswith("unsatisfied: no receipt (tree ")


def test_gate_check_refusal_exits_3(env):
    out = invoke(["work", "gate", "check", "work/missing", "--workspace", str(env.root), "--json"])
    assert out.exit_code == 3 and json.loads(out.stdout)["error"]["payload"]["refusal"]["reason"] == "unknown-item"


def test_gate_module_imports_no_work_tracker_okf():
    source = Path(gate_cli.__file__).read_text(encoding="utf-8")
    assert "work_tracker_okf" not in source


def _raiser(exc: Exception):
    def raise_(*args, **kwargs):
        raise exc

    return raise_


@pytest.mark.parametrize(
    ("verb", "attr", "extra"),
    [("run", "run_gate_run", []), ("wait", "run_gate_wait", []), ("check", "run_gate_check", [])],
)
@pytest.mark.parametrize(
    ("exc", "reason", "code"),
    [(WorkspaceError("bad manifest"), "workspace", exit_codes.SCHEMA_MISMATCH), (OSError("disk"), "io", 1)],
)
def test_core_errors_map_to_the_envelope(env, monkeypatch, verb, attr, extra, exc, reason, code):
    monkeypatch.setattr(gate_cli, attr, _raiser(exc))
    out = invoke(["work", "gate", verb, env.path, "--workspace", str(env.root), "--json", *extra])
    assert out.exit_code == code
    assert json.loads(out.stdout)["error"]["reason"] == reason
