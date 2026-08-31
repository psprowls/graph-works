"""One set of scenarios, two backends — which is what makes this a conformance suite.

`workflow-orca` inherits this by adding a third `BackendCase`. Until it exists,
the file lives in this package's tests and is reachable from a sibling package
by adding `packages/workflow-local/tests` to that package's pytest `pythonpath`.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fake import FakeBackend
from subagents_io.backend import DispatchBackend, WorkerDone, WorkerEvent
from subagents_io.dispatch import DISPATCH_MODES, PlannedDispatch, WorktreeAction
from workflow_local.backend import LocalBackend

CHILD = Path(__file__).resolve().parent / "child.py"


@dataclass(frozen=True)
class BackendCase:
    """One backend under test, plus the two facts the scenarios branch on."""

    id: str
    make: Callable[[Path], DispatchBackend]
    #: True when this backend runs real OS processes — the kill-and-resume
    #: scenario has no meaning without one.
    real_processes: bool
    #: True when `supported_modes` is narrower than `DISPATCH_MODES`, which is
    #: what makes `UnsupportedMode` reachable.
    narrows_modes: bool
    #: This backend's `supported_modes`, so a scenario can pick an unsupported
    #: one without reaching into a session's privates.
    modes: frozenset[str]


def _local(root: Path) -> DispatchBackend:
    return LocalBackend(
        root,
        argv_for=lambda d: [sys.executable, str(CHILD), *d.prompt.split()],
        poll_interval_s=0.02,
        stop_grace_s=0.5,
    )


def _fake(root: Path) -> DispatchBackend:
    del root  # in-memory; the registry on the instance is its durability
    return FakeBackend()


CASES = (
    BackendCase(id="fake", make=_fake, real_processes=False, narrows_modes=True, modes=frozenset({"autonomous"})),
    BackendCase(id="local", make=_local, real_processes=True, narrows_modes=False, modes=DISPATCH_MODES),
)


def make_dispatch(
    *program: str,
    worktree_path: str | None,
    key: str = "gw-plan-slug-00000000",
    slug: str = "work/feature-slug",
    phase: str = "plan",
    mode: str = "autonomous",
) -> PlannedDispatch:
    """A dispatch whose `prompt` IS the program both backends interpret."""
    return PlannedDispatch(
        key=key,
        slug=slug,
        phase=phase,
        kind="feature",
        effort="medium",
        skill="writing-plans",
        mode=mode,
        model=None,
        reasoning_effort=None,
        worktree=WorktreeAction(action="reuse", path=worktree_path, branch="b", base_branch=None, exists=True),
        merge_target="main",
        prompt=" ".join(program),
    )


def drain(session, *, until, timeout_s=20.0, ack=True) -> list[WorkerEvent]:
    """Pump `wait()` until `until(events)` holds or the clock runs out."""
    collected: list[WorkerEvent] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for event in session.wait(timeout_s=0.2):
            collected.append(event)
            if ack:
                session.ack(event)
        if until(collected):
            return collected
    return collected


def saw_done(events) -> bool:
    return any(isinstance(e, WorkerDone) for e in events)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
