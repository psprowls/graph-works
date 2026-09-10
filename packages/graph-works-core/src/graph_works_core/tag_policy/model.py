"""Frozen values the tag-policy vertical returns.

Frozen and slotted, matching the core's habit and `okf_ext.tags.model`'s.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

#: What happens to a tag. `merge` is never produced by `draft` -- retention
#: test 5 is the semantic judgment the disposition file exists to capture, and
#: no counter can make it. A human edits a `keep` into a `merge` by hand.
Verdict = Literal["keep", "merge", "strip"]

#: Which test decided it. `survivor` means all four mechanical tests passed;
#: `semantic` is the reason a human writes on a hand-authored merge.
Reason = Literal[
    "contributed",
    "survivor",
    "below-floor",
    "above-ceiling",
    "entity-dup",
    "field-dup",
    "semantic",
]


@dataclass(frozen=True, slots=True)
class TagVerdict:
    """One tag's disposition, with the count and the test that decided it.

    `into` is set only for `verdict == "merge"`; every other verdict leaves it
    `None`. Nothing here enforces that -- `disposition.load` does, because a
    hand-edited file is where the mistake would actually be made.
    """

    tag: str
    uses: int
    verdict: Verdict
    reason: Reason
    into: str | None = None


@dataclass(frozen=True, slots=True)
class Disposition:
    """Every tag in a bundle, judged once.

    **A dated decision record, accurate as of `generated`.** Nothing keeps it
    in sync with the vault afterwards, and re-running it carries no staleness
    detection: `apply_phase` is `apply(bundle, plan_phase(bundle, disposition,
    phase))`, which re-plans against the *live* bundle on every call, so a
    plan is never stale and this record's `generated` / `tagged_pages` are
    never compared against the bundle it is applied to. Re-applying an old
    disposition to a vault that has since grown a tag back, or re-declared
    one, silently strips or merges it again using yesterday's verdicts --
    there is no guard against this. Re-draft instead of re-running an old
    file.
    """

    generated: date
    total_tags: int
    tagged_pages: int
    verdicts: tuple[TagVerdict, ...]

    @property
    def keep(self) -> tuple[TagVerdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict == "keep")

    @property
    def merge(self) -> tuple[TagVerdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict == "merge")

    @property
    def strip(self) -> tuple[TagVerdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict == "strip")

    @property
    def strip_tags(self) -> tuple[str, ...]:
        return tuple(v.tag for v in self.strip)

    @property
    def merge_mapping(self) -> Mapping[str, str]:
        """`old -> into`, for the merge half. Targets are guaranteed non-None
        by `disposition.load`; `draft` never emits a merge at all."""
        return {v.tag: v.into for v in self.merge if v.into is not None}


__all__ = ["Disposition", "Reason", "TagVerdict", "Verdict"]
