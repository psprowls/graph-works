"""A page's code citations: every inline code span matching `<path>:<N>[-<M>]`.

The extraction and resolution live in `graph_works_core.workspace.citations` so the
proposals vertical can share them; this module keeps the page-level read.

`line` is body-relative: 1-based within the body `/v1/wiki/page` serves.
That page's outlink `line` counts file lines (okf-io adds the frontmatter
offset); the two must never be compared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.citations import (
    Citation,
    CitationCandidate,
    CitationStatus,
    extract_citations,
    resolve_citations,
)
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class WikiCitations:
    """A page's citations in body order, duplicates kept."""

    id: str
    citations: tuple[Citation, ...]
    refusal: Literal["unknown-page"] | None


def run_wiki_citations(layout: WorkspaceLayout, page_id: str) -> WikiCitations:
    """Extract and resolve a page's citations. Never writes."""
    document = load_workspace_bundle(layout).concept(page_id)
    if document is None:
        return WikiCitations(page_id, (), "unknown-page")
    return WikiCitations(page_id, resolve_citations(layout, document.body), None)


__all__ = [
    "Citation",
    "CitationCandidate",
    "CitationStatus",
    "WikiCitations",
    "extract_citations",
    "run_wiki_citations",
]
