"""The extractor system prompt: reasoner analysis in, strict JSON suggestions out.

The output contract is spec §4.5's: the extractor proposes a `lane`, one of
five, and no `concept_kind` — the Diataxis type *is* the kind. `mode`,
`existing_slug` and `slug` are derived downstream rather than proposed here.

**A `rationale` is required, not optional.** `doc_wiki_okf.diataxis.classify`
refuses a blank one with `reason="no-rationale"`, so a suggestion without one
is dropped rather than filed. Saying that here is cheaper than dropping it
there.

**The lane list is rendered, not written down.** `render_lane_lines` takes the
loaded `LaneSet`, so a lane this prompt does not name is unrepresentable. That
makes this a `build_extractor_system(lane_set=…)` renderer rather than a
module-level constant, following `build_ingestor_system`'s precedent -- and,
like it, keeping no backward-compat constant, which could not be built without
its argument.

**JSON, not YAML.** This package's declared dependencies (spec §6.9) include no
general YAML reader, and `json.loads` is stdlib. JSON is also a closed grammar,
so a parse miss is unambiguous rather than a guess about indentation.
"""

from __future__ import annotations

from textwrap import indent

from doc_wiki_okf.proposals.lanes import LaneSet

from graph_works_core.prompts._fragments.lane_list import render_lane_lines

_PREAMBLE = """\
You normalize source-backed proposal context into strict JSON.
You do NOT create wiki pages. You select at most 5 of the strongest proposals from the context.

Output ONE JSON object with a single key, `suggestions`, whose value is a list. No prose, no
code fence, nothing before or after the object.

Each suggestion is an object with these keys:
- lane: exactly one of these names.
"""

_TAIL = """\
- title: the page's title. Its slug is derived from it; do not propose one.
- rationale: one sentence saying WHY this lane, not merely why this page. Required.
  A suggestion whose rationale is blank is discarded, not defaulted into a lane.
- description: one line, what the proposed page would say.
- rank: integer starting at 1.
- confidence: high, medium, or low.
- evidence: list of source-grounded bullets.
- existing_pages_considered: list of bundle-relative page paths you weighed.
- reasoning_summary: one short paragraph.
- potential_conflicts: list, empty if none.
- implementation_notes: list, empty if none.

Rules:
- At most 5 suggestions.
- Do not propose a page the wiki already has unless the source genuinely adds to it;
  when it does, name that page in existing_pages_considered and keep the same title.
- Drop weak, duplicate, or unsupported candidates.
- Return {"suggestions": []} when no durable page is justified.
"""


def build_extractor_system(*, lane_set: LaneSet) -> str:
    """The extractor system prompt for one workspace's lanes.

    The lane bullets are indented under the `lane:` key they describe, which is
    where they sat when they were written out by hand.
    """
    return f"{_PREAMBLE}{indent(render_lane_lines(lane_set), '    ')}\n{_TAIL}"


__all__ = ["build_extractor_system"]
