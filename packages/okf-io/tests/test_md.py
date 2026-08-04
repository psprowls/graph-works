from __future__ import annotations

import pytest
from okf_io import _md

BODY = """# Definition

A [rel](../tables/orders.md#order_status), an [abs](/policies/p.md), an
[ext](https://example.com/a), an [enc](./café.md), a [ref][r].

![img](assets/x.png)

```sql
[fenced](/should/ignore.md)
```

    [indented](/also/ignore.md)

Cited here. [^one] And `[^inline]` is code, not a footnote.

[r]: /tables/orders.md

[^one]: the definition
[^two]: an unreferenced definition
"""


@pytest.fixture(autouse=True)
def _clear_cache():
    _md.parse_body.cache_clear()


def test_code_blocks_never_yield_links():
    """Regression: every regex link scanner reports links inside fences."""
    raws = [link.raw for link in _md.parse_body(BODY).links]
    assert "/should/ignore.md" not in raws
    assert "/also/ignore.md" not in raws


def test_reference_links_resolve():
    raws = [link.raw for link in _md.parse_body(BODY).links]
    assert "/tables/orders.md" in raws


def test_destinations_are_reported_undecoded():
    """markdown-it normalizes on output; decoding belongs to `links`, not here."""
    raws = [link.raw for link in _md.parse_body(BODY).links]
    assert "./caf%C3%A9.md" in raws


def test_fragments_survive_to_the_resolver():
    raws = [link.raw for link in _md.parse_body(BODY).links]
    assert "../tables/orders.md#order_status" in raws


def test_images_are_collected_and_flagged():
    images = [link for link in _md.parse_body(BODY).links if link.image]
    assert [link.raw for link in images] == ["assets/x.png"]


def test_link_lines_are_body_relative_and_one_based():
    index = _md.parse_body("# H\n\nText.\n\n[a](/x.md)\n")
    assert [link.line for link in index.links] == [5]


def test_footnote_refs_and_defs_are_separated():
    index = _md.parse_body(BODY)
    assert "one" in index.footnote_refs
    assert set(index.footnote_defs) == {"one", "two"}
    assert index.footnote_labels == {"one", "two"}


def test_inline_code_spans_do_not_manufacture_footnotes():
    assert "inline" not in _md.parse_body(BODY).footnote_labels


def test_footnotes_inside_a_fence_are_not_labels():
    body = "# H\n\n```\n[^fenced]: not a footnote\n```\n"
    assert _md.parse_body(body).footnote_labels == frozenset()


def test_code_blocks_are_selected_by_their_heading():
    body = "# Computation\n\n```sql\nSELECT 1\n```\n\n# Notes\n\n```py\nx = 1\n```\n"
    index = _md.parse_body(body)
    assert len(index.code_blocks) == 2
    assert len(_md.code_blocks_under(index, "computation")) == 1
    assert _md.code_blocks_under(index, "COMPUTATION")[0].fenced is True


def test_indented_blocks_count_as_code_blocks():
    """Spec §10.2's own worked example uses a four-space indented block."""
    body = "# Computation\n\n    SELECT 1\n"
    assert len(_md.code_blocks_under(_md.parse_body(body), "computation")) == 1


def test_list_items_are_selected_by_their_heading():
    body = "# Citations\n\n- [Policy](p.md)\n- Plain prose\n\n# Other\n\n- ignored\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert [(i.link_label, i.link_target) for i in items] == [("Policy", "p.md"), (None, None)]
    assert items[1].text == "Plain prose"


def test_list_item_label_with_inline_markup_is_flattened_to_text():
    """Regression: a bold label collapsed to None (the label's FIRST text
    child is an empty token before `strong_open`, not the rendered text)."""
    body = "# Citations\n\n- [**bold**](t.md)\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert items[0].link_label == "bold"
    assert items[0].link_target == "t.md"


def test_list_item_label_with_code_span_is_flattened_to_text():
    """Regression: a `code_inline` label is never a `text` token at all."""
    body = "# Citations\n\n- [`code`](t.md)\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert items[0].link_label == "code"
    assert items[0].link_target == "t.md"


def test_nested_list_item_own_paragraph_is_not_dropped():
    """Regression: a flat `item_line` variable is clobbered by a nested
    `list_item_open` and never restored, so the outer item's own paragraph
    -- following its nested sub-list -- silently produced no `ListItem`."""
    body = "# Citations\n\n- - inner [a](a.md)\n\n  outer own text [b](b.md)\n- next [c](c.md)\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    targets = [item.link_target for item in items]
    assert targets == ["a.md", "b.md", "c.md"]
    outer = items[1]
    assert outer.link_label == "b"
    assert outer.heading == "citations"


def test_parse_body_is_memoized(monkeypatch):
    calls = 0
    original = _md._MD.parse

    def counting(src, env=None):
        nonlocal calls
        calls += 1
        return original(src) if env is None else original(src, env)

    monkeypatch.setattr(_md._MD, "parse", counting)
    _md.parse_body(BODY)
    _md.parse_body(BODY)
    assert calls == 1


def test_list_item_end_covers_continuation_lines():
    body = "# Citations\n\n* one\n  continued\n* two\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert [(item.line, item.end) for item in items] == [(3, 4), (5, 5)]


def test_list_item_end_excludes_the_trailing_blank_line():
    """markdown-it's `[start, end)` map runs to the start of the next block.

    The last item of a list therefore owns the blank line that separates it
    from whatever follows. Deleting an entry by that range would take the
    separator with it and glue two blocks together.
    """
    body = "# Citations\n\n* one\n\n# Other\n\ntext\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert [(item.line, item.end) for item in items] == [(3, 3)]


def test_list_item_end_of_a_loose_list_item_excludes_its_blank_line():
    body = "# Citations\n\n* a\n\n* b\n\ntext\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert [(item.line, item.end) for item in items] == [(3, 3), (5, 5)]
