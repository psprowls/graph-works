"""A stateful fake of the `orca orchestration` CLI, for the conformance suite.

`orca_fakes.FakeRunner` replays one canned fixture per argv pattern — perfect
for this package's own unit tests, which each exercise a single call in
isolation, but wrong here: the conformance scenarios imported into
`test_conformance_orca.py` drive a whole session lifecycle (launch, then
`workers()` sees it, then `wait()` delivers events one at a time, then a
reopen must still see everything settled), and a fixed reply can't tell a
pre-launch `task-list` from a post-launch one.

This is `workflow-orca`'s answer to what `workflow-local`'s `fake.py` and
`child.py` are for the other two `BackendCase`s: an in-memory interpreter of
the scripted programs `conformance.make_dispatch` builds (`"heartbeat:planning"`,
`"done:succeeded"`, `"question:merge?"`, ...), except it sits one layer lower —
behind the argv boundary, answering as the `orca` binary would, so what is
under test is still `OrcaBackend`'s translation, not a second backend.

One step of a worker's program is revealed per `check --wait` call, not all at
once: `test_every_state_a_backend_emits_is_in_the_vocabulary` asserts the
lifecycle passes through at least two `WORKER_STATES`, which a fake that
resolved a worker to its final state at `worker-start` would make vacuous.

A message stays outstanding until acked, and every `check --wait` call
re-returns whatever is still outstanding — the redelivery the resume scenario
depends on falls out of that rule for free, with no session-scoped bookkeeping.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from workflow_orca._cli import OrcaResult


def _after(argv: Sequence[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class FakeOrcaCLI:
    """One Orca workspace: runs, tasks, workers and their message queues."""

    def __init__(self) -> None:
        self._counter = 0
        self._runs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._workers: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, ...]] = []

    # -- transport -----------------------------------------------------

    def __call__(self, argv: Sequence[str]) -> OrcaResult:
        argv = tuple(argv)
        self.calls.append(argv)
        if len(argv) >= 2 and argv[1] == "terminal":
            return self._ok({})
        if argv[1] == "status":
            return self._ok({"runtime": {"capabilities": ["orchestration.worker-launch-preferences.v1"]}})
        sub = argv[2]
        handler = getattr(self, f"_cmd_{sub.replace('-', '_')}", None)
        if handler is None:
            raise AssertionError(f"FakeOrcaCLI has no handler for {argv!r}")
        return handler(argv)

    def _ok(self, result: dict[str, Any]) -> OrcaResult:
        body = {"id": self._next_id("resp"), "ok": True, "result": result}
        return OrcaResult(returncode=0, stdout=json.dumps(body), stderr="")

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:012d}"

    # -- runs ------------------------------------------------------------

    def _cmd_run_list(self, argv: Sequence[str]) -> OrcaResult:
        del argv  # every known run fits on one page; no --cursor to honour
        runs = [{"id": rid, "objective": r["objective"]} for rid, r in self._runs.items()]
        return self._ok({"runs": runs})

    def _cmd_run_create(self, argv: Sequence[str]) -> OrcaResult:
        objective = _after(argv, "--objective")
        run_id = self._next_id("run")
        self._runs[run_id] = {"objective": objective}
        return self._ok({"run": {"id": run_id, "objective": objective}})

    def _cmd_run_use(self, argv: Sequence[str]) -> OrcaResult:
        del argv
        return self._ok({})

    # -- tasks -------------------------------------------------------------

    def _cmd_task_list(self, argv: Sequence[str]) -> OrcaResult:
        run_id = _after(argv, "--run")
        tasks = [
            {
                "id": tid,
                "run_id": run_id,
                "task_title": t["task_title"],
                "status": t["status"],
                "result": None,
                "spec": t["spec"],
            }
            for tid, t in self._tasks.items()
            if t["run_id"] == run_id
        ]
        return self._ok({"runId": run_id, "tasks": tasks, "count": len(tasks)})

    def _cmd_task_create(self, argv: Sequence[str]) -> OrcaResult:
        run_id = _after(argv, "--run")
        spec = _after(argv, "--spec")
        title = _after(argv, "--task-title")
        task_id = self._next_id("task")
        self._tasks[task_id] = {
            "run_id": run_id,
            "task_title": title,
            "status": "pending",
            # The prompt IS the program both `_local` (a real subprocess) and
            # `_fake` (in-process) interpret; here it is what this fake's
            # workers step through one token per `check --wait` tick.
            "spec": spec,
            "steps": spec.split("\n", 1)[1].split(),
        }
        return self._ok({"task": {"id": task_id, "run_id": run_id, "task_title": title}})

    def _cmd_task_update(self, argv: Sequence[str]) -> OrcaResult:
        task_id = _after(argv, "--id")
        status = _after(argv, "--status")
        if task_id not in self._tasks:
            raise AssertionError(f"FakeOrcaCLI: task-update against unknown task {task_id!r}")
        self._tasks[task_id]["status"] = status
        return self._ok({})

    # -- workers -----------------------------------------------------------

    def _cmd_worker_start(self, argv: Sequence[str]) -> OrcaResult:
        run_id = _after(argv, "--run")
        task_id = _after(argv, "--task")
        dispatch_id = self._next_id("ctx")
        terminal = self._next_id("term")
        if task_id not in self._tasks:
            raise AssertionError(f"FakeOrcaCLI: worker-start against unknown task {task_id!r}")
        task = self._tasks[task_id]
        # Synthetic receipt: field names observed in installed Orca 1.4.200.
        selected = {
            "agent": _after(argv, "--agent"),
            "model": _after(argv, "--model") if "--model" in argv else None,
            "effort": _after(argv, "--effort") if "--effort" in argv else None,
        }
        launch = {"requested": dict(selected), "effective": dict(selected)}
        self._workers[dispatch_id] = {
            "launch": launch,
            "run_id": run_id,
            "task_id": task_id,
            "raw_state": "running",
            "blocked": False,
            "finished": False,
            "revealed": 0,
            "steps": task["steps"],
            "terminal": terminal,
        }
        return self._ok(
            {
                "launch": launch,
                "dispatchId": dispatch_id,
                "taskId": task_id,
                "runId": run_id,
                "workerState": "running",
                "dispatchStatus": "running",
                "agentTerminalHandle": terminal,
            }
        )

    def _cmd_worker_list(self, argv: Sequence[str]) -> OrcaResult:
        run_id = _after(argv, "--run")
        rows = [
            {
                "dispatchId": wid,
                "taskId": w["task_id"],
                "runId": run_id,
                "workerState": w["raw_state"],
                "dispatchStatus": w["raw_state"],
                "agentTerminalHandle": w["terminal"],
            }
            for wid, w in self._workers.items()
            if w["run_id"] == run_id
        ]
        return self._ok({"workers": rows, "counts": {}})

    def _cmd_worker_show(self, argv: Sequence[str]) -> OrcaResult:
        handle = _after(argv, "--dispatch")
        worker = self._workers[handle]
        return self._ok(
            {
                "dispatch": {"id": handle, "last_heartbeat_at": "2026-08-14T00:00:00Z"},
                "worker": {"startOptions": {"launch": worker["launch"]}},
                "terminal": {"handle": worker["terminal"]} if worker["terminal"] else None,
            }
        )

    def _cmd_worker_stop(self, argv: Sequence[str]) -> OrcaResult:
        handle = _after(argv, "--dispatch")
        if handle not in self._workers:
            raise AssertionError(f"FakeOrcaCLI: worker-stop against unknown dispatch {handle!r}")
        worker = self._workers[handle]
        worker["raw_state"] = "stopped"
        worker["finished"] = True
        return self._ok({})

    def _cmd_worker_release(self, argv: Sequence[str]) -> OrcaResult:
        del argv  # idempotent; nothing to track for the reused scenarios
        return self._ok({})

    def _cmd_worker_read(self, argv: Sequence[str]) -> OrcaResult:
        handle = _after(argv, "--dispatch")
        # A non-empty transcript from a non-"terminal" source is what makes
        # `_nudge_sweep` decide a dialog is already on screen and stand down —
        # none of the reused scenarios leave a worker idle-with-no-heartbeat,
        # so this path is not expected to run, but it must answer safely
        # (never send a stray Enter) if it ever does.
        return self._ok(
            {
                "dispatchId": handle,
                "source": "api",
                "transcript": {"messages": [{"id": "seed"}], "limited": False, "returnedMessageCount": 1},
                "status": {"worker": "ready"},
            }
        )

    # -- messages ------------------------------------------------------------

    def _cmd_check(self, argv: Sequence[str]) -> OrcaResult:
        if "--ack" in argv:
            return self._check_ack(argv)
        return self._check_wait(argv)

    def _check_wait(self, argv: Sequence[str]) -> OrcaResult:
        run_id = _after(argv, "--run")
        for worker in self._workers.values():
            if worker["run_id"] == run_id:
                self._tick(worker)
        outstanding = [m for m in self._messages.values() if m["run_id"] == run_id and not m["acked"]]
        if not outstanding:
            return self._ok({"runId": run_id, "messages": [], "count": 0})
        delivery_id = self._next_id("dlv")
        for message in outstanding:
            message["delivery_id"] = delivery_id
        return self._ok(
            {
                "runId": run_id,
                "deliveryId": delivery_id,
                "messages": [self._public(m) for m in outstanding],
                "count": len(outstanding),
            }
        )

    def _check_ack(self, argv: Sequence[str]) -> OrcaResult:
        delivery_id = _after(argv, "--ack")
        for message in self._messages.values():
            if message.get("delivery_id") == delivery_id:
                message["acked"] = True
        return self._ok({})

    def _cmd_reply(self, argv: Sequence[str]) -> OrcaResult:
        message_id = _after(argv, "--id")
        if message_id not in self._messages:
            raise AssertionError(f"FakeOrcaCLI: reply against unknown message {message_id!r}")
        dispatch_id = self._messages[message_id]["payload"].get("dispatchId")
        if dispatch_id not in self._workers:
            raise AssertionError(f"FakeOrcaCLI: reply's message names unknown dispatch {dispatch_id!r}")
        # Unblocks the worker; the next `check --wait` tick reveals whatever
        # the program's next step is.
        self._workers[dispatch_id]["blocked"] = False
        return self._ok({})

    def _tick(self, worker: dict[str, Any]) -> None:
        """Reveal at most one more step of `worker`'s program, if it can."""
        if worker["blocked"] or worker["finished"]:
            return
        steps = worker["steps"]
        idx = worker["revealed"]
        if idx >= len(steps):
            return
        worker["revealed"] += 1
        kind, _, arg = steps[idx].partition(":")
        self._reveal(worker, kind, arg)

    def _reveal(self, worker: dict[str, Any], kind: str, arg: str) -> None:
        # `kind` is the program's own token vocabulary (`conformance.make_dispatch`'s
        # "done:succeeded", "heartbeat:planning", ...) — "done" is not itself an
        # Orca message type; `event_from_message` (`_map.py`) matches on
        # `EVENT_KINDS`, where a settled worker's type is "worker_done".
        message_type = "worker_done" if kind == "done" else kind
        payload: dict[str, Any] = {"taskId": worker["task_id"], "dispatchId": self._dispatch_id_of(worker)}
        if kind == "done":
            worker["finished"] = True
            worker["raw_state"] = "succeeded" if arg == "succeeded" else "failed"
            self._tasks[worker["task_id"]]["status"] = "completed" if arg == "succeeded" else "failed"
            payload |= {"outcome": worker["raw_state"], "filesModified": [], "reportPath": None}
        elif kind == "question":
            worker["blocked"] = True
            payload |= {"question": arg or "?", "options": ["a", "b"]}
        elif kind == "heartbeat":
            worker["raw_state"] = "running"
            payload |= {"phase": arg or None}
        elif kind == "escalation":
            payload |= {}
        else:
            raise AssertionError(f"FakeOrcaCLI does not know the program step {kind!r}")
        message_id = self._next_id("msg")
        self._messages[message_id] = {
            "id": message_id,
            "type": message_type,
            "subject": arg or kind,
            "body": arg or "",
            "from": worker["terminal"],
            "created_at": "2026-08-14T00:00:00Z",
            "payload": payload,
            "run_id": worker["run_id"],
            "acked": False,
        }

    def _dispatch_id_of(self, worker: dict[str, Any]) -> str:
        for dispatch_id, candidate in self._workers.items():
            if candidate is worker:
                return dispatch_id
        raise AssertionError("worker not registered under its own dispatch id")

    @staticmethod
    def _public(message: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in message.items() if k not in {"run_id", "acked", "delivery_id"}}
