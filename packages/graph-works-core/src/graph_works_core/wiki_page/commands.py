"""Read one wiki page with its frontmatter and link neighbourhood, and the root index as a tree.

Both reads run over a `ReadSession` (`graph_works_core.read_session`): the
page's frontmatter and body come from parsing that one file, its links from the
session's stored edges, and tree titles from stored member rows. `run_*` opens a
session (which reconciles the read index) around the session-taking `page_read`
and `wiki_tree`. No `ignore=` overlay: reference pages are readable too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol

from okf_io import Unreadable, read_member
from okf_io.bundle import INDEX_NAME
from okf_io.index import IndexEntry, IndexHeading, outline
from okf_io.links import Link, parse_destination, resolve_path

from graph_works_core.read_session import ReadSession, open_read_session
from graph_works_core.wiki_page.declarations import load_declarations
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


class _ConceptRow(Protocol):
    """The read-only member fields this vertical consumes from a session."""

    @property
    def id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def title(self) -> str | None: ...

    @property
    def type(self) -> str | None: ...


def _unknown(page_id: str) -> PageRead:
    return PageRead(page_id, MappingProxyType({}), "", (), (), (), None, "unknown-page")


def _concept_row(session: ReadSession, member_id: str) -> _ConceptRow | None:
    """The stored row for *member_id* when it is exactly that concept file.

    `ReadSession.member` also resolves an NFC variant, which `Bundle.concept`
    never did; the exact-id check keeps the old answer.
    """
    row = session.member(member_id)
    if row is None or row.id != member_id or row.kind != "concept":
        return None
    return row


def page_read(session: ReadSession, layout: WorkspaceLayout, page_id: str) -> PageRead:
    """Read an extensionless, bundle-relative page id from an open *session*. Never writes."""
    row = _concept_row(session, f"{page_id}.md")
    if row is None:
        return _unknown(page_id)
    document = read_member(layout.bundle_dir, row.id)
    if isinstance(document, Unreadable):
        return _unknown(page_id)
    error = document.parse_error
    return PageRead(
        id=page_id,
        frontmatter=MappingProxyType(document.fm_data(dates="iso")),
        body=document.body,
        outlinks=tuple(_link(link) for link in session.outlinks(page_id)),
        backlinks=tuple(session.backlinks(page_id)),
        broken=tuple(_link(link) for link in session.broken(page_id)),
        parse_error=None if error is None else f"{error.kind}: {error.message}",
        refusal=None,
    )


def run_page_read(layout: WorkspaceLayout, page_id: str) -> PageRead:
    """Read an extensionless, bundle-relative page id without writing state."""
    with open_read_session(layout) as session:
        return page_read(session, layout, page_id)


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
    root = load_declarations(layout).indexes.get("")
    if root is None:
        return MappingProxyType({})
    return MappingProxyType({spec.heading: spec.ownership for spec in root.sections})


def _tree_page(session: ReadSession, entry: IndexEntry) -> TreePage | None:
    """The page an entry links to, or `None` for a directory, an asset, an
    external URL or a dangling target -- none of which is a page to open."""
    path, _fragment, external = parse_destination(entry.href)
    if external or not path:
        return None
    member = resolve_path(path, source_id=INDEX_NAME)
    if member is None or not member.endswith(".md"):
        return None
    page_id = member[: -len(".md")]
    row = _concept_row(session, member)
    if row is None:
        return None
    title = (row.title or "").strip() or entry.label or page_id
    return TreePage(id=page_id, title=title, type=row.type or None)


def _tree_node(
    session: ReadSession, heading: IndexHeading, generated: bool, children: tuple[TreeNode, ...]
) -> TreeNode:
    pages = tuple(page for entry in heading.entries if (page := _tree_page(session, entry)) is not None)
    return TreeNode(heading.text, heading.level, generated, pages, children)


def wiki_tree(session: ReadSession, layout: WorkspaceLayout) -> WikiTree:
    """The root `index.md` as sections of pages. Never writes.

    One node per `##` heading in file order, each `###` beneath it nested as
    a child. The `#` title is not a node, and a later `#` heading (`#
    Subdirectories`) ends the tree. `generated` is true for a heading the
    section declarations own as `generated`; a `###` inherits its parent's
    flag unless it is declared itself. Titles and types come from stored
    member rows, so every id is one `/v1/wiki/page` accepts.
    """
    row = session.member(INDEX_NAME)
    if row is None or row.id != INDEX_NAME or row.kind != "index":
        return WikiTree(())
    index = read_member(layout.bundle_dir, INDEX_NAME)
    if isinstance(index, Unreadable):
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
                session,
                sub,
                ownership[sub.text] == "generated" if sub.text in ownership else flag,
                (),
            )
            for sub in subsections
        )
        sections.append(_tree_node(session, section, flag, children))
    return WikiTree(tuple(sections))


def run_wiki_tree(layout: WorkspaceLayout) -> WikiTree:
    """The root `index.md` as sections of pages. Never writes. See `wiki_tree`."""
    with open_read_session(layout) as session:
        return wiki_tree(session, layout)
