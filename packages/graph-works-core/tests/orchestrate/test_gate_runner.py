"""The detached gate runner, the default spawn, and `gw work gate wait`."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _gate_helpers import CLOCK, NOW, TODAY, fake_clock_at, finish_unrecorded, no_sleep, receipt_text, start, ticks
from graph_works_core.orchestrate import gate, gate_runner
from graph_works_core.orchestrate.gate import run_gate_run, run_gate_wait, runs_dir, spawn_runner
from graph_works_core.orchestrate.gate_receipts import GateRecord, parse_gate_receipt, satisfies
from graph_works_core.orchestrate.wait import WaitClock
from okf_ext.locking import locked


def real_wall() -> datetime:
    return datetime.now(UTC)


LATER = fake_clock_at(NOW + timedelta(minutes=5))


class FakeProc:
    pass


def write_record(tmp_path: Path) -> Path:
    record = tmp_path / "r.json"
    record.write_text(
        json.dumps({"log_path": str(tmp_path / "logs" / "r.log"), "worktree": str(tmp_path)}), encoding="utf-8"
    )
    return record


def test_execute_records_a_green_run(env):
    started = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=lambda p: None)
    record = runs_dir(env.layout, env.path) / f"{started.run_id}.json"
    assert gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0.0, 2.5), sleep=no_sleep) == 0
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["result"]["exit"] == 0 and data["recorded"] is True and data["runner_started"] is True
    _owner, runs = parse_gate_receipt(receipt_text(env))
    assert runs[0].run_id == started.run_id and runs[0].duration_s == 2.5 and runs[0].clean


def test_red_run_is_recorded_red(env):
    env.set_manifest(gate="    gate:\n      full: 'echo nope; exit 3'\n")
    record = start(env)
    gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep)
    run = parse_gate_receipt(receipt_text(env))[1][0]
    assert run.exit == 3 and run.log_tail == "nope"


def test_signal_exit_is_recorded_as_128_plus_n(env):
    record = start(env)
    gate_runner.execute(
        record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep, run=lambda command, cwd, log: -15
    )
    assert parse_gate_receipt(receipt_text(env))[1][0].exit == 143


def test_empty_log_records_empty_tail(env):
    env.set_manifest(gate="    gate:\n      full: 'true'\n")
    record = start(env)
    gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep)
    assert parse_gate_receipt(receipt_text(env))[1][0].log_tail == ""


def test_a_command_that_dirties_the_tree_is_recorded_tree_changed(env):
    env.set_manifest(gate="    gate:\n      full: 'touch packages/a/made.py'\n")
    record = start(env)
    gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep)
    run = parse_gate_receipt(receipt_text(env))[1][0]
    assert run.tree_changed and not satisfies(run, repo="code", tree=run.tree, command=run.command)


def test_record_failure_retries_three_times_then_leaves_recorded_false(env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        gate_runner,
        "record_gate_run",
        lambda *a, **k: calls.append(1) or GateRecord("transaction-refused", None, False, "x"),
    )
    record = start(env)
    gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep)
    assert len(calls) == 3 and json.loads(record.read_text(encoding="utf-8"))["recorded"] is False


def test_runner_lock_acquire_rides_out_a_momentary_probe(env, monkeypatch):
    real = gate_runner.locked
    attempts = []

    def flaky(path, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("held by an _alive probe")
        return real(path, **kwargs)

    monkeypatch.setattr(gate_runner, "locked", flaky)
    slept = []
    record = start(env)
    code = gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=slept.append)
    assert code == 0 and len(attempts) == 3 and slept[:2] == [gate_runner.LOCK_PAUSE] * 2
    assert json.loads(record.read_text(encoding="utf-8"))["recorded"] is True


def test_runner_gives_up_when_another_runner_owns_the_lock(env):
    record = start(env)
    with locked(record.with_suffix(".lock")):
        assert gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep) == 1
    assert json.loads(record.read_text(encoding="utf-8"))["runner_started"] is False


def test_wait_records_an_unrecorded_result_without_rerunning(env):
    record = start(env)
    finish_unrecorded(record, exit=0)
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert (result.status, result.exit, result.recorded) == ("finished", 0, True)
    assert len(parse_gate_receipt(receipt_text(env))[1]) == 1


def test_wait_times_out_as_running_while_the_runner_holds_its_lock(env):
    record = start(env)
    with locked(record.with_suffix(".lock")):
        result = run_gate_wait(
            env.layout, env.path, run_id=None, timeout=5, clock=fake_clock_at(NOW + timedelta(minutes=5), step=1.0),
            sleep=no_sleep, today=TODAY,
        )  # fmt: skip
    assert result.status == "running"


def test_wait_reports_orphaned_for_a_dead_runner(env):
    start(env)
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY)
    assert result.status == "orphaned" and result.log_path.endswith(".log")


def test_wait_treats_a_just_spawned_record_as_running_not_orphaned(env):
    start(env)
    soon = fake_clock_at(NOW + timedelta(seconds=5))
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=soon, sleep=no_sleep, today=TODAY)
    assert result.status == "running"


def test_run_shares_a_record_the_runner_has_not_yet_locked(env):
    first = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=lambda p: None)
    second = run_gate_run(env.layout, env.path, now=NOW + timedelta(seconds=5), token="1a1b2c3d", spawn=env.spawn)
    assert (second.status, second.run_id) == ("running", first.run_id) and env.spawned == []
    third = run_gate_run(env.layout, env.path, now=NOW + timedelta(minutes=5), token="2a1b2c3d", spawn=env.spawn)
    assert third.status == "started" and third.run_id != first.run_id


def test_a_spawn_failed_record_is_never_joined(env):
    def failing(_record):
        raise OSError("no exec")

    failed = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=failing)
    assert failed.refusal == "runner-failed"
    again = run_gate_run(env.layout, env.path, now=NOW + timedelta(seconds=1), token="1a1b2c3d", spawn=env.spawn)
    assert again.status == "started"


def test_wait_without_any_run_refuses(env):
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert result.refusal == "no-run"


def test_terminal_owner_keeps_the_result_unrecorded(env):
    record = start(env)
    finish_unrecorded(record, exit=0)
    env.set_status("resolved")
    env.set_phase("done")
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert (result.status, result.recorded) == ("finished", False)


@pytest.mark.skipif(sys.platform == "win32", reason="sh gate command")
def test_run_returns_only_after_the_runner_holds_its_lock(env):
    env.set_manifest(gate="    gate:\n      full: 'sleep 2'\n")
    started = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=spawn_runner)
    record = runs_dir(env.layout, env.path) / f"{started.run_id}.json"
    assert json.loads(record.read_text(encoding="utf-8"))["runner_started"] is True
    immediate = run_gate_wait(
        env.layout, env.path, run_id=started.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert immediate.status == "running"
    final = run_gate_wait(
        env.layout, env.path, run_id=started.run_id, timeout=30,
        clock=WaitClock(wall=real_wall, monotonic=time.monotonic), sleep=time.sleep, today=TODAY,
    )  # fmt: skip
    assert (final.status, final.exit, final.recorded) == ("finished", 0, True)


@pytest.mark.skipif(sys.platform == "win32", reason="sh gate command")
def test_the_runner_survives_its_caller_exiting(env):
    env.set_manifest(gate="    gate:\n      full: 'sleep 1'\n")
    code = textwrap.dedent(f"""
        from datetime import UTC, datetime
        from graph_works_core.workspace.layout import layout_for
        from graph_works_core.orchestrate.gate import run_gate_run, spawn_runner
        run_gate_run(layout_for({str(env.layout.root)!r}), {env.path!r}, now=datetime.now(UTC),
                     token="0a1b2c3d", spawn=spawn_runner)
    """)
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)
    final = run_gate_wait(
        env.layout, env.path, run_id=None, timeout=30,
        clock=WaitClock(wall=real_wall, monotonic=time.monotonic), sleep=time.sleep, today=TODAY,
    )  # fmt: skip
    assert (final.status, final.recorded) == ("finished", True)


def test_spawn_uses_detached_process_flags(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(gate.subprocess, "Popen", lambda argv, **kw: seen.update(kw) or FakeProc())
    monkeypatch.setattr(gate, "_await_runner_started", lambda record, timeout: None)
    monkeypatch.setattr(gate.sys, "platform", "win32")
    gate.spawn_runner(write_record(tmp_path))
    assert seen["creationflags"] == gate.WINDOWS_DETACHED_FLAGS
    monkeypatch.setattr(gate.sys, "platform", "linux")
    gate.spawn_runner(write_record(tmp_path))
    assert seen["start_new_session"] is True


def test_await_runner_started_times_out(tmp_path):
    with pytest.raises(OSError, match="did not start"):
        gate._await_runner_started(write_record(tmp_path), 0.1)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation flags")
def test_detached_flags_match_subprocess_constants():
    assert gate.WINDOWS_DETACHED_FLAGS == subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS


def test_main_usage_and_dispatch(env, capsys):
    assert gate_runner.main([]) == 2
    assert "usage" in capsys.readouterr().err
    record = start(env)
    assert gate_runner.main([str(record)]) == 0
    assert json.loads(record.read_text(encoding="utf-8"))["recorded"] is True


def test_wait_with_an_unknown_run_id_refuses(env):
    start(env)
    result = run_gate_wait(env.layout, env.path, run_id="nope", timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert result.refusal == "no-run"


def test_wait_with_an_unreadable_record_refuses(env):
    record = start(env)
    record.write_text("not json", encoding="utf-8")
    result = run_gate_wait(
        env.layout, env.path, run_id=record.stem, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert result.refusal == "no-run"


def test_wait_reports_a_spawn_failure_as_orphaned(env):
    def failing(_record):
        raise OSError("no exec")

    failed = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=failing)
    result = run_gate_wait(
        env.layout, env.path, run_id=failed.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert (result.status, result.detail) == ("orphaned", "no exec")


def test_wait_reports_a_refused_late_record_without_marking_it(env, monkeypatch):
    record = start(env)
    finish_unrecorded(record, exit=0)
    monkeypatch.setattr(gate, "record_gate_run", lambda *a, **k: GateRecord("transaction-refused", None, False, "busy"))
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert (result.status, result.recorded, result.detail) == ("finished", False, "busy")


def test_wait_lets_a_live_runner_finish_recording(env):
    record = start(env)
    finish_unrecorded(record, exit=0)
    with locked(record.with_suffix(".lock")):
        result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert result.status == "running"


def test_the_pending_record_must_be_a_mapping(tmp_path):
    record = tmp_path / "r.json"
    record.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a mapping"):
        gate_runner._read(record)


def test_a_failed_record_update_leaves_no_temp_file(tmp_path, monkeypatch):
    record = write_record(tmp_path)
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        gate_runner._update(record, recorded=True)
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_a_log_with_invalid_utf8_is_decoded_leniently(tmp_path):
    log = tmp_path / "x.log"
    log.write_bytes(b"ok \xff\n")
    assert "ok" in gate_runner._read_log(log)
    assert gate_runner._read_log(tmp_path / "missing.log") == ""
