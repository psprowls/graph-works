"""The agent-facing projection of a page: which declared sections an audience
reads, as separate entries.

    views = audience_view(document.body, section_set.types["Package"])

**Entries, not a joined string.** A consumer that budgets a dispatch cuts at
entry boundaries and needs to know where they are; a caller wanting text joins
them itself.

**Skips what says nothing.** A declared section absent from the body, empty,
or still equal to its placeholder is left out -- that is what keeps every
unfilled generated-page section out of an agent's context without a
type-specific rule. "Equal to its placeholder" is `okf_ext.body.normalized_text`,
the same comparison `sections.unfilled` makes.

Pure: no I/O, no clock. Imports stdlib, `okf_ext.body` and this package's own
model, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from okf_ext.body import find_section, normalized_text
from okf_ext.shape.model import Audience, TypeSections


@dataclass(frozen=True, slots=True)
class SectionView:
    """One section as an audience reads it. `heading` is the **declared**
    heading, not the page's spelling of it."""

    heading: str
    level: int
    text: str  # the section's own text, heading excluded, edge-stripped


def word_count(text: str) -> int:
    """Whitespace-separated tokens in *text*, code included.

    The single definition: the `sections.agent-oversize` rule and the scan
    prose sanitizer both call this, so the two cannot disagree about whether a
    section is over its cap.
    """
    return len(text.split())


def audience_view(
    body: str,
    declaration: TypeSections,
    audience: Audience = "agent",
    *,
    phase: str | None = None,
) -> tuple[SectionView, ...]:
    """*declaration*'s *audience* sections present and filled in *body*, in
    declaration order.

    With *phase*, a section whose `phases` is non-empty and does not contain it
    is left out; a section with empty `phases` matches every phase.
    """
    views: list[SectionView] = []
    for spec in declaration.sections:
        if spec.audience != audience:
            continue
        if phase is not None and spec.phases and phase not in spec.phases:
            continue
        found = find_section(body, spec.heading, level=spec.level)
        if found is None:
            continue
        text = found.slice(body)
        content = normalized_text(text)
        if not content or content == normalized_text(spec.placeholder):
            continue
        views.append(SectionView(heading=spec.heading, level=spec.level, text=text.strip()))
    return tuple(views)


__all__ = ["SectionView", "audience_view", "word_count"]
