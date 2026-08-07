"""The locator: exact spans for inline destinations, bounded by a real parse."""

from __future__ import annotations

import ext_helpers
from okf_ext.body import split_lines
from okf_ext.moves import locate


def spans(body: str):
    return [(c.line, c.column, c.text, c.image, c.bracketed) for c in locate.destinations(body)]


def test_a_plain_destination_is_located_exactly():
    body = "See [alpha](./alpha.md) here.\n"
    found = spans(body)
    assert found == [(1, 12, "./alpha.md", False, False)]


def test_every_span_indexes_its_own_line():
    """The invariant `apply` relies on: a recorded span holds the text it claims."""
    body = "A [one](./a.md) and [two](../b/c.md) on one line.\n"
    body_lines = split_lines(body)
    for candidate in locate.destinations(body):
        text = body_lines[candidate.line - 1]
        assert text[candidate.column : candidate.column + len(candidate.text)] == candidate.text


def test_a_percent_encoded_destination_is_located_as_written():
    """The text in the file is what gets replaced, so it is what is recorded --
    markdown-it's normalized `raw` would not match these bytes."""
    body = "A [cafe](./caf%C3%A9.md) link.\n"
    assert spans(body) == [(1, 9, "./caf%C3%A9.md", False, False)]


def test_an_angle_bracket_destination_excludes_its_brackets():
    body = "A [spaced](<./spaced name.md>) link.\n"
    assert spans(body) == [(1, 12, "./spaced name.md", False, True)]


def test_a_fragment_stays_part_of_the_destination_text():
    body = "A [frag](./alpha.md#heading) link.\n"
    assert spans(body) == [(1, 9, "./alpha.md#heading", False, False)]


def test_a_title_is_not_part_of_the_destination():
    body = 'A [titled](./a.md "The title") link.\n'
    assert spans(body) == [(1, 11, "./a.md", False, False)]


def test_an_image_is_located_and_flagged():
    body = "![diagram](../assets/diagram.png)\n"
    assert spans(body) == [(1, 11, "../assets/diagram.png", True, False)]


def test_a_linked_image_yields_both_destinations():
    body = "[![alt](./i.png)](./t.md)\n"
    found = spans(body)
    assert (1, 8, "./i.png", True, False) in found
    assert (1, 18, "./t.md", False, False) in found


def test_a_fenced_block_is_never_located():
    """The first of the three bugs `_md.py` attributes to regex scanners, and
    the reason the bound comes from a real parse rather than counting backticks."""
    body = "Real [a](./a.md).\n\n```markdown\n[fenced](./a.md)\n```\n"
    assert [c.line for c in locate.destinations(body)] == [1]


def test_an_indented_block_is_never_located():
    body = "Real [a](./a.md).\n\n    [indented](./a.md)\n"
    assert [c.line for c in locate.destinations(body)] == [1]


def test_an_inline_code_span_is_never_located():
    """`prose_lines` is a *line* mask, so it cannot exclude this one -- an
    inline span sits on a prose line. Rewriting it would be an edit the graph
    never saw, and the reconciliation's job is to catch a mismatch in
    `plan`, not to make this module's own scan sloppy."""
    body = "Real [a](./a.md) and `[inline](./a.md)` in code.\n"
    assert spans(body) == [(1, 9, "./a.md", False, False)]


def test_an_inline_code_span_crossing_a_line_break_is_never_located():
    """A CommonMark inline code span can cross a line break -- markdown-it
    parses this as one `code_inline` token and emits no link at all -- so
    masking has to run over the whole contiguous prose run containing both
    lines, never one line at a time, or the destination on the second line
    would still read as prose."""
    body = "Some `code [a](./x.md)\nspanning lines` end.\n"
    assert spans(body) == []


def test_a_stray_backtick_inside_one_fence_does_not_swallow_prose_between_fences():
    """A regression: masking used to run over the *raw* body in one pass, with
    no notion of a block boundary. A mid-line ``` inside this first fence read
    as that match's closer; the fence's real closing delimiter then read as
    the *opener* of a new match, which ran forward until the next fenced
    block's delimiter -- blanking the real prose (and its real link) sitting
    between the two fences. Masking each contiguous prose run independently,
    rather than the whole body, is what keeps the regex from ever seeing
    across a fence."""
    body = (
        "```\n"
        "example ``` mid-line, not a real close\n"
        "```\n"
        "\n"
        "Real [a](./a.md) prose here.\n"
        "\n"
        "```\n"
        "second fence content\n"
        "```\n"
    )
    assert spans(body) == [(5, 9, "./a.md", False, False)]


def test_an_unclosed_backtick_in_one_prose_run_does_not_affect_the_next():
    """The two prose runs on either side of the fence are masked separately,
    so a backtick that never closes in the first run cannot consume anything
    in the second -- the destination in the first run is genuinely not in a
    code span (CommonMark treats an unclosed backtick run as literal text)
    and the destination in the second is untouched by the first run's scan."""
    body = "Prose with an unclosed `tick [a](./a.md) here.\n\n```\nfence\n```\n\nLater [b](./b.md) prose.\n"
    assert spans(body) == [
        (1, 33, "./a.md", False, False),
        (7, 10, "./b.md", False, False),
    ]


