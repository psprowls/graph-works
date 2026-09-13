"""Waiting, acking, replying, stopping, closing."""

from __future__ import annotations

import json

import pytest
from orca_fakes import SyntheticEvidenceRunner as FakeRunner
from orca_fakes import fixture
from subagents_io.backend import Escalation, Heartbeat, UnknownWorker, WorkerDone, WorkerQuestion
from workflow_orca import OrcaBackend
from workflow_orca._cli import OrcaResult

TARGET = "auto-drive:2026-08-14-epic-feature-orca-dispatch-backend"

OPEN = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-use",), "run_create"),
]
BATCH = [*OPEN, (("task-list",), "task_list"), (("check", "--wait"), "check_batch")]
# An empty `wait()` runs the nudge sweep, which walks `workers()` — so this
# needs `worker-list`/`worker-show` routes too. `worker_show_live` carries a
# heartbeat, the sweep's hard veto, so the sweep stops there with no need for
# `worker-read` or `terminal send` routes.
EMPTY = [
    *OPEN,
    (("task-list",), "task_list"),
    (("worker-list",), "worker_list"),
    (("worker-show",), "worker_show_live"),
    (("check", "--wait"), "check_timeout"),
]

SETTLE = [
    *OPEN,
    (("task-list",), "task_list"),
    (("worker-list",), "worker_list"),
    (("worker-show",), "worker_show_live"),
    (("check", "--wait"), "check_batch"),
    (("check", "--ack"), "check_timeout"),
    (("task-update",), "check_timeout"),
    (("worker-release",), "check_timeout"),
    (("worker-stop",), "check_timeout"),
    (("reply",), "check_timeout"),
]


def session(routes):
    runner = FakeRunner(routes)
    return OrcaBackend(run=runner).open_session(TARGET), runner


def test_wait_requests_all_four_types_with_the_timeout_in_milliseconds():
    # All four, deliberately. The reference loop omits `heartbeat`; doing the
    # same here would make `Heartbeat` a union member no Orca backend ever
    # emits — dead vocabulary the conformance suite could not exercise.
    sess, runner = session(BATCH)
    sess.wait(timeout_s=2.5)
    call = runner.calls_matching("check", "--wait")[0]
    assert runner.argv_after("--types", call) == "worker_done,escalation,question,heartbeat"
    assert runner.argv_after("--timeout-ms", call) == "2500"
    assert runner.argv_after("--run", call) == "run_4b284b42f4a3"


def test_the_batch_maps_to_one_of_each_event_class():
    sess, _ = session(BATCH)
    events = sess.wait(timeout_s=1.0)
    assert [type(e) for e in events] == [WorkerDone, WorkerQuestion, Escalation, Heartbeat]


def test_the_delivery_id_is_stamped_on_every_event():
    # Orca acks a whole batch by delivery id, so every event in the batch
    # carries the same one and `ack()` on any of them settles the batch.
    sess, _ = session(BATCH)
    assert {e.delivery_id for e in sess.wait(timeout_s=1.0)} == {"dlv_0000000000a1"}


def test_events_are_attributed_to_dispatch_keys_not_task_ids():
    sess, _ = session(BATCH)
    done = sess.wait(timeout_s=1.0)[0]
    assert done.key == "2026-08-13-test-gap-code-wiki-smoke-script#execute"
    assert done.handle == "ctx_817ed5bf5986"


def test_a_timeout_returns_an_empty_list_and_does_not_raise():
    # So a coordinator's outer loop stays a plain `while`.
    sess, _ = session(EMPTY)
    assert sess.wait(timeout_s=0.5) == []


def test_a_batch_with_no_delivery_id_key_does_not_raise():
    # Verified live: `check` omits `deliveryId` entirely when the batch is
    # empty. Reading it with [] would turn a quiet timeout into a KeyError.
    sess, _ = session(EMPTY)
    sess.wait(timeout_s=0.5)


