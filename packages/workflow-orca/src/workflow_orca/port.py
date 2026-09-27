"""The `OrcaPort` graph-works-core declares, satisfied structurally — this module never imports core.

One method per Orca call; `OrcaSession` is unchanged and does not use it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, TypedDict

from workflow_orca._cli import OrcaCliError, OrcaResult, _subprocess_run, unwrap
from workflow_orca._launch import check_launch_receipt


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
    result: dict[str, Any] | None


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
    release_state: str | None
    terminal: str | None


class OrcaWorkerShow(TypedDict):
    worktree_id: str | None
    terminal: str | None
    last_heartbeat_at: str | None


class OrcaRead(TypedDict):
    source: str | None
    message_count: int


class OrcaMessage(TypedDict):
    id: str
    type: str
    subject: str | None
    body: str | None
    from_: str | None
    created_at: str | None
    payload: dict[str, Any] | None
    payload_raw: str | None


class OrcaDelivery(TypedDict):
    delivery_id: str | None
    messages: list[OrcaMessage]


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _payload(value: object) -> tuple[dict[str, Any] | None, str | None]:
    """Decode current JSON-string or historical mapping payloads without raising."""
    if value is None:
        return {}, None
    if isinstance(value, dict):
        return value, None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None, value
        return (decoded, None) if isinstance(decoded, dict) else (None, value)
    return None, json.dumps(value)


def _message(row: dict[str, Any]) -> OrcaMessage:
    payload, raw = _payload(row.get("payload"))
    current_sender = _string(row.get("from_handle"))
    historical_sender = _string(row.get("from"))
    sender = current_sender if current_sender and current_sender.strip() else historical_sender
    return {
        "id": str(row.get("id") or ""),
        "type": str(row.get("type") or ""),
        "subject": _string(row.get("subject")),
        "body": _string(row.get("body")),
        "from_": sender if sender and sender.strip() else None,
        "created_at": _string(row.get("created_at")),
        "payload": payload,
        "payload_raw": raw,
    }


def _result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _worktree(row: dict[str, Any]) -> OrcaWorktree:
    branch = _string(row.get("branch"))
    is_main = row.get("isMainWorktree")
    return {
        "id": str(row["id"]),
        "repo_id": _string(row.get("repoId")),
        "path": _string(row.get("path")),
        "branch": branch.removeprefix("refs/heads/") if branch is not None else None,
        "display_name": _string(row.get("displayName")),
        "is_main": is_main if isinstance(is_main, bool) else None,
        "parent_id": _string(row.get("parentWorktreeId")),
        "comment": _string(row.get("comment")),
    }


class OrcaCliPort:
    """A small, typed projection of one CLI operation per port method."""

    def __init__(self, *, run: Callable[[Sequence[str]], OrcaResult] = _subprocess_run) -> None:
        self._run = run

    def _call(self, argv: Sequence[str]) -> dict[str, Any]:
        full = ("orca", "orchestration", *argv, "--json")
        return unwrap(full, self._run(full))

    def _call_top(self, argv: Sequence[str]) -> dict[str, Any]:
        full = ("orca", *argv, "--json")
        return unwrap(full, self._run(full))

    def repo_list(self) -> list[OrcaRepo]:
        result = self._call_top(("repo", "list"))
        return [{"id": str(row["id"]), "path": str(row["path"])} for row in _rows(result.get("repos"))]

    def worktree_show(self, selector: str) -> OrcaWorktree | None:
        result = self._call_top(("worktree", "show", "--worktree", selector))
        row = _object(result.get("worktree"))
        if not row:
            return None
        return _worktree(row)

    def worktree_list(self, repo_id: str) -> list[OrcaWorktree]:
        """Every worktree of one repository, or an error when the inventory is incomplete.

        A truncated page or an omitted host would hide a marked reader
        checkout and let a second one be created beside it, so the listing is
        refused unless Orca states it is whole. A row lacking `comment` is
        re-read through `worktree show`, which always carries it.
        """
        argv = ("worktree", "list", "--repo", f"id:{repo_id}")
        result = self._call_top(argv)
        rows = result.get("worktrees")
        scope = _object(result.get("hostScope"))
        if (
            not isinstance(rows, list)
            or result.get("truncated") is not False
            or result.get("totalCount") != len(rows)
            or scope.get("omittedHostIds") != []
        ):
            raise OrcaCliError(
                ("orca", *argv, "--json"), returncode=0, stderr="", message="incomplete worktree inventory"
            )
        listed: list[OrcaWorktree] = []
        for row in _rows(rows):
            if "comment" not in row:
                shown = self.worktree_show(f"path:{_string(row.get('path')) or ''}")
                if shown is None:
                    raise OrcaCliError(
                        ("orca", *argv, "--json"),
                        returncode=0,
                        stderr="",
                        message="cannot inspect a listed worktree's comment",
                    )
                listed.append(shown)
            else:
                listed.append(_worktree(row))
        return listed

    def worktree_create(self, *, name: str, repo_id: str, base_branch: str, comment: str) -> OrcaWorktree:
        """Create one independent, setup-skipped checkout; the caller detaches and verifies it."""
        argv = (
            "worktree",
            "create",
            "--name",
            name,
            "--repo",
            f"id:{repo_id}",
            "--base-branch",
            base_branch,
            "--no-parent",
            "--setup",
            "skip",
            "--comment",
            comment,
        )
        row = _object(self._call_top(argv).get("worktree"))
        if not row or not isinstance(row.get("id"), str):
            raise OrcaCliError(
                ("orca", *argv, "--json"), returncode=0, stderr="", message="worktree create returned no worktree"
            )
        return _worktree(row)

    def worktree_set_parent(self, worktree_id: str, parent_id: str) -> None:
        self._call_top(("worktree", "set", "--worktree", f"id:{worktree_id}", "--parent-worktree", f"id:{parent_id}"))

    def worktree_set_status(self, worktree_id: str, status: str) -> None:
        self._call_top(("worktree", "set", "--worktree", f"id:{worktree_id}", "--workspace-status", status))

    def task_list(self, run_id: str) -> list[OrcaTask]:
        result = self._call(("task-list", "--run", run_id))
        return [
            {
                "id": str(row["id"]),
                "title": _string(row.get("task_title")),
                "display_name": _string(row.get("display_name")),
                "status": _string(row.get("status")),
                "spec": None if row.get("spec_truncated") else _string(row.get("spec")),
                "result": _result(row.get("result")),
            }
            for row in _rows(result.get("tasks"))
        ]

    def task_create(self, run_id: str, *, spec: str, title: str, display_name: str) -> str:
        argv = ("task-create", "--run", run_id, "--spec", spec, "--task-title", title, "--display-name", display_name)
        result = self._call(argv)
        task = _object(result.get("task")) or result
        task_id = _string(task.get("id"))
        if not task_id:
            raise OrcaCliError(
                ("orca", "orchestration", *argv, "--json"),
                returncode=0,
                stderr="",
                message="task-create returned no id",
                receipt={"ok": True, "result": result},
            )
        return task_id

    def task_update(self, run_id: str, task_id: str, status: str) -> None:
        self._call(("task-update", "--id", task_id, "--status", status, "--run", run_id))

    def worker_start(
        self, run_id: str, task_id: str, *, request: dict[str, Any], placement_argv: list[str]
    ) -> OrcaStart:
        argv = ["worker-start", "--task", task_id, "--agent", str(request["agent"]), "--run", run_id, *placement_argv]
        model = request.get("model")
        effort = request.get("reasoning_effort")
        if model is not None:
            argv.extend(("--model", str(model)))
        if effort is not None:
            argv.extend(("--effort", str(effort)))
        try:
            result = self._call(argv)
        except OrcaCliError as exc:
            # A failed launch can still carry a real dispatch. Keep every receipt
            # field and add the reserved task and request for Task 6 recovery.
            exc.details.setdefault("taskId", task_id)
            exc.details["launchRequest"] = request
            raise
        effects = _rows(result.get("effects"))
        worktree_id = next(
            (
                _string(row.get("id"))
                for row in effects
                if row.get("kind") == "worktree" and isinstance(row.get("id"), str)
            ),
            None,
        )
        terminal = _string(result.get("agentTerminalHandle"))
        if terminal is None:
            terminal = next(
                (
                    _string(row.get("id"))
                    for row in effects
                    if row.get("kind") == "terminal" and row.get("role") == "agent" and isinstance(row.get("id"), str)
                ),
                None,
            )
        launch = result.get("launch")
        return {
            "dispatch_id": _string(result.get("dispatchId")),
            "worktree_id": worktree_id,
            "terminal": terminal,
            "state": _string(result.get("workerState") or result.get("dispatchStatus") or result.get("state")),
            "receipt_problem": check_launch_receipt(request, launch if isinstance(launch, dict) else {}),
        }

    def worker_list(self, run_id: str) -> list[OrcaWorker]:
        workers: list[OrcaWorker] = []
        cursor: str | None = None
        while True:
            argv = ["worker-list", "--run", run_id]
            if cursor is not None:
                argv.extend(("--cursor", cursor))
            result = self._call(argv)
            for row in _rows(result.get("workers")):
                resource = _object(row.get("resource"))
                workers.append(
                    {
                        "dispatch_id": str(row["dispatchId"]),
                        "task_id": _string(row.get("taskId")),
                        "state": _string(row.get("workerState")),
                        "dispatch_status": _string(row.get("dispatchStatus")),
                        "worktree_id": _string(row.get("worktreeId")) or _string(resource.get("worktreeId")),
                        "release_state": _string(resource.get("releaseState")),
                        "terminal": _string(row.get("agentTerminalHandle")),
                    }
                )
            page = _object(result.get("page"))
            if not page.get("hasMore"):
                return workers
            cursor = _string(page.get("nextCursor"))
            if cursor is None or not cursor.strip():
                raise OrcaCliError(
                    ("orca", "orchestration", *argv, "--json"),
                    returncode=0,
                    stderr="",
                    message="worker-list page hasMore is true without a usable nextCursor",
                    receipt={"ok": True, "result": result},
                )

    def worker_show(self, dispatch_id: str) -> OrcaWorkerShow:
        result = self._call(("worker-show", "--dispatch", dispatch_id))
        dispatch = _object(result.get("dispatch"))
        worker = _object(result.get("worker"))
        terminal = _object(result.get("terminal"))
        heartbeat_fields = [name for name in ("lastHeartbeatAt", "last_heartbeat_at") if name in dispatch]
        if not heartbeat_fields or any(
            dispatch[name] is not None and (not isinstance(dispatch[name], str) or not dispatch[name].strip())
            for name in heartbeat_fields
        ):
            raise OrcaCliError(
                ("orca", "orchestration", "worker-show", "--dispatch", dispatch_id, "--json"),
                returncode=0,
                stderr="",
                message="worker-show has no usable explicit heartbeat evidence",
                receipt={"ok": True, "result": result},
            )
        return {
            "worktree_id": _string(worker.get("worktreeId")) or _string(worker.get("worktree_id")),
            "terminal": _string(terminal.get("handle"))
            or _string(worker.get("agent_terminal_handle"))
            or _string(worker.get("agentTerminalHandle")),
            "last_heartbeat_at": _string(dispatch.get("lastHeartbeatAt")) or _string(dispatch.get("last_heartbeat_at")),
        }

    def worker_read(self, dispatch_id: str, *, limit: int) -> OrcaRead:
        result = self._call(("worker-read", "--dispatch", dispatch_id, "--limit", str(limit)))
        transcript = _object(result.get("transcript"))
        messages = transcript.get("messages")
        if result.get("source") == "transcript" and (
            not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages)
        ):
            raise OrcaCliError(
                ("orca", "orchestration", "worker-read", "--dispatch", dispatch_id, "--limit", str(limit), "--json"),
                returncode=0,
                stderr="",
                message="worker-read has no complete transcript message array",
                receipt={"ok": True, "result": result},
            )
        return {
            "source": _string(result.get("source")),
            "message_count": len(messages) if isinstance(messages, list) else 0,
        }

    def terminal_send_enter(self, terminal: str) -> None:
        self._call_top(("terminal", "send", "--terminal", terminal, "--text", "", "--enter"))

    def check_wait(self, run_id: str, *, types: str, timeout_ms: int, ack: str | None) -> OrcaDelivery:
        """One blocking check; optionally acknowledge the prior delivery in the same call."""
        argv = ["check", "--run", run_id, "--wait", "--types", types, "--timeout-ms", str(timeout_ms)]
        if ack is not None:
            argv.extend(("--ack", ack))
        result = self._call(argv)
        rows = result.get("messages")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise OrcaCliError(
                ("orca", "orchestration", *argv, "--json"),
                returncode=0,
                stderr="",
                message="malformed check messages",
                receipt={"ok": True, "result": result},
            )
        return {
            "delivery_id": _string(result.get("deliveryId")),
            "messages": [_message(row) for row in rows],
        }

    def check_ack(self, run_id: str, delivery_id: str) -> None:
        self._call(("check", "--run", run_id, "--ack", delivery_id))

    def run_use(self, run_id: str) -> None:
        """Rebind this terminal as the Run's consumer after a fence."""
        self._call(("run-use", "--id", run_id))
