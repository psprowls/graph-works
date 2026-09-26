"""`top_level_items`: what counts as one key claim."""

from __future__ import annotations

from okf_ext.body import top_level_items


def test_top_level_items_numbered_with_nested_bullets_are_one_item_each() -> None:
    text = "1. First claim.\n   - detail a\n   - detail b\n2. Second claim.\n"
    items = top_level_items(text)
    assert len(items) == 2
    assert items[0].startswith("1. First claim.")
    assert "detail b" in items[0]
    assert items[1] == "2. Second claim."


def test_top_level_items_bold_lead_bullets() -> None:
    text = "- **Lead one.** Body.\n- **Lead two.** Body.\n- **Lead three.** Body.\n"
    assert len(top_level_items(text)) == 3


def test_top_level_items_mixed_lists_continue_counting() -> None:
    text = "1. One.\n2. Two.\n\nSome prose.\n\n- Three.\n- Four.\n"
    assert [item.split()[-1] for item in top_level_items(text)] == ["One.", "Two.", "Three.", "Four."]


def test_top_level_items_ignore_a_code_block() -> None:
    text = "- Real.\n\n```\n- not an item\n```\n"
    assert top_level_items(text) == ("- Real.",)


def test_top_level_items_ignore_a_list_in_a_blockquote() -> None:
    assert top_level_items("> - quoted\n> - quoted\n") == ()


def test_top_level_items_paragraph_only_and_empty() -> None:
    assert top_level_items("Just prose, no list.\n") == ()
    assert top_level_items("") == ()


def test_top_level_items_crlf() -> None:
    assert len(top_level_items("- a\r\n- b\r\n")) == 2
