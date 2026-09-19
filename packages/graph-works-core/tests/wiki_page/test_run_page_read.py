"""`run_page_read`: one wiki page with its computed link neighbourhood."""

from __future__ import annotations

from datetime import date

from graph_works_core import apply_init, plan_init
from graph_works_core.wiki_page import PageLink, run_page_read

TODAY = date(2026, 9, 18)


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Wiki")).layout
    concepts = layout.bundle_dir / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\n\nSee [B](b.md), [gone](missing.md) and [x](https://example.com).\n",
        encoding="utf-8",
    )
    (concepts / "b.md").write_text("---\ntype: Concept\ntitle: B\n---\n\nBack to [A](a.md).\n", encoding="utf-8")
    return layout


def test_a_page_reads_with_outlinks_backlinks_and_broken(tmp_path):
    layout = _workspace(tmp_path)
    result = run_page_read(layout, "concepts/a")

    assert result.refusal is None
    assert result.id == "concepts/a"
    assert result.frontmatter["title"] == "A"
    assert "See [B](b.md)" in result.body
    assert [link.raw for link in result.outlinks] == ["b.md", "https://example.com", "missing.md"]
    assert result.backlinks == ("concepts/b",)
    assert [link.raw for link in result.broken] == ["missing.md"]
    assert all(isinstance(link, PageLink) for link in result.outlinks)
    assert result.parse_error is None


def test_an_unknown_page_is_a_refusal(tmp_path):
    layout = _workspace(tmp_path)
    result = run_page_read(layout, "concepts/nope")
    assert result.refusal == "unknown-page"
    assert result.outlinks == () and result.backlinks == () and result.body == ""


def test_reference_pages_are_readable(tmp_path):
    layout = _workspace(tmp_path)
    refs = layout.bundle_dir / "work" / "feature-a" / "references"
    refs.mkdir(parents=True)
    (refs / "01-design.md").write_text("---\ntitle: D\n---\n\n[A](/concepts/a.md)\n", encoding="utf-8")
    assert run_page_read(layout, "work/feature-a/references/01-design").refusal is None
    assert "work/feature-a/references/01-design" in run_page_read(layout, "concepts/a").backlinks
