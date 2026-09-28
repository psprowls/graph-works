"""The coordinator's one wait (`gw work wait`): return only for a real event.

Decisions only. Every Orca call goes through `OrcaPort`, whose argv lives in
`workflow_orca.port`. Heartbeats never wake the caller. A delivery holding nothing
real is acked here and the wait resumes for the remaining time. A delivery holding
anything real is returned unacked, and the caller acks it on its next wait (`ack=`).
The verb observes liveness only on timeout, never nudges, and never reads the clock:
`WaitClock` is injected. Wall-minus-monotonic elapsed time is the time the host
slept, because both macOS and Linux monotonic clocks exclude suspend.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, Literal, TypeVar

from subagents_io.backend import BackendError

from graph_works_core.orchestrate.orca_port import (
    OrcaDelivery,
    OrcaMessage,
    OrcaPendingQuestion,
    OrcaPort,
    OrcaTask,
    OrcaWorker,
)

REAL_TYPES: tuple[str, ...] = ("worker_done", "escalation", "question")
SLEEP_GAP_FLOOR_S = 60
_FENCED = "consumer_fenced"
_SETTLED = frozenset({"succeeded", "failed", "stopped"})
_REASON = "duplicate-completion"
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class WaitClock:
    wall: Callable[[], datetime]
    monotonic: Callable[[], float]


@dataclass(frozen=True, slots=True)
class Absorbed:
    message_id: str
    type: str
    dispatch_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class WaitResult:
    status: Literal["event", "timeout"]
    run_id: str
    delivery_id: str | None
    messages: tuple[OrcaMessage, ...]
    absorbed: tuple[Absorbed, ...]
    self_acked: int
    rebound: bool
    sleep_gap_s: int | None
    waited_s: int
    liveness: list[dict[str, Any]] | None = None
    pending_questions: tuple[OrcaPendingQuestion, ...] | None = None
    warnings: tuple[str, ...] = ()


class WaitFailed(RuntimeError):
    """An Orca failure the wait could not recover from; nothing unprocessed was acked."""

    def __init__(self, run_id: str, code: str | None, detail: str) -> None:
        self.run_id = run_id
        self.code = code
        super().__init__(detail)


class _Fence:
    """Retry one port call once after `run-use` when Orca reports `consumer_fenced`."""

    def __init__(self, port: OrcaPort, run_id: str) -> None:
        self.port = port
        self.run_id = run_id
        self.rebound = False

    def call(self, fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except BackendError as exc:
            if getattr(exc, "code", None) != _FENCED:
                raise
        self.port.run_use(self.run_id)
        self.rebound = True
        return fn()


def _duplicate_completion(message: OrcaMessage, workers: Sequence[OrcaWorker], tasks: Sequence[OrcaTask]) -> str | None:
    """Return the dispatch ID only for a released, single-attempt recorded completion.

    Orca's Task result has no dispatch ID. The reporting terminal binds it to
    the sole worker attempt; missing or malformed proof keeps the message.
    """
    payload = message["payload"]
    if message["type"] != "worker_done" or payload is None:
        return None
    dispatch_id = payload.get("dispatchId")
    if not isinstance(dispatch_id, str) or not dispatch_id:
        return None
    rows = [worker for worker in workers if worker.get("dispatch_id") == dispatch_id]
    if len(rows) != 1:
        return None
    worker = rows[0]
    task_id = worker.get("task_id")
    terminal = worker.get("terminal")
    if not isinstance(task_id, str) or not task_id or not isinstance(terminal, str) or not terminal:
        return None
    if "taskId" in payload and payload["taskId"] != task_id:
        return None
    state = worker.get("state")
    if not isinstance(state, str) or state not in _SETTLED or worker.get("release_state") != "released":
        return None
    if sum(1 for other in workers if other.get("task_id") == task_id) != 1:
        return None
    matches = [task for task in tasks if task.get("id") == task_id]
    if len(matches) != 1:
        return None
    task = matches[0]
    result = task.get("result")
    if task.get("status") != "completed" or not isinstance(result, Mapping):
        return None
    if result.get("provenance") != "worker_report" or result.get("completedBy") != terminal:
        return None
    return dispatch_id


def _absorb(messages: Sequence[OrcaMessage], *, run_id: str, fence: _Fence) -> tuple[list[OrcaMessage], list[Absorbed]]:
    """Split duplicate completions off a batch. A failed read keeps everything."""
    if not any(message["type"] == "worker_done" and message["payload"] is not None for message in messages):
        return list(messages), []
    try:
        workers = fence.call(lambda: fence.port.worker_list(run_id))
        tasks = fence.call(lambda: fence.port.task_list(run_id))
    except BackendError:
        return list(messages), []
    kept: list[OrcaMessage] = []
    absorbed: list[Absorbed] = []
    for message in messages:
        dispatch_id = _duplicate_completion(message, workers, tasks)
        if dispatch_id is None:
            kept.append(message)
        else:
            absorbed.append(Absorbed(message["id"], message["type"], dispatch_id, _REASON))
    return kept, absorbed


def _pending_questions(port: OrcaPort, run_id: str) -> tuple[tuple[OrcaPendingQuestion, ...] | None, tuple[str, ...]]:
    """Re-derive questions on every return; failed reads are unknown, never empty."""
    try:
        read = port.pending_questions(run_id)
    except BackendError as exc:
        return None, (f"pending questions unavailable: {exc}",)
    warnings = list(read["warnings"])
    if read["truncated"]:
        warnings.append("pending questions may be incomplete: an Orca inbox read reached its limit")
    return tuple(read["questions"]), tuple(warnings)


def run_wait(port: OrcaPort, run_id: str, *, ack: str | None, timeout_s: float, clock: WaitClock) -> WaitResult:
    if timeout_s < 0:
        raise ValueError(f"timeout_s must be non-negative, got {timeout_s!r}")
    fence = _Fence(port, run_id)
    wall0, mono0 = clock.wall(), clock.monotonic()
    deadline = mono0 + timeout_s
    pending_ack = ack
    absorbed: list[Absorbed] = []
    self_acked = 0
    status: Literal["event", "timeout"] = "timeout"
    delivery_id: str | None = None
    messages: tuple[OrcaMessage, ...] = ()
    try:
        # Zero is a pure pending-question read, even when the caller supplied an ack.
        while timeout_s > 0:
            sent_ack = pending_ack

            def check_with_remaining_budget(ack_to_send: str | None = sent_ack) -> OrcaDelivery | None:
                remaining_ms = int((deadline - clock.monotonic()) * 1000)
                if remaining_ms <= 0:
                    if ack_to_send is None:
                        return None
                    remaining_ms = 1
                return port.check_wait(run_id, types=",".join(REAL_TYPES), timeout_ms=remaining_ms, ack=ack_to_send)

            delivery = fence.call(check_with_remaining_budget)
            if delivery is None:
                break
            pending_ack = None
            batch = delivery["messages"]
            if delivery["delivery_id"] is None and not batch:
                break
            real, dropped = _absorb([m for m in batch if m["type"] != "heartbeat"], run_id=run_id, fence=fence)
            absorbed.extend(dropped)
            if real:
                status, delivery_id, messages = "event", delivery["delivery_id"], tuple(real)
                break
            done = delivery["delivery_id"]
            if done is not None:
                fence.call(partial(port.check_ack, run_id, done))
                self_acked += 1
    except BackendError as exc:
        raise WaitFailed(run_id, getattr(exc, "code", None), str(exc)) from exc
    mono_elapsed = clock.monotonic() - mono0
    now = clock.wall()
    gap = int((now - wall0).total_seconds() - mono_elapsed)
    try:
        liveness = port.liveness(run_id, now=now) if status == "timeout" and timeout_s > 0 else None
    except BackendError as exc:
        raise WaitFailed(run_id, getattr(exc, "code", None), str(exc)) from exc
    pending_questions, warnings = _pending_questions(port, run_id)
    return WaitResult(
        status=status,
        run_id=run_id,
        delivery_id=delivery_id,
        messages=messages,
        absorbed=tuple(absorbed),
        self_acked=self_acked,
        rebound=fence.rebound,
        sleep_gap_s=gap if gap >= SLEEP_GAP_FLOOR_S else None,
        waited_s=int(mono_elapsed),
        liveness=liveness,
        pending_questions=pending_questions,
        warnings=warnings,
    )
