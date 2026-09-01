"""The subprocess backend: one child process per worker, one JSON ledger per session.

`root` is a constructor argument for the same reason a trace directory is: a
package that derives a path has started discovering a workspace. `argv_for` is
injected because a backend must not know what command runs a stage — that is
prompt-and-command assembly, a band up.

`wait()` does three things per poll: tail every live worker's `events.jsonl`
from the line it last delivered; reap any process that exited without settling
itself; return as soon as there is one event, or `[]` at the deadline. Draining
runs before reaping in the same pass, so a child that wrote `worker_done` and
then exited is settled by its own event rather than by a synthesized one.

There is deliberately no `mode` check in `launch()`. This backend's
`supported_modes` is all of `DISPATCH_MODES` — the events file carries
questions and escalations and `replies.jsonl` carries the answer back — so a
check here could never fire. `UnsupportedMode` is a protocol obligation for a
backend that genuinely narrows the set.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import subprocess  # running a child process is this package's whole job
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from subagents_io.backend import (
    BackendError,
    Escalation,
    Heartbeat,
    UnknownWorker,
    WorkerDone,
    WorkerEvent,
    WorkerQuestion,
    WorkerRecord,
    WorktreeNotProvisioned,
)
from subagents_io.dispatch import DISPATCH_MODES, PlannedDispatch

from workflow_local.ledger import LEDGER_NAME, LedgerEntry, read_ledger, write_ledger

#: States a worker cannot leave. The reaper skips these; `stop()` overrides them.
TERMINAL_STATES = frozenset({"succeeded", "failed", "stopped"})

#: How much of `stdout.log` a synthesized `WorkerDone` carries as its summary.
SUMMARY_TAIL_CHARS = 2000

EVENTS_NAME = "events.jsonl"
REPLIES_NAME = "replies.jsonl"
STDOUT_NAME = "stdout.log"


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _reject_windows() -> None:
    """Refuse before `_pid_alive`'s `os.kill(pid, 0)` probe can run.

    That probe maps to `TerminateProcess` on Windows, so this backend kills
    the worker it is asked to observe instead of reporting it. Read at call
    time (not asserted at import) so a test can monkeypatch `sys.platform`
    without needing a fresh interpreter.
    """
    if sys.platform == "win32":
        raise BackendError(
            "workflow-local's liveness probe (os.kill(pid, 0)) maps to TerminateProcess on "
            "Windows, so binding a session here would kill every worker in its ledger — use "
            "workflow-orca as the Windows dispatch backend instead"
        )


def _pid_alive(pid: int) -> bool:
    """Signal 0 probes a pid without delivering anything.

    A `PermissionError` means the pid exists and belongs to someone else, which
    for our purposes is alive. Pid reuse can still report a dead worker as
    running; corroborating a start time is platform-specific work this package
    does not do.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _handle_for(key: str) -> str:
    """A filesystem-safe, deterministic, collision-proof directory name.

    Deterministic because resumption looks the directory up by key with no live
    process to ask; digest-suffixed because two keys can sanitize alike.
    """
    safe = "".join(c if (c.isalnum() or c in "-._") else "_" for c in key)
    return f"{safe}-{hashlib.blake2s(key.encode('utf-8'), digest_size=4).hexdigest()}"


def _tail(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-SUMMARY_TAIL_CHARS:]


