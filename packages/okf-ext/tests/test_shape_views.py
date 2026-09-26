"""`audience_view` and `word_count`: the pure agent projection of a page."""

from __future__ import annotations

from okf_ext.shape import SectionSpec, SectionView, TypeSections, audience_view, word_count

PLACEHOLDER = "> TODO: what this is, in one paragraph.\n"

DECLARATION = TypeSections(
    sections=(
        SectionSpec(heading="Purpose", audience="agent", max_words=150, placeholder=PLACEHOLDER),
        SectionSpec(heading="History"),
        SectionSpec(heading="Public API", audience="agent", phases=("execute",)),
        SectionSpec(heading="Detail", level=3, audience="agent"),
    )
)


def test_word_count_is_whitespace_tokens_code_included():
    assert word_count("") == 0
    assert word_count("  one\ttwo\n\nthree  ") == 3
    assert word_count("```py\nx = 1\n```") == 5


def test_agent_sections_come_back_in_declaration_order_not_page_order():
    body = "## Public API\n\n`make()`.\n\n## History\n\nOld.\n\n## Purpose\n\nIt does X.\n"
    assert audience_view(body, DECLARATION) == (
        SectionView(heading="Purpose", level=2, text="It does X."),
        SectionView(heading="Public API", level=2, text="`make()`."),
    )


def test_human_audience_returns_the_complement():
    body = "## Purpose\n\nIt does X.\n\n## History\n\nOld.\n"
    assert audience_view(body, DECLARATION, "human") == (SectionView(heading="History", level=2, text="Old."),)


def test_a_missing_section_is_skipped_not_raised():
    assert audience_view("## Unrelated\n\nText.\n", DECLARATION) == ()


def test_an_empty_section_is_skipped():
    assert audience_view("## Purpose\n\n\n## History\n\nOld.\n", DECLARATION) == ()


def test_a_placeholder_equal_section_is_skipped_whitespace_aside():
    body = "## Purpose\n\n   > TODO: what this is, in one paragraph.   \n\n"
    assert audience_view(body, DECLARATION) == ()


def test_a_crlf_placeholder_is_still_skipped():
    body = "## Purpose\r\n\r\n> TODO: what this is, in one paragraph.\r\n"
    assert audience_view(body, DECLARATION) == ()


def test_heading_case_on_the_page_does_not_matter():
    (view,) = audience_view("## purpose\n\nIt does X.\n", DECLARATION)
    assert view.heading == "Purpose"


def test_the_match_is_level_specific():
    body = "## Detail\n\nWrong level.\n\n## Purpose\n\nIt does X.\n\n### Detail\n\nRight level.\n"
    headings = [(v.heading, v.level, v.text) for v in audience_view(body, DECLARATION)]
    assert ("Detail", 3, "Right level.") in headings
    assert ("Detail", 2, "Wrong level.") not in headings


def test_phase_filter_keeps_unphased_sections_and_matching_ones():
    body = "## Purpose\n\nIt does X.\n\n## Public API\n\n`make()`.\n"
    assert [v.heading for v in audience_view(body, DECLARATION, phase="execute")] == ["Purpose", "Public API"]
    assert [v.heading for v in audience_view(body, DECLARATION, phase="plan")] == ["Purpose"]
    # No phase given: every agent section, phased or not.
    assert [v.heading for v in audience_view(body, DECLARATION)] == ["Purpose", "Public API"]
