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


def test_a_bare_image_lands_in_links_flagged_as_an_image():
    """Pins documented behaviour, not a coverage gap: a bare image is not a
    link, and `_scan_link`'s docstring is explicit that an image contributes
    nothing to a label. It must still surface through `links` -- as an image,
    not silently dropped."""
    index = _md.parse_body("# H\n\n![alt](i.png)\n")
    assert len(index.links) == 1
    link = index.links[0]
    assert link.raw == "i.png"
    assert link.image is True


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


def test_an_image_wrapped_in_a_link_contributes_nothing_to_the_label():
    """Pins documented behaviour, not a coverage gap: `_scan_link`'s docstring
    is explicit that an image child's `content` is its alt text, and folding
    that into the label would make `[![alt](i.png)](t.md)` read "alt" rather
    than empty. The target still resolves to the enclosing link's `t.md`."""
    body = "# Citations\n\n- [![alt](i.png)](t.md)\n"
    items = _md.list_items_under(_md.parse_body(body), "citations")
    assert items[0].link_label is None
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


def test_citations_section_reads_the_list_dialect():
    body = "# Definition\n\nText.\n\n# Citations\n\n- [Policy](p.md)\n- [Other](o.md)\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.start == 5
    assert section.stop == 8
    assert section.pure
    assert [(c.link_label, c.link_target) for c in section.entries] == [
        ("Policy", "p.md"),
        ("Other", "o.md"),
    ]


def test_citations_section_reads_the_bracketed_number_dialect():
    """`crypto_bitcoin` writes citations as paragraph lines, not list items.

    They are one paragraph token with softbreaks, so the locator segments it by
    line and runs each line through the parser.
    """
    body = "# Schema\n\nText.\n\n# Citations\n\n[1] [First](a.md)\n[2] [Second](b.md)\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.pure
    assert [(c.link_label, c.link_target, c.line) for c in section.entries] == [
        ("First", "a.md", 7),
        ("Second", "b.md", 8),
    ]


def test_citations_section_ignores_the_heading_level():
    """One corpus file writes `### Citations`; the heading text is the key."""
    body = "### Citations\n\n[1] [First](a.md)\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.start == 1
    assert [c.link_target for c in section.entries] == ["a.md"]


def test_a_bare_url_item_carries_no_link():
    """`_MD` runs with linkify off, so a bare URL is text, not a link."""
    body = "# Citations\n\n- https://example.com/a\n"
    section = _md.citations_section(body)
    assert section is not None
    entry = section.entries[0]
    assert entry.link_target is None
    assert entry.text == "https://example.com/a"


def test_a_numbered_line_carrying_no_link_keeps_its_stripped_text():
    body = "# Citations\n\n[1] See the FY2026 policy binder\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.entries[0].link_target is None
    assert section.entries[0].text == "See the FY2026 policy binder"


def test_a_stray_paragraph_makes_the_section_impure():
    body = "# Citations\n\n- [Policy](p.md)\n\nSee also the binder.\n"
    section = _md.citations_section(body)
    assert section is not None
    assert not section.pure


def test_a_code_block_makes_the_section_impure():
    body = "# Citations\n\n- [Policy](p.md)\n\n```sql\nSELECT 1\n```\n"
    section = _md.citations_section(body)
    assert section is not None
    assert not section.pure


def test_a_blockquote_makes_the_section_impure():
    """A blockquoted line is not a top-level paragraph, so nothing covers it."""
    body = "# Citations\n\n> [1] [Quoted](q.md)\n"
    section = _md.citations_section(body)
    assert section is not None
    assert not section.pure
    assert section.entries == ()


def test_an_empty_section_is_pure_and_carries_no_entries():
    body = "# Definition\n\nText.\n\n# Citations\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.entries == ()
    assert section.pure


def test_the_section_stops_at_the_next_heading():
    body = "# Citations\n\n- [Policy](p.md)\n\n# Other\n\ntext\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.stop == 4
    assert [c.link_target for c in section.entries] == ["p.md"]


def test_the_section_owns_its_trailing_blank_lines():
    body = "# Citations\n\n- [Policy](p.md)\n\n\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.stop == 5


def test_a_second_citations_heading_is_not_folded_into_the_first():
    body = "# Citations\n\n- [A](a.md)\n\n# Citations\n\n- [B](b.md)\n"
    section = _md.citations_section(body)
    assert section is not None
    assert [c.link_target for c in section.entries] == ["a.md"]
    assert section.stop == 4


def test_a_second_numbered_citations_heading_is_not_folded_into_the_first():
    """The `[n]` dialect's own version of the list-dialect test above.

    Exercises the range filter on the paragraph-line branch, not just the
    list-item branch -- the numbered dialect is this change's headline
    capability, so its second-heading exclusion needs its own coverage.
    """
    body = "# Citations\n\n[1] [A](a.md)\n\n# Citations\n\n[1] [B](b.md)\n"
    section = _md.citations_section(body)
    assert section is not None
    assert [c.link_target for c in section.entries] == ["a.md"]
    assert section.stop == 4


def test_no_citations_heading_locates_nothing():
    assert _md.citations_section("# Definition\n\nText.\n") is None


def test_a_blockquoted_citations_heading_locates_nothing():
    """A `# Citations` heading inside a blockquote is somebody else's content.

    Regression: `parse_body` records headings unconditionally, so without a
    quoted-heading guard the locator anchored on this heading and reported a
    `pure=True` section sitting entirely inside the blockquote -- silent data
    loss once a writer deletes what it reports.
    """
    body = "# Notes\n\nSome intro text, unrelated.\n\n> # Citations\n> - [Quoted](q.md)\n"
    assert _md.citations_section(body) is None


def test_a_blockquoted_heading_inside_a_real_section_does_not_truncate_it():
    """A quoted heading is not a top-level heading, so it cannot end the
    section early. The section then runs through to the body's last line,
    the quoted lines are uncovered by any entry, and the section is
    therefore impure -- refused rather than partially rewritten, the same
    doctrine `test_a_blockquote_makes_the_section_impure` asserts elsewhere.
    """
    body = "# Citations\n\n- [A](a.md)\n\n> # Quoted Heading\n> more quoted prose\n"
    section = _md.citations_section(body)
    assert section is not None
    assert section.stop == 6
    assert not section.pure
    assert [c.link_target for c in section.entries] == ["a.md"]


def test_citations_section_reaches_the_last_line_with_no_trailing_newline():
    body = "# Citations\n\n- [A](a.md)"
    section = _md.citations_section(body)
    assert section is not None
    assert section.stop == 3
    assert section.pure
    assert [c.link_target for c in section.entries] == ["a.md"]


def test_paragraphs_exclude_list_item_and_blockquote_prose():
    body = "# H\n\ntop level\n\n- item\n\n  item's own second paragraph\n\n> quoted\n"
    index = _md.parse_body(body)
    assert [p.line for p in _md.paragraphs_under(index, "h")] == [3]
