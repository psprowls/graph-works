"""The proposal reasoner system prompt.

The candidate kinds are the proposal lanes; there is no `concept` lane
(`packages/doc-wiki-okf/docs/cutover-key-mapping.md`).

**The lane list is rendered, not written down** -- `render_lane_lines` takes
the loaded `LaneSet`, the same one `extractor.py` renders from, so the two
prompts cannot name different lanes. A renderer rather than a constant, and
like `build_ingestor_system` it keeps no backward-compat constant: one could
not be built without its argument.
"""

from __future__ import annotations

from doc_wiki_okf.proposals.lanes import LaneSet

from graph_works_core.prompts._fragments.lane_list import render_lane_lines

_PREAMBLE = """\
You are a code-wiki proposal reasoner.
Analyze an ingested source document and decide which durable wiki pages it justifies.
You do NOT write wiki pages. You produce candidate analyses for a downstream extractor.

Candidate lanes:
"""

_TAIL = """\
Rules:
- Read the provided wiki catalog before proposing anything new.
- Prefer arguing for an existing page when the idea is already covered; name it.
- Generate at most 10 candidates.
- Each candidate must carry source evidence, existing pages considered, a reasoning
  summary, potential conflicts, implementation notes, confidence, rank, and -- above
  all -- why its lane is the right one.
- Be conservative. Returning no candidates is a valid answer.
- Do not emit the final proposal JSON; the extractor normalizes your analysis."""


def build_proposal_reasoner_system(*, lane_set: LaneSet) -> str:
    """The reasoner system prompt for one workspace's lanes."""
    return f"{_PREAMBLE}{render_lane_lines(lane_set)}\n\n{_TAIL}"


__all__ = ["build_proposal_reasoner_system"]