def test_an_unknown_task_id_triggers_exactly_one_index_refresh():
    sess, runner = session(BATCH)
    before = len(runner.calls_matching("task-list"))
    sess.wait(timeout_s=1.0)
    after = len(runner.calls_matching("task-list"))
    assert after - before == 1


def test_keepalive_on_stderr_does_not_disturb_the_session():
    # End-to-end through OrcaSession, not just through `unwrap`: the runner
    # is the seam, and a runner that merged the streams must fail here.
    runner = FakeRunner(BATCH)
    runner.stderr = '{"_keepalive": true}\n'
    sess = OrcaBackend(run=runner).open_session(TARGET)
    assert len(sess.wait(timeout_s=1.0)) == 4


def test_ack_acknowledges_the_delivery():
    sess, runner = session(SETTLE)
    heartbeat = sess.wait(timeout_s=1.0)[3]
    sess.ack(heartbeat)
    call = runner.calls_matching("check", "--ack")[0]
    assert runner.argv_after("--ack", call) == "dlv_0000000000a1"
    assert runner.argv_after("--run", call) == "run_4b284b42f4a3"


def test_ack_on_an_event_with_no_delivery_id_emits_nothing():
    # `ack()` takes the event, not a bare id, so a backend with no delivery
    # concept no-ops without the caller branching. Orca has one, but a
    # replayed event may not.
    sess, runner = session(SETTLE)
    heartbeat = sess.wait(timeout_s=1.0)[3]
    before = len(runner.calls)
    sess.ack(Heartbeat(key=heartbeat.key, handle=heartbeat.handle, delivery_id=None, phase="x"))
    assert len(runner.calls) == before


def test_a_worker_done_ack_releases_after_runtime_owned_settlement():
    # Accepted worker_done already settles the task in Orca. Ack verifies
    # launch evidence and releases the settled resource without task-update.
    sess, runner = session(SETTLE)
    done = sess.wait(timeout_s=1.0)[0]
    sess.ack(done)
    assert not runner.calls_matching("task-update")
    assert runner.calls_matching("check", "--ack")
    release = runner.calls_matching("worker-release", "--dispatch")[0]
    assert runner.argv_after("--dispatch", release) == "ctx_817ed5bf5986"


def test_a_failed_outcome_ack_preserves_runtime_settlement():
    sess, runner = session(SETTLE)
    done = sess.wait(timeout_s=1.0)[0]
    sess.ack(
        WorkerDone(
            key=done.key,
            handle=done.handle,
            delivery_id=done.delivery_id,
            outcome="failed",
            summary="",
            files_modified=(),
            report_path=None,
        )
    )
    assert not runner.calls_matching("task-update")
    assert runner.calls_matching("check", "--ack")
    assert runner.calls_matching("worker-release")


def test_a_heartbeat_ack_settles_nothing():
    sess, runner = session(SETTLE)
    heartbeat = sess.wait(timeout_s=1.0)[3]
    sess.ack(heartbeat)
    assert not runner.calls_matching("task-update")
    assert not runner.calls_matching("worker-release")


def test_reply_uses_the_questions_own_message_id():
    sess, runner = session(SETTLE)
    question = sess.wait(timeout_s=1.0)[1]
    sess.reply(question.reply_token, "main")
    call = runner.calls_matching("reply", "--id")[0]
    assert runner.argv_after("--id", call) == "msg_1111111111aa"
    assert runner.argv_after("--body", call) == "main"
    assert runner.argv_after("--run", call) == "run_4b284b42f4a3"


def test_stop_resolves_the_handle_from_the_ledger():
    sess, runner = session(SETTLE)
    sess.stop("2026-08-13-test-gap-code-wiki-smoke-script#finish")
    call = runner.calls_matching("worker-stop", "--dispatch")[0]
    assert runner.argv_after("--dispatch", call) == "ctx_320c498114b8"


