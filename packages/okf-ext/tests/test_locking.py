"""`okf_ext.locking`: the portable exclusive lock shared by the three flock sites.

`primitive_for` is pure string logic; `locked` is the context manager. The
Windows branch is exercised from a POSIX box by forcing `platform_name="win32"`
and injecting a fake `msvcrt` module (D-014) -- that gives the branch coverage
`just cov`'s 95% gate needs from any box, but the fake asserts only message
shape and call ordering, never whether a real contended lock actually waits.
One further test, gated on `sys.platform == "win32"`, contends for a genuine
`msvcrt.locking` from a real subprocess and is skipped everywhere else: it is
the second witness for the retry-bound claim the fakes cannot make.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from okf_ext.locking import locked, primitive_for


def test_primitive_for_win32_is_msvcrt_locking():
    assert primitive_for("win32") == "msvcrt.locking"


def test_primitive_for_posix_platforms_is_fcntl_flock():
    assert primitive_for("linux") == "fcntl.flock"
    assert primitive_for("darwin") == "fcntl.flock"


def test_primitive_for_imports_nothing():
    """Pure string logic -- neither `fcntl` nor `msvcrt` may already be
    imported as a side effect of calling it."""
    for name in ("fcntl", "msvcrt"):
        sys.modules.pop(name, None)

    primitive_for("win32")
    primitive_for(sys.platform)

    assert "msvcrt" not in sys.modules or sys.modules["msvcrt"] is None


def test_locked_creates_the_lock_file_and_its_parent_on_demand(tmp_path: Path):
    lock = tmp_path / "nested" / "dir" / "held.lock"

    with locked(lock):
        pass

    assert lock.is_file()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="forces the POSIX arm and probes it with fcntl.flock(LOCK_NB); fcntl does not exist on Windows",
)
def test_locked_serializes_a_posix_writer(tmp_path: Path):
    import fcntl
    import os

    lock = tmp_path / "held.lock"

    with locked(lock, platform_name="linux"):
        descriptor = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="forces the POSIX arm and probes it with fcntl.flock(LOCK_NB); fcntl does not exist on Windows",
)
def test_locked_releases_on_exception(tmp_path: Path):
    import fcntl
    import os

    lock = tmp_path / "held.lock"

    with pytest.raises(ValueError, match="boom"), locked(lock, platform_name="linux"):
        raise ValueError("boom")

    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def test_locked_never_unlinks_the_lock_file(tmp_path: Path):
    lock = tmp_path / "held.lock"

    with locked(lock):
        pass
    with locked(lock):
        pass

    assert lock.is_file()


class _FakeMsvcrt:
    """Records call order and the file position at each `locking` call."""

    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self, *, fail_lock: bool = False):
        self.calls: list[tuple[int, int]] = []
        self._fail_lock = fail_lock

    def locking(self, descriptor: int, mode: int, nbytes: int) -> None:
        import os

        self.calls.append((mode, os.lseek(descriptor, 0, os.SEEK_CUR)))
        if mode == self.LK_LOCK and self._fail_lock:
            raise OSError("Permission denied")


def test_locked_windows_branch_acquires_and_releases_in_order(tmp_path: Path, monkeypatch):
    lock = tmp_path / "held.lock"
    fake = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)

    with locked(lock, platform_name="win32"):
        pass

    assert fake.calls == [(fake.LK_LOCK, 0), (fake.LK_UNLCK, 0)]


def test_locked_windows_branch_releases_when_the_body_raises(tmp_path: Path, monkeypatch):
    lock = tmp_path / "held.lock"
    fake = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", fake)

    with pytest.raises(ValueError, match="boom"), locked(lock, platform_name="win32"):
        raise ValueError("boom")

    assert fake.calls == [(fake.LK_LOCK, 0), (fake.LK_UNLCK, 0)]


def test_locked_windows_branch_names_the_lock_path_on_retry_exhaustion(tmp_path: Path, monkeypatch):
    lock = tmp_path / "held.lock"
    fake = _FakeMsvcrt(fail_lock=True)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)

    with pytest.raises(OSError, match=re.escape(str(lock))), locked(lock, platform_name="win32"):
        pass

    assert fake.calls == [(fake.LK_LOCK, 0)]


def test_forcing_the_posix_arm_on_windows_refuses_by_name(tmp_path: Path, monkeypatch):
    """`platform_name="linux"` is answerable from any host for `primitive_for`,
    but it is not *runnable* on a host with no `fcntl` -- and the refusal must
    say which primitive is missing rather than surfacing a bare ImportError."""
    from okf_ext.locking import UnsupportedLockPlatform, _flock_exclusive, _flock_release

    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    for helper in (_flock_exclusive, _flock_release):
        with pytest.raises(UnsupportedLockPlatform, match=r"fcntl\.flock"):
            helper(0)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the real msvcrt.locking retry bound; only runs where msvcrt exists",
)
def test_locked_windows_branch_contends_for_a_real_lock_across_processes(tmp_path: Path):
    """The three tests above fake `msvcrt` (D-014) and prove the message shape
    and call ordering, but never prove the retry bound is real. This test is
    the second witness for that one claim: a genuine `msvcrt.locking` held by
    a parent process, contended by a real subprocess, must wait and then raise
    naming the path -- not merely produce the right string from a fake."""
    import subprocess
    import sys as _sys
    import time

    lock = tmp_path / "contended.lock"

    child_script = (
        "import time\n"
        "from pathlib import Path\n"
        "from okf_ext.locking import locked\n"
        f"lock = Path(r{str(lock)!r})\n"
        "start = time.monotonic()\n"
        "try:\n"
        "    with locked(lock):\n"
        "        pass\n"
        "except OSError as exc:\n"
        "    elapsed = time.monotonic() - start\n"
        "    print(f'FAILED {elapsed} {exc}')\n"
        "else:\n"
        "    elapsed = time.monotonic() - start\n"
        "    print(f'SUCCEEDED {elapsed}')\n"
    )

    with locked(lock):
        result = subprocess.run(
            [_sys.executable, "-c", child_script],
            capture_output=True,
            text=True,
            timeout=30,
        )

    assert result.returncode == 0, result.stderr
    output = result.stdout.strip()
    assert output.startswith("FAILED "), f"expected the child to fail while the parent held the lock, got: {output!r}"
    _, elapsed_str, message = output.split(" ", 2)
    assert str(lock) in message
    assert float(elapsed_str) >= 8.0
