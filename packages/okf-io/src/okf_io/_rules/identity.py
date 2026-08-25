"""Bundle-structural identity concerns arising from ADR-0027's NFC-insensitive
matching. Not a spec-defined topic -- okf-io implements only what OKF v0.2
defines (ADR-0005) -- so this catalog entry cites the ADR that created the
hazard rather than a spec section, unlike every other topic in this
package."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = ("identity.canonical-collision",)


def _display(raw_id: str) -> str:
    """Render *raw_id* so an NFC and NFD spelling of the same glyphs are
    visually distinguishable in a `Finding.message`.

    A collision only ever fires on ids that are NFC-equal but byte-different
    (that's the whole hazard), so the winner and every loser render as
    *identical glyphs* if interpolated raw -- the message would be the entire
    actionable artifact of this rule (this hazard can't be reproduced on the
    normalization-folding filesystem this repo runs its tests on, ADR-0010)
    and yet tell an operator nothing about which file is which.
    """
    return raw_id.encode("unicode_escape").decode("ascii")


def canonical_collisions(ctx: RuleContext) -> Iterable[Finding]:
    """Two or more members that are NFC-equal but byte-different collapsed
    onto the same canonical id during the walk. `Bundle._load` resolves this
    last-write-wins, silently; this reports it as an ERROR instead of
    raising, matching every other structural issue in this package
    (unreadable files, broken links) being data rather than an exception.

    Reported in canonical-id order -- deterministic, independent of walk
    order.
    """
    for cid, ids in sorted(ctx.bundle.canonical_collisions.items()):
        winner = ids[-1]
        losers = ", ".join(f"`{_display(loser)}`" for loser in ids[:-1])
        yield Finding(
            "identity.canonical-collision",
            "error",
            f"{len(ids)} members normalize to the same id ({cid!r}); `{_display(winner)}` wins, {losers} lost silently",
            "ADR-0027",
            winner,
        )


RULES: tuple[Rule, ...] = (canonical_collisions,)
