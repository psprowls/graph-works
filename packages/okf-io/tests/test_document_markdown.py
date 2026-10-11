from dataclasses import replace

import pytest
from markdown_it import MarkdownIt
from okf_io import Document, ParsedMarkdown, _md, build_link_graph, load_bundle

TEXT = "---\ntitle: T\n---\n# Head\n\n[a](other.md)\n\n[ref]: target.md\n"


@pytest.fixture
def parses(monkeypatch):
    calls = []
    real = MarkdownIt.parse

    # Other tests restore an instance-bound method, which would mask this
    # class-level spy. Remove that shadow only for this fixture's duration.
    if "parse" in vars(_md._MD):
        monkeypatch.delattr(_md._MD, "parse")

    def counted(self, src, env=None):
        calls.append(src)
        return real(self, src, env)

    monkeypatch.setattr(MarkdownIt, "parse", counted)
    _md.parse_body.cache_clear()
    return calls


def test_markdown_parses_the_body_once(parses):
    doc = Document.parse(TEXT)
    first = doc.markdown()
    assert isinstance(first, ParsedMarkdown)
    assert doc.markdown() is first
    assert doc.body_index() is doc.body_index()
    assert len(parses) == 1


def test_markdown_carries_reference_definitions():
    refs = Document.parse(TEXT).markdown().references
    assert refs["REF"]["href"] == "target.md"


def test_set_body_invalidates_the_memo(parses):
    doc = Document.parse(TEXT)
    before = doc.markdown()
    before_index = doc.body_index()
    doc.set_body("# Other\n")
    after = doc.markdown()
    assert after is not before
    assert [t.type for t in after.tokens][:1] == ["heading_open"]
    assert doc.body_index() is not before_index
    assert [heading.text for heading in doc.body_index().headings] == ["Other"]
    assert len(parses) == 2


def test_direct_body_assignment_invalidates_the_memo():
    doc = Document.parse(TEXT)
    before = doc.markdown()
    before_index = doc.body_index()
    doc.body = "plain\n"
    assert doc.markdown() is not before
    assert doc.body_index() is not before_index
    assert doc.body_index().headings == ()


def test_a_shallow_clone_shares_the_memo_until_its_body_changes():
    doc = Document.parse(TEXT)
    memo = doc.markdown()
    index = doc.body_index()
    clone = replace(doc)
    assert clone.markdown() is memo
    assert clone.body_index() is index
    clone.set_body("x\n")
    assert clone.markdown() is not memo
    assert clone.body_index() is not index
    assert doc.markdown() is memo
    assert doc.body_index() is index


def test_body_index_equals_parse_body():
    doc = Document.parse(TEXT)
    assert doc.body_index() == _md.parse_body(doc.body)


def test_link_graph_build_parses_each_concept_once(tmp_path, parses):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.md").write_text(f"---\ntitle: {name}\n---\n[x](a.md)\n", encoding="utf-8", newline="")
    bundle = load_bundle(tmp_path)
    build_link_graph(bundle)
    build_link_graph(bundle)
    assert len(parses) == 3


def test_frontmatter_citations_and_graph_share_each_documents_parse(tmp_path, parses):
    text = "---\ntitle: Legacy\n---\n# Citations\n\n- [Target](target.md)\n"
    for name in ("a", "b"):
        (tmp_path / f"{name}.md").write_text(text, encoding="utf-8", newline="")
    bundle = load_bundle(tmp_path)
    for doc in bundle.concepts.values():
        assert doc.fm.sources[0].resource == "target.md"
        assert "sources" in doc.fm.fallbacks
    assert len(parses) == 2
    graph = build_link_graph(bundle)
    assert [link.target for link in graph.links] == ["target.md", "target.md"]
    assert len(parses) == 2
