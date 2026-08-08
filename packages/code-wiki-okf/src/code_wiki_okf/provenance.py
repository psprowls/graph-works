"""Pure builders for provenance values okf-io coerces cleanly.

No I/O, no clock — callers supply `at`/`sha`/`count`.
"""

from __future__ import annotations

from datetime import datetime


def generated_value(*, by: str, at: datetime) -> dict[str, str]:
    """The `{by, at}` mapping for a `generated` frontmatter key.

    `at` must be timezone-aware — `at.isoformat()` is what okf-io's
    `Generated.at_dt` coercion expects for an ISO-8601 instant.
    """
    if at.tzinfo is None:
        raise ValueError("generated_value(): `at` must be timezone-aware")
    return {"by": by, "at": at.isoformat()}


def last_updated_commit_value(sha: str) -> str:
    """The `last_updated_commit` value — a full git SHA string."""
    if not sha.strip():
        raise ValueError("last_updated_commit_value(): sha must be non-empty")
    return sha


def tokens_value(count: int) -> int:
    """The `tokens` value — an int from the injected counter."""
    if count < 0:
        raise ValueError(f"tokens_value(): count must be >= 0, got {count}")
    return count
