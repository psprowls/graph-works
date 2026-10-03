"""A page's code citations: every inline code span matching `<path>:<N>[-<M>]`.

The extraction and resolution live in `graph_works_core.workspace.citations` so the
proposals vertical can share them; this module keeps the page-level read.
The read session admits the page and supplies its indexed content hash. Spans
are cached under that hash; the bundle backend skips this cache and reads the
page fresh. Repository inventories come from the shared display cache.

`line` is body-relative: 1-based within the body `/v1/wiki/page` serves.
That page's outlink `line` counts file lines (okf-io adds the frontmatter
offset); the two must never be compared.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_io import Document

from graph_works_core.read_session import ReadSession, open_read_session
from graph_works_core.workspace import citations as shared
from graph_works_core.workspace.citations import (
    Citation,
    CitationCandidate,
    CitationStatus,
    extract_citations,
)
from graph_works_core.workspace.display_cache import DisplayCache, Span, open_display_cache
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class WikiCitations:
    """A page's citations in body order, duplicates kept."""

    id: str
    citations: tuple[Citation, ...]
    refusal: Literal["unknown-page"] | None


def run_wiki_citations(layout: WorkspaceLayout, page_id: str) -> WikiCitations:
    """Extract and resolve a page's citations. Never writes the bundle; may write display.db."""
    member_id = f"{page_id}.md"
    with open_read_session(layout) as session, open_display_cache(layout) as cache:
        row = session.member(member_id)
        if row is None or row.kind != "concept" or row.concept_id != page_id:
            return WikiCitations(page_id, (), "unknown-page")
        spans = _page_spans(layout, session, member_id, cache)
        if spans is None:
            return WikiCitations(page_id, (), "unknown-page")
        return WikiCitations(page_id, shared.resolve_spans(layout, spans, cache=cache), None)


def _page_spans(
    layout: WorkspaceLayout, session: ReadSession, member_id: str, cache: DisplayCache | None
) -> tuple[Span, ...] | None:
    stamp = session.content_hash(member_id) if session.backend == "index" else None
    version = shared.extractor_version()
    if stamp is not None and cache is not None:
        hit = cache.spans(stamp, version)
        if hit is not None:
            return hit
    read = _read_page(layout.bundle_dir / member_id)
    if read is None:
        return None
    digest, body = read
    spans = shared.extract_citations(body)
    if stamp is not None and cache is not None and digest == stamp:
        cache.store_spans(stamp, version, spans)
    return spans


def _read_page(path: Path) -> tuple[str, str] | None:
    """Return the raw bytes' SHA256 and full-load-equivalent body, or None if unreadable."""
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return hashlib.sha256(data).hexdigest(), Document.parse(text, path=path).body


__all__ = [
    "Citation",
    "CitationCandidate",
    "CitationStatus",
    "WikiCitations",
    "extract_citations",
    "run_wiki_citations",
]
