"""The Protocol's scenarios. Written once; run against every backend in CASES."""

from __future__ import annotations

import pytest
from conformance import drain, make_dispatch, saw_done
from subagents_io.backend import (
    WORKER_STATES,
    BackendError,
    UnsupportedMode,
    WorkerDone,
    WorkerQuestion,
    WorktreeNotProvisioned,
)
from subagents_io.dispatch import DISPATCH_MODES


def test_supported_modes_is_a_subset_of_the_vocabulary(case, tmp_path):
    backend = case.make(tmp_path / "root")
    assert backend.supported_modes <= DISPATCH_MODES
    assert backend.supported_modes


def test_provisions_worktrees_is_declared(case, tmp_path):
    # Every case this suite knows obtains no worktree of its own; a backend
    # that does (the workflow-orca sibling) sets this True and is what
    # inherits this scenario by adding a third BackendCase.
    backend = case.make(tmp_path / "root")
    assert backend.provisions_worktrees is False


def test_open_session_binds_or_creates_by_name(case, tmp_path):
    # Idempotent by name is what makes a coordinator restart a no-op rather
    # than a second run.
    backend = case.make(tmp_path / "root")
    first = backend.open_session("s")
    first.launch(make_dispatch("done:succeeded", worktree_path=str(tmp_path)))
    first.close()
    second = backend.open_session("s")
    assert [w.key for w in second.workers()] == ["slug#plan"]
    second.close()


def test_launch_refuses_an_unprovisioned_worktree(session, tmp_path):
    del tmp_path
    with pytest.raises(WorktreeNotProvisioned):
        session.launch(make_dispatch("done:succeeded", worktree_path=None))


def test_launch_refuses_a_duplicate_key(session, tmp_path):
    # Every backend's ledger IS the durable dedupe record: silently returning
    # the existing worker for a repeated key would make a re-dispatch after a
    # failure read as a success, so a duplicate key is refused rather than
    # answered from cache -- proven identically for every backend in CASES.
    session.launch(make_dispatch("done:succeeded", worktree_path=str(tmp_path)))
    with pytest.raises(BackendError, match="already"):
        session.launch(make_dispatch("done:succeeded", worktree_path=str(tmp_path)))
    assert len(session.workers()) == 1


def test_launch_refuses_an_unsupported_mode(case, session, tmp_path):
    if not case.narrows_modes:
        pytest.skip(f"{case.id} supports every mode in DISPATCH_MODES, so this can never fire")
    unsupported = next(iter(DISPATCH_MODES - case.modes))
    with pytest.raises(UnsupportedMode):
        session.launch(make_dispatch("done:succeeded", worktree_path=str(tmp_path), mode=unsupported))


def test_launch_enumerate_wait_done(session, tmp_path):
    record = session.launch(make_dispatch("heartbeat:planning", "done:succeeded", worktree_path=str(tmp_path)))
    assert record.key == "slug#plan"
    assert [w.key for w in session.workers()] == ["slug#plan"]

    events = drain(session, until=saw_done)
    done = next(e for e in events if isinstance(e, WorkerDone))
    assert done.outcome == "succeeded"
    assert done.key == "slug#plan"
    assert session.describe("slug#plan").state == "succeeded"


def test_a_settled_key_stays_enumerable(session, tmp_path):
    # `workers()` returns every worker EVER launched, because a settled key is
    # exactly what the dedupe check needs to see.
    session.launch(make_dispatch("done:succeeded", worktree_path=str(tmp_path)))
    drain(session, until=saw_done)
    assert [w.key for w in session.workers()] == ["slug#plan"]


def test_describe_is_none_for_a_key_never_launched(session):
    assert session.describe("nobody#plan") is None


def test_question_reply_done(session, tmp_path):
    session.launch(make_dispatch("question:merge?", "done:succeeded", worktree_path=str(tmp_path)))
    asked = drain(session, until=lambda evs: any(isinstance(e, WorkerQuestion) for e in evs))
    question = next(e for e in asked if isinstance(e, WorkerQuestion))
    assert question.options == ("a", "b")

    session.reply(question.reply_token, "merge")
    assert saw_done(drain(session, until=saw_done))


def test_stop_settles_a_running_worker(session, tmp_path):
    session.launch(make_dispatch("question:forever", worktree_path=str(tmp_path)))
    drain(session, until=lambda evs: any(isinstance(e, WorkerQuestion) for e in evs))
    session.stop("slug#plan")
    assert session.describe("slug#plan").state == "stopped"


def test_an_unacked_event_is_redelivered_after_a_reopen(case, tmp_path):
    # The property that makes the resumption claim true rather than hoped.
    backend = case.make(tmp_path / "root")
    first = backend.open_session("s")
    first.launch(make_dispatch("heartbeat:planning", "done:succeeded", worktree_path=str(tmp_path)))
    unacked = drain(first, until=saw_done, ack=False)
    assert saw_done(unacked)
    first.close()

    second = backend.open_session("s")
    replayed = drain(second, until=saw_done, ack=True)
    assert saw_done(replayed)
    second.close()


def test_an_acked_event_is_not_redelivered_after_a_reopen(case, tmp_path):
    backend = case.make(tmp_path / "root")
    first = backend.open_session("s")
    first.launch(make_dispatch("heartbeat:planning", "done:succeeded", worktree_path=str(tmp_path)))
    assert saw_done(drain(first, until=saw_done, ack=True))
    first.close()

    second = backend.open_session("s")
    assert second.wait(timeout_s=0.3) == []
    assert second.describe("slug#plan").state in {"succeeded", "failed"}
    second.close()


def test_every_state_a_backend_emits_is_in_the_vocabulary(session, tmp_path):
    # Run the whole lifecycle and check every state seen along the way. A
    # backend inventing a sixth state fails here rather than at a coordinator's
    # `match`.
    seen = set()
    record = session.launch(make_dispatch("heartbeat:planning", "done:succeeded", worktree_path=str(tmp_path)))
    seen.add(record.state)
    for _ in range(40):
        session.wait(timeout_s=0.05)
        seen.update(w.state for w in session.workers())
        if session.describe("slug#plan").state in {"succeeded", "failed"}:
            break
    assert seen <= WORKER_STATES
    assert len(seen) >= 2, f"the lifecycle produced only {seen} — the assertion above would be near-vacuous"
