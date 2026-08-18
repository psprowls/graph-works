"""Shared prompt material for the graph-works verticals.

Seven fragment constants and three renderers under `_fragments/`, one per
module except `lane_list`, which exports both, so each keeps its own
provenance header; this module is where a consumer reaches them. It is also
`project_context`'s home — the one prompt-adjacent renderer every vertical
reads (`ingest`, `scan`, `lint_drift`) rather than one vertical owning it.

Every vertical-specific prompt module (`ingestor`, `extractor`,
`proposal_reasoner`, `prose_refresher`, `librarian`, `synthesizer`,
`query_orchestrator`, `code_reader`, `linter`, `drift_propagator`) moved into
its owning vertical's own `prompts/` (or, where there is no collision risk,
flat into the vertical directory) — this package now re-exports only what
every vertical shares.
"""

from __future__ import annotations

from graph_works_core.prompts._fragments.architecture_overview import (
    render_architecture_overview,
)
from graph_works_core.prompts._fragments.citation_rules import CITATION_RULES
from graph_works_core.prompts._fragments.claude_md_disambiguation import (
    CLAUDE_MD_DISAMBIGUATION,
)
from graph_works_core.prompts._fragments.frontmatter_rules import FRONTMATTER_RULES
from graph_works_core.prompts._fragments.iron_rules import IRON_RULES
from graph_works_core.prompts._fragments.log_format import LOG_FORMAT
from graph_works_core.prompts._fragments.page_categories import render_page_categories
from graph_works_core.prompts._fragments.style_rules import STYLE_RULES
from graph_works_core.prompts.project_context import render_project_context

__all__ = [
    "CITATION_RULES",
    "CLAUDE_MD_DISAMBIGUATION",
    "FRONTMATTER_RULES",
    "IRON_RULES",
    "LOG_FORMAT",
    "STYLE_RULES",
    "render_architecture_overview",
    "render_page_categories",
    "render_project_context",
]
