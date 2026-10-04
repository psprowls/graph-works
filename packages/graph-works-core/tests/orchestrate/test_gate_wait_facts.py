"""`gate_wait_facts`: per-terminal facts from runner locks and records, never from the worker (D-005, D-009)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import get_args

import pytest
from _gate_helpers import CLOCK, NOW, TODAY, no_sleep, record_of
from graph_works_core.orchestrate import gate_runner
from graph_works_core.orchestrate.gate import (
    GATE_WAIT_STATES,
    GateWaitState,
    gate_wait_facts,
    run_gate_run,
    run_gate_wait,
    runs_dir,
)  # fmt: skip
from graph_works_core.orchestrate.gate_wake import WakeOutcome, resume_line

LATER = NOW + timedelta(minutes=5)  # past START_GRACE: an unlocked record reads as dead
GREEN = {"exit": 0, "tree_changed": False, "duration_s": 1.0, "log_tail": ""}


def notified(env, *, terminal="term_a", token="0a1b2c3d"):
    started = run_gate_run(
        env.layout, env.path, now=NOW, token=token, spawn=lambda r: None,
        notify=True, environ={"ORCA_TERMINAL_HANDLE": terminal},
    )  # fmt: skip
    return started.run_id


def edit(env, run_id, mutate):
    record = runs_dir(env.layout) / f"{run_id}.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    mutate(data)
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def woken(status):
    def mutate(data):
        data["waiters"][0]["woken"] = {"status": status, "at": "2026-09-28T12:00:00Z", "detail": ""}

    return mutate


def finish(data):
    data["result"] = GREEN


def test_the_state_vocabulary_is_closed():
    assert get_args(GateWaitState) == GATE_WAIT_STATES


def test_a_live_runner_without_a_result_is_running(env):
    rid = notified(env)
    env.mark_runner_alive(rid)
    fact = gate_wait_facts(env.layout, LATER)["term_a"]
    assert fact == {"run_id": rid, "path": env.path, "terminal": "term_a", "state": "running", "resume_line": None}


def test_a_dead_runner_without_a_result_is_orphaned(env):
    rid = notified(env)
    fact = gate_wait_facts(env.layout, LATER)["term_a"]
    assert fact["state"] == "orphaned" and fact["resume_line"] == resume_line(rid, env.path, None)


def test_a_result_still_being_woken_is_running(env):
    rid = notified(env)
    env.mark_runner_alive(rid)
    edit(env, rid, finish)
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "running"


@pytest.mark.parametrize(("status", "alive", "state"), [
    ("delivered", False, "woken"), ("collected", False, "woken"), ("failed", True, "wake_failed"),
    ("failed", False, "wake_failed"), (None, False, "wake_failed"),
])  # fmt: skip
def test_settled_states(env, status, alive, state):
    rid = notified(env)
    if alive:
        env.mark_runner_alive(rid)
    edit(env, rid, finish)
    if status is not None:
        edit(env, rid, woken(status))
    fact = gate_wait_facts(env.layout, LATER)["term_a"]
    assert fact["state"] == state
    assert fact["resume_line"] == (resume_line(rid, env.path, 0) if state == "wake_failed" else None)


def test_collected_reads_woken_even_without_a_result(env):
    rid = notified(env)
    edit(env, rid, woken("collected"))
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "woken"


def test_the_newest_run_per_terminal_wins(env):
    old = notified(env)
    edit(env, old, lambda data: data.update(result={**GREEN, "exit": 1}))  # red: not reusable evidence
    edit(env, old, woken("delivered"))
    new = notified(env, token="1b2c3d4e")  # the old run has a result, so this starts a new one
    env.mark_runner_alive(new)
    fact = gate_wait_facts(env.layout, LATER)["term_a"]
    assert (fact["run_id"], fact["state"]) == (new, "running")


def test_an_unknown_terminal_has_no_fact(env):
    notified(env)
    assert "term_z" not in gate_wait_facts(env.layout, LATER)


@pytest.mark.parametrize("bad", ["x", [3], [{"path": "work/feature-a"}], None])
def test_malformed_waiters_give_no_fact(env, bad):
    rid = notified(env)
    edit(env, rid, lambda data: data.update(waiters=bad))
    assert gate_wait_facts(env.layout, LATER) == {}


def test_a_foreign_version_is_ignored(env):
    rid = notified(env)
    edit(env, rid, lambda data: data.update(version=1))
    assert gate_wait_facts(env.layout, LATER) == {}


def test_no_runs_directory_is_no_facts(env):
    assert gate_wait_facts(env.layout, LATER) == {}


def test_gate_wait_collects_a_finished_result(env):
    rid = notified(env)

    def boom(terminal, line):
        raise OSError("no orca")

    record = runs_dir(env.layout) / f"{rid}.json"
    gate_runner.execute(record, wall=lambda: NOW, monotonic=iter([0.0, 1.0]).__next__, sleep=no_sleep, wake=boom)
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "wake_failed"
    done = run_gate_wait(env.layout, env.path, run_id=rid, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert done.status == "finished"
    assert record_of(env, rid)["waiters"][0]["woken"]["status"] == "collected"
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "woken"


def test_gate_wait_collects_a_run_whose_wake_failed_by_record(env):
    """The real waker never raises: it records `failed`; the worker's own wait must still clear it (D-009)."""
    rid = notified(env)
    record = runs_dir(env.layout) / f"{rid}.json"
    gate_runner.execute(
        record, wall=lambda: NOW, monotonic=iter([0.0, 1.0]).__next__, sleep=no_sleep,
        wake=lambda terminal, line: WakeOutcome("failed", "exit 1: no orca"),
    )  # fmt: skip
    assert record_of(env, rid)["waiters"][0]["woken"]["status"] == "failed"
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "wake_failed"
    done = run_gate_wait(env.layout, env.path, run_id=rid, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert done.status == "finished"
    assert record_of(env, rid)["waiters"][0]["woken"]["status"] == "collected"
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "woken"


@pytest.mark.parametrize("bad", [["failed"], {"x": 1}, 3])
def test_a_malformed_wake_status_does_not_crash(env, bad):
    rid = notified(env)
    edit(env, rid, lambda data: data["waiters"][0].update(woken={"status": bad}))
    edit(env, rid, finish)
    assert gate_wait_facts(env.layout, LATER)["term_a"]["state"] == "wake_failed"
    done = run_gate_wait(env.layout, env.path, run_id=rid, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert done.status == "finished"
    assert record_of(env, rid)["waiters"][0]["woken"]["status"] == "collected"


def test_gate_wait_collects_an_orphaned_run(env):
    rid = notified(env)
    clock = type(CLOCK)(wall=lambda: LATER, monotonic=CLOCK.monotonic)
    done = run_gate_wait(env.layout, env.path, run_id=rid, timeout=0, clock=clock, sleep=no_sleep, today=TODAY)
    assert done.status == "orphaned"
    assert record_of(env, rid)["waiters"][0]["woken"]["status"] == "collected"


def test_a_running_wait_collects_nothing(env):
    rid = notified(env)
    env.mark_runner_alive(rid)
    done = run_gate_wait(env.layout, env.path, run_id=rid, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert done.status in ("running", "queued")
    assert record_of(env, rid)["waiters"][0]["woken"] is None
