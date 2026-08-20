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

import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from okf_io import append_log_entry, load

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
    lock_path = _log_lock_path(log_path)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_replace(path: Path, data: bytes) -> None:
    """Replace *path* atomically without changing its existing file mode."""
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.chmod(mode)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
