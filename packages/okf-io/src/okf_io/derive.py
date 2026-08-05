"""Signals derived from the typed view (OKF v0.2 §5.3-§5.5).

Pure functions over :class:`~okf_io.models.Frontmatter`. Nothing here reads a
file, and nothing here reads a clock.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from okf_io.models import Frontmatter

TrustTier = Literal["unverified", "machine-confirmed", "human-reviewed"]

#: §5.4 — a concept with no `status` is stable.
DEFAULT_STATUS = "stable"


def trust_tier(fm: Frontmatter) -> TrustTier:
    """§5.3. No ``verified`` ⇒ unverified; any ``human:`` actor ⇒ human-reviewed.

    An entry whose ``by`` is absent counts toward machine-confirmed. §5.3's
    ladder is written for actors that are present but not human, and says
    nothing about an unattributed entry; this takes the reading that the
    presence of a ``verified`` record is itself the machine confirmation.
    Deciding whether an unattributed entry should count at all is a validation
    question, and validation belongs to the ``validate`` module.
    """
    if not fm.verified:
        return "unverified"
    if any(event.by is not None and event.by.kind == "human" for event in fm.verified):
        return "human-reviewed"
    return "machine-confirmed"


def effective_status(fm: Frontmatter) -> str:
    """§5.4. Absent ⇒ ``stable``.

    An unrecognized value is **returned verbatim** — not coerced, not rejected
    (§11 tolerance). Flagging it is the Lifecycle rule's job. That includes the
    empty string: ``status: ""`` is an authored value, however odd, and only an
    *absent* status defaults.
    """
    return fm.status if fm.status is not None else DEFAULT_STATUS


def is_stale(fm: Frontmatter, *, today: date) -> bool:
    """§5.5. ``today >= stale_after``; False when absent or uncoercible.

    ``today`` is keyword-only and has **no default**. Not an injectable clock
    with a ``date.today()`` fallback — a required argument, so there is no code
    path where a hidden call to the system clock can survive review. Callers at
    the edge supply it.
    """
    if fm.stale_after is None:
        return False
    return today >= fm.stale_after


def last_verified_at(fm: Frontmatter) -> datetime | None:
    """The most recent parseable ``verified[].at``, or None. Always tz-aware.

    A corpus mixes aware and naive stamps, so naive ones are read as UTC. The
    normalization is applied to the *returned* value, not only to the sort key:
    on a tie ``max`` yields whichever came first, so returning the original
    object would make the result's tz-awareness depend on the order keys happen
    to appear in the YAML. A caller comparing that to an aware datetime would
    then crash on some documents and not others.
    """
    stamps = [
        dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        for dt in (event.at_dt for event in fm.verified)
        if dt is not None
    ]
    if not stamps:
        return None
    return max(stamps)