class LocalSession:
    """One named, durable run. Everything it knows survives in `ledger.json`."""

    def __init__(
        self,
        *,
        name: str,
        directory: Path,
        argv_for: Callable[[PlannedDispatch], Sequence[str]],
        env: Mapping[str, str] | None,
        poll_interval_s: float,
        stop_grace_s: float,
    ) -> None:
        _reject_windows()
        self.name = name
        #: Lines skipped because they were not JSON or carried an unregistered
        #: kind. Counted rather than raised, and surfaced so it is not silent.
        self.skipped_lines = 0
        self._dir = directory
        self._argv_for = argv_for
        self._env = env
        self._poll_interval_s = poll_interval_s
        self._stop_grace_s = stop_grace_s
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._handles: dict[str, IO[bytes]] = {}
        self._dir.mkdir(parents=True, exist_ok=True)
        self._entries = read_ledger(self._ledger_path)
        self._cursors = {key: entry.acked_through for key, entry in self._entries.items()}
        self._probe_on_open()

    # -- protocol ---------------------------------------------------------

    def launch(self, dispatch: PlannedDispatch) -> WorkerRecord:
        # Silently returning the existing record would make a re-dispatch
        # after a failure read as a success -- matches workflow-orca's own
        # duplicate-key refusal (subagents_io.backend.DispatchSession.launch).
        if dispatch.key in self._entries:
            raise BackendError(f"{self.name}: key {dispatch.key!r} already has a worker in this session")
        if dispatch.worktree.path is None:
            raise WorktreeNotProvisioned(
                f"{dispatch.key}: worktree.path is None — this backend does not provision worktrees"
            )
        handle = _handle_for(dispatch.key)
        worker_dir = self._dir / handle
        worker_dir.mkdir(parents=True, exist_ok=True)
        (worker_dir / EVENTS_NAME).touch()
        (worker_dir / REPLIES_NAME).touch()
        argv = tuple(self._argv_for(dispatch))
        stdout = (worker_dir / STDOUT_NAME).open("wb")
        proc = subprocess.Popen(  # argv is caller-supplied by construction
            argv,
            cwd=dispatch.worktree.path,
            env=self._child_env(worker_dir, dispatch.key),
            stdout=stdout,
            stderr=subprocess.STDOUT,
        )
        self._procs[dispatch.key] = proc
        self._handles[dispatch.key] = stdout
        # `Popen.poll()`/`.wait()` are the only things that ever call `waitpid`
        # on this pid; nothing else reaps a terminated child on POSIX, so an
        # externally-killed process (a coordinator crash, a test harness, an
        # operator's `kill -9`) sits as a zombie — still visible to
        # `os.kill(pid, 0)` — until someone waits on it. A daemon thread
        # blocked in the real `waitpid` syscall reaps it the instant it exits,
        # by any cause, and captures the true exit status before anyone else
        # can observe a stale one. `_terminate`'s SIGTERM/SIGKILL polling loop
        # depends on this thread to ever see a killed pid go away.
        threading.Thread(target=proc.wait, daemon=True, name=f"reap-{dispatch.key}").start()
        entry = LedgerEntry(
            key=dispatch.key,
            handle=handle,
            pid=proc.pid,
            state="running",
            argv=argv,
            cwd=dispatch.worktree.path,
            started_at=_now(),
        )
        self._entries[dispatch.key] = entry
        self._cursors[dispatch.key] = 0
        self._flush()
        return _record(entry)

    def workers(self) -> list[WorkerRecord]:
        """Every worker ever launched here — a settled key is what dedupe reads."""
        return [_record(entry) for entry in self._entries.values()]

    def describe(self, key: str) -> WorkerRecord | None:
        entry = self._entries.get(key)
        return None if entry is None else _record(entry)

    def wait(self, *, timeout_s: float) -> list[WorkerEvent]:
        deadline = time.monotonic() + timeout_s
        while True:
            events = self._poll()
            if events:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            time.sleep(min(self._poll_interval_s, remaining))

    def ack(self, event: WorkerEvent) -> None:
        if event.delivery_id is None:
            return
        entry = self._entries.get(event.key)
        if entry is None:
            raise UnknownWorker(event.key)
        _, _, suffix = event.delivery_id.partition(":")
        if not suffix.isdigit():
            return  # "<handle>:reaped" — synthesized, with no line to advance past
        self._entries[event.key] = replace(entry, acked_through=max(entry.acked_through, int(suffix) + 1))
        self._flush()

    def reply(self, reply_token: str, answer: str) -> None:
        handle = reply_token.partition(":")[0]
        entry = next((e for e in self._entries.values() if e.handle == handle), None)
        if entry is None:
            raise UnknownWorker(reply_token)
        path = self._dir / entry.handle / REPLIES_NAME
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"reply_token": reply_token, "answer": answer}) + "\n")

    def stop(self, key: str) -> None:
        entry = self._entries.get(key)
        if entry is None:
            raise UnknownWorker(key)
        if entry.pid is not None and _pid_alive(entry.pid):
            self._terminate(entry.pid, self._procs.get(key))
        self._entries[key] = replace(entry, state="stopped", detail="stopped by the coordinator")
        self._flush()

    def close(self) -> None:
        """Flush and let go. Children are deliberately left running.

        A coordinator restart should find its workers where it left them; that
        is the whole point of re-deriving state from the ledger.
        """
        self._flush()
        for handle in self._handles.values():
            with contextlib.suppress(OSError):
                handle.close()
        self._handles.clear()

    # -- internals --------------------------------------------------------

    @property
    def _ledger_path(self) -> Path:
        return self._dir / LEDGER_NAME

    def _flush(self) -> None:
        write_ledger(self._ledger_path, self._entries)

    def _child_env(self, worker_dir: Path, key: str) -> dict[str, str]:
        base = dict(os.environ) if self._env is None else dict(self._env)
        base["SUBAGENT_EVENT_LOG"] = str(worker_dir / EVENTS_NAME)
        base["SUBAGENT_REPLY_LOG"] = str(worker_dir / REPLIES_NAME)
        base["SUBAGENT_DISPATCH_KEY"] = key
        return base

    def _probe_on_open(self) -> None:
        """Re-derive live state with no process of our own to ask.

        A live pid reports `running`; a dead one that never settled is left
        `unknown` and picked up by the next `wait()`'s reaper.
        """
        for key, entry in self._entries.items():
            if entry.state in TERMINAL_STATES:
                continue
            alive = entry.pid is not None and _pid_alive(entry.pid)
            self._entries[key] = replace(entry, state="running" if alive else "unknown")

    def _alive(self, entry: LedgerEntry) -> bool:
        proc = self._procs.get(entry.key)
        if proc is not None:
            return proc.poll() is None
        return entry.pid is not None and _pid_alive(entry.pid)

    def _poll(self) -> list[WorkerEvent]:
        events: list[WorkerEvent] = []
        for key in list(self._entries):
            events.extend(self._drain(key))
        for key in list(self._entries):
            events.extend(self._reap(key))
        if events:
            self._flush()
        return events

    def _drain(self, key: str) -> list[WorkerEvent]:
        entry = self._entries[key]
        path = self._dir / entry.handle / EVENTS_NAME
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        cursor = self._cursors.get(key, 0)
        out: list[WorkerEvent] = []
        for line_no in range(cursor, len(lines)):
            event = self._parse(self._entries[key], line_no, lines[line_no])
            if event is None:
                self.skipped_lines += 1
                continue
            self._entries[key] = _apply(self._entries[key], event)
            out.append(event)
        self._cursors[key] = len(lines)
        return out

    def _parse(self, entry: LedgerEntry, line_no: int, line: str) -> WorkerEvent | None:
        if not line.strip():
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        delivery_id = f"{entry.handle}:{line_no}"
        kind = payload.get("kind")
        if kind == "worker_done":
            return WorkerDone(
                key=entry.key,
                handle=entry.handle,
                delivery_id=delivery_id,
                outcome=str(payload.get("outcome", "failed")),
                summary=str(payload.get("summary", "")),
                files_modified=tuple(str(p) for p in payload.get("files_modified") or ()),
                report_path=_optional_str(payload.get("report_path")),
            )
        if kind == "question":
            return WorkerQuestion(
                key=entry.key,
                handle=entry.handle,
                delivery_id=delivery_id,
                question=str(payload.get("question", "")),
                options=tuple(str(o) for o in payload.get("options") or ()),
                reply_token=delivery_id,
            )
        if kind == "escalation":
            return Escalation(
                key=entry.key,
                handle=entry.handle,
                delivery_id=delivery_id,
                subject=str(payload.get("subject", "")),
                body=str(payload.get("body", "")),
            )
        if kind == "heartbeat":
            return Heartbeat(
                key=entry.key,
                handle=entry.handle,
                delivery_id=delivery_id,
                phase=_optional_str(payload.get("phase")),
            )
        return None  # an unregistered kind is skipped and counted, never raised

    def _reap(self, key: str) -> list[WorkerEvent]:
        entry = self._entries[key]
        if entry.state in TERMINAL_STATES or self._alive(entry):
            return []
        proc = self._procs.get(key)
        exit_code = None if proc is None else proc.returncode
        outcome = "succeeded" if exit_code == 0 else "failed"
        event = WorkerDone(
            key=entry.key,
            handle=entry.handle,
            delivery_id=f"{entry.handle}:reaped",
            outcome=outcome,
            summary=_tail(self._dir / entry.handle / STDOUT_NAME),
            files_modified=(),
            report_path=None,
        )
        self._entries[key] = replace(
            entry,
            state=outcome,
            exit_code=exit_code,
            detail=f"reaped: the process exited with {exit_code} without settling itself",
        )
        return [event]

    def _terminate(self, pid: int, proc: subprocess.Popen[bytes] | None) -> None:
        # `_pid_alive` sees a zombie as alive until it's reaped, so this loop's
        # progress depends on the daemon thread `launch()` starts to actually
        # call `waitpid` on `pid` — see the comment there.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + self._stop_grace_s
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                break
            time.sleep(0.02)
        else:
            if sys.platform == "win32":
                # Unreachable: `LocalBackend` refuses win32 at construction with a
                # BackendError naming workflow-orca. Narrowed anyway, because the
                # type gate now checks both platform arms from either host and an
                # unreachable line is still a checked line -- `signal.SIGKILL` does
                # not exist here, and the `SIGTERM` above is already the hard kill
                # that `TerminateProcess` gives.
                return
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if proc is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=self._stop_grace_s)


