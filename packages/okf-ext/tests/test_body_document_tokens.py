"""Document readers preserve string-reader semantics and share one body parse."""

import pytest
from ext_helpers import FIXTURES, linked_copy
from markdown_it import MarkdownIt
from okf_ext import body as body_mod
from okf_ext.body import document_prose_lines, document_sections, document_wikilinks, prose_lines, sections, wikilinks
from okf_ext.moves import plan_move_many
from okf_ext.moves.locate import (
    destinations,
    document_destinations,
    document_reference_definitions,
    reference_definitions,
)
from okf_io import Document, load_bundle

BODIES = sorted(FIXTURES.rglob("*.md"))


@pytest.mark.parametrize("path", BODIES, ids=lambda path: str(path.relative_to(FIXTURES)))
def test_document_variants_equal_string_variants(path):
    doc = Document.load(path)
    assert document_sections(doc) == sections(doc.body)
    assert document_prose_lines(doc) == prose_lines(doc.body)
    assert document_wikilinks(doc) == wikilinks(doc.body)
    assert document_destinations(doc) == destinations(doc.body)
    assert document_reference_definitions(doc) == reference_definitions(doc.body)


@pytest.mark.parametrize("path", BODIES, ids=lambda path: str(path.relative_to(FIXTURES)))
def test_okf_io_and_okf_ext_parsers_agree(path):
    """okf-ext's private parser and okf-io's must stay one configuration."""
    doc = Document.load(path)
    ours = [(token.type, token.map, token.tag, token.content) for token in body_mod._MD.parse(doc.body)]
    theirs = [(token.type, token.map, token.tag, token.content) for token in doc.markdown().tokens]
    assert ours == theirs


def test_plan_move_many_parses_each_member_at_most_once(tmp_path, monkeypatch):
    root = linked_copy(tmp_path)
    bundle = load_bundle(root)
    body_mod._skeleton.cache_clear()
    calls: list[str] = []
    real = MarkdownIt.parse

    def counted(self, src, env=None):
        calls.append(src)
        return real(self, src, env)

    monkeypatch.setattr(MarkdownIt, "parse", counted)
    member = sorted(bundle.concepts)[0]
    plan_move_many(bundle, {f"{member}.md": f"moved/{member}.md"})
    documents = (*bundle.concepts.values(), *bundle.indexes.values(), *bundle.logs.values())
    assert len(calls) <= len(documents)
    assert set(calls) <= {doc.body for doc in documents} | {""}
