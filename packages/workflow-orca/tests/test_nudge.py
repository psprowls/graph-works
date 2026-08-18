"""The unsent-prompt nudge: five vetoes, one press, once per worker."""

from __future__ import annotations

import json

from orca_fakes import FakeRunner, fixture
from workflow_orca import OrcaBackend
from workflow_orca._cli import OrcaResult

TARGET = "auto-drive:2026-08-14-epic-feature-orca-dispatch-backend"

BASE = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-use",), "run_create"),
    (("task-list",), "task_list"),
    (("worker-list",), "worker_list"),
    (("check", "--wait"), "check_timeout"),
    (("terminal", "send"), "check_timeout"),
]


def session(*extra, read="worker_read_empty", show="worker_show_settled"):
    runner = FakeRunner([*extra, (("worker-read",), read), (("worker-show",), show), *BASE])
    return OrcaBackend(run=runner).open_session(TARGET), runner


def test_a_heartbeat_is_a_hard_veto():
    # A worker that never submitted cannot have heartbeat, so any heartbeat
    # proves submission however late it arrives.
    sess, runner = session(show="worker_show_live")
    sess.wait(timeout_s=0.1)
    assert not runner.calls_matching("terminal", "send")


def test_a_non_empty_transcript_vetoes():
    # Putting a dialog on screen is itself agent activity and appears in the
    # transcript, so an *empty* transcript is what proves no dialog can be
    # up. This is the safety guarantee, not a guess about idle terminals.
    sess, runner = session(read="worker_read_transcript")
    sess.wait(timeout_s=0.1)
    assert not runner.calls_matching("terminal", "send")


def test_a_degraded_read_is_not_decisive():
    # `source == "terminal"` means the transcript was unavailable, so an
    # empty `messages` proves nothing.
    degraded = json.loads(fixture("worker_read_empty"))
    degraded["result"]["source"] = "terminal"

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-read" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(degraded), stderr="")
            return super().__call__(argv)

    runner = Runner([(("worker-show",), "worker_show_settled"), *BASE])
    OrcaBackend(run=runner).open_session(TARGET).wait(timeout_s=0.1)
    assert not runner.calls_matching("terminal", "send")


def test_an_errored_read_is_not_decisive():
    # Verified live: a dispatch whose process was replaced answers
    # `{"ok": false, "error": {"code": "worker_identity_changed"}}`. An
    # unreadable worker is never nudged.
    failed = json.dumps({"id": "x", "ok": False, "error": {"code": "worker_identity_changed", "message": "gone"}})

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-read" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=failed, stderr="")
            return super().__call__(argv)

    runner = Runner([(("worker-show",), "worker_show_settled"), *BASE])
    OrcaBackend(run=runner).open_session(TARGET).wait(timeout_s=0.1)
    assert not runner.calls_matching("terminal", "send")


def test_all_negative_presses_enter_once_on_the_agent_terminal():
    sess, runner = session()
    sess.wait(timeout_s=0.1)
    sends = runner.calls_matching("terminal", "send")
    assert len(sends) == 1
    assert runner.argv_after("--terminal", sends[0]) == "term_ff2faacf-db5b-43ad-b05b-c6dfee58eb53"
    assert "--enter" in sends[0]


def test_a_second_empty_wait_does_not_nudge_the_same_worker_again():
    # Once per worker per session, held in memory. Deliberately not
    # persisted: a coordinator restart re-earns one nudge, because a worker
    # that was nudged and then genuinely hung must stay nudgeable across the
    # restart that is the loop's own recovery path.
    sess, runner = session()
    sess.wait(timeout_s=0.1)
    sess.wait(timeout_s=0.1)
    assert len(runner.calls_matching("terminal", "send")) == 1


def test_settled_workers_are_not_probed_at_all():
    sess, runner = session()
    sess.wait(timeout_s=0.1)
    probed = {runner.argv_after("--dispatch", c) for c in runner.calls_matching("worker-read", "--dispatch")}
    assert probed == {"ctx_320c498114b8"}  # the only running worker


def test_a_worker_with_no_agent_terminal_handle_is_not_nudged():
    # `_agent_terminal` reads only what `workers()`'s own `worker-list` walk
    # already saw; a row that never carried the field means no send, because
    # there is no second per-dispatch call to re-derive it with.
    worker_list = json.loads(fixture("worker_list"))
    for row in worker_list["result"]["workers"]:
        if row["taskId"] == "task_338800a1fa14":
            row.pop("agentTerminalHandle", None)

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-list" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(worker_list), stderr="")
            return super().__call__(argv)

    runner = Runner(
        [
            (("run-list", "--cursor"), "run_list_page2"),
            (("run-list",), "run_list"),
            (("run-use",), "run_create"),
            (("task-list",), "task_list"),
            (("check", "--wait"), "check_timeout"),
            (("worker-read",), "worker_read_empty"),
            (("worker-show",), "worker_show_settled"),
        ]
    )
    OrcaBackend(run=runner).open_session(TARGET).wait(timeout_s=0.1)
    assert not runner.calls_matching("terminal", "send")


def test_the_sweep_does_not_run_when_events_arrived():
    # Never in the hot path: an event delivered is itself proof of life.
    runner = FakeRunner(
        [
            (("run-list", "--cursor"), "run_list_page2"),
            (("run-list",), "run_list"),
            (("run-use",), "run_create"),
            (("task-list",), "task_list"),
            (("check", "--wait"), "check_batch"),
        ]
    )
    OrcaBackend(run=runner).open_session(TARGET).wait(timeout_s=0.1)
    assert not runner.calls_matching("worker-read")
    assert not runner.calls_matching("terminal", "send")
