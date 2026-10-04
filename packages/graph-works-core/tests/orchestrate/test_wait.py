"""`run_wait`: wake only for real events, over a scripted OrcaPort and an injected clock."""

from __future__ import annotations

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from graph_works_core.orchestrate.wait import (
    SLEEP_GAP_FLOOR_S,
    WAIT_FLOOR_S,
    WaitClock,
    WaitFailed,
    apply_wait_floor,
    run_wait,
)
from subagents_io.backend import BackendError

FakeOrcaPort = runpy.run_path(str(Path(__file__).with_name("fake_orca_port.py")))["FakeOrcaPort"]
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class Clock:
    """Wall and monotonic advance together unless a test opens a sleep gap."""

    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall_at = T0

    def advance(self, seconds: float, *, slept: float = 0.0) -> None:
        self.mono += seconds
        self.wall_at += timedelta(seconds=seconds + slept)

    def clock(self) -> WaitClock:
        return WaitClock(wall=lambda: self.wall_at, monotonic=lambda: self.mono)


class Fenced(BackendError):
    code = "consumer_fenced"


class Refused(BackendError):
    code = "run_not_found"


def msg(mid: str, kind: str, payload: dict | None = None, *, raw: str | None = None) -> dict:
    return {
        "id": mid,
        "type": kind,
        "subject": kind,
        "body": "",
        "from_": "term_w",
        "created_at": "t",
        "payload": {} if payload is None and raw is None else payload,
        "payload_raw": raw,
    }


def hb(mid: str) -> dict:
    return msg(mid, "heartbeat", {"taskId": "task_1", "dispatchId": "ctx_1", "phase": "implementing"})


def delivery(did: str | None, *messages: dict) -> dict:
    return {"delivery_id": did, "messages": list(messages)}


def wait(port, clock: Clock, *, ack: str | None = None, timeout_s: float = 600):
    return run_wait(port, "run_1", ack=ack, timeout_s=timeout_s, clock=clock.clock())


def test_heartbeat_only_delivery_is_self_acked_then_real_event_returned_unacked():
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("dlv_hb", hb("m1")), delivery("dlv_real", msg("m2", "escalation"))],
        on_wait=lambda ms: clock.advance(10),
    )
    result = wait(port, clock)
    assert result.status == "event"
    assert result.delivery_id == "dlv_real"
    assert [m["id"] for m in result.messages] == ["m2"]
    assert result.self_acked == 1
    assert [c for c in port.calls if c[0] == "check_ack"] == [("check_ack", ("run_1", "dlv_hb"), {})]


def test_mixed_batch_strips_heartbeats_and_leaves_delivery_unacked():
    clock = Clock()
    port = FakeOrcaPort(deliveries=[delivery("dlv_1", hb("h"), msg("q", "question"), hb("h2"))])
    result = wait(port, clock)
    assert [m["id"] for m in result.messages] == ["q"]
    assert result.self_acked == 0
    assert "check_ack" not in port.names()


def test_question_and_worker_done_are_both_returned():
    port = FakeOrcaPort(
        deliveries=[delivery("d", msg("a", "question"), msg("b", "worker_done", {"outcome": "succeeded"}))]
    )
    assert [m["id"] for m in wait(port, Clock()).messages] == ["a", "b"]


def test_unknown_type_is_real():
    port = FakeOrcaPort(deliveries=[delivery("d", msg("x", "future_kind"))])
    assert wait(port, Clock()).status == "event"


def test_undecodable_non_heartbeat_is_real_and_never_self_acked():
    port = FakeOrcaPort(deliveries=[delivery("d", msg("x", "worker_done", None, raw="{bad"))])
    result = wait(port, Clock())
    assert result.status == "event" and result.messages[0]["payload_raw"] == "{bad"
    assert "check_ack" not in port.names()


def test_undecodable_heartbeat_is_still_stripped():
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("d", msg("h", "heartbeat", None, raw="{bad"))], on_wait=lambda ms: clock.advance(700)
    )
    result = wait(port, clock)
    assert result.status == "timeout" and result.messages == () and result.self_acked == 1


def test_ack_is_passed_on_the_first_wait_only():
    clock = Clock()
    port = FakeOrcaPort(deliveries=[delivery("d1", hb("h")), delivery("d2", msg("e", "escalation"))])
    wait(port, clock, ack="dlv_prev")
    acks = [kwargs["ack"] for name, _args, kwargs in port.calls if name == "check_wait"]
    assert acks == ["dlv_prev", None]


