"""Span arithmetic, level filtering, and what is deliberately not a heading."""

from __future__ import annotations

import pytest
from okf_ext.body import Section, find_section, prose_lines, sections, split_lines

DOC = "".join(
    [
        "# Title\n",  # 1
        "\n",  # 2
        "intro\n",  # 3
        "\n",  # 4
        "## Plan\n",  # 5
        "\n",  # 6
        "| A | B |\n",  # 7
        "\n",  # 8
        "### Detail\n",  # 9
        "deep\n",  # 10
        "\n",  # 11
        "## Notes\n",  # 12
        "tail\n",  # 13
    ]
)


@pytest.mark.parametrize("body", ["", "a", "a\n", "a\nb", "a\r\nb\r\n", "a\rb", "\n\n", DOC])
def test_lines_reassemble_the_body_byte_for_byte(body):
    assert "".join(split_lines(body)) == body


def test_lines_does_not_invent_a_line_after_a_trailing_terminator():
    assert split_lines("a\n") == ("a\n",)
    assert split_lines("") == ()


def test_section_spans():
    found = {section.heading: section for section in sections(DOC)}
    assert found["Title"] == Section(heading="Title", level=1, start=1, body_start=2, stop=13)
    assert found["Plan"] == Section(heading="Plan", level=2, start=5, body_start=6, stop=11)
    assert found["Detail"] == Section(heading="Detail", level=3, start=9, body_start=10, stop=11)
    assert found["Notes"] == Section(heading="Notes", level=2, start=12, body_start=13, stop=13)


def test_a_section_slice_excludes_its_heading_and_keeps_trailing_blanks():
    # Plan (stop=11, per test_section_spans) runs past the nested `### Detail`
    # subsection to the next same-or-shallower heading (`## Notes`), so its
    # slice carries that nested content too -- consistent with `Section.stop`
    # covering everything the section owns, not just its own direct prose.
    plan = find_section(DOC, "Plan")
    assert plan is not None
    assert plan.slice(DOC) == "\n| A | B |\n\n### Detail\ndeep\n\n"


def test_a_section_slice_preserves_crlf_terminators():
    # Same section, same span arithmetic, only the line ending changes -- the
    # slice must carry CRLF through, not normalize it to LF.
    crlf = DOC.replace("\n", "\r\n")
    plan = find_section(crlf, "Plan")
    assert plan is not None
    assert plan.slice(crlf) == "\r\n| A | B |\r\n\r\n### Detail\r\ndeep\r\n\r\n"


def test_a_section_with_nothing_under_it_slices_empty():
    body = "## A\n## B\n"
    first = find_section(body, "A")
    assert first is not None
    assert (first.start, first.body_start, first.stop) == (1, 2, 1)
    assert first.slice(body) == ""


def test_a_setext_heading_spans_two_lines():
    body = "Plan\n----\n\ntext\n"
    found = sections(body)
    assert found == (Section(heading="Plan", level=2, start=1, body_start=3, stop=4),)


def test_the_last_section_runs_to_the_end_of_the_body():
    body = "# A\nx\ny"
    section = find_section(body, "A")
    assert section is not None
    assert section.stop == 3


def test_find_section_is_case_insensitive_and_strips():
    assert find_section(DOC, "  plan  ") is not None
    assert find_section(DOC, "PLAN") is not None
    assert find_section(DOC, "plans") is None


def test_find_section_is_level_agnostic_by_default_and_narrows_on_request():
    body = "### Citations\nx\n"
    assert find_section(body, "Citations") is not None
    assert find_section(body, "Citations", level=3) is not None
    assert find_section(body, "Citations", level=2) is None


def test_a_blockquoted_heading_is_neither_a_section_nor_a_boundary():
    body = "## Plan\nrow\n> ## Quoted\nstill plan\n"
    assert [section.heading for section in sections(body)] == ["Plan"]
    plan = find_section(body, "Plan")
    assert plan is not None
    assert plan.stop == 4


def test_a_body_with_no_heading_has_no_sections():
    assert sections("just prose\n") == ()
    assert find_section("just prose\n", "Plan") is None


def test_prose_lines_covers_every_line_of_a_body_with_no_code():
    assert prose_lines("a\nb\nc\n") == frozenset({1, 2, 3})
    assert prose_lines("") == frozenset()


def test_normalized_text_is_the_comparable_form_of_a_section():
    from okf_ext.body import normalized_text

    assert normalized_text("\r\n\n  > TODO: x  \r\n  more\r\n\n") == "> TODO: x\nmore"
    assert normalized_text("a\rb") == "a\nb"
    assert normalized_text("   \n\n") == ""
    # Interior blank lines survive; only the edges are dropped.
    assert normalized_text("a\n\nb\n") == "a\n\nb"
