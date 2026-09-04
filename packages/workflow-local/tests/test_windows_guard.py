"""D-002's Windows refusal — proven against the real platform where the host is Windows.

No `pytestmark` here: these tests are the one place the guard they exist to
verify is actually exercised on native Windows, so they must never be skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from subagents_io.backend import BackendError
from workflow_local.backend import LocalSession
from workflow_local.ledger import LEDGER_NAME, LedgerEntry, read_ledger, write_ledger

CHILD = Path(__file__).resolve().parent / "child.py"


def make_backend_kwargs(tmp_path):
    return {
        "argv_for": lambda d: [sys.executable, str(CHILD), *d.prompt.split()],
        "poll_interval_s": 0.02,
    }


@pytest.fixture
def windows_platform(monkeypatch):
    """Windows — natively where the host is Windows, simulated where it is not.

    On a Windows host nothing is patched, so D-002's guard is exercised against
    the platform it exists to govern rather than against a `monkeypatch` of it.
    """
    from workflow_local import backend as backend_module

    if backend_module.sys.platform != "win32":
        monkeypatch.setattr(backend_module.sys, "platform", "win32")


def test_local_backend_refuses_windows(tmp_path, windows_platform):
    # `_pid_alive`'s `os.kill(pid, 0)` probe maps to `TerminateProcess` on
    # Windows, so the backend must refuse before anything destructive runs.
    from workflow_local.backend import LocalBackend

    with pytest.raises(BackendError):
        LocalBackend(tmp_path / "root", **make_backend_kwargs(tmp_path))


def test_windows_refusal_names_the_data_loss_and_points_at_workflow_orca(tmp_path, windows_platform):
    from workflow_local.backend import LocalBackend

    with pytest.raises(BackendError, match="workflow-orca") as excinfo:
        LocalBackend(tmp_path / "root", **make_backend_kwargs(tmp_path))
    assert "kill" in str(excinfo.value).lower() or "terminat" in str(excinfo.value).lower()


def test_local_session_refuses_windows(tmp_path, windows_platform):
    # LocalSession is the exported constructor in front of `_probe_on_open`;
    # guarding only the backend leaves the destructive path reachable.
    # Constructed directly, without a LocalBackend first, so this runs on
    # Windows too — where building a LocalBackend would itself already raise.
    with pytest.raises(BackendError):
        LocalSession(
            name="s",
            directory=tmp_path / "s",
            argv_for=lambda d: [sys.executable],
            env=None,
            poll_interval_s=0.02,
            stop_grace_s=0.5,
        )


def test_ledger_is_readable_on_a_windows_host(tmp_path, windows_platform):
    # Pure json/pathlib; inspecting a POSIX-written ledger on Windows is a
    # real use and a gain, not a side effect the guard should block.
    path = tmp_path / "s" / LEDGER_NAME
    write_ledger(
        path,
        {
            "gw-plan-slug-00000000": LedgerEntry(
                key="gw-plan-slug-00000000",
                handle="h",
                pid=None,
                state="succeeded",
                argv=("python", "child.py", "done:succeeded"),
                cwd=str(tmp_path),
                started_at="2026-01-01T00:00:00+00:00",
            )
        },
    )
    assert read_ledger(path)["gw-plan-slug-00000000"].state == "succeeded"
