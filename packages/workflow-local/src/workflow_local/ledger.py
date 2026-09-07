"""The durable half of a `LocalSession`: one JSON file, one entry per key.

This file is what makes `workers()` correct across a coordinator restart with
no live process at all, and it is why the dedupe check is a backend guarantee
rather than coordinator bookkeeping that a crash discards.

`acked_through` is the field that carries the redelivery rule: it counts the
leading lines of that worker's `events.jsonl` the coordinator has acked, so
reopening a session replays every unacked event and no acked one. A byte offset
would say where reading stopped, which is a different and less useful fact — it
cannot express "replay the tail I never confirmed".

The write is atomic because the alternative is a truncated ledger, and a
truncated ledger reads as "these keys were never dispatched" — which relaunches
work that is already running.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

#: The ledger's filename inside `<root>/<session>/`.
LEDGER_NAME = "ledger.json"


@dataclass(frozen=True)
class LedgerEntry:
    """Everything the backend knows about one worker, minus the live process."""

    key: str  # PlannedDispatch.key
    handle: str  # this backend's id for the worker; also its directory name
    pid: int | None  # None once the process is gone and reaped
    state: str  # one of subagents_io.backend.WORKER_STATES
    argv: tuple[str, ...]  # what was actually executed, for a human reading the file
    cwd: str  # the resolved worktree path
    started_at: str  # ISO-8601 on this machine's clock
    acked_through: int = 0  # leading events.jsonl lines the coordinator has acked
    exit_code: int | None = None  # set by the reaper when we owned the process
    last_heartbeat_at: str | None = None
    detail: str | None = None


def read_ledger(path: Path) -> dict[str, LedgerEntry]:
    """Every entry in the ledger, or `{}` if it has never been written.

    Raises on a corrupt file rather than returning what it could parse: a
    half-ledger reads as "these keys were never dispatched". `json.JSONDecodeError`
    covers unparseable text; a well-formed file with the wrong shape (a missing
    field, a mistyped `argv`) raises `KeyError`/`TypeError` from `LedgerEntry`
    construction instead — both are corruption, and both are loud.
    """
    if not path.is_file():
        return {}
    raw: dict[str, dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    return {key: LedgerEntry(**{**fields, "argv": tuple(fields["argv"])}) for key, fields in raw.items()}  # type: ignore[arg-type]


def write_ledger(path: Path, entries: dict[str, LedgerEntry]) -> None:
    """Replace the ledger atomically. Creates the parent directory on demand.

    The temp file is `mkstemp`-named (hidden, unique) rather than a fixed
    `.tmp` sibling, matching `config_io.projection.write_projection` and
    `okf_ext`'s writers: two writers racing on the same ledger must not
    interleave into the same inode, and a failed write must not leave a
    discoverable half-written file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: {**asdict(entry), "argv": list(entry.argv)} for key, entry in entries.items()}
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
