"""The wiki-page vertical: read one page with its link neighbourhood and code
citations, the root index as a tree of sections, and write one prose section.

Layer 2 -- independent of every other vertical; imports `okf_io`, `okf_ext`,
`code_wiki_okf`, `workspace` and the `read_session` substrate only.
"""

from __future__ import annotations

from graph_works_core.wiki_page.citations import (
    Citation,
    CitationCandidate,
    CitationStatus,
    WikiCitations,
    extract_citations,
    run_wiki_citations,
)
from graph_works_core.wiki_page.commands import (
    PageLink,
    PageRead,
    TreeNode,
    TreePage,
    WikiTree,
    page_read,
    run_page_read,
    run_wiki_tree,
    wiki_tree,
)
from graph_works_core.wiki_page.section import SectionRefusal, SectionWriteRun, run_section_write

__all__ = [
    "Citation",
    "CitationCandidate",
    "CitationStatus",
    "PageLink",
    "PageRead",
    "SectionRefusal",
    "SectionWriteRun",
    "TreeNode",
    "TreePage",
    "WikiCitations",
    "WikiTree",
    "extract_citations",
    "page_read",
    "run_page_read",
    "run_section_write",
    "run_wiki_citations",
    "run_wiki_tree",
    "wiki_tree",
]
