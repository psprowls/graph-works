"""LocalBackend's own behaviour — the parts no other backend can be asked to have."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest
from marks import POSIX_ONLY
from subagents_io.backend import (
    BackendError,
    DispatchBackend,
    Escalation,
    Heartbeat,
    UnknownWorker,
    WorkerDone,
    WorkerQuestion,
    WorktreeNotProvisioned,
)
from subagents_io.dispatch import DISPATCH_MODES, PlannedDispatch, WorktreeAction
from workflow_local.backend import LocalBackend
from workflow_local.ledger import LEDGER_NAME, read_ledger

pytestmark = POSIX_ONLY

CHILD = Path(__file__).resolve().parent / "child.py"


def dispatch(
    tmp_path, *program, key="gw-plan-slug-00000000", slug="work/feature-slug", phase="plan", mode="autonomous", path=...
):
    worktree = WorktreeAction(
        action="reuse",
        path=str(tmp_path) if path is ... else path,
        branch="b",
        base_branch=None,
        exists=True,
        parent_path=None,
    )
    return PlannedDispatch(
        agent="claude",
        key=key,
        slug=slug,
        phase=phase,
        kind="feature",
        effort="medium",
        skill="writing-plans",
        mode=mode,
        model=None,
        reasoning_effort=None,
        worktree=worktree,
        merge_target="main",
        auto_merge=False,
        prompt=" ".join(program),
    )


def make_backend(root, **kw):
    return LocalBackend(
        root,
        argv_for=lambda d: [sys.executable, str(CHILD), *d.prompt.split()],
        poll_interval_s=0.02,
        **kw,
    )


def drain(session, *, timeout_s=15.0, until=None):
    """Collect events until `until(events)` is true or the clock runs out."""
    collected = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for event in session.wait(timeout_s=0.2):
            collected.append(event)
            session.ack(event)
        if until is not None and until(collected):
            return collected
    return collected


def is_done(events):
    return any(isinstance(e, WorkerDone) for e in events)


def test_local_backend_satisfies_the_protocol(tmp_path):
    # A type-level check, not a duck-typing one: mypy --strict validates the
    # annotation on this parameter against LocalBackend's real shape.
    def takes_a_backend(backend: DispatchBackend) -> str:
        return backend.name

    assert takes_a_backend(make_backend(tmp_path)) == "workflow-local"


def test_supported_modes_is_all_of_dispatch_modes(tmp_path):
    # Genuinely supported rather than declared: the events file carries
    # questions and escalations, and replies.jsonl carries the answer back.
    assert make_backend(tmp_path).supported_modes == DISPATCH_MODES


def test_provisions_worktrees_is_false(tmp_path):
    # This backend never touches git; launch() keeps raising
    # WorktreeNotProvisioned for an unset worktree.path.
    assert make_backend(tmp_path).provisions_worktrees is False


def test_launch_refuses_an_unprovisioned_worktree(tmp_path):
    # Git stays out of this package. Provisioning belongs beside the one module
    # declared to run it.
    session = make_backend(tmp_path / "root").open_session("s")
    with pytest.raises(WorktreeNotProvisioned):
        session.launch(dispatch(tmp_path, "done:succeeded", path=None))
    assert session.workers() == []


def test_launch_refuses_a_duplicate_key(tmp_path):
    # Silently returning the existing record would make a re-dispatch after a
    # failure read as a success -- matches workflow-orca's own duplicate-key
    # refusal, and is exercised across every backend by the shared
    # conformance suite (test_conformance.py).
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "sleep:5"))
    with pytest.raises(BackendError, match="already"):
        session.launch(dispatch(tmp_path, "sleep:5"))
    assert len(session.workers()) == 1
    session.stop("gw-plan-slug-00000000")
    session.close()


def test_the_child_sees_exactly_the_three_contract_variables(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    record = session.launch(dispatch(tmp_path, "say:hello", "done:succeeded"))
    drain(session, until=is_done)
    worker_dir = tmp_path / "root" / "s" / record.handle
    assert (worker_dir / "events.jsonl").is_file()
    assert (worker_dir / "replies.jsonl").is_file()
    assert "hello" in (worker_dir / "stdout.log").read_text(encoding="utf-8")
    session.close()


def test_the_dispatch_key_reaches_the_child(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "escalate:blocked"))
    events = drain(session, until=lambda evs: any(isinstance(e, Escalation) for e in evs))
    escalation = next(e for e in events if isinstance(e, Escalation))
    assert escalation.body == "gw-plan-slug-00000000 is blocked"
    session.stop("gw-plan-slug-00000000")
    session.close()


def test_wait_returns_empty_at_timeout_rather_than_raising(tmp_path):
    # So a coordinator's outer loop stays a plain `while`.
    session = make_backend(tmp_path / "root").open_session("s")
    assert session.wait(timeout_s=0.05) == []


def test_a_full_run_yields_heartbeat_then_done(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "heartbeat:planning", "done:succeeded"))
    events = drain(session, until=is_done)
    assert [type(e) for e in events] == [Heartbeat, WorkerDone]
    assert events[0].phase == "planning"
    assert events[1].outcome == "succeeded"
    assert events[1].files_modified == ("a.py",)
    assert session.describe("gw-plan-slug-00000000").state == "succeeded"
    assert session.describe("gw-plan-slug-00000000").last_heartbeat_at is not None
    session.close()


def test_a_malformed_line_is_skipped_and_counted_not_raised(tmp_path):
    # A malformed line from one child must not blind the coordinator to its
    # siblings — including the sibling lines in its own file.
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "garbage", "unknown", "done:succeeded"))
    events = drain(session, until=is_done)
    assert [type(e) for e in events] == [WorkerDone]
    assert session.skipped_lines == 2
    session.close()


def test_a_blank_line_is_skipped_and_counted_not_raised(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "blank", "done:succeeded"))
    events = drain(session, until=is_done)
    assert [type(e) for e in events] == [WorkerDone]
    assert session.skipped_lines == 1
    session.close()


def test_a_non_object_json_payload_is_skipped_and_counted_not_raised(tmp_path):
    # `[1, 2, 3]` parses as JSON but is not the mapping every event kind
    # expects — that has to be a skip, not a crash or a `dict(...)` explosion.
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "array", "done:succeeded"))
    events = drain(session, until=is_done)
    assert [type(e) for e in events] == [WorkerDone]
    assert session.skipped_lines == 1
    session.close()


def test_a_silent_child_that_exits_zero_is_reaped_as_succeeded(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    record = session.launch(dispatch(tmp_path, "say:quiet", "exit:0"))
    events = drain(session, until=is_done)
    done = next(e for e in events if isinstance(e, WorkerDone))
    assert done.outcome == "succeeded"
    assert done.delivery_id == f"{record.handle}:reaped"
    assert "quiet" in done.summary
    session.close()


def test_a_silent_child_that_exits_nonzero_is_reaped_as_failed(tmp_path):
    # The honesty mechanism: a crashed child always settles, so no key can sit
    # live forever.
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "exit:3"))
    events = drain(session, until=is_done)
    done = next(e for e in events if isinstance(e, WorkerDone))
    assert done.outcome == "failed"
    assert session.describe("gw-plan-slug-00000000").state == "failed"
    session.close()


def test_drain_tolerates_events_file_disappearing_between_polls(tmp_path):
    # A worker directory is not a promise its files stay put — the reader has
    # to survive a poll landing between someone else's delete and any rewrite.
    session = make_backend(tmp_path / "root").open_session("s")
    record = session.launch(dispatch(tmp_path, "sleep:5"))
    events_path = tmp_path / "root" / "s" / record.handle / "events.jsonl"
    assert events_path.is_file()
    events_path.unlink()
    assert session.wait(timeout_s=0.1) == []
    session.stop("gw-plan-slug-00000000")
    session.close()


def test_reap_tolerates_a_missing_stdout_log(tmp_path):
    # `_tail` backs the reaper's synthesized summary; a `stdout.log` gone by
    # the time reaping happens must yield an empty summary, not a crash.
    session = make_backend(tmp_path / "root").open_session("s")
    record = session.launch(dispatch(tmp_path, "exit:0"))
    stdout_path = tmp_path / "root" / "s" / record.handle / "stdout.log"
    assert stdout_path.is_file()
    stdout_path.unlink()
    events = drain(session, until=is_done)
    done = next(e for e in events if isinstance(e, WorkerDone))
    assert done.summary == ""
    session.close()


def test_an_unrecognized_outcome_is_recorded_as_failed(tmp_path):
    # `_apply` is the one place a live child's own claim about its outcome is
    # folded into the ledger; an outcome outside the closed vocabulary must
    # not be trusted verbatim.
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "done:weird"))
    events = drain(session, until=is_done)
    done = next(e for e in events if isinstance(e, WorkerDone))
    assert done.outcome == "weird"
    assert session.describe("gw-plan-slug-00000000").state == "failed"
    session.close()


def test_a_reaped_worker_is_not_reaped_twice(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "exit:0"))
    drain(session, until=is_done)
    assert session.wait(timeout_s=0.1) == []
    session.close()


def test_a_question_is_answered_through_replies_jsonl(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    record = session.launch(dispatch(tmp_path, "question:merge?", "done:succeeded"))
    events = drain(session, until=lambda evs: any(isinstance(e, WorkerQuestion) for e in evs))
    question = next(e for e in events if isinstance(e, WorkerQuestion))
    assert question.options == ("a", "b")
    assert question.reply_token == question.delivery_id
    session.reply(question.reply_token, "merge")

    replies = (tmp_path / "root" / "s" / record.handle / "replies.jsonl").read_text(encoding="utf-8")
    assert json.loads(replies.strip()) == {"reply_token": question.reply_token, "answer": "merge"}

    assert is_done(drain(session, until=is_done))
    session.close()


def test_reply_to_an_unknown_token_raises(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    with pytest.raises(UnknownWorker):
        session.reply("nobody:0", "hi")


def test_stop_terminates_the_child_and_records_it(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    record = session.launch(dispatch(tmp_path, "sleep:30"))
    session.stop("gw-plan-slug-00000000")
    assert session.describe("gw-plan-slug-00000000").state == "stopped"
    assert not _pid_alive(read_ledger(tmp_path / "root" / "s" / LEDGER_NAME)["gw-plan-slug-00000000"].pid)
    assert record.state == "running"
    session.close()


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path):
    # The grace period is what makes this a two-step, and a child that traps
    # SIGTERM is exactly the case a one-step stop leaks. The child signals
    # `ready` only once the trap is actually installed, so `stop()` cannot
    # race a still-starting interpreter into the SIGTERM default action.
    trapper = tmp_path / "trapper.py"
    ready = tmp_path / "ready"
    trapper.write_text(
        "import signal, time, pathlib\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    backend = LocalBackend(
        tmp_path / "root",
        argv_for=lambda d: [sys.executable, str(trapper)],
        poll_interval_s=0.02,
        stop_grace_s=0.3,
    )
    session = backend.open_session("s")
    session.launch(dispatch(tmp_path, "ignored"))
    deadline = time.monotonic() + 15.0
    while not ready.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.is_file(), "trapper never installed its SIGTERM handler"
    session.stop("gw-plan-slug-00000000")
    assert session.describe("gw-plan-slug-00000000").state == "stopped"
    session.close()


def test_stop_on_a_resumed_session_has_no_process_handle_to_terminate_with(tmp_path):
    # A resumed `LocalSession` never held the `Popen` for work launched by an
    # earlier one — `_terminate` has to be able to kill by pid alone.
    backend = make_backend(tmp_path / "root")
    first = backend.open_session("s")
    first.launch(dispatch(tmp_path, "sleep:30"))
    first.close()  # the child is left running; nothing tracks its Popen now

    resumed = backend.open_session("s")
    pid = read_ledger(tmp_path / "root" / "s" / LEDGER_NAME)["gw-plan-slug-00000000"].pid
    resumed.stop("gw-plan-slug-00000000")
    assert resumed.describe("gw-plan-slug-00000000").state == "stopped"
    assert not _pid_alive(pid)
    resumed.close()


def test_stop_on_an_unknown_key_raises(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    with pytest.raises(UnknownWorker):
        session.stop("nobody#plan")


def test_stop_on_an_already_settled_worker_is_not_an_error(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "done:succeeded"))
    drain(session, until=is_done)
    session.stop("gw-plan-slug-00000000")
    assert session.describe("gw-plan-slug-00000000").state == "stopped"
    session.close()


def test_ack_of_an_event_with_no_delivery_id_is_a_no_op(tmp_path):
    # So a backend with no delivery concept needs no caller branching.
    session = make_backend(tmp_path / "root").open_session("s")
    session.ack(Heartbeat(key="nobody#plan", handle="h", delivery_id=None))


def test_ack_of_an_unknown_key_raises(tmp_path):
    session = make_backend(tmp_path / "root").open_session("s")
    with pytest.raises(UnknownWorker):
        session.ack(Heartbeat(key="nobody#plan", handle="h", delivery_id="h:0"))


def test_ack_of_the_reaped_delivery_id_is_tolerated(tmp_path):
    # "<handle>:reaped" has no line number to advance past.
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "exit:0"))
    events = drain(session, until=is_done)
    session.ack(next(e for e in events if isinstance(e, WorkerDone)))
    assert read_ledger(tmp_path / "root" / "s" / LEDGER_NAME)["gw-plan-slug-00000000"].acked_through == 0
    session.close()


def test_reopening_re_derives_every_record_from_the_ledger(tmp_path):
    # The load-bearing property: a coordinator resumes from the backend plus
    # the vault, never from memory.
    backend = make_backend(tmp_path / "root")
    first = backend.open_session("s")
    first.launch(dispatch(tmp_path, "done:succeeded"))
    drain(first, until=is_done)
    first.close()

    second = backend.open_session("s")
    assert [w.key for w in second.workers()] == ["gw-plan-slug-00000000"]
    assert second.describe("gw-plan-slug-00000000").state == "succeeded"
    second.close()


def test_a_dead_pid_with_no_terminal_event_reopens_as_unknown_then_settles(tmp_path):
    # "unknown" is what a backend reports when it holds a record but cannot
    # corroborate the process behind it. The next wait()'s reaper settles it.
    backend = make_backend(tmp_path / "root")
    session = backend.open_session("s")
    record = session.launch(dispatch(tmp_path, "sleep:30"))
    pid = read_ledger(tmp_path / "root" / "s" / LEDGER_NAME)["gw-plan-slug-00000000"].pid
    os.kill(pid, signal.SIGKILL)
    while _pid_alive(pid):
        time.sleep(0.02)
    session.close()

    resumed = backend.open_session("s")
    assert resumed.describe("gw-plan-slug-00000000").state == "unknown"
    done = next(e for e in drain(resumed, until=is_done) if isinstance(e, WorkerDone))
    assert done.outcome == "failed"
    assert done.handle == record.handle
    resumed.close()


def test_open_session_binds_rather_than_creating_a_second(tmp_path):
    backend = make_backend(tmp_path / "root")
    backend.open_session("s").close()
    backend.open_session("s").close()
    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["s"]


def test_two_sessions_are_two_directories(tmp_path):
    backend = make_backend(tmp_path / "root")
    backend.open_session("a").close()
    backend.open_session("b").close()
    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == ["a", "b"]


def test_env_replaces_rather_than_extends_when_supplied(tmp_path):
    backend = LocalBackend(
        tmp_path / "root",
        argv_for=lambda d: [sys.executable, str(CHILD), *d.prompt.split()],
        env={"PATH": os.environ.get("PATH", "")},
        poll_interval_s=0.02,
    )
    session = backend.open_session("s")
    session.launch(dispatch(tmp_path, "done:succeeded"))
    assert is_done(drain(session, until=is_done))
    session.close()


def test_pid_alive_treats_a_permission_error_as_alive(monkeypatch):
    # `PermissionError` from `os.kill(pid, 0)` means the pid exists and
    # belongs to someone else — for our purposes that is alive. Simulated
    # rather than run under a foreign uid, which this suite cannot arrange
    # portably.
    from workflow_local import backend as backend_module

    def fake_kill(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(backend_module.os, "kill", fake_kill)
    assert backend_module._pid_alive(1) is True


def test_close_tolerates_a_handle_whose_fd_was_already_closed(tmp_path):
    # `close()` suppresses `OSError` closing its own handles — this forces
    # exactly that: the handle's fd is torn out from under it, so the
    # `IO.close()` inside `close()` raises rather than the usual silent
    # double-close no-op.
    session = make_backend(tmp_path / "root").open_session("s")
    session.launch(dispatch(tmp_path, "sleep:5"))
    handle = session._handles["gw-plan-slug-00000000"]
    os.close(handle.fileno())
    try:
        session.close()  # must not raise despite the fd already being gone
    finally:
        # close() never touches processes; stop the still-live child so a
        # regression above can't orphan it for the rest of sleep:5's duration.
        session.stop("gw-plan-slug-00000000")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_local_backend_reports_no_worktree_of_its_own(tmp_path):
    # `provisions_worktrees is False` here: this backend runs a subprocess in
    # a worktree someone else made, and never learns a branch. Both fields
    # stay None, which is the protocol's own word for "not known" — not "",
    # and not the plan's guess.
    session = make_backend(tmp_path / "root").open_session("s")
    try:
        record = session.launch(dispatch(tmp_path, "done:succeeded"))
        assert record.worktree_path is None
        assert record.worktree_branch is None
    finally:
        session.close()


def test_open_session_refuses_when_the_platform_changes_under_a_live_backend(tmp_path, monkeypatch):
    # Delegation coverage for the path `test_local_session_refuses_windows`
    # used to exercise before it was rewritten (in test_windows_guard.py) to
    # construct `LocalSession` directly: `LocalBackend.open_session` still has
    # to refuse when the platform changes out from under an already-live
    # backend. POSIX-only because it needs a real backend to construct first.
    from workflow_local import backend as backend_module

    backend = make_backend(tmp_path / "root")
    monkeypatch.setattr(backend_module.sys, "platform", "win32")
    with pytest.raises(BackendError):
        backend.open_session("s")
