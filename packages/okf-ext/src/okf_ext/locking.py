"""One portable exclusive file lock for every process-serializing site.

**Shared layer, not a capability.** `work_tracker_okf.decisions`,
`graph_works_core.work.commands`, and `okf_ext.logs` each took the same five
lines of `fcntl.flock` around a dedicated lock file, and the independence
contract forbids one of those sites reaching into another for it — the same
reasoning `okf_ext.writing` documents for the write engine. `okf_ext` is the
lowest package all three sites can reach.

Imports stdlib only, and imports its platform-specific primitive (`fcntl` or
`msvcrt`) only inside the branch that uses it, never at module scope --
that is what lets this module import cleanly on native Windows and fail, if
at all, only when a lock is actually taken.

`locked()` always locks a *dedicated* lock file beside the data it
serializes, never the data itself: the ledger may not exist yet, a replace
changes inode, and a bundle can be swapped wholesale between checks. That
shared property is also what makes the Windows branch a straight
substitution rather than a semantics negotiation -- `fcntl.flock` is
whole-file advisory and `msvcrt.locking` is a byte-range mandatory lock, and
the two coincide exactly when nothing else ever reads or writes the locked
file.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["locked", "primitive_for"]


def primitive_for(platform_name: str) -> str:
    """The locking primitive `locked()` uses on *platform_name*.

    Pure string logic -- imports nothing -- so the platform report can ask
    this question for a foreign platform without importing that platform's
    module.
    """
    return "msvcrt.locking" if platform_name == "win32" else "fcntl.flock"


@contextmanager
def locked(path: Path, *, platform_name: str = sys.platform) -> Iterator[None]:
    """Take an exclusive lock on *path* for the duration of the `with` block.

    Creates `path.parent` (`parents=True, exist_ok=True`) and `path` itself
    on demand, and never unlinks it afterward -- unlinking races the next
    writer's open.

    On `win32`, `msvcrt.locking(LK_LOCK, ...)` retries once per second for
    ten attempts and then raises `OSError`; that failure is re-raised naming
    *path* so a caller sees what happened and that a retry is possible,
    rather than a bare `OSError` after ten silent seconds. POSIX's
    `fcntl.flock(LOCK_EX)` blocks indefinitely and is unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if platform_name == "win32":
            # typeshed's msvcrt stub is itself gated on `sys.platform ==
            # "win32"`, so mypy running on any other host sees a moduleless
            # `msvcrt` here regardless of this branch's runtime guard.
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            except OSError as exc:
                raise OSError(
                    f"could not acquire the lock at {path}: another process holds it; the operation can be retried"
                ) from exc
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
