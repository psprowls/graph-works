"""An in-memory DispatchBackend, so the scenarios test the Protocol and not one backend.

It exists for three jobs a real backend cannot do:

1. **Narrow `supported_modes`.** `LocalBackend` supports all three modes and so
   can never raise `UnsupportedMode`; this one supports only `autonomous`, which
   is what makes that protocol obligation testable at all.
2. **Emit `"pending"`.** No real state in `LocalBackend` is pending, and a
   vocabulary member no backend ever emits is one nobody notices going wrong.
3. **Be a second implementation.** A protocol proven against one backend is a
   protocol its one implementation defined.

It reads the same little program language the test child does, out of
`PlannedDispatch.prompt`, so a scenario reads identically for both cases.
Durability is a registry on the backend instance rather than a file: reopening
by name binds the same session, which is the property the resume scenario
actually exercises.
"""

from __future__ import annotations

from dataclasses import replace

from subagents_io.backend import (
    BackendError,
    Escalation,
    Heartbeat,
    UnknownWorker,
    UnsupportedMode,
    WorkerDone,
    WorkerEvent,
    WorkerQuestion,
    WorkerRecord,
    WorktreeNotProvisioned,
)
from subagents_io.dispatch import PlannedDispatch

TERMINAL_STATES = frozenset({"succeeded", "failed", "stopped"})


class _WorkerState:
    def __init__(self, key: str, handle: str, program: list[str]) -> None:
        self.record = WorkerRecord(key=key, handle=handle, state="pending", last_heartbeat_at=None, detail=None)
        self.program = program
        self.step = 0
        self.lines: list[WorkerEvent] = []  # every event ever produced, in order
        self.acked_through = 0
        self.blocked_on: str | None = None  # a reply_token this worker is waiting for


class FakeSession:
    """Durable by registry: the backend hands the same object back for the same name."""

    def __init__(self, name: str, backend: FakeBackend) -> None:
        self.name = name
        self._backend = backend
        self._workers: dict[str, _WorkerState] = {}
        self._cursors: dict[str, int] = {}
        self.skipped_lines = 0

    # -- protocol ---------------------------------------------------------

    def launch(self, dispatch: PlannedDispatch) -> WorkerRecord:
        if dispatch.mode not in self._backend.supported_modes:
            raise UnsupportedMode(f"{dispatch.key}: mode {dispatch.mode!r} is not in {self._backend.supported_modes}")
        if dispatch.worktree.path is None:
            raise WorktreeNotProvisioned(f"{dispatch.key}: worktree.path is None")
        if dispatch.key in self._workers:
            raise BackendError(f"{dispatch.key}: already has a worker in this session")
        state = _WorkerState(dispatch.key, f"fake-{len(self._workers)}", dispatch.prompt.split())
        self._workers[dispatch.key] = state
        self._cursors[dispatch.key] = 0
        return state.record

    def workers(self) -> list[WorkerRecord]:
        return [w.record for w in self._workers.values()]

    def describe(self, key: str) -> WorkerRecord | None:
        state = self._workers.get(key)
        return None if state is None else state.record

    def wait(self, *, timeout_s: float) -> list[WorkerEvent]:
        del timeout_s  # nothing here blocks; a step either runs now or never
        for state in self._workers.values():
            self._advance(state)
        out: list[WorkerEvent] = []
        for key, state in self._workers.items():
            cursor = self._cursors.get(key, 0)
            out.extend(state.lines[cursor:])
            self._cursors[key] = len(state.lines)
        return out

    def ack(self, event: WorkerEvent) -> None:
        if event.delivery_id is None:
            return
        state = self._workers.get(event.key)
        if state is None:
            raise UnknownWorker(event.key)
        line_no = int(event.delivery_id.partition(":")[2])
        state.acked_through = max(state.acked_through, line_no + 1)

    def reply(self, reply_token: str, answer: str) -> None:
        for state in self._workers.values():
            if state.blocked_on == reply_token:
                state.blocked_on = None
                state.record = replace(state.record, detail=answer)
                return
        raise UnknownWorker(reply_token)

    def stop(self, key: str) -> None:
        state = self._workers.get(key)
        if state is None:
            raise UnknownWorker(key)
        state.record = replace(state.record, state="stopped", detail="stopped by the coordinator")
        state.blocked_on = None

    def close(self) -> None:
        """Drop the read cursors, keep the durable state — same as reopening."""
        self._cursors = {key: state.acked_through for key, state in self._workers.items()}

    # -- internals --------------------------------------------------------

    def _advance(self, state: _WorkerState) -> None:
        while state.blocked_on is None and state.step < len(state.program):
            if state.record.state in TERMINAL_STATES:
                return
            instruction = state.program[state.step]
            state.step += 1
            self._run(state, instruction)

    def _run(self, state: _WorkerState, instruction: str) -> None:
        verb, _, arg = instruction.partition(":")
        delivery_id = f"{state.record.handle}:{len(state.lines)}"
        base = {"key": state.record.key, "handle": state.record.handle, "delivery_id": delivery_id}
        if verb == "heartbeat":
            state.lines.append(Heartbeat(**base, phase=arg or None))
            state.record = replace(state.record, state="running", last_heartbeat_at="fake-clock", detail=arg or None)
        elif verb == "escalate":
            state.lines.append(Escalation(**base, subject=arg, body=f"{state.record.key} is blocked"))
            state.record = replace(state.record, state="running")
        elif verb == "question":
            state.lines.append(WorkerQuestion(**base, question=arg, options=("a", "b"), reply_token=delivery_id))
            state.record = replace(state.record, state="running")
            state.blocked_on = delivery_id
        elif verb == "done":
            state.lines.append(
                WorkerDone(
                    **base,
                    outcome=arg,
                    summary=f"{state.record.key} finished",
                    files_modified=("a.py",),
                    report_path=None,
                )
            )
            state.record = replace(state.record, state=arg)
        # every other instruction ("say", "sleep", "garbage", "unknown",
        # "exit") is a real-process concern with no in-memory analogue, and is
        # deliberately a no-op here rather than a fake approximation.


class FakeBackend:
    """Narrowed to `autonomous` on purpose — see the module docstring."""

    def __init__(self) -> None:
        self.name = "fake"
        self.supported_modes = frozenset({"autonomous"})
        self.provisions_worktrees = False
        self._sessions: dict[str, FakeSession] = {}

    def open_session(self, name: str) -> FakeSession:
        session = self._sessions.get(name)
        if session is None:
            session = FakeSession(name, self)
            self._sessions[name] = session
        session.close()  # reopening resets the read cursor to acked_through
        return session
