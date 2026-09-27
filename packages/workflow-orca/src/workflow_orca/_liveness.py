"""Liveness rows: what the coordinator can see of a live worker, as facts.

Pure: no process, no argv, no clock. `backend.py` fetches; this module
normalises and projects. A row carries no verdict: what counts as stalled is
the coordinator's decision, not the backend's. That is the same rule as
`WorkerRecord`: a backend records, it does not reconcile.

Orca spells a moment three ways: ISO-8601 with `Z` (`lastHeartbeatAt`), a
naive `YYYY-MM-DD HH:MM:SS` that is UTC (`createdAt`), and epoch milliseconds
(`terminal.lastOutputAt`, a transcript message's `timestamp`). `instant()` is
the one place that knows all three.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from workflow_orca._progress import SddProgress

NO_TERMINAL = "no terminal"
WORKTREE_UNKNOWN = "worktree unknown"
CREATED_AT_UNKNOWN = "createdAt unknown"
TRANSCRIPT_EMPTY = "transcript empty"
TRANSCRIPT_TIMESTAMP_MISSING = "transcript timestamp missing"


def show_failed(code: str) -> str:
    return f"worker-show failed: {code}"


def read_failed(code: str) -> str:
    return f"worker-read failed: {code}"


def transcript_unavailable(source: str) -> str:
    return f"transcript unavailable ({source} source)"


@dataclass(frozen=True)
class LivenessRow:
    """One live dispatch at the moment a wait timed out."""

    key: str
    handle: str
    state: str
    heartbeat_at: str | None
    heartbeat_age_s: int | None
    transcript_at: str | None
    transcript_age_s: int | None
    output_at: str | None
    output_age_s: int | None
    worktree_path: str | None
    progress: SddProgress | None
    notes: tuple[str, ...]


def as_object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def instant(value: object) -> datetime | None:
    """Any of Orca's spellings of a moment, as an aware UTC datetime."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except (ValueError, OverflowError):
            return None
    return None


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_s(now: datetime, moment: datetime) -> int:
    """Whole seconds, floored at 0 so clock skew never reads as negative."""
    return max(0, int((now - moment).total_seconds()))


def stamp(raw: object, field: str, now: datetime, notes: list[str]) -> tuple[str | None, int | None]:
    """`(iso, age)` for a raw Orca value; an unparseable one is noted, an absent one is not."""
    if raw is None:
        return None, None
    moment = instant(raw)
    if moment is None:
        notes.append(f"{field} unparseable")
        return None, None
    return iso(moment), age_s(now, moment)


def liveness_data(rows: Sequence[LivenessRow]) -> list[dict[str, Any]]:
    """Plain data, `json.dumps`-able with no encoder; `progress` nested or None."""
    return [{**asdict(row), "notes": list(row.notes)} for row in rows]