class LocalBackend:
    """Runs each worker as a child process on this machine."""

    def __init__(
        self,
        root: Path,
        *,
        argv_for: Callable[[PlannedDispatch], Sequence[str]],
        env: Mapping[str, str] | None = None,
        poll_interval_s: float = 0.25,
        stop_grace_s: float = 2.0,
    ) -> None:
        _reject_windows()
        self.name = "workflow-local"
        #: All three. The events file carries questions and escalations and
        #: `replies.jsonl` carries the answer back, so `attend` and `relay` are
        #: genuinely supported rather than declared.
        self.supported_modes = DISPATCH_MODES
        #: This backend never touches git; `launch()` keeps raising
        #: WorktreeNotProvisioned for an unset worktree.path.
        self.provisions_worktrees = False
        self._root = root
        self._argv_for = argv_for
        self._env = env
        self._poll_interval_s = poll_interval_s
        self._stop_grace_s = stop_grace_s

    def open_session(self, name: str) -> LocalSession:
        """Bind or create by name — which is what makes a restart a no-op."""
        return LocalSession(
            name=name,
            directory=self._root / name,
            argv_for=self._argv_for,
            env=self._env,
            poll_interval_s=self._poll_interval_s,
            stop_grace_s=self._stop_grace_s,
        )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _record(entry: LedgerEntry) -> WorkerRecord:
    return WorkerRecord(
        key=entry.key,
        handle=entry.handle,
        state=entry.state,
        last_heartbeat_at=entry.last_heartbeat_at,
        detail=entry.detail,
    )


def _apply(entry: LedgerEntry, event: WorkerEvent) -> LedgerEntry:
    """Fold one event into the durable record."""
    if isinstance(event, WorkerDone):
        state = event.outcome if event.outcome in {"succeeded", "failed"} else "failed"
        return replace(entry, state=state, detail=event.summary[:200] or None)
    if isinstance(event, Heartbeat):
        return replace(entry, state="running", last_heartbeat_at=_now(), detail=event.phase)
    return entry
