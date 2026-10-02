from __future__ import annotations

import pytest
from helpers import BUNDLES, EDGE
from okf_io import Heading, _md, build_link_graph, document_headings, document_links, load_bundle, parse


@pytest.mark.parametrize("root", [BUNDLES / "acme_retail", BUNDLES / "ga4", EDGE], ids=lambda p: p.name)
def test_document_links_reassemble_build(root) -> None:
    bundle = load_bundle(root)
    graph = build_link_graph(bundle)
    for cid, doc in bundle.concepts.items():
        assert document_links(doc, source_id=cid) == graph.out.get(cid, ())


def test_document_links_resolve_relative_and_absolute() -> None:
    doc = parse("---\ntitle: t\n---\n[a](../b.md#x) [c](/d.md) [e](https://x) ![i](p.png) [f](#only)\n")
    links = document_links(doc, source_id="dir/src")
    assert [(link.target, link.fragment, link.external, link.image) for link in links] == [
        ("b.md", "x", False, False),
        ("d.md", None, False, False),
        (None, None, True, False),
        ("dir/p.png", None, False, True),
    ]
    assert {link.line for link in links} == {4}


def test_document_headings_are_file_relative() -> None:
    doc = parse("---\ntitle: t\n---\n# One\n\n> ## Quoted\n")
    assert document_headings(doc) == (Heading(1, "One", 4, False), Heading(2, "Quoted", 6, True))


def test_headings_match_parse_body_with_offset() -> None:
    doc = parse("---\na: 1\n---\n\n## X\ntext\n### Y\n")
    expected = tuple(
        Heading(h.level, h.text, h.line + doc.body_line_offset, h.quoted) for h in _md.parse_body(doc.body).headings
    )
    assert document_headings(doc) == expected
