"""The Diátaxis taxonomy as data, and the markdown an agent reads to apply it.

The content is the `writing-diataxis` skill's -- the learning / problem /
information / understanding split, its four decision questions and its four
title patterns. The skill teaches an agent; `brief()` is the same material in a
form an ingest brief can embed.

Nothing here scores a page. Spec §4.1: Diátaxis types differ by author intent,
text signals do not carry intent, and a confident wrong answer is worse than no
answer when retyping is a move rather than an edit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TypeRubric:
    """One type's selecting question and the evidence for and against it."""

    type_name: str
    question: str
    signals: tuple[str, ...]
    anti_signals: tuple[str, ...]
    title_pattern: str


RUBRIC: tuple[TypeRubric, ...] = (
    TypeRubric(
        type_name="Tutorial",
        question="Is the reader learning by doing, for the first time?",
        signals=(
            "the reader is new to the subject and needs to be taken through it",
            "every step ends in a visible, verifiable result",
            "one path only, with no alternatives to weigh",
            "explanation is minimised in favour of doing",
        ),
        anti_signals=(
            "the reader already knows what they want to accomplish",
            "the page is something to look a fact up in",
            "the page argues for a design",
        ),
        title_pattern="Starts with a verb: `Build your first ...`, `Create a ... from scratch`",
    ),
    TypeRubric(
        type_name="HowTo",
        question="Does the reader already understand the problem and need to solve it?",
        signals=(
            "the reader arrives with a goal and baseline knowledge",
            "the page states what it assumes",
            "the steps end in one expected result",
            "alternatives are noted where they exist",
        ),
        anti_signals=(
            "the reader is meeting the subject for the first time",
            "the page teaches concepts before acting on them",
            "there is no single task being completed",
        ),
        title_pattern="Names the task: `How to configure ...`, `How to deploy ... to ...`",
    ),
    TypeRubric(
        type_name="Reference",
        question="Is the reader looking up a technical fact?",
        signals=(
            "one repeatable format per entry",
            "facts, with instruction kept to a minimal usage example",
            "a reader finds one fact in under a minute without reading around it",
            "it is true of a named version or component",
        ),
        anti_signals=(
            "the page walks the reader through anything",
            "the page explains why something is the way it is",
            "reading it end to end is the intended use",
        ),
        title_pattern="Names the thing: `Configuration options`, `API endpoints`, `CLI flags`",
    ),
    TypeRubric(
        type_name="Explanation",
        question="Does the reader want to understand why?",
        signals=(
            "context comes before the idea itself",
            "alternatives and trade-offs are weighed",
            "there are no steps to follow and nothing to look up",
            "a reader could restate the idea in their own words afterwards",
        ),
        anti_signals=(
            "the page prescribes an order of operations",
            "the page is a table of facts",
            "the reader is expected to type along",
        ),
        title_pattern="Frames a concept: `How ... works`, `Understanding ...`, `Why ... is designed this way`",
    ),
)

#: Unpacked from `RUBRIC` rather than typed twice, so a rename in one cannot
#: drift from the other -- the habit `okf_ext.tags.vocabulary` set.
TYPE_NAMES: tuple[str, ...] = tuple(entry.type_name for entry in RUBRIC)


def brief() -> str:
    """`RUBRIC` as the markdown an agent reads.

    Carries no `<slot>` and no `[[wikilink]]`: an ingest brief embedding this
    would otherwise seed a `render.angle-bracket` or `render.wikilink` finding
    in whatever page quotes it. `tests/test_rubric.py` asserts both.
    """
    lines = ["# Choosing a Diátaxis type", ""]
    for entry in RUBRIC:
        lines.append(f"## {entry.type_name}")
        lines.append("")
        lines.append(f"**The question:** {entry.question}")
        lines.append("")
        lines.append(f"**Title pattern:** {entry.title_pattern}")
        lines.append("")
        lines.append("**Signals:**")
        lines.extend(f"- {signal}" for signal in entry.signals)
        lines.append("")
        lines.append("**Anti-signals:**")
        lines.extend(f"- {signal}" for signal in entry.anti_signals)
        lines.append("")
    return "\n".join(lines)


__all__ = ["RUBRIC", "TYPE_NAMES", "TypeRubric", "brief"]
