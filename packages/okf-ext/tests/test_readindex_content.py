"""Verified page content served from stored rows (item D-003, epic D-005)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from ext_helpers import ACME_RETAIL
from okf_ext.readindex import PageContent as ExportedPageContent
from okf_ext.readindex import StoredContent as ExportedStoredContent
from okf_ext.readindex import open_index, read, reconcile
from okf_ext.readindex.content import StoredContent, body_at, content_of, read_content, serve
from okf_ext.readindex.model import PageContent
from okf_io import Document, Unreadable
from readindex_helpers import tree

OKF_IO_FIXTURES = ACME_RETAIL.parents[1]


def _fixture_texts() -> list[pytest.ParameterSet]:
    cases = []
    for path in sorted(OKF_IO_FIXTURES.rglob("*.md")):
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        cases.append(pytest.param(text, id=str(path.relative_to(OKF_IO_FIXTURES))))
    return cases


GENERATED = {
    "crlf": "---\r\ntitle: A\r\n---\r\n\r\nbody\r\nmore\r\n",
    "lone-cr": "---\rtitle: A\r---\r\rbody\rmore\r",
    "bom-frontmatter": "﻿---\ntitle: A\n---\nbody\n",
    "bom-no-frontmatter": "﻿just body\n",
    "bom-only": "﻿",
    "unterminated-newline": "---\ntitle: A\nbody\n",
    "unterminated-no-newline": "---\ntitle: A\nbody",
    "ends-at-close-no-newline": "---\ntitle: A\n---",
    "ends-at-close-newline": "---\ntitle: A\n---\n",
    "ends-at-dots": "---\ntitle: A\n...",
    "empty-block-no-newline": "---\n---",
    "trailing-gap-fragment": "---\ntitle: A\n---\n\n   ",
    "multi-line-gap": "---\ntitle: A\n---\n\n\n  \n\nbody\n",
    "body-is-a-delimiter": "---\ntitle: A\n---\n---",
    "body-fragment": "---\ntitle: A\n---\nbody",
    "vt-in-scalar": '---\ntitle: "a\x0b---"\n---\nbody\n',
    "u2028-in-scalar": '---\ntitle: "a\u2028---"\n---\nbody\u2028more\n',
    "no-frontmatter": "# Title\n\nbody\n",
    "empty": "",
    "yaml-error": "---\ntitle: [unclosed\n---\nbody\n",
    "not-a-mapping": "---\n- a\n---\nbody\n",
}


@pytest.mark.parametrize("text", _fixture_texts())
def test_body_at_equals_document_body_over_okf_io_fixtures(text: str) -> None:
    doc = Document.parse(text)
    assert body_at(text, doc.body_line_offset, doc.parse_error) == doc.body


@pytest.mark.parametrize("text", list(GENERATED.values()), ids=list(GENERATED))
def test_body_at_equals_document_body_over_generated_cases(text: str) -> None:
    doc = Document.parse(text)
    assert body_at(text, doc.body_line_offset, doc.parse_error) == doc.body


@pytest.mark.parametrize(
    "name",
    [
        "ends-at-close-no-newline",
        "ends-at-dots",
        "empty-block-no-newline",
        "trailing-gap-fragment",
        "unterminated-no-newline",
    ],
)
def test_body_at_fragment_tail_cases(name: str) -> None:
    text = GENERATED[name]
    doc = Document.parse(text)
    assert doc.body == ""
    assert body_at(text, doc.body_line_offset, doc.parse_error) == ""


def test_fixture_corpus_is_not_empty() -> None:
    assert len(_fixture_texts()) > 20
    assert any("crlf" in p.id for p in _fixture_texts())
    assert any("bom" in p.id for p in _fixture_texts())


def test_fixtures_root_is_okf_io_fixtures() -> None:
    assert OKF_IO_FIXTURES.name == "fixtures" and (OKF_IO_FIXTURES / "edge").is_dir()
    assert isinstance(OKF_IO_FIXTURES, Path)


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    calls: list[str] = []
    real = Document.__dict__["parse"]

    def parse(cls: type[Document], text: str, *, path: Path | None = None) -> Document:
        calls.append(text)
        return real.__func__(cls, text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    yield calls


def _projection(member_id: str, doc: Document) -> PageContent:
    return PageContent(member_id, doc.fm_data(dates="iso"), doc.body, doc.parse_error, doc.fm.coercion_failures)


def _stored(data: bytes, doc: Document, *, exact: bool = True) -> StoredContent:
    return StoredContent(
        hashlib.sha256(data).hexdigest(),
        doc.fm_data(dates="iso"),
        exact,
        doc.body_line_offset,
        doc.parse_error,
        doc.fm.coercion_failures,
    )


@pytest.mark.parametrize("text", _fixture_texts() + [pytest.param(t, id=k) for k, t in GENERATED.items()])
def test_content_of_equals_the_document_projection(text: str) -> None:
    doc = Document.parse(text)
    assert content_of("a.md", doc) == _projection("a.md", doc)


@pytest.mark.parametrize("text", list(GENERATED.values()), ids=list(GENERATED))
def test_serve_verified_exact_row_parses_nothing(text: str, parses: list[str]) -> None:
    data = text.encode("utf-8")
    doc = Document.parse(text)
    stored = _stored(data, doc)
    parses.clear()
    assert serve("a.md", data, stored) == _projection("a.md", doc)
    assert parses == []


def test_serve_hash_mismatch_parses_once_and_returns_new_bytes(parses: list[str]) -> None:
    old = b"---\ntitle: Old\n---\nold body\n"
    new = b"---\ntitle: New\n---\n\nnew body\n"
    stored = _stored(old, Document.parse(old.decode()))
    parses.clear()
    result = serve("a.md", new, stored)
    assert parses == [new.decode()]
    assert result == _projection("a.md", Document.parse(new.decode()))


def test_serve_without_stored_row_parses_once(parses: list[str]) -> None:
    data = b"---\ntitle: A\n---\nbody\n"
    assert serve("a.md", data, None) == _projection("a.md", Document.parse(data.decode()))
    assert len(parses) == 2  # one inside serve, one for the expectation


def test_serve_inexact_row_parses_once(parses: list[str]) -> None:
    data = b"---\ntitle: A\nratio: .nan\n---\nbody\n"
    doc = Document.parse(data.decode())
    stored = _stored(data, doc, exact=False)
    parses.clear()
    result = serve("a.md", data, stored)
    assert len(parses) == 1
    assert isinstance(result, PageContent)
    assert result.frontmatter["ratio"] != result.frontmatter["ratio"]  # NaN, not a repr string
    assert result.body == "body\n"


def test_serve_non_utf8_is_unreadable(parses: list[str]) -> None:
    result = serve("a.md", b"\xff\xfe bad", None)
    assert isinstance(result, Unreadable)
    assert result.reason.startswith("not valid UTF-8: ")
    assert parses == []


def test_read_content_missing_file_is_unreadable(tmp_path: Path) -> None:
    result = read_content(tmp_path, "docs/gone.md", None)
    assert isinstance(result, Unreadable)
    assert result.reason.startswith("could not be read: ")


def test_read_content_reads_nested_ids(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_bytes(b"---\ntitle: A\n---\nbody\n")
    result = read_content(tmp_path, "docs/a.md", None)
    assert isinstance(result, PageContent)
    assert (result.id, result.body, dict(result.frontmatter)) == ("docs/a.md", "body\n", {"title": "A"})


def test_exports() -> None:
    assert ExportedPageContent is PageContent
    assert ExportedStoredContent is StoredContent


def test_stored_content_round_trips_every_markdown_row(tmp_path: Path, parses: list[str]) -> None:
    files = {
        "a.md": "---\ntitle: A\ntags: [x]\n---\n\nbody\n",
        "crlf.md": "---\r\ntitle: C\r\n---\r\nbody\r\n",
        "bad.md": "---\ntitle: [unclosed\n---\nbody\n",
        "coercion.md": "---\ntitle: A\ngenerated: invalid\n---\nbody\n",
        "index.md": "# I\n",
        "sub/log.md": "## 2026-10-08\n",
    }
    root = tree(tmp_path / "b", files)
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        with read(index) as view:
            parses.clear()
            for member_id, text in files.items():
                stored = view.stored_content(member_id)
                assert stored is not None
                assert serve(member_id, text.encode(), stored) == content_of(member_id, Document.parse(text))
    assert len(parses) == len(files)  # only the expectations parsed


def test_stored_content_is_none_for_non_markdown_unreadable_and_absent(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: A\n---\n", "p.png": "x"})
    (root / "bad.md").write_bytes(b"\xff\xfe bad")
    with open_index(tmp_path / "i.db", root, ignore=("skip/**",)) as index:
        (root / "skip").mkdir()
        (root / "skip" / "s.md").write_bytes(b"---\ntitle: S\n---\n")
        reconcile(index)
        with read(index) as view:
            ignored = view.member("skip/s.md")
            assert ignored is not None and ignored.kind == "ignored"
            for member_id in ("p.png", "bad.md", "skip/s.md", "absent.md"):
                assert view.stored_content(member_id) is None
            assert view.stored_content("a.md") is not None


def test_stored_content_uses_exact_raw_id(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"caf\u00e9.md": "---\ntitle: A\n---\nbody\n"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        with read(index) as view:
            assert view.member("cafe\u0301.md") is not None
            assert view.stored_content("cafe\u0301.md") is None
            assert view.stored_content("caf\u00e9.md") is not None
