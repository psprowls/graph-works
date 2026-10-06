"""The proposal reasoner system prompt, rendered from pool guidance.

The extractor uses summary bullets and the reasoner the full rubric, so the two
prompts cannot name different types.
"""

from __future__ import annotations

from doc_wiki_okf.proposals.pool import ProposalPool

from graph_works_core.prompts._fragments.type_list import render_type_rubric

_PREAMBLE = """\
You are a code-wiki proposal reasoner.
Analyze an ingested source document and decide which durable wiki pages it justifies.
You do NOT write wiki pages. You produce candidate analyses for a downstream extractor.

Candidate page types:
"""

_TAIL = """\
Rules:
- Read the provided wiki catalog before proposing anything new.
- Prefer arguing for an existing page when the idea is already covered; name it.
- Generate at most 10 candidates.
- Each candidate must carry source evidence, existing pages considered, a reasoning
  summary, potential conflicts, implementation notes, confidence, rank, and -- above
  all -- why its type is the right one.
- Be conservative. Returning no candidates is a valid answer.
- Do not emit the final proposal JSON; the extractor normalizes your analysis."""


def build_proposal_reasoner_system(*, pool: ProposalPool) -> str:
    """The reasoner system prompt for one workspace's proposal pool."""
    return f"{_PREAMBLE}{render_type_rubric(pool.types)}\n\n{_TAIL}"


__all__ = ["build_proposal_reasoner_system"]
