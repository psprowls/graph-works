"""A scripted `OrcaPort`: every call is recorded, every answer is set by the test."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def read(
    source: str = "transcript",
    count: int = 0,
    messages: list[dict[str, Any]] | None = None,
    *,
    window_complete: bool = True,
) -> dict[str, Any]:
    msgs = messages if messages is not None else [{"role": "user", "text": "prompt"}] * count
    return {
        "source": source,
        "message_count": len(msgs),
        "source_exact": source == "transcript",
        "window_complete": window_complete,
        "messages": msgs,
    }


@dataclass
class FakeOrcaPort:
    repos: list[dict[str, Any]] = field(default_factory=lambda: [{"id": "repo1", "path": "/repo"}])
    worktrees: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    #: Rows `worktree_list` returns; a reader test seeds a marked checkout here.
    listed: list[dict[str, Any]] = field(default_factory=list)
    #: `worktree_create` calls this with its keyword arguments and returns the row it gives back.
    create: Callable[..., dict[str, Any]] | None = None
    tasks: list[dict[str, Any]] = field(default_factory=list)
    workers: list[dict[str, Any]] = field(default_factory=list)
    start: dict[str, Any] = field(
        default_factory=lambda: {
            "dispatch_id": "ctx_1",
            "worktree_id": "wt1",
            "terminal": "term_1",
            "state": "ready",
            "receipt_problem": None,
        }
    )
    show: dict[str, Any] = field(
        default_factory=lambda: {
            "worktree_id": "wt1",
            "terminal": "term_1",
            "last_heartbeat_at": None,
        }
    )
    reads: list[dict[str, Any]] = field(default_factory=lambda: [read(count=1)])
    pending: dict[str, Any] = field(default_factory=lambda: {"questions": [], "truncated": False, "warnings": []})
    #: Deliveries `check_wait` returns in order; an exhausted script returns an empty batch.
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    #: `check_wait` raises these, in order, before consuming a delivery.
    wait_errors: list[BaseException] = field(default_factory=list)
    #: Called with the timeout of each wait; tests may advance their clock here.
    on_wait: Callable[[int], None] | None = None
    fail: dict[str, BaseException] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    next_task: int = 1

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))
        if name in self.fail:
            raise self.fail[name]

    def names(self) -> list[str]:
        return [name for name, _a, _k in self.calls]

    def liveness(self, run_id, *, now):
        self._record("liveness", run_id, now=now)
        return []

    def repo_list(self):
        self._record("repo_list")
        return list(self.repos)

    def worktree_show(self, selector):
        self._record("worktree_show", selector)
        return self.worktrees.get(selector)

    def worktree_list(self, repo_id):
        self._record("worktree_list", repo_id)
        return [dict(row) for row in self.listed]

    def worktree_create(self, *, name, repo_id, base_branch, comment):
        self._record("worktree_create", worktree_name=name, repo_id=repo_id, base_branch=base_branch, comment=comment)
        assert self.create is not None, "worktree_create was not scripted"
        return self.create(name=name, repo_id=repo_id, base_branch=base_branch, comment=comment)

    def worktree_set_parent(self, worktree_id, parent_id):
        self._record("worktree_set_parent", worktree_id, parent_id)
        row = self.worktrees.get(f"id:{worktree_id}")
        if row is not None:
            row["parent_id"] = parent_id

    def worktree_set_status(self, worktree_id, status):
        self._record("worktree_set_status", worktree_id, status)

    def task_list(self, run_id):
        self._record("task_list", run_id)
        return [dict(t) for t in self.tasks]

    def task_create(self, run_id, *, spec, title, display_name):
        self._record("task_create", run_id, spec=spec, title=title, display_name=display_name)
        task_id = f"task_{self.next_task}"
        self.next_task += 1
        self.tasks.append(
            {"id": task_id, "title": title, "display_name": display_name, "status": "pending", "spec": spec}
        )
        return task_id

    def task_update(self, run_id, task_id, status):
        self._record("task_update", run_id, task_id, status)
        for task in self.tasks:
            if task["id"] == task_id:
                task["status"] = status

    def worker_start(self, run_id, task_id, *, request, placement_argv):
        self._record("worker_start", run_id, task_id, request=request, placement_argv=placement_argv)
        self.workers.insert(
            0,
            {
                "dispatch_id": self.start["dispatch_id"],
                "task_id": task_id,
                "state": "running",
                "dispatch_status": "dispatched",
                "worktree_id": self.start["worktree_id"],
            },
        )
        return dict(self.start)

    def worker_list(self, run_id):
        self._record("worker_list", run_id)
        return [dict(w) for w in self.workers]

    def worker_show(self, dispatch_id):
        self._record("worker_show", dispatch_id)
        return dict(self.show)

    def worker_read(self, dispatch_id, *, limit):
        self._record("worker_read", dispatch_id, limit=limit)
        return dict(self.reads.pop(0) if len(self.reads) > 1 else self.reads[0])

    def pending_questions(self, run_id):
        self._record("pending_questions", run_id)
        return {
            "questions": [dict(q) for q in self.pending["questions"]],
            "truncated": self.pending["truncated"],
            "warnings": list(self.pending["warnings"]),
        }

    def terminal_send_enter(self, terminal):
        self._record("terminal_send_enter", terminal)

    def check_nowait(self, run_id):
        self._record("check_nowait", run_id)
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        if not self.deliveries:
            return {"delivery_id": None, "messages": []}
        return self.deliveries.pop(0)

    def check_wait(self, run_id, *, types, timeout_ms, ack):
        self._record("check_wait", run_id, types=types, timeout_ms=timeout_ms, ack=ack)
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        if self.on_wait is not None:
            self.on_wait(timeout_ms)
        if not self.deliveries:
            return {"delivery_id": None, "messages": []}
        return self.deliveries.pop(0)

    def check_ack(self, run_id, delivery_id):
        self._record("check_ack", run_id, delivery_id)

    def run_use(self, run_id):
        self._record("run_use", run_id)
