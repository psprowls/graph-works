"""The extractor system prompt, rendered from pool guidance.

The extractor uses summary bullets and the reasoner the full rubric, so the two
prompts cannot name different types.
"""

from __future__ import annotations

from textwrap import indent

from doc_wiki_okf.proposals.pool import ProposalPool

from graph_works_core.prompts._fragments.type_list import render_type_summaries

_PREAMBLE = """\
You normalize source-backed proposal context into strict JSON.
You do NOT create wiki pages. You select at most 5 of the strongest proposals from the context.

Output ONE JSON object with a single key, `suggestions`, whose value is a list. No prose, no
code fence, nothing before or after the object.

Each suggestion is an object with these keys:
- type: exactly one of these page types, spelled as written.
"""

_TAIL = """\
- title: the page's title. Its slug is derived from it; do not propose one.
- rationale: one sentence saying WHY this type, not merely why this page. Required.
  A suggestion whose rationale is blank is discarded, not defaulted into a type.
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


def build_extractor_system(*, pool: ProposalPool) -> str:
    """The extractor system prompt for one workspace's proposal pool.

    The type bullets are indented under the `type:` key they describe.
    """
    return f"{_PREAMBLE}{indent(render_type_summaries(pool.types), '    ')}\n{_TAIL}"


__all__ = ["build_extractor_system"]
