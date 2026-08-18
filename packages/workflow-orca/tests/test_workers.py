"""Enumeration: what the session knows, and what it admits it does not."""

from __future__ import annotations

import json

from orca_fakes import FakeRunner, fixture
from workflow_orca import OrcaBackend
from workflow_orca._cli import OrcaResult

TARGET = "auto-drive:2026-08-14-epic-feature-orca-dispatch-backend"

ROUTES = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-use",), "run_create"),
    (("task-list",), "task_list"),
    (("worker-list",), "worker_list"),
    (("worker-show",), "worker_show_live"),
]

EXECUTE = "2026-08-13-test-gap-code-wiki-smoke-script#execute"
FINISH = "2026-08-13-test-gap-code-wiki-smoke-script#finish"
ORPHAN = "2026-08-13-orphan#plan"


def session():
    runner = FakeRunner(ROUTES)
    return OrcaBackend(run=runner).open_session(TARGET), runner


def test_one_record_per_task_including_the_one_with_no_worker():
    sess, _ = session()
    assert {r.key for r in sess.workers()} == {EXECUTE, FINISH, ORPHAN}


def test_a_task_with_no_worker_row_is_unknown_not_failed():
    # `worker-start` died after `task-create` succeeded. That key is
    # recoverable; reporting it `failed` would make it not.
    sess, _ = session()
    orphan = next(r for r in sess.workers() if r.key == ORPHAN)
    assert orphan.state == "unknown"
    assert orphan.handle == ""


def test_two_worker_rows_on_one_task_resolve_to_the_last():
    # Retries produce several rows; the most recent is the live one.
    sess, _ = session()
    executed = next(r for r in sess.workers() if r.key == EXECUTE)
    assert executed.handle == "ctx_817ed5bf5986"
    assert executed.state == "succeeded"


def test_worker_show_is_called_only_for_live_workers():
    # A settled worker has no current heartbeat, so `None` is honest and the
    # call is waste. `worker-list` carries no heartbeat at all, so the live
    # ones genuinely need the extra call.
    sess, runner = session()
    sess.workers()
    shows = runner.calls_matching("worker-show", "--dispatch")
    assert len(shows) == 1
    assert runner.argv_after("--dispatch", shows[0]) == "ctx_320c498114b8"


def test_the_call_budget_is_two_plus_the_live_count():
    # Asserted on recorded argv, so the cost is part of the contract rather
    # than an implementation detail that can quietly become unbounded.
    sess, runner = session()
    before = len(runner.calls)
    sess.workers()
    assert len(runner.calls) - before == 3  # task-list + worker-list + one live worker-show


def test_a_live_worker_reports_the_fetched_heartbeat():
    sess, _ = session()
    live = next(r for r in sess.workers() if r.key == FINISH)
    assert live.state == "running"
    assert live.last_heartbeat_at == "2026-08-13T18:38:36Z"


def test_a_settled_worker_reports_none_honestly():
    sess, _ = session()
    settled = next(r for r in sess.workers() if r.key == EXECUTE)
    assert settled.last_heartbeat_at is None
    assert settled.detail == "completed"


def test_describe_finds_a_key_and_returns_none_for_an_unknown_one():
    sess, _ = session()
    assert sess.describe(FINISH).handle == "ctx_320c498114b8"
    assert sess.describe("no-such-slug#execute") is None


def _runner_with_worker_list(mutated: dict, *extra_routes) -> FakeRunner:
    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-list" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(mutated), stderr="")
            return super().__call__(argv)

    return Runner(
        [
            (("run-list", "--cursor"), "run_list_page2"),
            (("run-list",), "run_list"),
            (("run-use",), "run_create"),
            (("task-list",), "task_list"),
            *extra_routes,
        ]
    )


def test_a_worker_list_row_with_no_task_id_is_ignored():
    # `worker-list` is the source of truth for the "latest row per task"
    # join; a row this backend cannot attribute to any task must not throw
    # off that join, so it is dropped rather than crashing the walk.
    worker_list = json.loads(fixture("worker_list"))
    worker_list["result"]["workers"].append({"dispatchId": "ctx_orphan_row", "workerState": "running"})
    runner = _runner_with_worker_list(worker_list, (("worker-show",), "worker_show_live"))
    sess = OrcaBackend(run=runner).open_session(TARGET)
    assert {r.key for r in sess.workers()} == {EXECUTE, FINISH, ORPHAN}


def test_a_live_worker_row_with_no_dispatch_id_skips_the_heartbeat_call():
    # `_heartbeat` is a per-dispatch call; a live row that never carried a
    # handle has nothing to address it with, so the call is skipped rather
    # than made with an empty `--dispatch`.
    worker_list = json.loads(fixture("worker_list"))
    for row in worker_list["result"]["workers"]:
        if row["taskId"] == "task_338800a1fa14":
            row["dispatchId"] = ""
    runner = _runner_with_worker_list(worker_list)
    sess = OrcaBackend(run=runner).open_session(TARGET)
    records = sess.workers()
    finish = next(r for r in records if r.key == FINISH)
    assert finish.handle == ""
    assert finish.last_heartbeat_at is None
    assert not runner.calls_matching("worker-show")


def test_worker_show_failing_reports_no_heartbeat_rather_than_raising():
    # Enumerating a Run must not fail because one worker of many became
    # unreadable — an unreachable dispatch is `None`'s territory, not an
    # exception's.
    failed = json.dumps({"id": "x", "ok": False, "error": {"code": "worker_identity_changed", "message": "gone"}})

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-show" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=failed, stderr="")
            return super().__call__(argv)

    runner = Runner(
        [
            (("run-list", "--cursor"), "run_list_page2"),
            (("run-list",), "run_list"),
            (("run-use",), "run_create"),
            (("task-list",), "task_list"),
            (("worker-list",), "worker_list"),
        ]
    )
    sess = OrcaBackend(run=runner).open_session(TARGET)
    live = next(r for r in sess.workers() if r.key == FINISH)
    assert live.state == "running"
    assert live.last_heartbeat_at is None
