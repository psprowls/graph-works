"""`run_item_read`: one work item, whole, read-only; unknown targets are data."""

from __future__ import annotations

from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.read_session import BundleSession, materialize, open_read_session
from graph_works_core.read_session.index_backend import IndexSession
from graph_works_core.work import ItemRead, ItemSource, run_item_read
from graph_works_core.work import commands as work
from okf_io import Document

TODAY = date(2026, 9, 18)
PATH = "work/feature-a"
ITEM = """---
type: Feature
title: A
description: d
status: stable
sources:
  - id: design
    resource: /work/feature-a/references/01-design.md
    title: 'Design: A'
work_status: open
phase: plan
effort: medium
opened: 2026-09-01
updated: 2026-09-02
affects:
- packages/a
---

## Summary
d
"""


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, text=ITEM):
    (layout.bundle_dir / f"{PATH}.md").write_text(text, encoding="utf-8")
    refs = layout.bundle_dir / PATH / "references"
    refs.mkdir(parents=True)
    (refs / "01-design.md").write_text("# D\n", encoding="utf-8")
    (refs / "orca-placement").mkdir()
    (refs / "orca-placement" / "k.json").write_text("{}", encoding="utf-8")


def test_an_item_reads_whole(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout)
    before = (layout.bundle_dir / f"{PATH}.md").read_bytes()

    result = work.run_item_read(layout, PATH)

    assert result.refusal is None and result.detail is None
    assert result.path == PATH
    assert result.frontmatter["title"] == "A"
    assert result.frontmatter["opened"] == "2026-09-01"
    assert result.body.startswith("## Summary")
    assert result.sources == (work.ItemSource("design", "/work/feature-a/references/01-design.md", "Design: A"),)
    assert result.references == (
        "work/feature-a/references/01-design.md",
        "work/feature-a/references/orca-placement/k.json",
    )
    assert result.parse_error is None and result.coercion_failures == ()
    assert (layout.bundle_dir / f"{PATH}.md").read_bytes() == before


def test_an_unknown_item_is_a_refusal_not_a_raise(tmp_path):
    layout = _workspace(tmp_path)
    result = work.run_item_read(layout, "work/nope")
    assert result.refusal == "unknown-item"
    assert result.frontmatter == {} and result.body == "" and result.references == ()


def test_a_non_work_concept_is_unknown(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "concepts").mkdir()
    (layout.bundle_dir / "concepts" / "x.md").write_text("---\ntype: Concept\ntitle: X\n---\n", encoding="utf-8")
    assert work.run_item_read(layout, "concepts/x").refusal == "unknown-item"


def test_an_item_with_no_references_dir_has_none(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / f"{PATH}.md").write_text(ITEM, encoding="utf-8")
    assert work.run_item_read(layout, PATH).references == ()


def test_malformed_frontmatter_still_reads(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout, ITEM.replace("title: A", "title: [unclosed"))
    result = work.run_item_read(layout, PATH)
    assert result.refusal is None
    assert result.parse_error is not None and ":" in result.parse_error


def test_item_read_is_available_from_work_facade(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout)

    result = run_item_read(layout, PATH)

    assert isinstance(result, ItemRead)
    assert result.sources == (ItemSource("design", "/work/feature-a/references/01-design.md", "Design: A"),)


def test_item_deleted_after_reconcile_is_unreadable(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout)
    with open_read_session(layout) as session:
        (layout.bundle_dir / f"{PATH}.md").unlink()
        result = work.run_item_read(layout, PATH, session=session)
    assert result.refusal == "unreadable"
    assert result.detail
    assert result.body == ""


def test_item_edited_after_reconcile_returns_one_version(tmp_path, monkeypatch):
    layout = _workspace(tmp_path)
    _write(layout)
    with open_read_session(layout) as session:
        (layout.bundle_dir / f"{PATH}.md").write_text(
            ITEM.replace("title: A", "title: Edited")
            .replace("## Summary\nd", "## Summary\nedited body")
            .replace("id: design", "id: changed")
            .replace("title: 'Design: A'", "title: 2026-09-01"),
            encoding="utf-8",
            newline="\n",
        )
        seen = []
        real = Document.parse

        def parse(cls, text, *, path=None):
            seen.append(path)
            return real(text, path=path)

        monkeypatch.setattr(Document, "parse", classmethod(parse))
        result = work.run_item_read(layout, PATH, session=session)
    assert len(seen) == 1
    assert result.refusal is None
    assert result.frontmatter["title"] == "Edited"
    assert "edited body" in result.body
    assert result.sources[0].id == "changed"
    assert result.sources[0].title is None
    assert "sources.0.title" in result.coercion_failures


def test_unreadable_item_page_is_refused_with_detail(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / f"{PATH}.md").write_bytes(b"\xff\xfe nope")
    result = work.run_item_read(layout, PATH)
    assert result.refusal == "unreadable"
    assert result.detail


