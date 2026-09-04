"""Where this suite records that its subject is POSIX-only (D-002)."""

from __future__ import annotations

import sys

import pytest

ON_WINDOWS = sys.platform == "win32"

POSIX_ONLY_REASON = (
    "workflow-local refuses to construct on Windows (D-002): os.kill(pid, 0) maps to "
    "TerminateProcess. workflow-orca is the Windows dispatch backend; the refusal itself "
    "is covered by test_windows_guard.py, which runs on every host."
)

#: Applied to every test that needs a constructible LocalBackend/LocalSession.
POSIX_ONLY = pytest.mark.skipif(ON_WINDOWS, reason=POSIX_ONLY_REASON)
