"""`gw work gate run --notify`: a waiter registration on the run record (D-001, D-003, D-007, D-008)."""

from __future__ import annotations

import json

import pytest
from _gate_helpers import NOW, add_item, record_of, write_receipt
from graph_works_core.orchestrate.gate import NotifyResult, run_gate_run, runs_dir

TERM_A = {"ORCA_TERMINAL_HANDLE": "term_a"}
TERM_B = {"ORCA_TERMINAL_HANDLE": "term_b"}


def run(env, path=None, *, token="0a1b2c3d", environ=TERM_A, notify=True, spawn=None):
    return run_gate_run(
        env.layout, path or env.path, now=NOW, token=token, spawn=spawn or env.spawn, notify=notify, environ=environ
    )


def waiter(path: str, terminal: str) -> dict:
    return {"path": path, "terminal": terminal, "registered_at": "2026-09-28T12:00:00Z", "woken": None}


def edit(env, run_id: str, **fields) -> None:
    record = runs_dir(env.layout) / f"{run_id}.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(fields)
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def test_starting_with_notify_registers_the_starter(env):
    started = run(env)
    assert started.status == "started" and started.notify == NotifyResult(True, "term_a", None)
    assert record_of(env, started.run_id)["waiters"] == [waiter(env.path, "term_a")]


def test_without_notify_nothing_registers(env):
    started = run(env, notify=False)
    assert started.notify is None and record_of(env, started.run_id)["waiters"] == []


@pytest.mark.parametrize("environ", [{}, {"ORCA_TERMINAL_HANDLE": ""}, {"ORCA_TERMINAL_HANDLE": "   "}])
def test_no_terminal_runs_unregistered(env, environ):
    started = run(env, environ=environ)
    assert started.status == "started" and started.notify == NotifyResult(False, None, "no-terminal")
    assert record_of(env, started.run_id)["waiters"] == []


def test_a_joiner_registers_beside_the_starter(env):
    other = add_item(env)
    first = run(env)
    joined = run(env, other, token="1b2c3d4e", environ=TERM_B)
    assert joined.run_id == first.run_id and joined.notify == NotifyResult(True, "term_b", None)
    assert record_of(env, first.run_id)["waiters"] == [waiter(env.path, "term_a"), waiter(other, "term_b")]
    assert len(env.spawned) == 1


def test_registering_twice_does_not_duplicate(env):
    first = run(env)
    again = run(env, token="1b2c3d4e")
    assert again.run_id == first.run_id and again.notify == NotifyResult(True, "term_a", None)
    assert record_of(env, first.run_id)["waiters"] == [waiter(env.path, "term_a")]


def test_joining_without_notify_leaves_the_waiters_alone(env):
    first = run(env)
    joined = run(env, token="1b2c3d4e", notify=False)
    assert joined.notify is None and record_of(env, first.run_id)["waiters"] == [waiter(env.path, "term_a")]


def test_a_satisfied_request_never_registers(env):
    write_receipt(env.layout.bundle_dir, env.path, worktree=env.repo, tree=env.tree)
    result = run(env)
    assert result.status == "satisfied" and result.notify == NotifyResult(False, "term_a", "satisfied")
    assert not list(runs_dir(env.layout).glob("*.json"))


def test_a_failed_spawn_reports_runner_failed_and_keeps_the_waiter(env):
    def boom(record):
        raise OSError("no python")

    result = run(env, spawn=boom)
    assert result.refusal == "runner-failed" and result.notify == NotifyResult(False, "term_a", "runner-failed")
    # a joiner may already be registered: the entry stays and reads wake_failed/orphaned for the coordinator
    assert record_of(env, result.run_id)["waiters"] == [waiter(env.path, "term_a")]


def test_a_finished_run_is_never_joined(env):
    """D-007: a request after a result starts a new run; the finished record's waiters are untouched."""
    first = run(env)
    edit(env, first.run_id, result={"exit": 1, "tree_changed": False, "duration_s": 1.0, "log_tail": ""})
    second = run(env, token="1b2c3d4e", environ=TERM_B)
    assert second.run_id != first.run_id and second.notify == NotifyResult(True, "term_b", None)
    assert record_of(env, first.run_id)["waiters"] == [waiter(env.path, "term_a")]


@pytest.mark.parametrize("bad", ["x", None, [3], [{"path": "work/feature-a"}]])
def test_a_malformed_waiter_list_still_registers(env, bad):
    first = run(env, notify=False)
    edit(env, first.run_id, waiters=bad)
    joined = run(env, token="1b2c3d4e")
    assert joined.notify == NotifyResult(True, "term_a", None)
    assert waiter(env.path, "term_a") in record_of(env, first.run_id)["waiters"]
