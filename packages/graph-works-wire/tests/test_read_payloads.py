"""Key-for-key shapes of the three read projections serve introduces."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import MappingProxyType

from graph_works_core.code_read import CodeExcerpt
from graph_works_core.util.commands import InvalidLogSection, LogEntryRead, LogRead
from graph_works_core.wiki_page.citations import Citation, CitationCandidate, WikiCitations
from graph_works_core.wiki_page.commands import PageLink, PageRead
from graph_works_core.work.commands import ItemRead, ItemSource
from graph_works_wire import code, util, wiki, work


def test_item_payload() -> None:
    result = ItemRead(
        path="work/a",
        frontmatter=MappingProxyType({"title": "A", "opened": "2026-09-01"}),
        body="b",
        sources=(ItemSource("design", "/work/a/references/01-design.md", None),),
        references=("work/a/references/01-design.md",),
        parse_error=None,
        coercion_failures=("effort",),
        refusal=None,
        detail=None,
    )
    assert work.item_payload(result) == {
        "path": "work/a",
        "frontmatter": {"title": "A", "opened": "2026-09-01"},
        "body": "b",
        "sources": [{"id": "design", "resource": "/work/a/references/01-design.md", "title": None}],
        "references": ["work/a/references/01-design.md"],
        "parse_error": None,
        "coercion_failures": ["effort"],
        "refusal": None,
        "detail": None,
    }


def test_page_payload() -> None:
    link = PageLink("concepts/a", "b.md", "concepts/b.md", False, 3)
    result = PageRead("concepts/a", MappingProxyType({"title": "A"}), "b", (link,), ("concepts/b",), (), None, None)
    assert wiki.page_payload(result) == {
        "id": "concepts/a",
        "frontmatter": {"title": "A"},
        "body": "b",
        "outlinks": [{"source": "concepts/a", "raw": "b.md", "target": "concepts/b.md", "external": False, "line": 3}],
        "backlinks": ["concepts/b"],
        "broken": [],
        "parse_error": None,
        "refusal": None,
    }


def test_citations_payload() -> None:
    result = WikiCitations(
        "concepts/a",
        (
            Citation("a.py:3", 2, "gw", "src/a.py", 3, 3, "resolved", ()),
            Citation(
                "b.py:1-4",
                5,
                None,
                None,
                1,
                4,
                "ambiguous",
                (CitationCandidate("gw", "x/b.py"), CitationCandidate("ui", "b.py")),
            ),
        ),
        None,
    )
    assert wiki.citations_payload(result) == {
        "id": "concepts/a",
        "citations": [
            {
                "raw": "a.py:3",
                "line": 2,
                "repo": "gw",
                "path": "src/a.py",
                "start": 3,
                "end": 3,
                "status": "resolved",
                "candidates": [],
            },
            {
                "raw": "b.py:1-4",
                "line": 5,
                "repo": None,
                "path": None,
                "start": 1,
                "end": 4,
                "status": "ambiguous",
                "candidates": [{"repo": "gw", "path": "x/b.py"}, {"repo": "ui", "path": "b.py"}],
            },
        ],
        "refusal": None,
    }


def test_excerpt_payload() -> None:
    result = CodeExcerpt("gw", "a.py", 3, 3, 1, 8, 10, "python", ("x", "y"), None)
    assert code.excerpt_payload(result) == {
        "repo": "gw",
        "path": "a.py",
        "start": 3,
        "end": 3,
        "first": 1,
        "last": 8,
        "total_lines": 10,
        "language": "python",
        "lines": ["x", "y"],
        "refusal": None,
    }


def test_log_read_payload() -> None:
    result = LogRead(
        path=Path("/ws/okf/log.md"),
        exists=True,
        entries=(LogEntryRead(date(2026, 9, 17), 4, 4, "scan", "- **scan** x"),),
        invalid_sections=(InvalidLogSection(9, "nope"),),
    )
    assert util.log_read_payload(result) == {
        "path": "/ws/okf/log.md",
        "exists": True,
        "entries": [{"date": "2026-09-17", "line": 4, "end": 4, "op": "scan", "text": "- **scan** x"}],
        "invalid_sections": [{"line": 9, "heading": "nope"}],
    }
