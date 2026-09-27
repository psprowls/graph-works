"""The coordinator's one wait (`gw work wait`): return only for a real event.

Decisions only. Every Orca call goes through `OrcaPort`, whose argv lives in
`workflow_orca.port`. Heartbeats never wake the caller. A delivery holding nothing
real is acked here and the wait resumes for the remaining time. A delivery holding
anything real is returned unacked, and the caller acks it on its next wait (`ack=`).
The verb never nudges and never reads a worker, and it never reads the clock:
`WaitClock` is injected. Wall-minus-monotonic elapsed time is the time the host
slept, because both macOS and Linux monotonic clocks exclude suspend.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Literal, TypeVar

from subagents_io.backend import BackendError

from graph_works_core.orchestrate.orca_port import OrcaMessage, OrcaPort

REAL_TYPES: tuple[str, ...] = ("worker_done", "escalation", "question")
SLEEP_GAP_FLOOR_S = 60
_FENCED = "consumer_fenced"
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


def _absorb(messages: Sequence[OrcaMessage], *, run_id: str, fence: _Fence) -> tuple[list[OrcaMessage], list[Absorbed]]:
    """Split duplicate completions off a batch. Task 3 implements the rule."""
    return list(messages), []


def run_wait(port: OrcaPort, run_id: str, *, ack: str | None, timeout_s: float, clock: WaitClock) -> WaitResult:
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be positive, got {timeout_s!r}")
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
        while True:
            remaining_ms = int((deadline - clock.monotonic()) * 1000)
            if remaining_ms <= 0:
                if pending_ack is None:
                    break
                remaining_ms = 1
            sent_ack = pending_ack
            delivery = fence.call(
                partial(port.check_wait, run_id, types=",".join(REAL_TYPES), timeout_ms=remaining_ms, ack=sent_ack)
            )
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
    gap = int((clock.wall() - wall0).total_seconds() - mono_elapsed)
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
    )
