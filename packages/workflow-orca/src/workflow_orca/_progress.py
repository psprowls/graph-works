"""Read SDD progress from a dispatch worktree without interpreting worker text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LAST_LINE_MAX = 200
NO_LEDGER = "no SDD ledger"
LEDGER_UNREADABLE = "ledger unreadable"
PLAN_UNREADABLE = "plan unreadable"

_HEADER = re.compile(r"^#\s*SDD ledger\s*(?:—|-{1,2})\s*plan:\s*(?P<plan>\S.*?)\s*$")
_COMPLETE = re.compile(r"^\s*(?:[-*]\s+)?Task\s+(?P<n>\d+):\s+complete\b")
_PLAN_TASK = re.compile(r"^###\s+Task\s+(?P<n>\d+):")


@dataclass(frozen=True)
class SddProgress:
    """Facts recorded by one fresh SDD ledger."""

    ledger: str
    plan: str | None
    completed: int
    total: int | None
    last_line: str


def find_progress(worktree: Path, *, since: datetime) -> tuple[SddProgress | None, tuple[str, ...]]:
    """Read the newest ledger at or after the dispatch creation time."""
    fresh: list[tuple[datetime, Path]] = []
    unreadable = False
    try:
        for ledger in (worktree / ".superpowers" / "sdd").glob("*/progress.md"):
            try:
                mtime = datetime.fromtimestamp(ledger.stat().st_mtime, tz=UTC)
            except OSError:
                unreadable = True
                continue
            if mtime >= since:
                fresh.append((mtime, ledger))
    except OSError:
        return None, (LEDGER_UNREADABLE,)
    if not fresh:
        return None, (LEDGER_UNREADABLE if unreadable else NO_LEDGER,)

    _, ledger = max(fresh)
    try:
        text = ledger.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, (LEDGER_UNREADABLE,)

    lines = text.splitlines()
    header = _HEADER.match(lines[0]) if lines else None
    plan = header["plan"] if header else None
    nonblank = [line.strip() for line in lines if line.strip()]
    completed = len({int(match["n"]) for line in lines if (match := _COMPLETE.match(line))})
    total = _plan_total(worktree, plan) if plan else None
    progress = SddProgress(
        ledger=str(ledger),
        plan=plan,
        completed=completed,
        total=total,
        last_line=nonblank[-1][:LAST_LINE_MAX] if nonblank else "",
    )
    return progress, (() if total is not None else (PLAN_UNREADABLE,))


def _plan_total(worktree: Path, plan: str) -> int | None:
    """Count distinct task headings, or return None when the plan cannot say."""
    path = Path(plan)
    if not path.is_absolute():
        path = worktree / path
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    numbers = {int(match["n"]) for line in text.splitlines() if (match := _PLAN_TASK.match(line))}
    return len(numbers) or None