def test_types_are_the_real_types():
    port = FakeOrcaPort(deliveries=[delivery("d", msg("e", "escalation"))])
    wait(port, Clock())
    [(_, _, kwargs)] = [c for c in port.calls if c[0] == "check_wait"]
    assert kwargs["types"] == "worker_done,escalation,question"


def test_empty_batch_is_timeout():
    clock = Clock()
    port = FakeOrcaPort(on_wait=lambda ms: clock.advance(ms / 1000))
    result = wait(port, clock, timeout_s=30)
    assert (result.status, result.delivery_id, result.messages, result.waited_s) == ("timeout", None, (), 30)


def test_deadline_shrinks_across_heartbeat_loops_then_times_out():
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("d1", hb("a")), delivery("d2", hb("b")), delivery("d3", hb("c"))],
        on_wait=lambda ms: clock.advance(250),
    )
    result = wait(port, clock, timeout_s=600)
    timeouts = [kwargs["timeout_ms"] for name, _a, kwargs in port.calls if name == "check_wait"]
    assert timeouts == [600000, 350000, 100000]
    assert result.status == "timeout" and result.self_acked == 3


def test_filtered_batch_without_delivery_id_is_not_acked():
    clock = Clock()
    port = FakeOrcaPort(deliveries=[delivery(None, hb("a"))], on_wait=lambda ms: clock.advance(ms / 1000))
    result = wait(port, clock, timeout_s=5)
    assert result.status == "timeout" and result.self_acked == 0
    assert "check_ack" not in port.names()


@pytest.mark.parametrize("timeout_s", [-1, -0.5])
def test_negative_timeout_is_refused(timeout_s):
    with pytest.raises(ValueError, match="timeout_s"):
        wait(FakeOrcaPort(), Clock(), timeout_s=timeout_s)


def test_consumer_fenced_rebinds_once_and_retries():
    port = FakeOrcaPort(wait_errors=[Fenced("fenced")], deliveries=[delivery("d", msg("e", "escalation"))])
    result = wait(port, Clock(), ack="dlv_prev")
    assert result.rebound is True
    assert port.names().count("run_use") == 1
    acks = [kwargs["ack"] for name, _a, kwargs in port.calls if name == "check_wait"]
    assert acks == ["dlv_prev", "dlv_prev"]  # the retried call repeats the idempotent ack


def test_fenced_retry_uses_only_budget_left_after_failed_wait_and_rebind():
    clock = Clock()
    port = FakeOrcaPort()
    attempts = 0

    def check(run_id, *, types, timeout_ms, ack):
        nonlocal attempts
        port._record("check_wait", run_id, types=types, timeout_ms=timeout_ms, ack=ack)
        attempts += 1
        clock.advance(4 if attempts == 1 else timeout_ms / 1000)
        if attempts == 1:
            raise Fenced("fenced")
        return delivery(None)

    def rebind(run_id):
        port._record("run_use", run_id)
        clock.advance(4)

    port.check_wait = check
    port.run_use = rebind
    result = wait(port, clock, timeout_s=10)
    waits = [kwargs["timeout_ms"] for name, _args, kwargs in port.calls if name == "check_wait"]
    assert waits == [10000, 2000]
    assert result.waited_s == 10 and result.rebound


@pytest.mark.parametrize("ack,expected_waits", [(None, [10000]), ("dlv_prev", [10000, 1])])
def test_fenced_rebind_after_deadline_skips_retry_except_pending_ack(ack, expected_waits):
    clock = Clock()
    port = FakeOrcaPort(wait_errors=[Fenced("fenced")])
    original_check = port.check_wait

    def check(run_id, *, types, timeout_ms, ack):
        clock.advance(6 if port.wait_errors else timeout_ms / 1000)
        return original_check(run_id, types=types, timeout_ms=timeout_ms, ack=ack)

    def rebind(run_id):
        port._record("run_use", run_id)
        clock.advance(6)

    port.check_wait = check
    port.run_use = rebind
    result = wait(port, clock, ack=ack, timeout_s=10)
    waits = [kwargs for name, _args, kwargs in port.calls if name == "check_wait"]
    assert [row["timeout_ms"] for row in waits] == expected_waits
    assert all(row["ack"] == ack for row in waits)
    assert result.status == "timeout" and result.rebound and result.waited_s >= 12


