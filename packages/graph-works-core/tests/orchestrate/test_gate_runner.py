"""The detached gate runner, the default spawn, and `gw work gate wait`."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _gate_helpers import CLOCK, NOW, TODAY, fake_clock_at, finish_unrecorded, no_sleep, receipt_text, start, ticks
from graph_works_core.orchestrate import gate, gate_runner
from graph_works_core.orchestrate.gate import run_gate_run, run_gate_wait, runs_dir, spawn_runner
from graph_works_core.orchestrate.gate_receipts import GateRecord, parse_gate_receipt
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
    record = runs_dir(env.layout) / f"{started.run_id}.json"
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
        record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep, run=lambda command, cwd, log, env: -15
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
    assert run.tree_changed


def test_record_failure_retries_three_times_then_leaves_recorded_false(env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        gate,
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
    assert result.units == () and result.repo_wide_exit is None
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
    record = runs_dir(env.layout) / f"{started.run_id}.json"
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


def test_wait_probes_liveness_before_reading_the_record(env, monkeypatch):
    """A runner finishing between probe and read must not look orphaned."""
    record = start(env)
    real_probe = gate._alive

    def probe_then_finish(path):
        alive = real_probe(path)
        finish_unrecorded(record, exit=0)  # the runner completes right after the lock is seen free
        return alive

    monkeypatch.setattr(gate, "_alive", probe_then_finish)
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY)
    assert (result.status, result.exit) == ("finished", 0)


def test_existing_run_probes_liveness_before_reading_the_record(env, monkeypatch):
    record = start(env)
    monkeypatch.setattr(gate, "recover_unrecorded", lambda *a, **k: None)  # isolate _existing_run's probe order
    monkeypatch.setattr(gate, "_alive", lambda path: (finish_unrecorded(record, exit=0), False)[1])
    second = run_gate_run(env.layout, env.path, now=NOW + timedelta(seconds=5), token="1a1b2c3d", spawn=env.spawn)
    assert second.status == "started" and len(env.spawned) == 1  # finished record is not joined


def test_a_slow_runner_start_is_not_declared_failed(env, monkeypatch):
    def slow(record_path):
        gate_lock = locked(record_path.with_suffix(".lock"))
        env.stack.enter_context(gate_lock)  # the runner took its lock just after the timeout
        raise OSError("gate runner did not start within 10s")

    result = run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=slow)
    assert result.status == "started" and result.refusal is None
    record = runs_dir(env.layout) / f"{result.run_id}.json"
    assert json.loads(record.read_text(encoding="utf-8"))["result"] is None


def test_runner_exits_early_when_the_record_already_has_a_result(env):
    record = start(env)
    finish_unrecorded(record, exit=7)
    calls = []
    code = gate_runner.execute(
        record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=no_sleep, run=lambda *a: calls.append(a) or 0
    )
    assert code == 0 and calls == []
    assert json.loads(record.read_text(encoding="utf-8"))["result"]["exit"] == 7


def test_await_runner_started_tolerates_a_torn_record(tmp_path):
    record = tmp_path / "r.json"
    record.write_text("{", encoding="utf-8")
    with pytest.raises(OSError, match="did not start"):
        gate._await_runner_started(record, 0.1)


def test_record_update_retries_a_blocked_replace(tmp_path, monkeypatch):
    record = write_record(tmp_path)
    real = Path.replace
    failures = []

    def flaky(self, target):
        if len(failures) < 2:
            failures.append(1)
            raise PermissionError("held by a reader")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    slept = []
    gate_runner._update(record, sleep=slept.append, recorded=True)
    assert json.loads(record.read_text(encoding="utf-8"))["recorded"] is True and len(slept) == 2


def test_record_update_gives_up_after_bounded_permission_errors(tmp_path, monkeypatch):
    record = write_record(tmp_path)
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError("held")))
    with pytest.raises(PermissionError):
        gate_runner._update(record, sleep=no_sleep, recorded=True)
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


@pytest.mark.parametrize("bad", ["../x", "..", "a/b", "nope", "20260928T120000Z-ZZZZZZZZ"])
def test_wait_rejects_a_malformed_run_id(env, bad):
    start(env)
    result = run_gate_wait(env.layout, env.path, run_id=bad, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert result.refusal == "no-run"


def test_run_records_an_unrecorded_result_and_finds_it_instead_of_rerunning(env):
    record = start(env)
    finish_unrecorded(record, exit=0)
    spawned: list[Path] = []
    result = run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=spawned.append)
    assert result.status == "satisfied" and not spawned
    assert len(parse_gate_receipt(receipt_text(env))[1]) == 1
    assert json.loads(record.read_text(encoding="utf-8"))["recorded"] is True


def test_run_leaves_a_live_runners_result_alone(env):
    record = start(env)
    finish_unrecorded(record, exit=0)
    with locked(record.with_suffix(".lock")):
        result = run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=lambda r: None)
    assert result.status != "satisfied"
    assert json.loads(record.read_text(encoding="utf-8"))["recorded"] is False


def test_run_skips_spawn_failed_and_malformed_pending_records(env):
    record = start(env)
    data = json.loads(record.read_text(encoding="utf-8"))
    data["result"] = {"exit": None, "error": "boom"}
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    (record.parent / "20260928T120001Z-00000001.json").write_text(
        json.dumps({"run_id": "x", "result": {"exit": 0}}), encoding="utf-8", newline="\n"
    )
    (record.parent / "20260928T120002Z-00000002.json").write_text("not json", encoding="utf-8", newline="\n")
    result = run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=lambda r: None)
    assert result.status == "started"


def test_wait_sweeps_an_older_unrecorded_result_before_choosing_the_newest(env):
    older = start(env)
    finish_unrecorded(older, exit=0)
    newer = older.parent / "20260928T130000Z-0a1b2c3d.json"
    data = json.loads(older.read_text(encoding="utf-8"))
    data.update(run_id=newer.stem, started="2026-09-28T13:00:00Z", result=None, runner_started=False)
    newer.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    result = run_gate_wait(env.layout, env.path, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert result.run_id == newer.stem
    assert len(parse_gate_receipt(receipt_text(env))[1]) == 1


@pytest.fixture
def units_record(env):
    record = start(env)
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(
        implicit=False,
        manifest_hash="d" * 64,
        jobs=2,
        setup="setup cmd",
        repo_wide={"command": "rw cmd", "planned": True, "reused_from": None},
        units=[
            {
                "name": n,
                "hash": h * 64,
                "command": f"cmd {n}",
                "env": {"PYTEST_XDIST_AUTO_NUM_WORKERS": "2"} if n == "a" else {},
                "planned": True,
                "reused_from": None,
            }
            for n, h in (("a", "a"), ("b", "b"))
        ],
    )
    gate.write_record(record, data)
    return record


def fake_run(exits, seen):
    def run(command, cwd, log, env):
        seen.append((command, dict(env)))
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"output of {command}\n")
        return exits.get(command, 0)

    return run


def execute_fake(record, run):
    return gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(*range(100)), sleep=no_sleep, run=run)


def test_one_failing_unit_does_not_stop_the_others(units_record, env):
    seen = []
    execute_fake(units_record, fake_run({"cmd a": 1}, seen))
    result = json.loads(units_record.read_text(encoding="utf-8"))["result"]
    assert result["units"]["a"]["exit"] == 1 and result["units"]["b"]["exit"] == 0
    assert result["exit"] == 1 and result["log_tail"] == "output of cmd a"
    entries = {u.name: u for u in parse_gate_receipt(receipt_text(env))[1][-1].units}
    assert entries["a"].exit == 1 and entries["b"].exit == 0 and entries["b"].ran
    waited = run_gate_wait(
        env.layout, env.path, run_id=units_record.stem, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY
    )
    assert waited.units == (("a", 1), ("b", 0)) and waited.repo_wide_exit == 0


def test_order_is_setup_then_repo_wide_then_units_with_unit_env(units_record):
    seen = []
    execute_fake(units_record, fake_run({}, seen))
    commands = [c for c, _ in seen]
    assert commands[:2] == ["setup cmd", "rw cmd"] and sorted(commands[2:]) == ["cmd a", "cmd b"]
    assert dict(seen)["cmd a"] == {"PYTEST_XDIST_AUTO_NUM_WORKERS": "2"}
    data = json.loads(units_record.read_text(encoding="utf-8"))
    root_log = Path(data["log_path"])
    for name, outcome in data["result"]["units"].items():
        assert Path(outcome["log_path"]) == root_log.with_suffix("") / f"{name}.log"
    assert "[a] started" in root_log.read_text(encoding="utf-8")
    assert "[b] finished exit=0" in root_log.read_text(encoding="utf-8")


def test_unit_pool_executes_concurrently_with_bounded_jobs(units_record):
    barrier = threading.Barrier(2)
    seen = []
    inner = fake_run({}, seen)

    def run(command, cwd, log, env):
        if command.startswith("cmd "):
            barrier.wait(timeout=5)
        return inner(command, cwd, log, env)

    execute_fake(units_record, run)
    assert sorted(c for c, _ in seen if c.startswith("cmd ")) == ["cmd a", "cmd b"]


def test_repo_wide_failure_still_runs_units(units_record):
    execute_fake(units_record, fake_run({"rw cmd": 2}, []))
    result = json.loads(units_record.read_text(encoding="utf-8"))["result"]
    assert result["repo_wide"]["exit"] == 2 and result["units"]["a"]["exit"] == 0
    assert result["log_tail"].endswith("output of rw cmd") and result["exit"] == 1


def test_setup_failure_records_every_planned_unit_red_and_runs_none(units_record, env):
    seen = []
    execute_fake(units_record, fake_run({"setup cmd": 5}, seen))
    assert [c for c, _ in seen] == ["setup cmd"]
    run = parse_gate_receipt(receipt_text(env))[1][-1]
    assert {u.name: u.exit for u in run.units if u.ran} == {"a": 5, "b": 5}
    assert run.repo_wide.exit == 5


def test_tree_change_taints_every_v2_entry(units_record, env):
    inner = fake_run({}, [])

    def run(command, cwd, log, unit_env):
        if command == "setup cmd":
            (cwd / "changed.txt").write_text("changed", encoding="utf-8", newline="\n")
        return inner(command, cwd, log, unit_env)

    execute_fake(units_record, run)
    from graph_works_core.orchestrate.gate_receipts import evaluate

    assert parse_gate_receipt(receipt_text(env))[1][-1].tree_changed
    outcome = evaluate(
        env.layout,
        repo="code",
        tree=env.tree,
        full_command="true",
        repo_wide_command="rw cmd",
        hashes={"a": "a" * 64, "b": "b" * 64},
    )
    assert not outcome.satisfied and not outcome.green and not outcome.repo_wide_green


def test_reused_units_and_repo_wide_are_recorded_ran_false(units_record, env):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    reuse = {"owner": "work/x", "run_id": "20260928T110000Z-00000001"}
    data["units"][1].update(planned=False, reused_from=reuse)
    data["repo_wide"].update(planned=False, reused_from=reuse)
    gate.write_record(units_record, data)
    seen = []
    execute_fake(units_record, fake_run({}, seen))
    run = parse_gate_receipt(receipt_text(env))[1][-1]
    assert [c for c, _ in seen] == ["setup cmd", "cmd a"]
    assert not run.units[1].ran and run.units[1].reused_from.owner == "work/x"
    assert not run.repo_wide.ran and run.repo_wide.reused_from.owner == "work/x"


def test_no_setup_is_run_when_all_checks_are_reused(units_record):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    for entry in [*data["units"], data["repo_wide"]]:
        entry.update(planned=False, reused_from={"owner": "work/x", "run_id": "old"})
    gate.write_record(units_record, data)
    seen = []
    execute_fake(units_record, fake_run({}, seen))
    assert seen == []


@pytest.mark.parametrize("mutation", ["version", "requesters"])
def test_a_foreign_pending_record_is_never_run_or_recorded(env, mutation):
    """Every reader ignores a record that is not a current run-index record; the runner too."""
    record = start(env)
    data = json.loads(record.read_text(encoding="utf-8"))
    data.pop(mutation)
    gate.write_record(record, data)
    seen: list[tuple[str, dict[str, str]]] = []
    assert execute_fake(record, fake_run({}, seen)) == 1
    receipt = env.layout.bundle_dir / env.path / "references" / "03-gate-receipts.md"
    assert seen == [] and not receipt.exists()
    assert json.loads(record.read_text(encoding="utf-8")) == data


def test_no_result_written_means_nothing_recorded(units_record):
    def crash(command, cwd, log, env):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_fake(units_record, crash)
    data = json.loads(units_record.read_text(encoding="utf-8"))
    assert data["result"] is None and not data["recorded"]


@pytest.mark.parametrize("full", ["true", "echo a"])
def test_implicit_scoped_record_never_satisfies_full_even_equal_command(env, full):
    env.set_manifest(
        gate=f"    gate:\n      full: '{full}'\n      scoped:\n"
        "        roots: packages/*\n        command: 'echo {name}'\n"
    )
    started = run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="0a1b2c3d", spawn=env.spawn)
    execute_fake(env.spawned[0], fake_run({}, []))
    run = parse_gate_receipt(receipt_text(env))[1][-1]
    assert run.scope == "scoped" and run.command == "echo a" and run.exit == 0
    assert run.manifest_hash is None and run.units == () and run.repo_wide is None
    checked = gate.run_gate_check(env.layout, env.path)
    assert checked.status == "unsatisfied"
    again = run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert again.status == "started" and again.run_id != started.run_id


@pytest.mark.parametrize("missing", ["unit", "repo_wide"])
def test_missing_planned_result_never_yields_green(units_record, env, missing):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    result = {
        "exit": 0,
        "tree_changed": False,
        "duration_s": 1.0,
        "log_tail": "",
        "units": {n: {"exit": 0, "duration_s": 1.0, "log_path": "/l"} for n in ("a", "b")},
        "repo_wide": {"exit": 0, "duration_s": 1.0},
    }
    if missing == "unit":
        result["units"].pop("a")
    else:
        result["repo_wide"] = None
    data.update(result=result, runner_started=True)
    gate.write_record(units_record, data)
    waited = run_gate_wait(
        env.layout, env.path, run_id=units_record.stem, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY
    )
    assert waited.exit != 0
    run = parse_gate_receipt(receipt_text(env))[1][-1]
    assert run.exit != 0
    assert (run.units[0].exit if missing == "unit" else run.repo_wide.exit) != 0


@pytest.mark.parametrize(
    "location",
    [
        "exit",
        "duration_s",
        "unit_exit",
        "unit_duration",
        "wide_exit",
        "wide_duration",
        "setup_exit",
        "setup_duration",
        "tree_changed",
    ],
)
@pytest.mark.parametrize("bad", [False, "0"])
def test_malformed_result_evidence_fails_closed(units_record, env, location, bad):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    result = {
        "exit": 0,
        "tree_changed": False,
        "duration_s": 1.0,
        "log_tail": "",
        "setup": {"exit": 0, "duration_s": 1.0},
        "units": {n: {"exit": 0, "duration_s": 1.0, "log_path": "/l"} for n in ("a", "b")},
        "repo_wide": {"exit": 0, "duration_s": 1.0},
    }
    if location.startswith("unit_"):
        result["units"]["a"]["exit" if location.endswith("exit") else "duration_s"] = bad
    elif location.startswith("wide_") or location.startswith("setup_"):
        result["repo_wide" if location.startswith("wide_") else "setup"][
            "exit" if location.endswith("exit") else "duration_s"
        ] = bad
    elif location == "tree_changed":
        result[location] = 0 if bad is False else bad
    else:
        result[location] = bad
    data.update(result=result, runner_started=True)
    gate.write_record(units_record, data)
    waited = run_gate_wait(
        env.layout, env.path, run_id=units_record.stem, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY
    )
    assert waited.status == "orphaned" and not waited.recorded
    gate.recover_unrecorded(env.layout, env.path, now=LATER.wall())
    assert not (env.layout.bundle_dir / env.path / "references/03-gate-receipts.md").exists()


def test_run_command_merges_environment_and_preserves_windows_shell(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("EXISTING_GATE_ENV", "preserved")

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(gate_runner.subprocess, "run", run)
    monkeypatch.setattr(gate_runner.sys, "platform", "win32")
    assert gate_runner.run_command("echo ok", tmp_path, tmp_path / "out.log", {"OVERRIDE": "unit"}) == 0
    assert seen["env"]["EXISTING_GATE_ENV"] == "preserved" and seen["env"]["OVERRIDE"] == "unit"
    assert seen["argv"][1:] == ["/c", "echo ok"]


def test_repo_wide_failure_tail_is_captured_before_many_unit_progress_lines(units_record):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    data["units"] = [
        {
            "name": f"unit-{i:02}",
            "hash": "a" * 64,
            "command": f"cmd {i}",
            "planned": True,
            "env": {},
            "reused_from": None,
        }
        for i in range(25)
    ]
    gate.write_record(units_record, data)
    execute_fake(units_record, fake_run({"rw cmd": 2}, []))
    result = json.loads(units_record.read_text(encoding="utf-8"))["result"]
    assert result["log_tail"] == "output of setup cmd\noutput of rw cmd"
    assert len(result["units"]) == 25


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 10**1000])
def test_result_duration_rejects_nonfinite_negative_or_overflow(bad):
    with pytest.raises(ValueError):
        gate._result_duration(bad)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("units", []),
        ("units", {"a": None}),
        ("repo_wide", []),
        ("setup", {}),
        ("units", {"a": {"exit": 0, "duration_s": 1, "log_path": 5}}),
    ],
)
def test_malformed_operation_shape_cannot_be_recovered(units_record, env, field, bad):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    data["result"] = {"exit": 0, "duration_s": 1, "tree_changed": False, "log_tail": "", field: bad}
    gate.write_record(units_record, data)
    waited = run_gate_wait(
        env.layout, env.path, run_id=units_record.stem, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY
    )
    assert waited.status == "orphaned" and not waited.recorded
    gate.recover_unrecorded(env.layout, env.path, now=LATER.wall())
    assert not (env.layout.bundle_dir / env.path / "references/03-gate-receipts.md").exists()


def test_units_run_without_setup_or_repo_wide_and_respect_one_job(units_record):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    data.update(setup=None, repo_wide=None, jobs=1)
    gate.write_record(units_record, data)
    seen = []
    execute_fake(units_record, fake_run({}, seen))
    assert [c for c, _ in seen] == ["cmd a", "cmd b"]
    result = json.loads(units_record.read_text(encoding="utf-8"))["result"]
    assert result["setup"] is None and result["repo_wide"] is None


def test_first_failing_unit_tail_is_deterministic_and_sanitized(units_record):
    seen = []
    inner = fake_run({"cmd a": 2, "cmd b": 3}, seen)

    def run(command, cwd, log, env):
        code = inner(command, cwd, log, env)
        if command == "cmd a":
            with log.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\x1b[31mfirst failure\x1b[0m\n")
        return code

    execute_fake(units_record, run)
    result = json.loads(units_record.read_text(encoding="utf-8"))["result"]
    assert result["log_tail"] == "output of cmd a\nfirst failure"


@pytest.mark.parametrize("green", ["unit", "repo_wide", "both"])
@pytest.mark.parametrize("aggregate_exit", [0, 1])
def test_failed_setup_rejects_green_downstream_conversion_wait_and_recovery(units_record, env, green, aggregate_exit):
    from graph_works_core.orchestrate.gate_receipts import evaluate

    data = json.loads(units_record.read_text(encoding="utf-8"))
    result = {
        "exit": aggregate_exit,
        "tree_changed": False,
        "duration_s": 1.0,
        "log_tail": "setup failed",
        "setup": {"exit": 5, "duration_s": 1.0},
        "units": {n: {"exit": 5, "duration_s": 0.0, "log_path": data["log_path"]} for n in ("a", "b")},
        "repo_wide": {"exit": 5, "duration_s": 0.0},
    }
    if green in ("unit", "both"):
        result["units"]["a"]["exit"] = 0
    if green in ("repo_wide", "both"):
        result["repo_wide"]["exit"] = 0
    with pytest.raises(ValueError, match="setup failed"):
        gate.gate_run_from_record(data, result)
    data.update(result=result, runner_started=True)
    gate.write_record(units_record, data)
    waited = run_gate_wait(
        env.layout, env.path, run_id=units_record.stem, timeout=0, clock=LATER, sleep=no_sleep, today=TODAY
    )
    assert waited.status == "orphaned" and not waited.recorded
    assert "setup failed" in waited.detail
    gate.recover_unrecorded(env.layout, env.path, now=LATER.wall())
    assert not json.loads(units_record.read_text(encoding="utf-8"))["recorded"]
    assert not (env.layout.bundle_dir / env.path / "references/03-gate-receipts.md").exists()
    evaluation = evaluate(
        env.layout,
        repo="code",
        tree=env.tree,
        full_command="true",
        repo_wide_command="rw cmd",
        hashes={"a": "a" * 64, "b": "b" * 64},
    )
    assert not evaluation.satisfied and not evaluation.green and not evaluation.repo_wide_green


def test_failed_setup_preserves_reused_entries_and_records_planned_checks_red(units_record, env):
    data = json.loads(units_record.read_text(encoding="utf-8"))
    reuse = {"owner": "work/x", "run_id": "20260928T110000Z-00000001"}
    data["units"][1].update(planned=False, reused_from=reuse)
    data["repo_wide"].update(planned=False, reused_from=reuse)
    gate.write_record(units_record, data)
    seen = []
    execute_fake(units_record, fake_run({"setup cmd": 5}, seen))
    run = parse_gate_receipt(receipt_text(env))[1][-1]
    assert [c for c, _ in seen] == ["setup cmd"]
    assert run.exit != 0 and run.units[0].ran and run.units[0].exit == 5
    assert not run.units[1].ran and run.units[1].reused_from.owner == "work/x"
    assert not run.repo_wide.ran and run.repo_wide.reused_from.owner == "work/x"
