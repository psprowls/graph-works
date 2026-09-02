"""Locked, atomic appends to a bundle's root `log.md`.

    from okf_ext import logs
    landed = logs.append_entry(root, "**note** wave 2 kickoff", on=today)

Lifted verbatim out of `work_tracker_okf.compose`, where these three functions
already served two call sites -- the work lane's own log line and the filing
plan's compare-and-swap. A third consumer (`gw util log`, through
`graph_works_core.util`) would have made a third copy of the same `fcntl` +
`mkstemp` dance, so it moves here: a beyond-spec write over *any* OKF bundle,
which is what tier 2 is for.

**All three names are public.** `compose.py`'s compare-and-swap needs
`locked_log` and `atomic_replace` directly, and leaving private copies behind
for it is exactly the duplication this move exists to prevent.

`append_entry` reports refusals by returning `None` rather than by raising:
nothing on this band's content path raises, and an out-of-order log is a
human's problem, not a reason to lose a write that already happened.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import date
from pathlib import Path

from okf_io import append_log_entry, load

from okf_ext.locking import locked

#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces it.
__all__ = [
    "append_entry",
    "atomic_replace",
    "locked_log",
]


def _log_lock_path(log_path: Path) -> Path:
    """A stable lock beside the bundle, outside the bundle's indexed bytes."""
    bundle_root = log_path.parent
    return bundle_root.parent / f".{bundle_root.name}.{log_path.name}.lock"


@contextmanager
def locked_log(log_path: Path) -> Iterator[None]:
    """Serialize compare-and-replace cycles even though replacement changes inode."""
    with locked(_log_lock_path(log_path)):
        yield


def atomic_replace(path: Path, data: bytes) -> None:
    """Replace *path* atomically.

    On POSIX, the existing file's full mode is preserved. On Windows, only
    the read-only bit is representable: a read-only destination is cleared
    just before the replace so the write is never silently refused, then the
    captured mode -- carried onto the temp file -- restores it. A failed
    replace never leaves a temp file behind, and the caller sees the
    exception the replace raised, not one from cleanup.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    cleared_destination_readonly = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.chmod(mode)
        if sys.platform == "win32" and not mode & stat.S_IWRITE:
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWRITE)
            cleared_destination_readonly = True
        temporary.replace(path)
    except BaseException:
        _cleanup_after_failed_replace(path, temporary, cleared_destination_readonly)
        raise


def _cleanup_after_failed_replace(path: Path, temporary: Path, cleared_destination_readonly: bool) -> None:
    """Best-effort: restore a cleared read-only bit and remove the temp file.

    Runs inside `atomic_replace`'s `except BaseException`, so any `OSError`
    raised here must be suppressed -- it would otherwise replace the
    original exception as the one the caller sees.
    """
    if cleared_destination_readonly:
        with suppress(OSError):
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~stat.S_IWRITE)
    with suppress(OSError):
        if sys.platform == "win32":
            temporary.chmod(stat.S_IMODE(temporary.stat().st_mode) | stat.S_IWRITE)
        temporary.unlink(missing_ok=True)


def append_entry(root: Path, entry: str, *, on: date) -> str | None:
    """Append one `log.md` line to the bundle at *root*. Returns what landed.

    `None` when the bundle carries no root `log.md`, when it will not parse, or
    when the append is refused -- an out-of-order log whose dated sections
    `okf_io.append_log_entry` will not scan safely, or an `OSError` on the
    replace. Refusals are reported by returning nothing rather than by raising.

    The read-plan-replace cycle runs under `locked_log`, and the `is_file()`
    check is repeated inside it: another process can replace the whole bundle
    between the first check and the read.
    """
    log_path = root / "log.md"
    if not log_path.is_file():
        return None
    try:
        with locked_log(log_path):
            if not log_path.is_file():
                return None
            log_document = load(log_path)
            if log_document.parse_error is not None:
                return None
            planned = append_log_entry(log_document, entry, on=on, dry_run=True)
            atomic_replace(log_path, planned.after.encode("utf-8"))
    except (ValueError, OSError):
        return None
    return entry
