"""max_concurrent: queued runs, FIFO slots, and the re-checks a slot triggers."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import timedelta

from _gate_helpers import CLOCK, NOW, TODAY, mint_receipt, no_sleep, record_of, set_max_concurrent
from graph_works_core.orchestrate import gate, gate_runner
from graph_works_core.orchestrate.gate import queue_head, run_gate_run, run_gate_wait, runs_dir, slots_dir
from okf_ext.locking import locked


def _commit(env, name: str) -> None:
    (env.repo / name).write_text(name, encoding="utf-8", newline="\n")
    for args in (["add", name], ["commit", "-qm", name]):
        subprocess.run(["git", *args], cwd=env.repo, capture_output=True, text=True, check=True)


def _running(env, run_id: str) -> None:
    record = runs_dir(env.layout) / f"{run_id}.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data.update(status="running", runner_started=True)
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def test_unlimited_never_queues(env):
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    env.mark_runner_alive(first.run_id)
    _running(env, first.run_id)
    _commit(env, "b.txt")
    second = run_gate_run(env.layout, env.path, now=NOW, token="bbbbbbbb", spawn=env.spawn)
    assert (second.status, second.position) == ("started", None)


def test_a_second_tree_queues_behind_the_limit(env):
    set_max_concurrent(env, "1")
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    assert (first.status, first.position) == ("started", None)
    env.mark_runner_alive(first.run_id)
    _running(env, first.run_id)
    _commit(env, "b.txt")
    second = run_gate_run(env.layout, env.path, now=NOW + timedelta(seconds=1), token="bbbbbbbb", spawn=env.spawn)
    assert (second.status, second.position) == ("queued", 0)
    env.mark_runner_alive(second.run_id)
    waited = run_gate_wait(
        env.layout, env.path, run_id=second.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert (waited.status, waited.position) == ("queued", 0)
    joined = run_gate_run(env.layout, env.path, now=NOW + timedelta(seconds=2), token="cccccccc", spawn=env.spawn)
    assert (joined.status, joined.run_id, joined.position) == ("queued", second.run_id, 0)


def test_queue_order_is_fifo_and_skips_dead_records(env):
    set_max_concurrent(env, "1")
    ids = []
    for index, token in enumerate(("aaaaaaaa", "bbbbbbbb", "cccccccc")):
        _commit(env, f"f{index}.txt")
        started = run_gate_run(env.layout, env.path, now=NOW + timedelta(seconds=index), token=token, spawn=env.spawn)
        ids.append(started.run_id)
    env.mark_runner_alive(ids[1])
    env.mark_runner_alive(ids[2])
    later = NOW + timedelta(minutes=5)  # ids[0] is dead and past the start grace
    assert queue_head(runs_dir(env.layout), later) == runs_dir(env.layout) / f"{ids[1]}.json"
    third = record_of(env, ids[2])
    assert gate.queue_state(runs_dir(env.layout), runs_dir(env.layout) / f"{ids[2]}.json", third, 1, later) == 1


def test_a_queued_runner_takes_the_slot_when_it_frees(env):
    set_max_concurrent(env, "1")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{started.run_id}.json"
    holder = ExitStack()
    holder.enter_context(locked(slots_dir(env.layout) / "slot-0.lock"))
    polls: list[float] = []

    def sleep(seconds: float) -> None:
        polls.append(seconds)
        holder.close()  # the running gate finishes

    assert gate_runner.execute(record, wall=lambda: NOW, monotonic=iter([0.0, 1.0]).__next__, sleep=sleep) == 0
    assert gate.SLOT_POLL in polls
    data = record_of(env, started.run_id)
    assert data["status"] == "running" and data["result"]["exit"] == 0 and data["recorded"] is True


def test_a_killed_slot_holder_frees_its_slot(env):
    set_max_concurrent(env, "1")
    slot = slots_dir(env.layout) / "slot-0.lock"
    slot.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "import pathlib, time\nfrom okf_ext.locking import locked\n"
        f"with locked(pathlib.Path({str(slot)!r})):\n    time.sleep(60)"
    )
    holder = subprocess.Popen([sys.executable, "-c", script])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:  # wait until the child holds the slot
        try:
            with locked(slot, blocking=False):
                pass
        except OSError:
            break
        time.sleep(0.05)
    try:
        started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
        record = runs_dir(env.layout) / f"{started.run_id}.json"
        gate_runner.execute(
            record,
            wall=lambda: NOW,
            monotonic=iter([0.0, 1.0]).__next__,
            sleep=lambda s: holder.kill() or holder.wait(),
        )
    finally:
        holder.kill()
        holder.wait()
    assert record_of(env, started.run_id)["result"]["exit"] == 0


def test_a_tree_that_moved_while_queued_is_orphaned(env):
    set_max_concurrent(env, "1")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    _commit(env, "moved.txt")
    ran: list[str] = []
    record = runs_dir(env.layout) / f"{started.run_id}.json"
    gate_runner.execute(
        record,
        wall=lambda: NOW,
        monotonic=iter([0.0, 1.0]).__next__,
        sleep=no_sleep,
        run=lambda *a: ran.append("x") or 0,
    )
    assert ran == [] and record_of(env, started.run_id)["result"] == {
        "exit": None,
        "error": "tree changed while queued",
    }
    waited = run_gate_wait(
        env.layout, env.path, run_id=started.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert (waited.status, waited.detail) == ("orphaned", "tree changed while queued")


def test_a_key_satisfied_while_queued_runs_nothing(env):
    set_max_concurrent(env, "1")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    mint_receipt(env, tree=env.tree, owner="work/other")
    ran: list[str] = []
    record = runs_dir(env.layout) / f"{started.run_id}.json"
    gate_runner.execute(
        record,
        wall=lambda: NOW,
        monotonic=iter([0.0, 1.0]).__next__,
        sleep=no_sleep,
        run=lambda *a: ran.append("x") or 0,
    )
    data = record_of(env, started.run_id)
    assert ran == [] and data["recorded"] is True and data["result"]["satisfied_by"]
    waited = run_gate_wait(
        env.layout, env.path, run_id=started.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert (waited.status, waited.exit, waited.receipt_path) == ("finished", 0, None)
    assert waited.detail.startswith("satisfied by ")


def test_a_runner_that_cannot_read_the_limit_runs_unthrottled(env, monkeypatch):
    set_max_concurrent(env, "1")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    set_max_concurrent(env, "0")
    with locked(slots_dir(env.layout) / "slot-0.lock"):
        record = runs_dir(env.layout) / f"{started.run_id}.json"
        gate_runner.execute(record, wall=lambda: NOW, monotonic=iter([0.0, 1.0]).__next__, sleep=no_sleep)
    assert record_of(env, started.run_id)["result"]["exit"] == 0


def test_a_fresh_run_is_not_satisfied_away_while_queued(env):
    set_max_concurrent(env, "1")
    mint_receipt(env, tree=env.tree, owner="work/other")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None, fresh=True)
    ran: list[str] = []
    record = runs_dir(env.layout) / f"{started.run_id}.json"
    gate_runner.execute(
        record,
        wall=lambda: NOW,
        monotonic=iter([0.0, 1.0]).__next__,
        sleep=no_sleep,
        run=lambda *a: ran.append("x") or 0,
    )
    data = record_of(env, started.run_id)
    assert ran == ["x"] and data["result"]["exit"] == 0 and "satisfied_by" not in data["result"]


def test_a_fresh_joiner_keeps_a_queued_non_fresh_run_from_being_satisfied_away(env):
    set_max_concurrent(env, "1")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    joined = run_gate_run(
        env.layout, env.path, now=NOW + timedelta(seconds=1), token="bbbbbbbb", spawn=lambda p: None, fresh=True
    )
    assert joined.run_id == started.run_id
    assert record_of(env, started.run_id)["fresh"] is True
    mint_receipt(env, tree=env.tree, owner="work/other")
    ran: list[str] = []
    record = runs_dir(env.layout) / f"{started.run_id}.json"
    gate_runner.execute(
        record,
        wall=lambda: NOW,
        monotonic=iter([0.0, 1.0]).__next__,
        sleep=no_sleep,
        run=lambda *a: ran.append("x") or 0,
    )
    data = record_of(env, started.run_id)
    assert ran == ["x"] and data["result"]["exit"] == 0 and "satisfied_by" not in data["result"]


def test_queue_state_is_none_once_a_record_runs_or_finishes(env):
    set_max_concurrent(env, "1")
    first = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=env.spawn)
    env.mark_runner_alive(first.run_id)
    _running(env, first.run_id)
    path = runs_dir(env.layout) / f"{first.run_id}.json"
    assert gate.queue_state(runs_dir(env.layout), path, record_of(env, first.run_id), 1, NOW) is None
    assert gate.queue_state(runs_dir(env.layout), path, {"status": "queued", "result": {"exit": 0}}, 1, NOW) is None
    assert gate.queue_state(runs_dir(env.layout), path, {"status": "queued"}, None, NOW) is None
    assert queue_head(runs_dir(env.layout), NOW) is None


def test_a_slot_wait_polls_until_the_record_heads_the_queue(env, monkeypatch):
    set_max_concurrent(env, "1")
    started = run_gate_run(env.layout, env.path, now=NOW, token="aaaaaaaa", spawn=lambda p: None)
    record = runs_dir(env.layout) / f"{started.run_id}.json"
    heads = iter([None, record])
    monkeypatch.setattr(gate_runner, "queue_head", lambda directory, now: next(heads))
    polls: list[float] = []
    gate_runner.execute(record, wall=lambda: NOW, monotonic=iter([0.0, 1.0]).__next__, sleep=polls.append)
    assert polls.count(gate.SLOT_POLL) == 1 and record_of(env, started.run_id)["result"]["exit"] == 0
