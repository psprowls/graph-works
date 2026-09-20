"""`run_wiki_citations`: grammar-matching inline code spans and repository resolution."""

from __future__ import annotations

from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.wiki_page import CitationCandidate, extract_citations, run_wiki_citations

TODAY = date(2026, 9, 19)


def _layout(tmp_path):
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(host / ".works", today=TODAY, topic="Code")).layout
    (layout.bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    return layout


def _page(layout, body: str, *, frontmatter: str = "type: Concept\ntitle: A\n") -> str:
    (layout.bundle_dir / "concepts" / "a.md").write_text(
        f"---\n{frontmatter}---\n{body}", encoding="utf-8", newline="\n"
    )
    return "concepts/a"


@pytest.mark.parametrize(
    ("span", "expected"),
    [("a.py:3", ("a.py", 3, 3)), ("pkg/a.py:3-9", ("pkg/a.py", 3, 9))],
)
def test_grammar_accepts(span, expected):
    ((raw, line, path, start, end),) = extract_citations(f"see `{span}`\n")
    assert (raw, line) == (span, 1)
    assert (path, start, end) == expected


@pytest.mark.parametrize("span", ["a.py", "a:3", "a b.py:3", "a.py:0", "a.py:3-", "a.py:3:4"])
def test_grammar_rejects(span):
    assert extract_citations(f"see `{span}`\n") == ()


def test_fenced_and_indented_blocks_are_excluded():
    body = "```\n`a.py:1`\n```\n\n    `b.py:2`\n\nand `c.py:3`\n"
    assert [raw for raw, *_ in extract_citations(body)] == ["c.py:3"]


def test_lines_count_softbreaks_hardbreaks_lists_tables_and_headings():
    body = (
        "\n"
        "# Head `h.py:1`\n"
        "\n"
        "para one\n"
        "two `p.py:5`  \n"
        "three `q.py:6`\n"
        "\n"
        "- item\n"
        "  cont `l.py:9`\n"
        "\n"
        "| h |\n"
        "|---|\n"
        "| `t.py:13` |\n"
    )
    lines = {raw: line for raw, line, *_ in extract_citations(body)}
    assert lines == {"h.py:1": 2, "p.py:5": 5, "q.py:6": 6, "l.py:9": 9, "t.py:13": 13}


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("`not a citation\nspan` then `a.py:4`\n", 2),
        ("``not ` a\ncitation\nspan`` then `a.py:4`\n", 3),
        ("`not a citation\nspan`\nthen `a.py:4`\n", 3),
        ("`not a citation\nspan`\n\nthen `a.py:4`\n", 4),
        ("> - `not a citation\n>   span` then `a.py:4`\n", 2),
        ('[link](target\n"title") then `a.py:4`\n', 2),
    ],
)
def test_multiline_inline_content_preserves_following_body_source_lines(body, line):
    assert extract_citations(body) == (("a.py:4", line, "a.py", 4, 4),)


def test_multiline_citation_uses_opening_line_and_preserves_later_citation_lines():
    body = "`\na.py:4\n` then `b.py:5` and `more\ncode` then `c.py:6`\n"
    assert extract_citations(body) == (
        ("a.py:4", 1, "a.py", 4, 4),
        ("b.py:5", 3, "b.py", 5, 5),
        ("c.py:6", 4, "c.py", 6, 6),
    )


def test_duplicates_are_kept_in_order():
    assert [line for _raw, line, *_ in extract_citations("`a.py:1` `a.py:1`\n\n`a.py:1`\n")] == [1, 1, 3]


def test_lines_are_body_relative_after_long_frontmatter(tmp_path):
    layout = _layout(tmp_path)
    page = _page(layout, "\n`a.py:1`\n", frontmatter="type: Concept\ntitle: A\n" + "tags:\n" + "  - t\n" * 20)
    (citation,) = run_wiki_citations(layout, page).citations
    assert citation.line == 1


def test_resolution_exact_suffix_ambiguous_missing(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"src/cli.py": "", "src/mycli.py": "", "x/dup.py": ""})
    two = git_repo(tmp_path / "two", {"src/cli.py": "", "y/dup.py": "", "only.py": ""})
    declare_repos(layout, {"one": (one, []), "two": (two, [])})
    page = _page(layout, "\n`src/cli.py:1` `cli.py:2` `dup.py:3` `only.py:4` `nope.py:5` `../only.py:6`\n")

    result = run_wiki_citations(layout, page)

    assert result.refusal is None
    by_raw = {c.raw: c for c in result.citations}
    exact = by_raw["src/cli.py:1"]
    assert (exact.status, exact.repo, exact.path, exact.candidates) == ("resolved", "one", "src/cli.py", ())
    suffix = by_raw["cli.py:2"]
    assert suffix.status == "ambiguous" and (suffix.repo, suffix.path) == (None, None)
    assert suffix.candidates == (CitationCandidate("one", "src/cli.py"), CitationCandidate("two", "src/cli.py"))
    dup = by_raw["dup.py:3"]
    assert dup.candidates == (CitationCandidate("one", "x/dup.py"), CitationCandidate("two", "y/dup.py"))
    only = by_raw["only.py:4"]
    assert (only.status, only.repo, only.path) == ("resolved", "two", "only.py")
    for raw in ("nope.py:5", "../only.py:6"):
        missing = by_raw[raw]
        assert (missing.status, missing.repo, missing.path, missing.candidates) == ("missing", None, None, ())
    assert (only.start, only.end) == (4, 4)


def test_suffix_matching_respects_the_component_boundary(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"src/mycli.py": "", "pkg/cli.py": ""})
    declare_repos(layout, {"one": (one, [])})
    page = _page(layout, "\n`cli.py:1`\n")

    (citation,) = run_wiki_citations(layout, page).citations

    assert (citation.status, citation.path) == ("resolved", "pkg/cli.py")


def test_exact_match_prefers_the_first_repository_in_manifest_order(tmp_path, git_repo, declare_repos):
    layout = _layout(tmp_path)
    one = git_repo(tmp_path / "one", {"a.py": ""})
    two = git_repo(tmp_path / "two", {"a.py": ""})
    declare_repos(layout, {"two": (two, []), "one": (one, [])})
    page = _page(layout, "\n`a.py:1`\n")

    (citation,) = run_wiki_citations(layout, page).citations

    assert (citation.status, citation.repo) == ("resolved", "two")


def test_an_unknown_page_is_a_refusal(tmp_path):
    layout = _layout(tmp_path)
    result = run_wiki_citations(layout, "concepts/nope")
    assert (result.id, result.citations, result.refusal) == ("concepts/nope", (), "unknown-page")


def test_a_page_without_citations_needs_no_repositories(tmp_path):
    layout = _layout(tmp_path)
    page = _page(layout, "\nplain `code` only\n")
    assert run_wiki_citations(layout, page).citations == ()
