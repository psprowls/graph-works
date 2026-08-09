"""The ledger body: deterministic, byte-stable, machine-owned while proposed."""

from __future__ import annotations

from okf_ext.proposals.render import HEADER, render_body

SOURCES = (
    {"id": "src-a", "resource": "/sources/a.md", "title": "Claude path skill ingest guidance"},
    {"id": "src-b", "resource": "/sources/b.md", "title": "Bedrock ingest notes"},
)


def test_the_rendered_body_is_exactly_this():
    """Pinned whole, not by fragments. A body regenerated on every merge is
    worth exactly as much as the reviewer's ability to diff it."""
    assert render_body(description="Two sources argue for one page.", sources=SOURCES) == (
        f"{HEADER}\n"
        "\n"
        "Two sources argue for one page.\n"
        "\n"
        "## Sources\n"
        "\n"
        "- Claude path skill ingest guidance[^src-a]\n"
        "- Bedrock ingest notes[^src-b]\n"
        "\n"
        "[^src-a]: /sources/a.md\n"
        "[^src-b]: /sources/b.md\n"
    )


def test_rendering_twice_is_byte_identical():
    """The determinism property. Key order is fixed and nothing here reads a
    clock, a hash seed or a set."""
    first = render_body(description="why", sources=SOURCES)
    assert first == render_body(description="why", sources=SOURCES)


def test_the_newline_is_honoured_throughout():
    """A merge onto a CRLF document renders CRLF, so the splice does not
    staple LF lines into a CRLF body."""
    rendered = render_body(description="why", sources=SOURCES, newline="\r\n")
    assert "\n" not in rendered.replace("\r\n", "")
    assert rendered.endswith("\r\n")


def test_no_sources_still_renders_a_stable_body():
    assert render_body(description="why", sources=()) == f"{HEADER}\n\nwhy\n\n## Sources\n"
    assert render_body(description="why", sources=()) == render_body(description="why", sources=())


def test_no_description_omits_the_paragraph_without_a_doubled_blank():
    rendered = render_body(description="   ", sources=SOURCES)
    assert rendered.startswith(f"{HEADER}\n\n## Sources\n")
    assert "\n\n\n" not in rendered


def test_a_source_falls_back_from_title_to_id_to_resource():
    rendered = render_body(
        description="d",
        sources=(
            {"id": "src-a", "resource": "/sources/a.md"},
            {"id": "", "resource": "/sources/b.md"},
        ),
    )
    assert "- src-a[^src-a]" in rendered
    assert "- /sources/b.md" in rendered


def test_nothing_is_rendered_as_an_autolink():
    """`okf_ext.render`'s `render.angle-bracket` rule fires for a bare
    `<destination>`. A capability that generates bodies must not generate
    findings for the capability that judges them."""
    rendered = render_body(description="d", sources=SOURCES)
    assert "<" not in rendered.removeprefix(HEADER)


def test_the_citation_marker_resolves_to_the_bundle_path_not_a_literal_bracket_string():
    """`okf_io`'s parser is plain CommonMark with no footnote extension, so
    `[^src-a]` in the bullet is itself a shortcut reference link resolving
    against the `[^src-a]: ...` definition line below it. A definition wrapped
    in a second `[text](url)` would make that link's href the literal string
    `[/sources/a.md](/sources/a.md)` -- not a bundle member, and a `links.broken`
    finding on every cited proposal. This pins the destination as bare, so the
    citation marker resolves to the real bundle path instead."""
    from markdown_it import MarkdownIt

    rendered = render_body(description="d", sources=SOURCES)
    tokens = MarkdownIt("commonmark").parse(rendered)
    hrefs = [
        child.attrs["href"]
        for token in tokens
        if token.type == "inline"
        for child in token.children or ()
        if child.type == "link_open"
    ]
    assert hrefs == ["/sources/a.md", "/sources/b.md"]
