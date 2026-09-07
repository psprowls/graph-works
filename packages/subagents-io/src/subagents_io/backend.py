"""The worker-dispatch seam: the protocol a coordinator drives, and the events it reads.

Band 1's other half of `dispatch.py`. That module says what a planner hands a
runner; this one says what a runner is. Both stay inert — frozen value types and
two Protocols, no implementation, no vendor.

The two frozensets here are owned for the reason `dispatch.py` gives for its
own: a worker's lifecycle state and the kinds of thing a worker can say are
*how to run a worker*, and there is no second owner to defer to. A work item's
`phase` belongs to `work-tracker-okf`; a worker's state belongs to nobody else.
So they are closed vocabularies, and each event kind is its own frozen
dataclass carrying a `kind` discriminator — which is what makes `match ev.kind`
exhaustive and an unhandled kind a `mypy --strict` error rather than a silently
dropped message.

The two-level shape — a backend opens a named, durable session, and a session
can enumerate every worker it ever launched — is not decoration. A coordinator
resumes by re-deriving its state from the backend, and vault state cannot tell
a still-running worker from a dead one. Enumeration is therefore a protocol
requirement, not a backend's optional extra, and `open_session` binds-or-creates
by name so a coordinator restart is a no-op rather than a second session.

Sync, deliberately. Every consumer is sync, and the real wait is one multiplexed
blocking call that returns whichever worker spoke first — async buys no
concurrency where it would matter, and would only push `asyncio.run()` into a
Typer command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from subagents_io.dispatch import PlannedDispatch

#: The legal values of `WorkerRecord.state`.
#:
#: `"unknown"` is not a synonym for `"failed"`. It is what a backend reports
#: when it holds a record but cannot presently corroborate the process behind
#: it — the resume case, where a recorded pid no longer answers. Collapsing it
#: into `"failed"` would make a recoverable worker unrecoverable.
WORKER_STATES: frozenset[str] = frozenset({"pending", "running", "succeeded", "failed", "stopped", "unknown"})

#: The legal values of the `kind` discriminator on a `WorkerEvent`.
EVENT_KINDS: frozenset[str] = frozenset({"worker_done", "question", "escalation", "heartbeat"})


class BackendError(RuntimeError):
    """Base for every mistake a caller can make against a dispatch backend."""


class UnsupportedMode(BackendError):
    """The dispatch's `mode` is outside this backend's `supported_modes`."""


class UnknownWorker(BackendError):
    """No worker with that key or reply token exists in this session."""


class WorktreeNotProvisioned(BackendError):
    """The dispatch's `worktree.path` is None; a backend does not create it."""


@dataclass(frozen=True)
class WorkerRecord:
    """What a backend knows about one worker it launched.

    `worktree_path` and `worktree_branch` are what the worker *actually* runs
    on, as the backend read it back after launching — not what a planner
    asked for. They are optional because a backend that provisions no
    worktree (`provisions_worktrees is False`) has nothing to report, and
    because a read-back that failed must say "not known" rather than invent a
    value. `None` therefore means exactly one thing: this backend did not
    learn it. Comparing either field against a plan is a caller's business;
    a backend records, it does not reconcile.
    """

    key: str  # PlannedDispatch.key — the worker's name, opaque to this layer
    handle: str  # backend-scoped worker id
    state: str  # one of WORKER_STATES
    last_heartbeat_at: str | None  # ISO-8601 on the backend's clock; None = never spoke
    detail: str | None  # the backend's own status string, for humans only
    worktree_path: str | None = None  # the checkout this worker runs in; None = not known
    worktree_branch: str | None = None  # the branch that checkout is on, short form; None = not known


@dataclass(frozen=True)
class WorkerEventBase:
    """The three fields a coordinator handles generically: attribute, ack, dedupe."""

    key: str
    handle: str
    delivery_id: str | None  # None = this backend has no at-least-once delivery to ack


@dataclass(frozen=True)
class WorkerDone(WorkerEventBase):
    """A worker settled. Terminal for its key."""

    outcome: str  # "succeeded" or "failed"
    summary: str
    files_modified: tuple[str, ...]
    report_path: str | None
    kind: Literal["worker_done"] = "worker_done"


@dataclass(frozen=True)
class WorkerQuestion(WorkerEventBase):
    """A worker is blocked on an answer. `reply_token` goes back via `reply()`."""

    question: str
    options: tuple[str, ...]
    reply_token: str
    kind: Literal["question"] = "question"


@dataclass(frozen=True)
class Escalation(WorkerEventBase):
    """A worker hit a blocker it cannot resolve. Not terminal on its own."""

    subject: str
    body: str
    kind: Literal["escalation"] = "escalation"


@dataclass(frozen=True)
class Heartbeat(WorkerEventBase):
    """A worker is alive. `phase` is free text for humans."""

    phase: str | None = None
    kind: Literal["heartbeat"] = "heartbeat"


WorkerEvent = WorkerDone | WorkerQuestion | Escalation | Heartbeat


@runtime_checkable
class DispatchSession(Protocol):
    """One durable, named run: everything launched under it, and everything it said.

    `workers()` returns every worker ever launched in the session, not just the
    live ones, because a settled key is exactly what a dedupe check needs to
    see. `wait()` returns `[]` on timeout rather than raising, so a
    coordinator's outer loop stays a plain `while`. `ack()` takes the event
    rather than a bare id, so a backend with no delivery concept no-ops on
    `delivery_id is None` without the caller branching.

    `launch()` refuses a key it has already launched in this session, rather
    than answering it from cache: silently returning the existing record
    would make a re-dispatch after a failure read as a success. Every backend
    raises `BackendError` for this case, proven identically across
    implementations by `workflow-local`'s shared conformance suite.
    """

    name: str

    def launch(self, dispatch: PlannedDispatch) -> WorkerRecord: ...
    def workers(self) -> list[WorkerRecord]: ...
    def describe(self, key: str) -> WorkerRecord | None: ...
    def wait(self, *, timeout_s: float) -> list[WorkerEvent]: ...
    def ack(self, event: WorkerEvent) -> None: ...
    def reply(self, reply_token: str, answer: str) -> None: ...
    def stop(self, key: str) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class DispatchBackend(Protocol):
    """A way of running workers. `open_session` binds or creates by name.

    `supported_modes` lets a coordinator refuse an `attend` dispatch before
    launching it, rather than discovering the gap when a human is expected and
    no channel exists; `launch()` raises `UnsupportedMode` for a dispatch
    outside the set.

    `provisions_worktrees` is `False` on every backend this package ships.
    It exists so a future backend that obtains its own worktrees (Orca's
    `worker-start --worktree new-child`, see the `workflow-orca` sibling) can
    declare that capability on the shared Protocol instead of a new method —
    a coordinator checks the flag before it plans, rather than discovering the
    gap when a `PlannedDispatch` that worked on one backend raises
    `WorktreeNotProvisioned` on another.
    """

    name: str
    supported_modes: frozenset[str]  # a subset of DISPATCH_MODES
    provisions_worktrees: bool  # True only for a backend that obtains its own worktrees

    def open_session(self, name: str) -> DispatchSession: ...
