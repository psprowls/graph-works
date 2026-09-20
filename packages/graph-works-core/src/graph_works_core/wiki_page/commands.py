"""Read one wiki page with its frontmatter and computed link neighbourhood.

No cache: each call walks the bundle and builds its link graph.  The bundle is
loaded without ``ignore=`` so reference pages are readable too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.shape import SectionError, load_sections
from okf_io import Bundle, build_link_graph, load_bundle
from okf_io.bundle import INDEX_NAME
from okf_io.index import IndexEntry, IndexHeading, outline
from okf_io.links import Link, parse_destination, resolve_path

from graph_works_core.workspace.errors import WorkspaceError
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


@dataclass(frozen=True, slots=True)
class TreePage:
    """A bundle page listed in the root index: its id, title and type."""

    id: str
    title: str
    type: str | None


@dataclass(frozen=True, slots=True)
class TreeNode:
    """One `##` section (level 2) or `###` subsection (level 3) of the root index."""

    heading: str
    level: int
    generated: bool
    pages: tuple[TreePage, ...]
    children: tuple[TreeNode, ...]


@dataclass(frozen=True, slots=True)
class WikiTree:
    """The root index's sections in file order."""

    sections: tuple[TreeNode, ...]


def _root_ownership(layout: WorkspaceLayout) -> Mapping[str, str]:
    """Root-index heading -> declared `ownership`, from every section file's `directories[""]`."""
    try:
        declarations = load_sections(layout.config_dir / SECTIONS_DIRNAME)
    except SectionError as exc:
        raise WorkspaceError(str(exc)) from exc
    root = declarations.indexes.get("")
    if root is None:
        return MappingProxyType({})
    return MappingProxyType({spec.heading: spec.ownership for spec in root.sections})


def _tree_page(bundle: Bundle, entry: IndexEntry) -> TreePage | None:
    """The page an entry links to, or `None` for a directory, an asset, an
    external URL or a dangling target -- none of which is a page to open."""
    path, _fragment, external = parse_destination(entry.href)
    if external or not path:
        return None
    member = resolve_path(path, source_id=INDEX_NAME)
    if member is None or not member.endswith(".md"):
        return None
    page_id = member[: -len(".md")]
    document = bundle.concept(page_id)
    if document is None:
        return None
    title = (document.fm.title or "").strip() or entry.label or page_id
    return TreePage(id=page_id, title=title, type=document.fm.type or None)


def _tree_node(bundle: Bundle, heading: IndexHeading, generated: bool, children: tuple[TreeNode, ...]) -> TreeNode:
    pages = tuple(page for entry in heading.entries if (page := _tree_page(bundle, entry)) is not None)
    return TreeNode(heading.text, heading.level, generated, pages, children)


def run_wiki_tree(layout: WorkspaceLayout) -> WikiTree:
    """The root `index.md` as sections of pages. Never writes.

    One node per `##` heading in file order, each `###` beneath it nested as
    a child. The `#` title is not a node, and a later `#` heading (`#
    Subdirectories`) ends the tree. `generated` is true for a heading the
    section declarations own as `generated`; a `###` inherits its parent's
    flag unless it is declared itself. The bundle is loaded without
    `ignore=`, so every id is one `/v1/wiki/page` accepts.
    """
    bundle = load_bundle(layout.bundle_dir)
    index = bundle.indexes.get("")
    if index is None:
        return WikiTree(())
    ownership = _root_ownership(layout)
    groups: list[tuple[IndexHeading, list[IndexHeading]]] = []
    for heading in outline(index.body).headings:
        if heading.level == 1:
            if groups:
                break
            continue
        if heading.level == 2:
            groups.append((heading, []))
        elif heading.level == 3 and groups:
            groups[-1][1].append(heading)
    sections: list[TreeNode] = []
    for section, subsections in groups:
        flag = ownership.get(section.text) == "generated"
        children = tuple(
            _tree_node(
                bundle,
                sub,
                ownership[sub.text] == "generated" if sub.text in ownership else flag,
                (),
            )
            for sub in subsections
        )
        sections.append(_tree_node(bundle, section, flag, children))
    return WikiTree(tuple(sections))