def test_warm_item_read_parses_no_files(tmp_path, monkeypatch):
    from okf_io import Document

    layout = _workspace(tmp_path)
    _write(layout)
    work.run_status(layout)
    seen = []
    real = Document.parse

    def parse(cls, text, *, path=None):
        seen.append(path)
        return real(text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    result = work.run_item_read(layout, PATH)
    assert result.frontmatter["title"] == "A"
    assert seen == []


@pytest.mark.parametrize("backend", ["index", "snapshot", "bundle"])
def test_session_backends_add_no_parse_and_keep_content_coherent(tmp_path, monkeypatch, backend):
    layout = _workspace(tmp_path)
    _write(layout)
    with open_read_session(layout) as indexed:
        session = materialize(indexed) if backend == "snapshot" else indexed
        if backend == "bundle":
            session = BundleSession(layout.bundle_dir, fallback=None)
        expected = work.run_item_read(layout, PATH, session=session)
        if backend == "index":
            seen = []
            real = Document.parse

            def parse(cls, text, *, path=None):
                seen.append(path)
                return real(text, path=path)

            monkeypatch.setattr(Document, "parse", classmethod(parse))
            assert work.run_item_read(layout, PATH, session=session) == expected
            assert seen == []
    if backend == "index":
        return
    if backend == "bundle":
        (layout.bundle_dir / f"{PATH}.md").write_text(ITEM.replace("title: A", "title: Later"), encoding="utf-8")
    seen = []
    real = Document.parse

    def parse(cls, text, *, path=None):
        seen.append(path)
        return real(text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    assert work.run_item_read(layout, PATH, session=session) == expected
    assert seen == []


def test_known_item_without_content_is_unreadable(tmp_path, monkeypatch):
    layout = _workspace(tmp_path)
    _write(layout)
    with open_read_session(layout) as session:
        monkeypatch.setattr(IndexSession, "content", lambda self, member_id: None)
        result = work.run_item_read(layout, PATH, session=session)
    assert result.refusal == "unreadable" and result.detail


@pytest.mark.parametrize(
    "sources",
    [
        "sources: {id: design, resource: /d, title: D}",
        "sources: []",
        "sources: [bad, {id: 2026-09-01, resource: 42, title: true}]",
        "sources: [{id: 2026-09-01T12:00:00Z, resource: 2026-09-01, title: 2026-09-01T12:00:00Z}]",
        "sources: [{id: '2026-09-01', resource: '2026-09-01T12:00:00Z', title: '2026-09-01'}]",
        "sources: [{resource: /d}, {resource: /d}]",
        "sources: [{id: 1, resource: false, title: 42}]",
        "sources: [{id: 2026-09-01, resource: 2026-09-01T12:00:00Z, title: 2026-09-01}]",
        "sources: null",
        "sources: bad",
        "sources: {id: 2026-09-01, resource: /d}",
    ],
)
def test_sources_retain_original_typed_projection(tmp_path, sources):
    layout = _workspace(tmp_path)
    start = ITEM.index("sources:")
    end = ITEM.index("work_status:")
    text = ITEM[:start] + sources + "\n" + ITEM[end:]
    _write(layout, text)
    original = Document.parse(text)
    result = work.run_item_read(layout, PATH)
    assert result.sources == tuple(ItemSource(s.id, s.resource, s.title) for s in original.fm.sources)
    assert result.coercion_failures == tuple(sorted(original.fm.coercion_failures))


def test_body_citations_are_sources(tmp_path):
    layout = _workspace(tmp_path)
    start, end = ITEM.index("sources:"), ITEM.index("work_status:")
    text = ITEM[:start] + ITEM[end:] + "\n# Citations\n- [Legacy](https://example.com/legacy)\n"
    _write(layout, text)
    original = Document.parse(text)
    result = work.run_item_read(layout, PATH)
    assert original.fm.sources
    assert result.sources == tuple(ItemSource(s.id, s.resource, s.title) for s in original.fm.sources)


@pytest.mark.parametrize("edited", ["work/feature-a.md", "work/feature-b.md"])
def test_one_edit_before_new_read_parses_only_changed_file(tmp_path, monkeypatch, edited):
    layout = _workspace(tmp_path)
    _write(layout)
    other = layout.bundle_dir / "work/feature-b.md"
    other.write_text(ITEM.replace("title: A", "title: B"), encoding="utf-8", newline="\n")
    work.run_item_read(layout, PATH)
    target = layout.bundle_dir / edited
    target.write_text(ITEM.replace("title: A", "title: Changed") + "\nChanged body\n", encoding="utf-8", newline="\n")
    seen = []
    real = Document.parse

    def parse(cls, text, *, path=None):
        seen.append(path)
        return real(text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    result = work.run_item_read(layout, PATH)
    assert seen == [target]
    assert result.frontmatter["title"] == ("Changed" if edited == f"{PATH}.md" else "A")


def test_frontmatter_and_source_input_are_unchanged(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout)
    with open_read_session(layout) as session:
        content = session.content(f"{PATH}.md")
        before = repr(content.frontmatter)
        result = work.run_item_read(layout, PATH, session=session)
        assert repr(content.frontmatter) == before
    with pytest.raises(TypeError):
        result.frontmatter["title"] = "Changed"
