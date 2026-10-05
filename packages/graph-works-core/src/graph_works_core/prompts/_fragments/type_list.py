# Source: adopted from `lane_list.py`'s renderer; the text now comes off each type's `x-okf-proposal-guidance`.

"""The page-type list both proposal prompts render, driven by the proposal pool.

Names and text both come off the schemas: a type the pool admits is listed
with its own guidance, and a type it does not admit cannot be named. Taking
`ProposableType` values rather than the pool keeps this module free of the
doc-wiki vertical; callers pass `pool.types`.
"""

from __future__ import annotations

from collections.abc import Sequence

from okf_ext.schemas import ProposableType


def render_type_summaries(types: Sequence[ProposableType]) -> str:
    """One `- Name: summary` bullet per type, in the given order -- the extractor's list."""
    return "\n".join(f"- {entry.name}: {entry.guidance.summary}" for entry in types)


def render_type_rubric(types: Sequence[ProposableType]) -> str:
    """Each type's summary, question, signals, anti-signals and title pattern -- the reasoner's list."""
    blocks: list[str] = []
    for entry in types:
        guidance = entry.guidance
        lines = [f"- {entry.name}: {guidance.summary}", f"  Question: {guidance.question}"]
        if guidance.signals:
            lines.append(f"  Signals: {'; '.join(guidance.signals)}")
        if guidance.anti_signals:
            lines.append(f"  Anti-signals: {'; '.join(guidance.anti_signals)}")
        if guidance.title_pattern:
            lines.append(f"  Title pattern: {guidance.title_pattern}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


__all__ = ["render_type_rubric", "render_type_summaries"]
