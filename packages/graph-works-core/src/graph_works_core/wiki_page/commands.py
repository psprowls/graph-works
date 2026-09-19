"""Read one wiki page with its frontmatter and computed link neighbourhood.

No cache: each call walks the bundle and builds its link graph.  The bundle is
loaded without ``ignore=`` so reference pages are readable too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from okf_io import build_link_graph, load_bundle
from okf_io.links import Link

from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class PageLink:
    """A page link projected from the OKF link graph."""

    source: str
    raw: str
    target: str | None
    external: bool
    line: int | None


@dataclass(frozen=True, slots=True)
class PageRead:
    """The requested page and its computed link neighbourhood."""

    id: str
    frontmatter: Mapping[str, object]
    body: str
    outlinks: tuple[PageLink, ...]
    backlinks: tuple[str, ...]
    broken: tuple[PageLink, ...]
    parse_error: str | None
    refusal: Literal["unknown-page"] | None


def _link(link: Link) -> PageLink:
    return PageLink(link.source, link.raw, link.target, link.external, link.line)


def run_page_read(layout: WorkspaceLayout, page_id: str) -> PageRead:
    """Read an extensionless, bundle-relative page id without writing state."""
    bundle = load_bundle(layout.bundle_dir)
    document = bundle.concept(page_id)
    if document is None:
        return PageRead(page_id, MappingProxyType({}), "", (), (), (), None, "unknown-page")

    graph = build_link_graph(bundle)
    error = document.parse_error
    return PageRead(
        id=page_id,
        frontmatter=MappingProxyType(document.fm_data(dates="iso")),
        body=document.body,
        outlinks=tuple(_link(link) for link in graph.out.get(page_id, ())),
        backlinks=tuple(graph.backlinks.get(page_id, ())),
        broken=tuple(_link(link) for link in graph.broken if link.source == page_id),
        parse_error=None if error is None else f"{error.kind}: {error.message}",
        refusal=None,
    )