def test_four_links_in_one_paragraph_yield_four_spans():
    """The trap `MdLink.line` sets: markdown-it reports the containing block's
    first line for every one of these."""
    body = "One [a](./a.md), [b](./b.md), [c](./c.md) and [d](./d.md).\n"
    found = spans(body)
    assert len(found) == 4
    assert {c[2] for c in found} == {"./a.md", "./b.md", "./c.md", "./d.md"}
    assert {c[0] for c in found} == {1}


def test_a_multiline_paragraph_reports_each_links_own_line():
    body = "First [a](./a.md)\nsecond [b](./b.md)\nthird [c](./c.md)\n"
    assert [(c[0], c[2]) for c in spans(body)] == [(1, "./a.md"), (2, "./b.md"), (3, "./c.md")]


def test_crlf_gives_the_same_line_numbers_as_lf():
    lf = "A [x](./x.md)\n\nB [y](./y.md)\n"
    assert [(c[0], c[2]) for c in spans(lf)] == [(1, "./x.md"), (3, "./y.md")]
    assert [(c[0], c[2]) for c in spans(lf.replace("\n", "\r\n"))] == [(1, "./x.md"), (3, "./y.md")]


def test_a_destination_with_balanced_parens_is_located_whole():
    body = "A [x](./a(b).md) link.\n"
    assert spans(body) == [(1, 6, "./a(b).md", False, False)]


def test_an_empty_destination_is_not_a_candidate():
    body = "An [empty]() link.\n"
    assert spans(body) == []


def test_a_fragment_only_destination_is_still_a_candidate():
    """It is not an edge -- `_link_from` returns None for it -- but the locator
    does not resolve, it locates. `plan` drops it when resolution says so."""
    body = "An [anchor](#heading) link.\n"
    assert spans(body) == [(1, 12, "#heading", False, False)]


def test_a_bracket_paren_sequence_in_prose_is_not_a_link():
    """A `](` that is not opened by any real, unescaped `[` is not a link --
    it is prose that happens to contain the two-character sequence. Emitting
    a candidate for it would rewrite ordinary text and (per the count
    reconciliation the excess text above documents) turn a legitimate move
    into a confusing refusal rather than an applied one."""
    body = "Note: writing something](wrong) is a common mistake.\n"
    assert spans(body) == []


def test_a_bracket_inside_a_link_title_is_not_a_second_destination():
    """The real link's own `](` is still found -- only the fake one nested in
    the title is rejected, by walking back to a `[` that is not itself
    already spoken for by a nested `]`."""
    body = '[a](./x.md "titled ](weird)")\n'
    assert spans(body) == [(1, 4, "./x.md", False, False)]


def test_an_unterminated_destination_yields_nothing():
    """A destination that breaks on whitespace but never reaches a closing
    `)` anywhere on the line is not a link -- CommonMark would parse `[a](`
    followed by bare words as literal text, and so does this scanner."""
    body = "See [a](unterminated and more text\n"
    assert spans(body) == []


def test_opens_image_does_not_misread_an_escaped_bang():
    """`X\\![b](x.md)` is a literal `!` followed by a real, non-image link --
    markdown-it agrees -- so the `!` must itself be checked for its own
    escaping, not just presence."""
    body = "X\\![b](x.md)\n"
    assert spans(body) == [(1, 7, "x.md", False, False)]


def test_a_literal_backslash_before_a_real_bracket_does_not_escape_it():
    """Two backslashes before `[` are one escaped backslash and then a real,
    unescaped `[` -- an even run of backslashes cancels itself out, and only
    an odd run escapes the character that follows."""
    body = "X\\\\[b](x.md)\n"
    assert spans(body) == [(1, 7, "x.md", False, False)]


def test_reference_definitions_are_reported_with_their_line():
    body = 'See [one][ref].\n\n[ref]: ./target.md "Title"\n[other]: /root/x.png\n'
    found = {(d.label, d.href, d.line) for d in locate.reference_definitions(body)}
    assert found == {("ref", "./target.md", 3), ("other", "/root/x.png", 4)}


def test_a_reference_style_link_yields_no_span():
    """The destination lives in a definition markdown-it's block parser
    consumes, producing no token with a position -- so the graph sees an edge
    whose text the locator cannot find. That gap is what the count
    reconciliation turns into a refusal."""
    body = "See [one][ref].\n\n[ref]: ./target.md\n"
    assert spans(body) == []


def test_no_definitions_when_there_are_none():
    assert locate.reference_definitions("Just [a](./a.md).\n") == ()


def test_reference_definition_label_keeps_its_source_casing():
    """markdown-it's `env["references"]` key is case-folded for lookup, which
    is not the source: a definition written `[Ref]:` must not come back
    lower-cased just because the normalizer folds case internally."""
    body = "See [one][Ref].\n\n[Ref]: ./target.md\n"
    found = locate.reference_definitions(body)
    assert found == (locate.RefDef(label="Ref", href="./target.md", line=3),)


def test_every_span_in_the_corpus_round_trips():
    """The property, over every body in the fixture bundle."""
    bundle = ext_helpers.linked_bundle()
    documents = [*bundle.concepts.values(), *bundle.indexes.values(), *bundle.logs.values()]
    seen = 0
    for document in documents:
        body_lines = split_lines(document.body)
        for candidate in locate.destinations(document.body):
            text = body_lines[candidate.line - 1]
            assert text[candidate.column : candidate.column + len(candidate.text)] == candidate.text
            seen += 1
    assert seen > 10, "the corpus should exercise more than a handful of destinations"
