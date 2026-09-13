"""`OrcaBackend` and `OrcaSession` — every argv this package builds.

`open_session` binds an existing Run whose `objective` exactly equals the
session name, or creates one. Bind-or-create by name is what makes a
coordinator restart a no-op instead of a second Run; a settled task's `result`
carries its whole `worker_done` payload, so a session reconstructs completely
from `task-list` with no live process anywhere.

Every command carries `--run <run_id>` explicitly. `run-use` is called exactly
once, on the bind path only, because `check --run <id>` refuses with
`consumer_fenced` from a terminal that is not bound to the Run — the read
paths (`task-list`, `worker-list`) do not, and are not re-bound for.

Three things Orca needs that the protocol does not name — releasing a settled
worker's resource, verifying successful completion, and nudging a worker whose prompt
was typed but never submitted — are folded into `ack()`, `close()` and
`wait()`. Nothing new is exported: an Orca-aware coordinator with extra methods
to call is a coordinator that no longer swaps backends.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from subagents_io.backend import (
    BackendError,
    UnknownWorker,
    UnsupportedMode,
    WorkerDone,
    WorkerEvent,
    WorkerRecord,
    WorktreeNotProvisioned,
)
from subagents_io.dispatch import DISPATCH_MODES, PlannedDispatch, WorktreeAction

from workflow_orca._cli import OrcaCliError, OrcaResult, _subprocess_run, unwrap
from workflow_orca._launch import check_launch_receipt, decode_launch_spec, encode_launch_spec, launch_preferences
from workflow_orca._map import event_from_message, normalize_message, parse_task_result, worker_state

#: Every argv this package builds starts here.
_ORCA = ("orca", "orchestration")

#: `run-list` page size. Large enough that the common case is one call, small
#: enough that a workspace with thousands of Runs does not pay for all of them
#: on a page that already held the answer.
_RUN_PAGE = 50

#: Orca reports a fully-qualified ref; every consumer of a branch name here
#: wants the short form.
_REF_PREFIX = "refs/heads/"


def _short_branch(ref: str) -> str:
    """`refs/heads/psprowls/x` -> `psprowls/x`; anything else, verbatim."""
    return ref[len(_REF_PREFIX) :] if ref.startswith(_REF_PREFIX) else ref


class OrcaBackend:
    """The Orca implementation of `subagents_io.backend.DispatchBackend`."""

    name = "orca"
    #: All three, genuinely rather than declared: question/reply carries
    #: `relay`, and a human joining the agent terminal carries `attend`.
    supported_modes = DISPATCH_MODES
    #: Orca's `worker-start --worktree new-child|new-top-level` creates the
    #: worktree itself, and `main`/`reuse` point at one that already exists, so
    #: this backend handles all four WORKTREE_ACTIONS.
    provisions_worktrees = True

    def __init__(
        self,
        *,
        run: Callable[[Sequence[str]], OrcaResult] = _subprocess_run,
        repo_selector: str | None = None,
    ) -> None:
        self._run = run
        #: Needed only by `create-top-level`, which is why it is optional
        #: rather than required.
        self._repo_selector = repo_selector

    def open_session(self, name: str) -> OrcaSession:
        run_id = self._find_run(name)
        if run_id is None:
            created = self._call(["run-create", "--objective", name])
            raw_run_id = (created.get("run") or created).get("id")
            if not raw_run_id:
                raise BackendError(f"{self.name}: run-create for {name!r} returned no id")
            run_id = str(raw_run_id)
        else:
            # `run-create` binds on its own; the reuse path has to ask.
            self._call(["run-use", "--id", run_id])
        return OrcaSession(
            name=name,
            run_id=run_id,
            run=self._run,
            repo_selector=self._repo_selector,
        )

    def _find_run(self, objective: str) -> str | None:
        """Page `run-list` until the objective matches exactly, or pages run out."""
        cursor: str | None = None
        while True:
            argv = ["run-list", "--limit", str(_RUN_PAGE)]
            if cursor is not None:
                argv += ["--cursor", cursor]
            page = self._call(argv)
            for row in page.get("runs") or []:
                if row.get("objective") == objective:
                    return str(row["id"])
            cursor = page.get("nextCursor")
            if not cursor:
                return None

    def _call(self, argv: Sequence[str]) -> dict[str, Any]:
        full = [*_ORCA, *argv, "--json"]
        return unwrap(full, self._run(full))


class OrcaSession:
    """One Orca Run, driven as a `subagents_io.backend.DispatchSession`."""

    def __init__(
        self,
        *,
        name: str,
        run_id: str,
        run: Callable[[Sequence[str]], OrcaResult],
        repo_selector: str | None,
    ) -> None:
        self.name = name
        self.run_id = run_id
        self._run = run
        self._repo_selector = repo_selector
        #: task id -> dispatch key, refreshed from `task-list`. `wait()` uses
        #: it to attribute a message to a key without a second walk.
        self._keys_by_task: dict[str, str] = {}
        #: "once per worker per session", deliberately not persisted — a
        #: coordinator restart re-earns one nudge per worker, because a
        #: worker that was nudged and then genuinely hung must stay
        #: nudgeable across the restart that is the loop's own recovery path.
        self._nudged: set[str] = set()
        #: Dispatch handles whose terminal `close()` still owes a release.
        self._unreleased: set[str] = set()
        #: dispatch handle -> agent terminal handle, refreshed from
        #: matching worker-list/worker-show evidence on every enumeration.
        #: An agent handle alone can also identify a terminal-free worker.
        self._agent_terminals: dict[str, str] = {}
        #: Handles with verified durable launch receipts in the latest enumeration.
        self._verified_launches: set[str] = set()
        self._done_by_delivery: dict[str, tuple[WorkerDone, ...]] = {}

    def _call(self, argv: Sequence[str]) -> dict[str, Any]:
        full = [*_ORCA, *argv, "--run", self.run_id, "--json"]
        return unwrap(full, self._run(full))

    def _call_unscoped(self, argv: Sequence[str]) -> dict[str, Any]:
        """For the per-dispatch commands, which take no `--run`."""
        full = [*_ORCA, *argv, "--json"]
        return unwrap(full, self._run(full))

    def _call_top_level(self, argv: Sequence[str]) -> dict[str, Any]:
        """For top-level `orca …` commands (`terminal`, `worktree`), which
        are not `orchestration` subcommands and take no `--run`."""
        full = ["orca", *argv, "--json"]
        return unwrap(full, self._run(full))

    def launch(self, dispatch: PlannedDispatch) -> WorkerRecord:
        if dispatch.mode not in DISPATCH_MODES:
            raise UnsupportedMode(f"{self.name}: mode {dispatch.mode!r} is not one of {sorted(DISPATCH_MODES)}")
        preferences = launch_preferences(dispatch)
        worktree_flags = self._worktree_flags(dispatch.worktree)
        spec = encode_launch_spec(dispatch, placement_argv=worktree_flags)
        request = decode_launch_spec(spec)
        if dispatch.model is not None:
            status = self._call_top_level(["status"])
            if "orchestration.worker-launch-preferences.v1" not in (status.get("runtime") or {}).get(
                "capabilities", []
            ):
                raise BackendError(
                    "Orca runtime lacks worker launch preferences; update or restart Orca before launching."
                )

        # The session is the ledger: a settled key must reconstruct from
        # `task-list` alone, so a duplicate is refused here rather than
        # risked as a silent double-dispatch under the same key.
        tasks = self._call(["task-list"]).get("tasks") or []
        self._keys_by_task = {str(t["id"]): str(t.get("task_title") or "") for t in tasks}
        for existing_id, existing_key in self._keys_by_task.items():
            if existing_key == dispatch.key:
                raise BackendError(
                    f"{self.name}: key {dispatch.key!r} already has a task ({existing_id}) in this session"
                )

        created = self._call(
            [
                "task-create",
                "--spec",
                spec,
                "--task-title",
                dispatch.key,
                # One identifier, not two. `--task-title` is load-bearing --
                # `_refresh_keys` rebuilds the `task_id -> key` ledger by reading
                # it back out of `task-list` -- and `--display-name` is what a
                # human reads. Sending anything else here means the string on
                # screen is not the string anything looks up.
                "--display-name",
                dispatch.key,
            ]
        )
        raw_task_id = (created.get("task") or created).get("id")
        if not raw_task_id:
            raise BackendError(f"{self.name}: task-create for {dispatch.key!r} returned no id")
        task_id = str(raw_task_id)
        self._keys_by_task[task_id] = dispatch.key

        start_argv = ["worker-start", "--task", task_id, *preferences, *worktree_flags]
        try:
            started = self._call(start_argv)
        except OrcaCliError as exc:
            exc.details.update(taskId=task_id, dispatchKey=dispatch.key, launchRequest=request)
            exc.add_note(f"Task {task_id} remains reserved; inspect recovery evidence before any retry.")
            raise
        raw_handle = started.get("dispatchId")
        if not raw_handle:
            missing_handle = OrcaCliError(
                start_argv,
                returncode=0,
                stderr="",
                message=f"worker-start returned no dispatchId; task {task_id} requires recovery inspection",
                receipt={"ok": True, "result": started},
            )
            missing_handle.details.update(taskId=task_id, dispatchKey=dispatch.key, launchRequest=request)
            raise missing_handle
        handle = str(raw_handle)
        launch = started.get("launch")
        reason = check_launch_receipt(request, launch if isinstance(launch, dict) else {})
        self._unreleased.add(handle)
        worktree_path, worktree_branch = self._resolve_worktree(dispatch.worktree, started, handle)
        return WorkerRecord(
            key=dispatch.key,
            handle=handle,
            state=worker_state(started.get("workerState") or started.get("workerOutcome") or started.get("state")),
            last_heartbeat_at=None,
            detail=f"taskId={task_id}; dispatchId={handle}; {reason}" if reason else started.get("dispatchStatus"),
            worktree_path=worktree_path,
            worktree_branch=worktree_branch,
        )

    #: The states that mean a worker might still be talking, and so the ones
    #: `last_heartbeat_at` is worth a per-dispatch call for.
    _LIVE_STATES = frozenset({"pending", "running"})

    def workers(self) -> list[WorkerRecord]:
        """Every worker ever launched in this session.

        Costs `2 + W` calls, where `W` counts workers with handles. Full task
        specs and durable worker launch receipts are required even after
        settlement. The same worker-show also supplies heartbeat and actual
        terminal proof; lifecycle never depends on the presence of a terminal.
        """
        tasks = self._call(["task-list"]).get("tasks") or []
        rows = self._call(["worker-list"]).get("workers") or []

        # Later rows win: retries append, so the last row for a task is the
        # most recent attempt.
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            task_id = str(row.get("taskId") or "")
            if task_id:
                latest[task_id] = row

        self._keys_by_task = {str(t["id"]): str(t.get("task_title") or "") for t in tasks}
        records: list[WorkerRecord] = []
        self._agent_terminals.clear()
        self._verified_launches.clear()
        for task in tasks:
            task_id = str(task["id"])
            key = self._keys_by_task[task_id]
            row = latest.get(task_id)
            if row is None:
                # `worker-start` died after `task-create` succeeded. That is
                # what "unknown" is for.
                records.append(
                    WorkerRecord(
                        key=key,
                        handle="",
                        state="unknown",
                        last_heartbeat_at=None,
                        detail=(
                            f"taskId={task_id}; Launch configuration unverified: "
                            "no worker receipt; inspect task recovery."
                        ),
                    )
                )
                continue
            handle = str(row.get("dispatchId") or "")
            state = worker_state(row.get("workerState"))
            shown = self._show(handle)
            context = shown.get("dispatch") or {}
            heartbeat = (
                (context.get("lastHeartbeatAt") or context.get("last_heartbeat_at"))
                if state in self._LIVE_STATES
                else None
            )
            detail = row.get("dispatchStatus") or parse_task_result(task.get("result")).get("outcome")
            reason = self._launch_verification(task, shown)
            if reason:
                detail = f"taskId={task_id}; dispatchId={handle}; {reason}"
            else:
                self._verified_launches.add(handle)
            terminal = shown.get("terminal")
            if isinstance(terminal, dict):
                terminal_handle = terminal.get("handle")
                if handle and terminal_handle and terminal_handle == row.get("agentTerminalHandle"):
                    self._agent_terminals[handle] = str(terminal_handle)
            records.append(
                WorkerRecord(key=key, handle=handle, state=state, last_heartbeat_at=heartbeat, detail=detail)
            )
            if state in {"succeeded", "failed", "stopped"} and handle and not (state == "succeeded" and reason):
                # `_unreleased` is not persisted, so a worker settled before
                # this session object existed (e.g. across a coordinator
                # restart) never went through `launch()`'s own `.add()` —
                # enumeration is what re-earns its release obligation.
                self._unreleased.add(handle)
        return records

    def describe(self, key: str) -> WorkerRecord | None:
        """`workers()` filtered to one key. Same cost, same honesty — a
        separate cheaper path would be a second state-derivation to keep in
        sync with this one."""
        for record in self.workers():
            if record.key == key:
                return record
        return None

    #: All four kinds, requested every time. The cost is real and accepted: a
    #: heartbeat arrives roughly every 5 minutes per worker and returns from
    #: `wait()`, so a coordinator's outer loop re-derives about that often. A
    #: constructor flag to switch it off would be two code paths the suite has
    #: to cover, for a behavior nobody has asked for.
    _EVENT_TYPES = "worker_done,escalation,question,heartbeat"

    def wait(self, *, timeout_s: float) -> list[WorkerEvent]:
        """Block for one batch of events, or return `[]` on timeout.

        Never raises on a quiet Run: a timeout and an empty batch are the
        same outcome to a caller, so the outer loop stays a plain `while`.
        The unsent-prompt nudge — Orca hygiene the protocol does not name —
        is folded in here rather than exported, and only runs when nothing
        arrived: an event delivered is itself proof of life.
        """
        batch = self._call(
            ["check", "--wait", "--types", self._EVENT_TYPES, "--timeout-ms", str(int(timeout_s * 1000))]
        )
        messages = [normalize_message(message) for message in (batch.get("messages") or [])]
        # Absent entirely on an empty batch — verified live.
        delivery_id = batch.get("deliveryId")

        if any((m.get("payload") or {}).get("taskId") not in self._keys_by_task for m in messages):
            self._refresh_keys()

        events: list[WorkerEvent] = []
        for message in messages:
            event = event_from_message(message, self._keys_by_task, delivery_id=delivery_id)
            if event is not None:
                events.append(event)

        if delivery_id:
            self._done_by_delivery[str(delivery_id)] = tuple(e for e in events if isinstance(e, WorkerDone))
        if not events:
            # Never in the hot path: an event delivered is itself proof of
            # life, so the probe runs only when nothing arrived.
            self._nudge_sweep()
        return events

    def _refresh_keys(self) -> None:
        tasks = self._call(["task-list"]).get("tasks") or []
        self._keys_by_task = {str(t["id"]): str(t.get("task_title") or "") for t in tasks}

    def _nudge_sweep(self) -> None:
        """Press Enter for a worker whose prompt was typed but never submitted.

        `worker-start` sometimes leaves the prompt on screen unsubmitted. The
        worker then reports `running` forever, `wait()` blocks forever, and
        the loop has no exit — so this is correctness, not taste, and the
        backend is the only layer that can see it.

        Ordered, first decisive answer wins, at most one nudge per worker per
        session. A bare Enter into a live agent answers whatever is on screen
        with its highlighted default, and `attend` dispatches are
        simultaneously the likeliest to look idle and the costliest to nudge
        blind — which is why step 4's transcript check, not elapsed time, is
        what decides.
        """
        for record in self.workers():
            if record.state not in self._LIVE_STATES or not record.handle:
                continue
            if record.handle in self._nudged:
                continue
            # 1. A worker that never submitted cannot have heartbeat.
            if record.last_heartbeat_at is not None:
                continue
            try:
                read = self._call_unscoped(["worker-read", "--dispatch", record.handle, "--limit", "5"])
            except OrcaCliError:
                # 2. The read failed outright (`worker_identity_changed` and
                #    its kin). An unreadable worker is never nudged.
                continue
            # 3. A degraded read proves nothing about what is on screen.
            if read.get("source") == "terminal":
                continue
            # 4. A dialog on screen is itself transcript activity, so an
            #    empty transcript is what proves no dialog can be up.
            if (read.get("transcript") or {}).get("messages"):
                continue
            terminal = self._agent_terminal(record.handle)
            if terminal is None:
                continue
            self._nudged.add(record.handle)
            self._call_top_level(["terminal", "send", "--terminal", terminal, "--text", "", "--enter"])

    def _agent_terminal(self, handle: str) -> str | None:
        """Only the matching actual terminal proven by the latest worker-show."""
        return self._agent_terminals.get(handle)

    def _show(self, handle: str) -> dict[str, Any]:
        if not handle:
            return {}
        try:
            return self._call_unscoped(["worker-show", "--dispatch", handle])
        except OrcaCliError:
            return {}

    @staticmethod
    def _launch_verification(task: dict[str, Any], shown: dict[str, Any]) -> str | None:
        try:
            spec = task.get("spec")
            if not isinstance(spec, str) or task.get("spec_truncated"):
                raise BackendError("Missing full launch envelope; inspect the untruncated task spec.")
            request = decode_launch_spec(spec)
            if request["dispatch_key"] != task.get("task_title"):
                raise BackendError("The launch envelope dispatch_key does not match the task title; inspect recovery.")
        except BackendError as exc:
            return f"Launch configuration unverified: {exc}"
        worker = shown.get("worker")
        options = worker.get("startOptions") if isinstance(worker, dict) else None
        receipt = options.get("launch") if isinstance(options, dict) else None
        return check_launch_receipt(request, receipt if isinstance(receipt, dict) else {})

    def _worktree_flags(self, worktree: WorktreeAction) -> list[str]:
        if worktree.action in ("reuse", "main"):
            if worktree.path is None:
                raise WorktreeNotProvisioned(
                    f"{self.name}: a {worktree.action} dispatch needs a concrete worktree path"
                )
            # Creation flags are rejected by Orca for an existing worktree, so
            # this branch passes the selector and nothing else. "main" is the
            # repository's own checkout: on disk already, never created here.
            return ["--worktree", f"path:{worktree.path}"]
        if worktree.action == "fork-child":
            if worktree.base_branch is None:
                raise BackendError(f"{self.name}: a fork-child dispatch needs a base_branch")
            return ["--worktree", "new-child", "--name", worktree.branch, "--base-branch", worktree.base_branch]
        if worktree.action == "create-top-level":
            if worktree.base_branch is None:
                raise BackendError(f"{self.name}: a create-top-level dispatch needs a base_branch")
            if self._repo_selector is None:
                raise BackendError(f"{self.name}: a create-top-level dispatch needs repo_selector on the backend")
            return [
                "--worktree",
                "new-top-level",
                "--name",
                worktree.branch,
                "--base-branch",
                worktree.base_branch,
                "--repo",
                self._repo_selector,
            ]
        raise BackendError(f"{self.name}: unknown worktree action {worktree.action!r}")

    #: The two actions where Orca provisions the worktree, and so the only
    #: ones with anything to read back. `reuse`/`main` point at a checkout
    #: that already existed before this dispatch.
    _PROVISIONING_ACTIONS = frozenset({"fork-child", "create-top-level"})

    def _resolve_worktree(
        self, worktree: WorktreeAction, started: dict[str, Any], handle: str
    ) -> tuple[str | None, str | None]:
        """What Orca actually provisioned: `(path, short branch)`.

        Read-back only. Orca's `--name` is a worktree display name and Orca
        derives the branch from it by its own rule, so the planned and actual
        names differ routinely — that divergence is data here, never an error,
        and nothing in this method compares, warns, or corrects.

        Every failure degrades to `None`. `launch()` has already started a
        real worker by the time this runs, and a read that could undo a write
        would be the worse bug: `None` means "not learned", which is exactly
        what happened.
        """
        if worktree.action not in self._PROVISIONING_ACTIONS:
            # Nothing was created, so there is nothing to ask Orca about. The
            # path is the concrete one `_worktree_flags` just validated; the
            # branch stays unknown rather than being copied from the plan,
            # since the plan is the thing this field exists not to trust.
            return worktree.path, None
        worktree_id = self._worktree_id(started, handle)
        if worktree_id is None:
            return None, None
        try:
            shown = self._call_top_level(["worktree", "show", "--worktree", f"id:{worktree_id}"])
        except OrcaCliError:
            return None, None
        row = shown.get("worktree") or {}
        path = row.get("path")
        branch = row.get("branch")
        return (
            str(path) if path else None,
            _short_branch(str(branch)) if branch else None,
        )

    def _worktree_id(self, started: dict[str, Any], handle: str) -> str | None:
        """The `<repoId>::<path>` id of the worktree this dispatch got.

        The captured `worker-start` response does not carry it and
        `worker-show`'s `worker` block does, so the start payload is checked
        first purely so the extra call disappears on the day Orca adds the
        field — not because any response is known to carry it today.
        """
        direct = started.get("worktreeId") or (started.get("worker") or {}).get("worktree_id")
        if direct:
            return str(direct)
        try:
            shown = self._call_unscoped(["worker-show", "--dispatch", handle])
        except OrcaCliError:
            return None
        worker = shown.get("worker") or {}
        found = worker.get("worktreeId") or worker.get("worktree_id")
        return str(found) if found else None

    def ack(self, event: WorkerEvent) -> None:
        """Acknowledge a delivery and release a proven settled worker.

        Orca's accepted worker_done already owns task settlement. Successful
        results with unverified launch choices stay unacknowledged for recovery.
        """
        if event.delivery_id is None:
            return
        pending = list(self._done_by_delivery.get(event.delivery_id, ()))
        if isinstance(event, WorkerDone) and event not in pending:
            pending.append(event)
        for done in pending:
            self._verify_done(done)
        self._call(["check", "--ack", event.delivery_id])
        self._done_by_delivery.pop(event.delivery_id, None)
        if isinstance(event, WorkerDone):
            self._release(event.handle)

    def _verify_done(self, event: WorkerDone) -> None:
        task_id = self._task_id_for(event.key)
        if task_id is None:
            raise UnknownWorker(f"{self.name}: no task tracked for key {event.key!r}")
        if event.outcome == "failed":
            return
        tasks = self._call(["task-list"]).get("tasks") or []
        task: dict[str, Any] = next((t for t in tasks if str(t["id"]) == task_id), {})
        reason = self._launch_verification(task, self._show(event.handle))
        if reason:
            raise BackendError(f"taskId={task_id}; dispatchId={event.handle}; deliveryId={event.delivery_id}; {reason}")

    def reply(self, reply_token: str, answer: str) -> None:
        """`reply --id <msg_id>`. The token is the question message's own id,
        so no second correlation scheme exists to get wrong."""
        self._call(["reply", "--id", reply_token, "--body", answer])

    def stop(self, key: str) -> None:
        record = self.describe(key)
        if record is None or not record.handle:
            raise UnknownWorker(f"{self.name}: no worker for key {key!r}")
        self._call_unscoped(["worker-stop", "--dispatch", record.handle])

    def close(self) -> None:
        """Release settled-but-unreleased terminals.

        Does not stop live workers, and does not delete the Run: the Run *is*
        the durable session, and deleting it would destroy the resume path.
        """
        for record in self.workers():
            if (
                record.state not in {"succeeded", "failed", "stopped"}
                or not record.handle
                or (record.state == "succeeded" and record.handle not in self._verified_launches)
            ):
                self._unreleased.discard(record.handle)
        for handle in sorted(self._unreleased):
            self._release(handle)

    def _release(self, handle: str) -> None:
        if not handle:
            return
        self._unreleased.discard(handle)
        try:
            self._call_unscoped(["worker-release", "--dispatch", handle])
        except OrcaCliError:
            # `worker-release` is idempotent and reports `already_released`
            # on a repeat; a release that cannot be proven is not worth
            # failing an ack over.
            return

    def _task_id_for(self, key: str) -> str | None:
        for task_id, task_key in self._keys_by_task.items():
            if task_key == key:
                return task_id
        self._refresh_keys()
        for task_id, task_key in self._keys_by_task.items():
            if task_key == key:
                return task_id
        return None
