"""The one seam between the orchestrate vertical and Orca (spec §3 Q3).

Declared here, satisfied structurally by `workflow_orca.port.OrcaCliPort`,
which does not import this package. The TypedDicts below are mirrored item
for item in that module; `graph_works_cli.work_cli.orca.orca_port()` is the
typed assignment `just types` checks, and a CLI test compares the two sets
of annotations at runtime. Every failure crosses the seam as
`subagents_io.backend.BackendError`.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


class OrcaRepo(TypedDict):
    id: str
    path: str


class OrcaWorktree(TypedDict):
    id: str
    repo_id: str | None
    path: str | None
    branch: str | None
    display_name: str | None
    is_main: bool | None
    parent_id: str | None
    comment: str | None


class OrcaTask(TypedDict):
    id: str
    title: str | None
    display_name: str | None
    status: str | None
    spec: str | None


class OrcaStart(TypedDict):
    dispatch_id: str | None
    worktree_id: str | None
    terminal: str | None
    state: str | None
    receipt_problem: str | None


class OrcaWorker(TypedDict):
    dispatch_id: str
    task_id: str | None
    state: str | None
    dispatch_status: str | None
    worktree_id: str | None


class OrcaWorkerShow(TypedDict):
    worktree_id: str | None
    terminal: str | None
    last_heartbeat_at: str | None


class OrcaRead(TypedDict):
    source: str | None
    message_count: int


@runtime_checkable
class OrcaPort(Protocol):
    def repo_list(self) -> list[OrcaRepo]: ...
    def worktree_show(self, selector: str) -> OrcaWorktree | None: ...
    def worktree_list(self, repo_id: str) -> list[OrcaWorktree]: ...
    def worktree_create(self, *, name: str, repo_id: str, base_branch: str, comment: str) -> OrcaWorktree: ...
    def worktree_set_parent(self, worktree_id: str, parent_id: str) -> None: ...
    def worktree_set_status(self, worktree_id: str, status: str) -> None: ...
    def task_list(self, run_id: str) -> list[OrcaTask]: ...
    def task_create(self, run_id: str, *, spec: str, title: str, display_name: str) -> str: ...
    def task_update(self, run_id: str, task_id: str, status: str) -> None: ...
    def worker_start(
        self, run_id: str, task_id: str, *, request: dict[str, Any], placement_argv: list[str]
    ) -> OrcaStart: ...
    def worker_list(self, run_id: str) -> list[OrcaWorker]: ...
    def worker_show(self, dispatch_id: str) -> OrcaWorkerShow: ...
    def worker_read(self, dispatch_id: str, *, limit: int) -> OrcaRead: ...
    def terminal_send_enter(self, terminal: str) -> None: ...
