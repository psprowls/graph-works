"""Builders and the pre-change full-load oracle for the wiki read-model tests."""

from __future__ import annotations

import os
import shutil
import time
from datetime import date
from pathlib import Path
from types import MappingProxyType

from graph_works_core import apply_init, plan_init
from graph_works_core.wiki_page.commands import (
    PageRead,
    TreeNode,
    TreePage,
    WikiTree,
    _link,
    _root_ownership,
)
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import Bundle, build_link_graph
from okf_io.bundle import INDEX_NAME
from okf_io.index import IndexEntry, IndexHeading, outline
from okf_io.links import parse_destination, resolve_path

OKF_IO_FIXTURES = Path(__file__).resolve().parents[3] / "okf-io" / "tests" / "fixtures"
FIXTURE_BUNDLES = [
    OKF_IO_FIXTURES / "bundles" / "acme_retail",
    OKF_IO_FIXTURES / "bundles" / "ga4",
    OKF_IO_FIXTURES / "edge",
    OKF_IO_FIXTURES / "nonconformant",
]

ROOT_INDEX = (
    "---\nokf_version: 0.2\n---\n\n# Index\n\n## Concepts\n\n### Docs\n\n"
    "- [P](/docs/p.md) — p\n- [Yaml](docs/yaml.md)\n- [NFC](/docs/café.md)\n"
    "- [Ref](/work/feature-a/references/01-design.md)\n- [gone](/docs/gone.md)\n"
    "- [clone](/repositories/clone/references/git/README.md)\n- [asset](/pic.png)\n- [ext](https://example.com)\n\n"
    "## Work\n\n- [Feature](/work/feature-a.md)\n\n# Subdirectories\n\n- [x](docs/)\n"
)

GENERATED: dict[str, str] = {
    "index.md": ROOT_INDEX,
    "docs/p.md": (
        "---\ntype: Explanation\ntitle: P\n---\n[w](/work/feature-a.md) "
        "[r](/work/feature-a/references/01-design.md) [x](/docs/missing.md) "
        "[c](/repositories/clone/references/git/README.md) [e](https://example.com)\n"
    ),
    "docs/yaml.md": "---\ntitle: [unclosed\n---\n\nbody survives [p](p.md)\n",
    "docs/unterminated.md": "---\ntitle: never closed\n\n[p](p.md)\n",
    "docs/café.md": "---\ntitle: NFC\n---\n[p](p.md)\n",
    "work/feature-a.md": "---\ntype: Feature\ntitle: A\n---\n[d](/work/feature-a/references/01-design.md)\n",
    "work/feature-a/references/01-design.md": "---\ntitle: Design\n---\n[back](/work/feature-a.md) [p](/docs/p.md)\n",
    "pic.png": "png",
    "repositories/clone/references/git/README.md": "clone content [p](/docs/p.md)\n",
}


def new_layout(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=date(2026, 10, 2), topic="Wiki")).layout


def write(layout: WorkspaceLayout, rel: str, text: str) -> Path:
    path = layout.bundle_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_bytes(layout: WorkspaceLayout, rel: str, data: bytes) -> Path:
    path = layout.bundle_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def populate_fixture(layout: WorkspaceLayout, source: Path) -> None:
    shutil.rmtree(layout.bundle_dir)
    shutil.copytree(source, layout.bundle_dir)


def populate_generated(layout: WorkspaceLayout) -> None:
    for rel, text in GENERATED.items():
        write(layout, rel, text)
    write_bytes(layout, "docs/bad.md", b"---\ntitle: x\n---\n\xff\xfe bad")


def concept_ids(layout: WorkspaceLayout) -> list[str]:
    return sorted(load_workspace_bundle(layout).concepts)


def oracle_page_read(layout: WorkspaceLayout, page_id: str) -> PageRead:
    bundle = load_workspace_bundle(layout)
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


def _oracle_tree_page(bundle: Bundle, entry: IndexEntry) -> TreePage | None:
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


def _oracle_tree_node(
    bundle: Bundle, heading: IndexHeading, generated: bool, children: tuple[TreeNode, ...]
) -> TreeNode:
    pages = tuple(page for entry in heading.entries if (page := _oracle_tree_page(bundle, entry)) is not None)
    return TreeNode(heading.text, heading.level, generated, pages, children)


def oracle_wiki_tree(layout: WorkspaceLayout) -> WikiTree:
    bundle = load_workspace_bundle(layout)
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
            _oracle_tree_node(bundle, sub, ownership[sub.text] == "generated" if sub.text in ownership else flag, ())
            for sub in subsections
        )
        sections.append(_oracle_tree_node(bundle, section, flag, children))
    return WikiTree(tuple(sections))


def age_bundle(layout: WorkspaceLayout) -> None:
    """Place count-test members outside the read index's two-second racy window."""
    aged = time.time_ns() - 4_000_000_000
    for path in layout.bundle_dir.rglob("*"):
        if path.is_file():
            os.utime(path, ns=(aged, aged))
