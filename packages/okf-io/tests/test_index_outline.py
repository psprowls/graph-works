"""`outline`: the headings and linked entries of an index body, read-only.

`outline` and `update` must cut the same entries from the same text, so
`test_outline_and_update_agree_on_entries` compares them over one file.
"""

from __future__ import annotations

from pathlib import Path

from helpers import write_tree
from okf_io import bundle, index
from okf_io.index import IndexEntry, IndexHeading, outline

CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# T\n"


def test_headings_in_order_with_their_entries() -> None:
    body = (
        "# Title\n\n"
        "- [Before](before.md) - under the title\n\n"
        "## ADRs\n\n"
        "- [One](/adrs/one.md) — first\n"
        "- [Two](adrs/two.md): second\n"
        "- plain note, no link\n"
        "- see [not first](x.md)\n\n"
        "## Concepts\n\n"
        "### Architecture\n\n"
        "- [Arch](/concepts/arch.md)\n"
    )

    result = outline(body)

    assert [(h.level, h.text) for h in result.headings] == [
        (1, "Title"),
        (2, "ADRs"),
        (2, "Concepts"),
        (3, "Architecture"),
    ]
    assert result.headings[0].entries == (IndexEntry("before.md", "Before", "under the title"),)
    assert result.headings[1].entries == (
        IndexEntry("/adrs/one.md", "One", "first"),
        IndexEntry("adrs/two.md", "Two", "second"),
    )
    assert result.headings[2].entries == ()
    assert result.headings[3].entries == (IndexEntry("/concepts/arch.md", "Arch", None),)


def test_items_before_any_heading_are_dropped() -> None:
    assert outline("- [A](a.md)\n\n## S\n\n- [B](b.md)\n").headings == (
        IndexHeading(2, "S", (IndexEntry("b.md", "B", None),)),
    )


def test_fenced_code_holds_no_headings_or_entries() -> None:
    body = "## Real\n\n```md\n## Not a heading\n- [Nope](nope.md)\n```\n\n- [Yes](yes.md)\n"

    result = outline(body)

    assert [h.text for h in result.headings] == ["Real"]
    assert result.headings[0].entries == (IndexEntry("yes.md", "Yes", None),)


def test_quoted_headings_are_not_headings() -> None:
    body = "## Real\n\n> ## Quoted\n\n- [A](a.md)\n"

    assert [h.text for h in outline(body).headings] == ["Real"]
    assert outline(body).headings[0].entries == (IndexEntry("a.md", "A", None),)


def test_an_escaped_bracket_label_is_unreadable_and_skipped() -> None:
    """The same rule `update` applies (`_read_entry`): a label holding `]` cannot be cut cleanly."""
    body = "## S\n\n- [a \\[b\\]](x.md)\n- [c](c.md)\n"

    assert outline(body).headings[0].entries == (IndexEntry("c.md", "c", None),)


def test_href_is_the_destination_as_written() -> None:
    body = "## S\n\n- [Encoded](caf%C3%A9.md)\n- [Raw](café.md)\n"

    assert [e.href for e in outline(body).headings[0].entries] == ["caf%C3%A9.md", "café.md"]


def test_empty_body_has_no_headings() -> None:
    assert outline("").headings == ()


def test_outline_and_update_agree_on_entries(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        {
            "index.md": (
                "# Index\n\n## A\n\n- [One](one.md) - d\n- [Esc \\]](one.md)\n- note\n\n"
                "## B\n\n- [Two](/sub/two.md)\n- [Gone](gone.md)\n- [Ext](https://example.com)\n"
            ),
            "one.md": CONCEPT,
            "sub/two.md": CONCEPT,
        },
    )
    loaded = bundle.load(root)
    planned = index._plan(loaded, "", index._directories(loaded))
    readable = [entry.target for entry in planned.entries if entry.prefix is not None]

    body = loaded.indexes[""].body
    outlined = [
        index._resolve(entry.href, source="index.md") for heading in outline(body).headings for entry in heading.entries
    ]

    assert [target for target in outlined if target is not None] == readable
