"""`gate_wake`: the resume line, `<orca>` resolution, and one `orca terminal send` (D-004)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from graph_works_core.orchestrate import gate_wake
from graph_works_core.orchestrate.gate_wake import WakeOutcome, orca_command, resume_line, send_argv, wake

RID = "20260928T120000Z-0a1b2c3d"


def _reply(*, ok: bool = True, accepted: bool = True, stages: list[str] | None = None) -> str:
    send = {"handle": "term_a", "accepted": accepted, "prompt": {"stages": stages or ["input_accepted"]}}
    return json.dumps({"id": "x", "ok": ok, "result": {"send": send}})


def _fake_run(monkeypatch, *, returncode: int = 0, stdout: str = "", stderr: str = "", raises: Exception | None = None):
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(gate_wake.subprocess, "run", run)
    return calls


def test_resume_line_is_exact():
    assert resume_line(RID, "work/feature-a", 0) == (
        f"Gate run {RID} for work/feature-a finished (exit 0). "
        f"Run gw work gate wait work/feature-a --run {RID}, then continue the stage."
    )


@pytest.mark.parametrize("value", [None, True, "1", 1.0])
def test_a_non_integer_exit_renders_none(value):
    assert "(exit none)" in resume_line(RID, "work/a", value)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [({"ORCA_CLI_COMMAND": "/opt/orca-dev"}, "/opt/orca-dev"), ({"ORCA_CLI_COMMAND": ""}, "orca"), ({}, "orca")],
)
def test_orca_command_precedence(environ, expected):
    assert orca_command(environ) == expected


def test_send_argv_shape():
    assert send_argv("orca", "term_a", "hi there") == [
        "orca", "terminal", "send", "--terminal", "term_a", "--text", "hi there",
        "--enter", "--wait-submit", "5", "--json",
    ]  # fmt: skip


def test_accepted_is_delivered_and_stages_are_detail(monkeypatch):
    calls = _fake_run(monkeypatch, stdout=_reply(stages=["input_accepted", "turn_started"]))
    outcome = wake("term_a", "line", environ={"ORCA_CLI_COMMAND": "/x/orca"})
    assert outcome == WakeOutcome("delivered", "input_accepted,turn_started")
    assert calls == [send_argv("/x/orca", "term_a", "line")]


def test_input_accepted_alone_is_delivered(monkeypatch):
    _fake_run(monkeypatch, stdout=_reply(stages=["input_accepted"]))
    assert wake("term_a", "line", environ={}).status == "delivered"


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(0, _reply(accepted=False)), (0, _reply(ok=False)), (0, "not json"), (0, "[]"), (1, ""), (1, _reply())],
)
def test_anything_else_is_failed(monkeypatch, returncode, stdout):
    _fake_run(monkeypatch, returncode=returncode, stdout=stdout, stderr="terminal not found")
    outcome = wake("term_a", "line", environ={})
    assert outcome.status == "failed" and outcome.detail


@pytest.mark.parametrize("exc", [FileNotFoundError("orca"), subprocess.TimeoutExpired(["orca"], 30)])
def test_a_missing_or_hung_orca_is_failed_not_raised(monkeypatch, exc):
    _fake_run(monkeypatch, raises=exc)
    assert wake("term_a", "line", environ={}).status == "failed"


@pytest.mark.skipif(sys.platform == "win32", reason="the stub is a POSIX shell script")
def test_a_real_stub_executable_receives_the_argv(tmp_path: Path):
    log = tmp_path / "argv.txt"
    stub = tmp_path / "orca"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            printf '%s\\n' "$@" > {log}
            printf '%s' '{_reply()}'
            """
        ),
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    assert wake("term_a", "a line", environ={"ORCA_CLI_COMMAND": str(stub)}).status == "delivered"
    assert log.read_text(encoding="utf-8").splitlines() == send_argv(str(stub), "term_a", "a line")[1:]