def test_fenced_twice_raises_wait_failed():
    port = FakeOrcaPort(wait_errors=[Fenced("a"), Fenced("b")])
    with pytest.raises(WaitFailed) as caught:
        wait(port, Clock())
    assert (caught.value.run_id, caught.value.code) == ("run_1", "consumer_fenced")
    assert port.names().count("run_use") == 1


def test_fenced_self_ack_rebinds_and_retries():
    clock = Clock()
    port = FakeOrcaPort(deliveries=[delivery("d1", hb("a")), delivery("d2", msg("e", "escalation"))])
    fenced = [Fenced("f")]
    original = port.check_ack

    def flaky(run_id, delivery_id):
        original(run_id, delivery_id)
        if fenced:
            raise fenced.pop()

    port.check_ack = flaky
    result = wait(port, clock)
    assert result.rebound and result.self_acked == 1 and port.names().count("check_ack") == 2


def test_other_backend_errors_become_wait_failed_without_acking():
    port = FakeOrcaPort(wait_errors=[Refused("gone")])
    with pytest.raises(WaitFailed) as caught:
        wait(port, Clock())
    assert caught.value.code == "run_not_found"
    assert "run_use" not in port.names() and "check_ack" not in port.names()


def test_sleep_gap_is_wall_minus_monotonic():
    clock = Clock()
    port = FakeOrcaPort(on_wait=lambda ms: clock.advance(600, slept=3000))
    result = wait(port, clock)
    assert result.sleep_gap_s == 3000 and result.waited_s == 600


def test_sleep_gap_under_floor_is_none():
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("d", msg("e", "escalation"))],
        on_wait=lambda ms: clock.advance(5, slept=SLEEP_GAP_FLOOR_S - 1),
    )
    result = wait(port, clock)
    assert result.status == "event" and result.sleep_gap_s is None


def test_sleep_gap_at_floor_is_reported_on_event():
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("d", msg("e", "escalation"))], on_wait=lambda ms: clock.advance(5, slept=SLEEP_GAP_FLOOR_S)
    )
    assert wait(port, clock).sleep_gap_s == SLEEP_GAP_FLOOR_S


def test_never_nudges_or_reads_workers():
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("d1", hb("a")), delivery("d2", msg("e", "escalation"))],
        on_wait=lambda ms: clock.advance(1),
    )
    wait(port, clock)
    assert not {"worker_show", "worker_read", "terminal_send_enter"} & set(port.names())


def test_initial_ack_is_sent_after_deadline_expires_during_clock_sampling():
    clock = Clock()
    reads = iter([1000.0, 1001.0, 1001.0, 1001.0])
    supplied = WaitClock(wall=lambda: clock.wall_at, monotonic=lambda: next(reads))
    port = FakeOrcaPort()
    result = run_wait(port, "run_1", ack="dlv_prev", timeout_s=0.5, clock=supplied)
    assert result.status == "timeout"
    waits = [kwargs for name, _args, kwargs in port.calls if name == "check_wait"]
    assert len(waits) == 1 and waits[0]["ack"] == "dlv_prev"
    assert waits[0]["timeout_ms"] == 1


def settled_run() -> dict:
    """A single-attempt Task completed by its worker's terminal."""
    return {
        "workers": [
            {
                "dispatch_id": "ctx_1",
                "task_id": "task_1",
                "state": "succeeded",
                "dispatch_status": "completed",
                "worktree_id": None,
                "release_state": "released",
                "terminal": "term_1",
            },
        ],
        "tasks": [
            {
                "id": "task_1",
                "title": "k",
                "display_name": "k",
                "status": "completed",
                "spec": None,
                "result": {"provenance": "worker_report", "completedBy": "term_1", "outcome": "succeeded"},
            },
        ],
    }


def done(mid: str = "m_dup", **payload) -> dict:
    return msg(mid, "worker_done", {"taskId": "task_1", "dispatchId": "ctx_1", "outcome": "succeeded", **payload})


def test_duplicate_completion_alone_is_absorbed_and_self_acked():
    clock = Clock()
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d_dup", done())], on_wait=lambda ms: clock.advance(700))
    result = wait(port, clock)
    assert result.status == "timeout" and result.messages == ()
    assert [(a.message_id, a.type, a.dispatch_id, a.reason) for a in result.absorbed] == [
        ("m_dup", "worker_done", "ctx_1", "duplicate-completion")
    ]
    assert result.self_acked == 1
    assert port.names().count("worker_list") == port.names().count("task_list") == 1


