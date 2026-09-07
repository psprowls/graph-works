"""The sibling's conformance scenarios, third implementation.

Imported, never copied. `workflow-local/tests/conformance.py` and
`test_conformance.py` are plain sibling modules on this package's `pythonpath`
(see `pyproject.toml`), and every scenario body below (`test_launch_enumerate_
wait_done`, `test_question_reply_done`, `test_stop_settles_a_running_worker`,
the resume pair, ...) comes in through the wildcard import further down —
nothing here re-implements one. A scenario added to the sibling runs here with
no edit, which is the whole claim the phrase "conformance suite" makes, and
this package is the first thing that tests it, since a second implementation
written against a shipped Protocol is exactly where a wrong abstraction shows.

What IS this package's own: `ORCA_CASE`, wiring `OrcaBackend` behind
`fake_orca_cli.FakeOrcaCLI` into the `BackendCase` shape the sibling's
scenarios expect, and the `case` / `session` fixtures that hand it to them —
the same kind of backend-specific plumbing `conformance._local` /
`conformance._fake` already are for the other two backends. `case` is
deliberately unparametrized (a single fixed `ORCA_CASE`): this suite exists to
answer whether `OrcaBackend` passes, not to re-run `fake`/`local`, which the
sibling's own suite already does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conformance import BackendCase  # the sibling's harness, not copied
from fake_orca_cli import FakeOrcaCLI
from subagents_io.backend import DispatchBackend
from subagents_io.dispatch import DISPATCH_MODES
from workflow_orca import OrcaBackend


def _make_orca(root: Path) -> DispatchBackend:
    del root  # Orca has no on-disk root of its own; the Run is the durable store.
    return OrcaBackend(run=FakeOrcaCLI())


ORCA_CASE = BackendCase(
    id="orca",
    make=_make_orca,
    # `FakeOrcaCLI` is in-memory: no OS process sits behind a worker, so a
    # future kill-and-resume scenario gated on `case.real_processes` self-skips
    # here exactly as it would for the sibling's own `fake` case.
    real_processes=False,
    # `OrcaBackend.supported_modes == DISPATCH_MODES` (all three, genuinely),
    # so `test_launch_refuses_an_unsupported_mode` self-skips via
    # `case.narrows_modes` — that is the shared suite's own stated skip path,
    # not a route-table gap this package failed to fill.
    narrows_modes=False,
    modes=DISPATCH_MODES,
    # `OrcaBackend` is D-002's designated native-Windows dispatch backend, so
    # it is the opposite of POSIX-only; the sibling's `local` case sets this
    # True because `LocalBackend` refuses to construct there. Nothing to skip.
    posix_only=False,
)


@pytest.fixture
def case() -> BackendCase:
    return ORCA_CASE


@pytest.fixture
def session(case: BackendCase, tmp_path: Path):
    backend = case.make(tmp_path / "root")
    opened = backend.open_session("conformance")
    yield opened
    opened.close()


# ruff: noqa: E402, F403 -- the wildcard import must follow the `case` /
# `session` fixtures it depends on, and everything it pulls in (test
# functions, `drain`, `saw_done`, ...) is used by pytest's own collection, not
# by name from this module.
from test_conformance import *  # imported, not copied


def test_provisions_worktrees_is_declared(case: BackendCase, tmp_path: Path) -> None:
    # Overrides the sibling's copy (test_conformance.py), which hardcodes
    # `is False` with a comment noting every case *it* knows obtains no
    # worktree of its own, and that a backend which does is what "inherits
    # this scenario by adding a third BackendCase" — i.e. this file. The
    # shared suite has not generalized that assertion to read the expectation
    # off `case` yet, so importing it verbatim would report a real capability
    # (`OrcaBackend.provisions_worktrees is True`, via `worker-start
    # --worktree new-child|new-top-level`) as a failure. This is a gap in the
    # shared test's generality, not in `OrcaBackend` or in the Protocol
    # (`provisions_worktrees` already exists on `DispatchBackend` for exactly
    # this), so it is fixed locally rather than carried to the README as
    # Protocol friction.
    backend = case.make(tmp_path / "root")
    assert backend.provisions_worktrees is True
