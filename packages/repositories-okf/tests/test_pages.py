from __future__ import annotations

from okf_io import parse
from repositories_okf.pages import new_page_text


def test_new_page_text_writes_frontmatter_and_body_that_reparse() -> None:
    text = new_page_text(
        {
            "type": "RepositoryChangelog",
            "title": "demo changelog",
            "range": {"commits": 3, "files_changed": 2},
            "previous": None,
            "rewritten": False,
        },
        "## Entries\n\n- one\n",
    )
    document = parse(text)
    assert document.parse_error is None
    data = document.fm_data(dates="iso")
    assert data["type"] == "RepositoryChangelog"
    assert data["range"] == {"commits": 3, "files_changed": 2}
    assert data["previous"] is None and data["rewritten"] is False
    assert document.body.lstrip("\n") == "## Entries\n\n- one\n"


def test_a_string_that_looks_like_a_timestamp_stays_a_string() -> None:
    document = parse(new_page_text({"type": "X", "fetched_at": "2026-09-29T20:40:00Z"}, "body\n"))
    assert document.fm_raw["fetched_at"] == "2026-09-29T20:40:00Z"