def test_absorbed_beside_a_real_message_leaves_delivery_unacked():
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", done(), msg("q", "question"))])
    result = wait(port, Clock())
    assert [m["id"] for m in result.messages] == ["q"] and len(result.absorbed) == 1
    assert "check_ack" not in port.names()


def _mutate(state: dict, where: str, key: str, value) -> dict:
    row = state["workers"][0] if where == "worker" else state["tasks"][0]
    if value is KeyError:
        row.pop(key)
    else:
        row[key] = value
    return state


@pytest.mark.parametrize(
    ("where", "key", "value"),
    [
        ("worker", "state", "running"),
        ("worker", "state", None),
        ("worker", "state", []),
        ("worker", "state", {}),
        ("worker", "release_state", "retained"),
        ("worker", "release_state", None),
        ("worker", "release_state", KeyError),
        ("worker", "terminal", None),
        ("worker", "terminal", KeyError),
        ("worker", "terminal", []),
        ("worker", "terminal", {}),
        ("worker", "task_id", None),
        ("worker", "task_id", []),
        ("worker", "task_id", {}),
        ("task", "status", "dispatched"),
        ("task", "result", None),
        ("task", "result", KeyError),
        ("task", "result", {"provenance": "coordinator", "completedBy": "term_1"}),
        ("task", "result", {"provenance": "worker_report", "completedBy": "term_other"}),
        ("task", "result", {"provenance": "worker_report"}),
        ("task", "result", {"provenance": "worker_report", "completedBy": []}),
        ("task", "result", {"provenance": "worker_report", "completedBy": {}}),
    ],
)
def test_each_precondition_false_keeps_the_message(where, key, value):
    port = FakeOrcaPort(**_mutate(settled_run(), where, key, value), deliveries=[delivery("d", done())])
    result = wait(port, Clock())
    assert result.status == "event" and result.absorbed == ()


@pytest.mark.parametrize("dispatch_id", ["ctx_other", None, "", [], {}])
def test_unknown_or_malformed_dispatch_is_kept(dispatch_id):
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", done(dispatchId=dispatch_id))])
    assert wait(port, Clock()).status == "event"


@pytest.mark.parametrize("task_id", ["task_other", None, [], {}])
def test_present_mismatched_task_id_is_kept(task_id):
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", done(taskId=task_id))])
    assert wait(port, Clock()).status == "event"


def test_missing_payload_task_id_can_be_absorbed():
    state = settled_run()
    message = done()
    message["payload"].pop("taskId")
    port = FakeOrcaPort(**state, deliveries=[delivery("d", message)])
    assert len(wait(port, Clock()).absorbed) == 1


def test_missing_payload_dispatch_id_is_kept():
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", msg("m", "worker_done", {"outcome": "succeeded"}))])
    assert wait(port, Clock()).status == "event"


def test_multi_attempt_task_is_kept():
    state = settled_run()
    state["workers"].append({**state["workers"][0], "dispatch_id": "ctx_0", "terminal": "term_0"})
    port = FakeOrcaPort(**state, deliveries=[delivery("d", done())])
    assert wait(port, Clock()).status == "event"


def test_duplicated_dispatch_row_is_kept():
    state = settled_run()
    state["workers"].append(dict(state["workers"][0]))
    port = FakeOrcaPort(**state, deliveries=[delivery("d", done())])
    assert wait(port, Clock()).status == "event"


def test_duplicated_task_row_is_kept():
    state = settled_run()
    state["tasks"].append(dict(state["tasks"][0]))
    port = FakeOrcaPort(**state, deliveries=[delivery("d", done())])
    assert wait(port, Clock()).status == "event"


def test_stopped_released_worker_without_result_is_kept():
    state = settled_run()
    state["workers"][0]["state"] = "stopped"
    state["tasks"][0].update(status="blocked", result=None)
    port = FakeOrcaPort(**state, deliveries=[delivery("d", done())])
    assert wait(port, Clock()).status == "event"


@pytest.mark.parametrize("failing", ["worker_list", "task_list"])
def test_a_failed_read_keeps_the_message(failing):
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", done())])
    port.fail[failing] = BackendError("unavailable")
    result = wait(port, Clock())
    assert result.status == "event" and result.absorbed == ()


