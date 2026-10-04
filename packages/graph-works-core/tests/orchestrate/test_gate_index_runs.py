"""The workspace-wide run index: one run per key, every requester recorded."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from _gate_helpers import CLOCK, NOW, TODAY, add_item, finish_unrecorded, no_sleep, record_of
from graph_works_core.orchestrate import gate, gate_runner
from graph_works_core.orchestrate.gate import RunKey, prune, run_gate_run, run_gate_wait, runs_dir
from graph_works_core.orchestrate.gate_receipts import parse_gate_receipt
from okf_ext.locking import locked


def _execute(env, run_id: str) -> int:
    record = runs_dir(env.layout) / f"{run_id}.json"
    return gate_runner.execute(record, wall=lambda: NOW, monotonic=iter([0.0, 1.0]).__next__, sleep=no_sleep)


def _rewrite(record, **fields) -> None:
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(fields)
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def test_a_new_record_is_version_2_with_the_starter_as_first_requester(env):
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    data = record_of(env, started.run_id)
    assert data["version"] == 2 and data["requesters"] == [env.path] and data["status"] == "queued"
    assert data["recorded_for"] == [] and data["skipped_for"] == [] and "owner" not in data
    assert env.spawned == [runs_dir(env.layout) / f"{started.run_id}.json"]
    assert runs_dir(env.layout) == env.layout.cache_dir / "gate" / "runs"
    assert not (env.layout.cache_dir / "gate-runs").exists()


def test_run_key_matches_the_full_execution_request(env):
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    data = record_of(env, started.run_id)
    key = RunKey.of(data)
    assert (key.repo, key.tree, key.scope, key.command) == ("code", env.tree, "full", "true")
    assert key.matches(data)
    assert not key.matches({**data, "jobs": 99})  # a per-package field the join compares
    assert not key.matches({**data, "tree": "0" * 40})


def test_two_items_on_one_tree_share_one_runner(env):
    other = add_item(env)
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    env.mark_runner_alive(first.run_id)
    second = run_gate_run(env.layout, other, now=NOW, token="bbbbbbbb", spawn=env.spawn)
    assert (second.status, second.run_id, second.log_path) == ("running", first.run_id, first.log_path)
    assert second.command == first.command
    assert len(env.spawned) == 1 and record_of(env, first.run_id)["requesters"] == [env.path, other]
    again = run_gate_run(env.layout, other, now=NOW, token="cccccccc", spawn=env.spawn)
    assert again.run_id == first.run_id and record_of(env, first.run_id)["requesters"] == [env.path, other]


def test_the_runner_records_on_every_requester(env):
    other = add_item(env)
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    run_gate_run(env.layout, other, now=NOW + timedelta(seconds=1), token="bbbbbbbb", spawn=env.spawn)
    assert env.spawned == []  # joined within the start grace
    assert _execute(env, first.run_id) == 0
    data = record_of(env, first.run_id)
    assert data["recorded_for"] == [env.path, other] and data["recorded"] is True
    for owner in (env.path, other):
        text = (env.layout.bundle_dir / owner / "references/03-gate-receipts.md").read_text(encoding="utf-8")
        assert [run.run_id for run in parse_gate_receipt(text)[1]] == [first.run_id]
    for owner in (env.path, other):
        waited = run_gate_wait(env.layout, owner, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
        assert (waited.status, waited.exit, waited.recorded) == ("finished", 0, True)
        assert waited.receipt_path == f"{owner}/references/03-gate-receipts.md"


def test_a_terminal_requester_is_skipped_and_the_rest_recorded(env):
    other = add_item(env, status="resolved")
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{first.run_id}.json"
    _rewrite(record, requesters=[env.path, other])
    _execute(env, first.run_id)
    data = record_of(env, first.run_id)
    assert (data["recorded_for"], data["skipped_for"], data["recorded"]) == ([env.path], [other], True)


def test_recovery_records_only_the_missing_requesters(env, monkeypatch):
    other = add_item(env)
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{first.run_id}.json"
    finish_unrecorded(record, exit=0)
    _rewrite(record, requesters=[env.path, other], recorded_for=[env.path])
    seen: list[str] = []
    real = gate.record_gate_run
    monkeypatch.setattr(
        gate, "record_gate_run", lambda layout, owner, run, **k: seen.append(owner) or real(layout, owner, run, **k)
    )
    gate.recover_unrecorded(env.layout, other, now=NOW + timedelta(minutes=5))
    assert seen == [other] and record_of(env, first.run_id)["recorded"] is True


def test_recovery_ignores_records_that_do_not_name_the_item(env, monkeypatch):
    other = add_item(env)
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    finish_unrecorded(runs_dir(env.layout) / f"{first.run_id}.json", exit=0)
    monkeypatch.setattr(gate, "record_gate_run", lambda *a, **k: pytest.fail("recorded a foreign run"))
    gate.recover_unrecorded(env.layout, other, now=NOW + timedelta(minutes=5))
    assert record_of(env, first.run_id)["recorded"] is False


def test_wait_by_run_id_from_a_non_requester_reports_without_a_receipt(env):
    other = add_item(env)
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    _execute(env, first.run_id)
    waited = run_gate_wait(env.layout, other, run_id=first.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert (waited.status, waited.exit, waited.recorded, waited.receipt_path) == ("finished", 0, True, None)


def test_wait_without_a_run_id_finds_only_the_items_own_runs(env):
    other = add_item(env)
    run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    waited = run_gate_wait(env.layout, other, run_id=None, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY)
    assert waited.refusal == "no-run"


def test_a_satisfied_while_queued_result_records_nothing(env, monkeypatch):
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{first.run_id}.json"
    _rewrite(record, runner_started=True, result={"exit": 0, "satisfied_by": "20260928T110000Z-00000001"})
    monkeypatch.setattr(gate, "record_gate_run", lambda *a, **k: pytest.fail("recorded a satisfied run"))
    assert gate.record_requesters(env.layout, record, today=TODAY) == {}
    waited = run_gate_wait(
        env.layout, env.path, run_id=first.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert (waited.status, waited.exit, waited.receipt_path) == ("finished", 0, None)
    assert waited.detail == "satisfied by 20260928T110000Z-00000001"


@pytest.mark.parametrize(
    "mutation",
    [
        {"requesters": "work/feature-a"},
        {"requesters": [3]},
        {"requesters": []},
        {"requesters": None},
        {"version": 1},
        {"schema": "other"},
    ],
)
def test_a_malformed_or_foreign_record_is_skipped_everywhere(env, mutation):
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{first.run_id}.json"
    env.mark_runner_alive(first.run_id)
    _rewrite(record, **mutation)
    second = run_gate_run(env.layout, env.path, now=NOW, token="bbbbbbbb", spawn=env.spawn)
    assert second.status == "started" and second.run_id != first.run_id
    assert gate.live_pending(runs_dir(env.layout), NOW) == [
        (runs_dir(env.layout) / f"{second.run_id}.json", record_of(env, second.run_id))
    ]
    gate.recover_unrecorded(env.layout, env.path, now=NOW)
    prune(runs_dir(env.layout), NOW + timedelta(days=30))
    assert record.exists()
    waited = run_gate_wait(
        env.layout, env.path, run_id=first.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert waited.refusal == "no-run"


def test_a_torn_finished_record_is_left_alone_by_recovery_and_prune(env):
    first = run_gate_run(env.layout, env.path, now=NOW - timedelta(days=20), token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{first.run_id}.json"
    finish_unrecorded(record, exit=0)
    _rewrite(record, requesters=["work/feature-a", 7], recorded=True)
    gate.recover_unrecorded(env.layout, env.path, now=NOW)
    run_gate_run(env.layout, env.path, now=NOW, token="bbbbbbbb", spawn=env.spawn)
    assert record.exists()


def test_prune_removes_only_recorded_dead_old_records(env):
    def make(token: str, *, recorded: bool, age: timedelta, alive: bool = False) -> str:
        started = run_gate_run(env.layout, env.path, now=NOW - age, token=token, spawn=lambda p: None)
        assert started.run_id is not None, started
        record = runs_dir(env.layout) / f"{started.run_id}.json"
        finish_unrecorded(record, exit=0)
        _rewrite(record, recorded=recorded)
        if alive:
            env.mark_runner_alive(started.run_id)
        return started.run_id

    # The unrecorded record is made last: a later `gate run` would otherwise recover (record) it.
    old = make("aaaaaaaa", recorded=True, age=timedelta(days=15))
    young = make("bbbbbbbb", recorded=True, age=timedelta(days=13))
    live = make("dddddddd", recorded=True, age=timedelta(days=15), alive=True)
    unrecorded = make("cccccccc", recorded=False, age=timedelta(days=15))
    with locked(runs_dir(env.layout) / ".dir.lock"):
        prune(runs_dir(env.layout), NOW)
    left = {p.stem for p in runs_dir(env.layout).glob("*.json")}
    assert left == {young, unrecorded, live}
    assert not (runs_dir(env.layout) / f"{old}.lock").exists()


def test_gate_run_prunes(env):
    stale = run_gate_run(env.layout, env.path, now=NOW - timedelta(days=20), token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{stale.run_id}.json"
    finish_unrecorded(record, exit=0)
    _rewrite(record, recorded=True, recorded_for=[env.path])
    run_gate_run(env.layout, env.path, now=NOW, token="bbbbbbbb", spawn=env.spawn)
    assert not record.exists()
