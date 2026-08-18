# Source: adopted from the hand-written lane bullets in `extractor.py` and `proposal_reasoner.py`, glosses merged.

"""The lane list both proposal prompts render, driven by the loaded `LaneSet`.

Lane *names* always come off the set. `catalog_lanes` already goes to trouble
to write none of them down, and a prompt that hardcodes them defeats that one
layer up: rename or add a lane and the model keeps proposing the old names
while `_validate_suggestion` drops every suggestion in the new one -- a run
that reports zero proposals with no error anywhere.

Glosses cannot come off the set. `Lane` carries no description text, and a
type-keyed map would be wrong rather than merely absent: the `adr` and
`explanation` lanes share `type_name="Explanation"`, so the ADR lane would get
the explanation blurb. They are optional editorial instead, keyed on the lane
name -- a lane with no gloss renders as its bare name rather than blocking the
render on someone writing one.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from doc_wiki_okf.proposals.lanes import LaneSet

#: One line of editorial per lane there is one for, keyed on the lane's name.
#: Frozen: a stray write here would change both prompts for the rest of the
#: process, which is the drift this module exists to prevent.
LANE_GLOSSES: Mapping[str, str] = MappingProxyType(
    {
        "tutorial": "the reader is learning by doing, for the first time.",
        "how-to": "the reader already has the goal and needs a task-shaped recipe.",
        "reference": "the reader is looking up a fact -- one repeatable format per entry, facts not instruction.",
        "explanation": "the reader wants to understand why; an argument for why something is the way it is.",
        "adr": "a dated, consequential decision the source records or strongly implies.",
    }
)


def render_lane_lines(lane_set: LaneSet) -> str:
    """One `- name: gloss` bullet per lane, in the set's own order."""
    return "\n".join(
        f"- {lane.name}: {LANE_GLOSSES[lane.name]}" if lane.name in LANE_GLOSSES else f"- {lane.name}"
        for lane in lane_set.lanes
    )


__all__ = ["LANE_GLOSSES", "render_lane_lines"]