def test_undecodable_worker_done_skips_the_reads():
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", msg("m", "worker_done", None, raw="{bad"))])
    assert wait(port, Clock()).status == "event"
    assert not {"worker_list", "task_list"} & set(port.names())


def test_batches_without_worker_done_cost_no_reads():
    port = FakeOrcaPort(**settled_run(), deliveries=[delivery("d", msg("e", "escalation"))])
    wait(port, Clock())
    assert not {"worker_list", "task_list"} & set(port.names())


def test_absorbed_accumulates_across_loops():
    clock = Clock()
    port = FakeOrcaPort(
        **settled_run(),
        deliveries=[delivery("d1", done("a")), delivery("d2", done("b")), delivery("d3", msg("e", "escalation"))],
        on_wait=lambda ms: clock.advance(1),
    )
    result = wait(port, clock)
    assert [a.message_id for a in result.absorbed] == ["a", "b"] and result.self_acked == 2


def test_timeout_liveness_uses_the_final_sleep_gap_sample():
    final = T0 + timedelta(seconds=3600)
    walls = iter([T0, final])
    monos = iter([0.0, 0.0, 600.0])
    port = FakeOrcaPort()
    rows = [{"handle": "ctx_1", "heartbeat_age_s": 90}]

    def observe(run_id, *, now):
        port._record("liveness", run_id, now=now)
        return rows

    port.liveness = observe
    result = run_wait(
        port, "run_1", ack=None, timeout_s=600, clock=WaitClock(wall=lambda: next(walls), monotonic=lambda: next(monos))
    )
    assert result.sleep_gap_s == 3000 and result.waited_s == 600
    assert result.liveness == rows
    assert [c for c in port.calls if c[0] == "liveness"] == [("liveness", ("run_1",), {"now": final})]


def test_timeout_with_no_live_workers_has_empty_liveness():
    port = FakeOrcaPort()
    assert wait(port, Clock()).liveness == []
    assert port.names().count("liveness") == 1


def test_event_has_null_liveness_without_observation():
    port = FakeOrcaPort(deliveries=[delivery("d", msg("e", "question"))])
    port.fail["liveness"] = AssertionError("events must not observe")
    assert wait(port, Clock()).liveness is None
    assert "liveness" not in port.names()


def test_liveness_failure_is_wait_failed_without_rebinding():
    port = FakeOrcaPort()
    port.fail["liveness"] = Fenced("observation failed")
    with pytest.raises(WaitFailed, match="observation failed") as caught:
        wait(port, Clock())
    assert caught.value.code == "consumer_fenced"
    assert "run_use" not in port.names()


QUESTION = {
    "message_id": "msg_0000000000b2",
    "label": "q-00b2",
    "dispatch_id": "ctx_b",
    "task_id": "task_b",
    "question": "Merge?",
    "options": ["merge", "hold"],
    "ask_resource": None,
    "asked_at": "2026-09-27T15:30:00Z",
}


@pytest.mark.parametrize("event", [False, True])
@pytest.mark.parametrize("slept", [0, 3000])
def test_pending_questions_on_event_timeout_and_sleep_gap(event, slept):
    clock = Clock()
    port = FakeOrcaPort(
        deliveries=[delivery("hb", hb("h")), delivery("real", done())] if event else [],
        on_wait=lambda ms: clock.advance(1 if event else ms / 1000, slept=slept),
    )
    port.pending = {"questions": [QUESTION], "truncated": False, "warnings": []}
    result = wait(port, clock, timeout_s=5)
    assert result.status == ("event" if event else "timeout")
    assert result.pending_questions == (QUESTION,)
    assert result.warnings == ()
    assert port.names().count("pending_questions") == 1
    assert port.calls[-1] == ("pending_questions", ("run_1",), {})
    assert result.sleep_gap_s == (slept * (2 if event else 1) or None)
    if event:
        assert result.delivery_id == "real" and result.self_acked == 1
        assert [c[1][1] for c in port.calls if c[0] == "check_ack"] == ["hb"]
    else:
        assert port.names().count("liveness") == 1


@pytest.mark.parametrize("ack", [None, "dlv_prev"])
def test_zero_timeout_is_a_pure_pending_read_even_with_ack(ack):
    port = FakeOrcaPort()
    port.pending = {"questions": [QUESTION], "truncated": False, "warnings": []}
    result = wait(port, Clock(), timeout_s=0, ack=ack)
    assert result.status == "timeout" and result.pending_questions == (QUESTION,)
    assert result.delivery_id is None and result.messages == () and result.absorbed == ()
    assert result.self_acked == 0 and not result.rebound and result.waited_s == 0
    assert result.liveness is None
    assert port.calls == [("pending_questions", ("run_1",), {})]


@pytest.mark.parametrize("timeout_s", [0, 5])
@pytest.mark.parametrize("event", [False, True])
def test_failed_pending_read_is_null_plus_warning_without_fence_retry(timeout_s, event):
    port = FakeOrcaPort(deliveries=[delivery("d", msg("e", "question"))] if event else [])
    port.fail["pending_questions"] = Fenced("inbox refused")
    result = wait(port, Clock(), timeout_s=timeout_s)
    assert result.status == ("event" if event and timeout_s else "timeout")
    assert result.pending_questions is None
    assert result.warnings == ("pending questions unavailable: inbox refused",)
    assert port.names().count("pending_questions") == 1
    assert "run_use" not in port.names() and "check_ack" not in port.names()


def test_pending_truncation_and_derivation_warnings_are_merged():
    port = FakeOrcaPort()
    port.pending = {"questions": [], "truncated": True, "warnings": ["question msg_d left out: ..."]}
    result = wait(port, Clock(), timeout_s=5)
    assert result.pending_questions == ()
    assert "question msg_d left out: ..." in result.warnings
    assert any("may be incomplete" in w for w in result.warnings)


def test_wait_floor_is_55():
    assert WAIT_FLOOR_S == 55


@pytest.mark.parametrize("requested", [1, 45, 54.9])
def test_below_floor_clamps_and_warns(requested):
    effective, warning = apply_wait_floor(requested, exempt_zero=False, option="--timeout", default=540)
    assert effective == 55
    assert warning == f"--timeout {requested:g} raised to the 55 s wait floor; the default is 540"


@pytest.mark.parametrize("requested", [55, 60, 540, 600])
def test_at_floor_passes_silently(requested):
    assert apply_wait_floor(requested, exempt_zero=False, option="--timeout", default=540) == (requested, None)


def test_zero_clamps_when_not_exempt():
    effective, warning = apply_wait_floor(0, exempt_zero=False, option="--timeout", default=540)
    assert effective == 55
    assert warning == "--timeout 0 raised to the 55 s wait floor; the default is 540"


def test_zero_passes_when_exempt():
    assert apply_wait_floor(0, exempt_zero=True, option="--timeout-s", default=600) == (0, None)


def test_exempt_still_clamps_positive_values():
    effective, warning = apply_wait_floor(1, exempt_zero=True, option="--timeout-s", default=600)
    assert effective == 55
    assert warning == "--timeout-s 1 raised to the 55 s wait floor; the default is 600"


FACT = {"run_id": "r", "path": "work/a", "terminal": "term_a", "state": "running", "resume_line": None}


def _rows(port, rows):
    def observe(run_id, *, now):
        port._record("liveness", run_id, now=now)
        return rows

    port.liveness = observe


def test_timeout_rows_carry_their_gate_wait_joined_on_terminal():
    port = FakeOrcaPort()
    _rows(port, [{"handle": "ctx_1", "terminal": "term_a"}, {"handle": "ctx_2", "terminal": None}, {"handle": "ctx_3"}])
    seen = []
    result = run_wait(
        port, "run_1", ack=None, timeout_s=600, clock=Clock().clock(),
        gate_waits=lambda now: seen.append(now) or {"term_a": FACT},
    )  # fmt: skip
    assert [row["gate_wait"] for row in result.liveness] == [FACT, None, None]
    assert seen == [T0] and result.liveness[0]["handle"] == "ctx_1"


def test_a_failed_gate_wait_read_nulls_the_fact_and_warns():
    port = FakeOrcaPort()
    _rows(port, [{"handle": "ctx_1", "terminal": "term_a"}])

    def broken(now):
        raise OSError("disk gone")

    result = run_wait(port, "run_1", ack=None, timeout_s=600, clock=Clock().clock(), gate_waits=broken)
    assert result.liveness == [{"handle": "ctx_1", "terminal": "term_a", "gate_wait": None}]
    assert "gate waits unavailable: disk gone" in result.warnings


def test_an_event_never_reads_gate_waits():
    port = FakeOrcaPort(deliveries=[delivery("d", msg("e", "question"))])

    def never(now):
        raise AssertionError("events must not read gate waits")

    assert run_wait(port, "run_1", ack=None, timeout_s=600, clock=Clock().clock(), gate_waits=never).liveness is None
