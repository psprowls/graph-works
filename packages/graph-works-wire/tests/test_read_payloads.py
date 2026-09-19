"""Key-for-key shapes of the three read projections serve introduces."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import MappingProxyType

from graph_works_core.util.commands import InvalidLogSection, LogEntryRead, LogRead
from graph_works_core.wiki_page.commands import PageLink, PageRead
from graph_works_core.work.commands import ItemRead, ItemSource
from graph_works_wire import util, wiki, work


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
