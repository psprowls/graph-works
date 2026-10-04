"""The runner wakes each registered waiter once, after every receipt; a wake never touches the gate."""

from __future__ import annotations

import json

from _gate_helpers import NOW, add_item, no_sleep, receipt_text, record_of, ticks
from conftest import GATE
from graph_works_core.orchestrate import gate_runner
from graph_works_core.orchestrate.gate import run_gate_run, runs_dir
from graph_works_core.orchestrate.gate_wake import WakeOutcome, resume_line

DELIVERED = WakeOutcome("delivered", "input_accepted,turn_started")
FAILED = WakeOutcome("failed", "exit 1: terminal not found")


class Waker:
    def __init__(self, *outcomes: WakeOutcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, terminal: str, line: str) -> WakeOutcome:
        self.calls.append((terminal, line))
        return self.outcomes.pop(0) if self.outcomes else DELIVERED


def notified(env, path=None, *, terminal="term_a", token="0a1b2c3d", notify=True):
    started = run_gate_run(
        env.layout, path or env.path, now=NOW, token=token, spawn=lambda record: None,
        notify=notify, environ={"ORCA_TERMINAL_HANDLE": terminal},
    )  # fmt: skip
    return runs_dir(env.layout) / f"{started.run_id}.json"


def execute(record, wake, slept=None):
    sleep = slept.append if slept is not None else no_sleep
    return gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(0, 1), sleep=sleep, wake=wake)


def test_each_waiter_is_woken_once_after_every_receipt(env):
    other = add_item(env)
    record = notified(env)
    notified(env, other, terminal="term_b", token="1b2c3d4e")
    seen = []

    def wake(terminal, line):
        seen.append((terminal, line, record_of(env, record.stem)["recorded"]))
        return DELIVERED

    assert execute(record, wake) == 0
    rid = record.stem
    assert seen == [("term_a", resume_line(rid, env.path, 0), True), ("term_b", resume_line(rid, other, 0), True)]
    stamp = {"status": "delivered", "at": "2026-09-28T12:00:00Z", "detail": "input_accepted,turn_started"}
    assert [w["woken"] for w in record_of(env, rid)["waiters"]] == [stamp, stamp]


def test_a_failing_wake_is_retried_then_recorded_failed(env):
    record = notified(env)
    waker, slept = Waker(FAILED, FAILED, FAILED), []
    assert execute(record, waker, slept) == 0
    assert len(waker.calls) == 3 and slept == [1.0, 2.0, 4.0]
    data = record_of(env, record.stem)
    assert data["waiters"][0]["woken"]["status"] == "failed" and data["waiters"][0]["woken"]["detail"] == FAILED.detail
    assert data["result"]["exit"] == 0 and data["recorded"] is True


def test_a_second_attempt_can_deliver(env):
    record = notified(env)
    waker, slept = Waker(FAILED, DELIVERED), []
    execute(record, waker, slept)
    assert len(waker.calls) == 2 and slept == [1.0]
    assert record_of(env, record.stem)["waiters"][0]["woken"]["status"] == "delivered"


def test_a_raising_wake_never_touches_the_gate(env):
    record = notified(env)

    def boom(terminal, line):
        raise OSError("orca vanished")

    assert execute(record, boom) == 0
    data = record_of(env, record.stem)
    assert data["result"]["exit"] == 0 and data["recorded"] is True and data["waiters"][0]["woken"] is None
    assert record.stem in receipt_text(env)


def test_a_red_gate_wakes_with_its_exit_and_the_runner_still_exits_0(env):
    env.set_manifest(gate=GATE.replace("full: 'true'", "full: 'false'"))
    record = notified(env)
    waker = Waker()
    assert execute(record, waker) == 0
    assert waker.calls == [("term_a", resume_line(record.stem, env.path, 1))]


def test_no_waiters_means_no_wake(env):
    record = notified(env, notify=False)
    waker = Waker()
    execute(record, waker)
    assert waker.calls == []


def test_a_settled_waiter_is_not_woken_again(env):
    record = notified(env)
    execute(record, Waker())
    waker = Waker()
    assert execute(record, waker) == 0  # result present: the run is not repeated
    assert waker.calls == []


def test_a_collected_waiter_is_not_woken(env):
    record = notified(env)
    data = json.loads(record.read_text(encoding="utf-8"))
    data["waiters"][0]["woken"] = {"status": "collected", "at": "2026-09-28T12:00:00Z", "detail": "gate wait"}
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    waker = Waker()
    execute(record, waker)
    assert waker.calls == []


def test_a_non_oserror_wake_failure_leaves_the_other_waiters_woken(env):
    other = add_item(env)
    record = notified(env)
    notified(env, other, terminal="term_b", token="1b2c3d4e")
    calls = []

    def wake(terminal, line):
        calls.append(terminal)
        if terminal == "term_a":
            raise AttributeError("boom")
        return DELIVERED

    assert execute(record, wake) == 0
    woken = {w["terminal"]: w["woken"] for w in record_of(env, record.stem)["waiters"]}
    assert woken["term_a"] is None and woken["term_b"]["status"] == "delivered"
    assert "term_b" in calls


def test_malformed_waiters_are_skipped_and_the_rest_woken(env):
    record = notified(env)
    data = json.loads(record.read_text(encoding="utf-8"))
    data["waiters"] = [{"path": 3, "terminal": "x", "woken": None}, "junk", {"terminal": "t"}, *data["waiters"]]
    record.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    waker = Waker()
    assert execute(record, waker) == 0
    assert [c[0] for c in waker.calls] == ["term_a"]