def test_stopping_an_unknown_key_raises():
    sess, _ = session(SETTLE)
    with pytest.raises(UnknownWorker):
        sess.stop("no-such-slug#execute")


def test_close_releases_settled_terminals_and_leaves_live_ones_alone():
    sess, runner = session(SETTLE)
    sess.workers()
    sess.close()
    released = {runner.argv_after("--dispatch", c) for c in runner.calls_matching("worker-release")}
    assert released == {"ctx_817ed5bf5986"}  # the settled one; the running one is untouched
    assert not runner.calls_matching("worker-stop")


def test_close_does_not_delete_the_run():
    # The Run *is* the durable session; deleting it would destroy the resume
    # path that re-deriving state from `task-list` depends on.
    sess, runner = session(SETTLE)
    sess.workers()
    sess.close()
    assert not runner.calls_matching("run-delete")


def test_an_unrecognised_message_type_in_a_batch_is_dropped_not_raised():
    # A new Orca message type must not blind the coordinator to the rest of
    # the batch it arrived in.
    batch = json.loads(fixture("check_batch"))
    batch["result"]["messages"].append(
        {"id": "msg_x", "type": "some_future_type", "payload": {"taskId": "task_5ca8c19ffa5e"}}
    )

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "check" in argv and "--wait" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(batch), stderr="")
            return super().__call__(argv)

    runner = Runner([*OPEN, (("task-list",), "task_list")])
    sess = OrcaBackend(run=runner).open_session(TARGET)
    events = sess.wait(timeout_s=1.0)
    assert len(events) == 4  # the unrecognised-type message is dropped, not appended


def test_ack_on_a_worker_done_with_no_matching_task_raises():
    # A key this session's ledger has never heard of, even after a refresh,
    # is a stale or corrupted event — the same case LocalSession.ack() raises
    # UnknownWorker for, so a coordinator sees one consistent signal across
    # backends instead of a silent, unflagged no-op on this one.
    sess, runner = session(SETTLE)
    sess.wait(timeout_s=1.0)  # populates _keys_by_task
    ghost = WorkerDone(
        key="no-such-slug#execute",
        handle="",
        delivery_id="dlv_0000000000a1",
        outcome="succeeded",
        summary="",
        files_modified=(),
        report_path=None,
    )
    with pytest.raises(UnknownWorker):
        sess.ack(ghost)
    assert not runner.calls_matching("task-update")
    assert not runner.calls_matching("worker-release")


def test_ack_resolves_a_task_id_via_refresh_when_the_ledger_starts_cold():
    # `_keys_by_task` starts empty until something populates it. `ack()`
    # calling it cold must still resolve — via the same refresh-then-retry
    # `wait()`'s own miss path uses, just landing on the second attempt
    # instead of failing it.
    sess, runner = session(SETTLE)
    done = WorkerDone(
        key="2026-08-13-test-gap-code-wiki-smoke-script#execute",
        handle="ctx_817ed5bf5986",
        delivery_id="dlv_0000000000a1",
        outcome="succeeded",
        summary="",
        files_modified=(),
        report_path=None,
    )
    sess.ack(done)
    assert runner.calls_matching("task-list")
    assert runner.calls_matching("check", "--ack")
    assert not runner.calls_matching("task-update")


def test_ack_swallows_a_failed_worker_release():
    # `worker-release` is idempotent and reports `already_released` on a
    # repeat; a release that cannot be proven is not worth failing an ack
    # over.
    failed = json.dumps({"id": "x", "ok": False, "error": {"code": "already_released", "message": "gone"}})

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-release" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=failed, stderr="")
            return super().__call__(argv)

    runner = Runner(SETTLE)
    sess = OrcaBackend(run=runner).open_session(TARGET)
    done = sess.wait(timeout_s=1.0)[0]
    sess.ack(done)  # must not raise
    assert runner.calls_matching("worker-release")
