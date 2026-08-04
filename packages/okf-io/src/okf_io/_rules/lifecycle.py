"""Rules for `status` and `stale_after` (OKF v0.2 §5.4, §5.5)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from okf_io._rules._common import concepts, member_path
from okf_io.derive import is_stale
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "lifecycle.status-unknown",
    "lifecycle.stale-after-malformed",
    "lifecycle.stale",
)

_KNOWN_STATUS = frozenset({"draft", "stable", "deprecated"})
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def status(ctx: RuleContext) -> Iterable[Finding]:
    """§5.4: `status` is one of draft, stable, deprecated.

    Read raw, not through ``effective_status``. An *absent* status defaults to
    ``stable`` and is fine; ``status: ""`` is an authored value, however odd,
    and is not one of the three.
    """
    for concept_id, document in concepts(ctx):
        value = document.fm.status
        if value is not None and value.strip() not in _KNOWN_STATUS:
            yield Finding(
                "lifecycle.status-unknown",
                "error",
                f"`status` {value!r} is outside draft / stable / deprecated",
                "§5.4",
                member_path(concept_id),
            )


def stale_after(ctx: RuleContext) -> Iterable[Finding]:
    """§5.5: `stale_after` is `YYYY-MM-DD`, and `today >= stale_after` is stale.

    Staleness goes through ``derive.is_stale`` rather than re-implementing the
    comparison: a prior child already owns §5.5, and a second copy is a second
    thing to get wrong at the boundary. ``ctx.today`` is the only clock in the
    package.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        frontmatter = document.fm
        raw = document.fm_raw.get("stale_after")
        malformed = False
        if raw is not None:
            malformed = "stale_after" in frontmatter.coercion_failures or (
                isinstance(raw, str) and _ISO_DATE_RE.fullmatch(raw.strip()) is None
            )
            if malformed:
                yield Finding(
                    "lifecycle.stale-after-malformed",
                    "error",
                    f"`stale_after` {raw!r} is not `YYYY-MM-DD`",
                    "§5.5",
                    path,
                )
        # A date-prefixed value like "2026-01-01T00:00:00Z" coerces successfully via
        # _as_date's truncation (it takes [:10]), so "malformed" and "stale" are not
        # mutually exclusive by construction and must be made so here.
        if not malformed and is_stale(frontmatter, today=ctx.today):
            yield Finding(
                "lifecycle.stale",
                "warn",
                f"`stale_after` {frontmatter.stale_after} is on or before {ctx.today}",
                "§5.5",
                path,
            )


RULES: tuple[Rule, ...] = (status, stale_after)
