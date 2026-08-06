from __future__ import annotations

from datetime import date

import ext_helpers
import pytest
from okf_ext.render import CODES, TOPIC, render_rule
from okf_io import Finding, load_bundle, parse, validate

TODAY = date(2026, 8, 6)


def run(bundle, *, severity="warn", strict=False):
    report = validate(bundle, today=TODAY, extra_rules=[render_rule(severity=severity)], strict=strict)
    return [f for f in report.findings if f.code.startswith(f"{TOPIC}.")]


def one(tmp_path, name, text):
    """A one-file bundle. Cheaper than a fixture for a single branch."""
    (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
    return run(load_bundle(tmp_path))


# --- The corpus walk --------------------------------------------------------


def test_the_corpus_reports_exactly_the_expected_set():
    found = {(f.code, f.path) for f in run(ext_helpers.unrendered_bundle())}
    assert found == ext_helpers.RENDER_EXPECTED


def test_indexes_and_logs_are_walked_not_only_concepts():
    """Render correctness is a property of markdown, not of a document's role."""
    paths = {f.path for f in run(ext_helpers.unrendered_bundle())}
    assert "index.md" in paths
    assert "log.md" in paths


def test_assets_are_never_parsed_as_markdown():
    """`page.html` is full of real HTML tags. Parsing it would flag every one --
    the `ga4/viz.html` regression."""
    assert "page.html" in ext_helpers.unrendered_bundle().assets
    assert not [f for f in run(ext_helpers.unrendered_bundle()) if f.path == "page.html"]


def test_a_parse_error_is_skipped_not_re_reported():
    """`broken.md` carries a bare `<slug>`. okf-io already emitted
    `frontmatter.unparseable`; saying so twice in two vocabularies helps
    nobody."""
    assert not [f for f in run(ext_helpers.unrendered_bundle()) if f.path == "broken.md"]


# --- Angle brackets ---------------------------------------------------------


def test_a_bare_placeholder_tag_is_flagged_at_its_file_line(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\nReplace <slug> with the id.\n")
    assert len(found) == 1
    assert found[0].code == "render.angle-bracket"
    assert "<slug>" in found[0].message
    assert found[0].line == 6  # body line 1 + body_line_offset 5


def test_an_html_block_is_flagged_too(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\n<placeholder>\nbody\n</placeholder>\n")
    assert [f.code for f in found] == ["render.angle-bracket"]


@pytest.mark.parametrize(
    "body",
    [
        "A line<br>and another.\n",
        "<!-- a reviewer note -->\n",
        "Wrapped in `<slug>` backticks.\n",
    ],
)
def test_allowlisted_tags_comments_and_code_never_fire(tmp_path, body):
    assert one(tmp_path, "d", f"---\ntype: Note\ntitle: T\n---\n\n{body}") == []


# --- Callouts ---------------------------------------------------------------


def test_a_malformed_callout_header_is_flagged(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\n> [!note malformed\n> body\n")
    assert len(found) == 1
    assert found[0].code == "render.callout"
    assert "Malformed" in found[0].message
    assert found[0].line == 6


def test_an_unknown_callout_type_is_flagged_separately(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\n> [!nope] Title\n> body\n")
    assert len(found) == 1
    assert found[0].code == "render.callout"
    assert "Unknown callout type" in found[0].message
    assert "nope" in found[0].message


@pytest.mark.parametrize(
    "body",
    [
        "> just a quote\n> across two lines\n",
        "> [!note] Fine\n> body\n",
        "> [!WARNING]- Folded and fine\n> body\n",
    ],
)
def test_ordinary_and_well_formed_blockquotes_never_fire(tmp_path, body):
    assert one(tmp_path, "d", f"---\ntype: Note\ntitle: T\n---\n\n{body}") == []


# --- Wikilinks --------------------------------------------------------------


def test_an_unbalanced_wikilink_is_flagged(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\nSee [[some-target for more.\n")
    assert len(found) == 1
    assert found[0].code == "render.wikilink"
    assert "Unbalanced" in found[0].message


def test_a_blank_wikilink_target_is_flagged_as_empty(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\nSee [[ ]] here.\n")
    assert len(found) == 1
    assert "Empty wikilink target" in found[0].message


def test_a_zero_length_wikilink_reads_as_unbalanced(tmp_path):
    """`[[]]` never matches the target regex (it requires one character), so it
    falls to the unbalanced branch. Ported behaviour-for-behaviour from wiki-io;
    asserted so nobody "fixes" it into the other branch by accident."""
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\nSee [[]] here.\n")
    assert len(found) == 1
    assert "Unbalanced" in found[0].message


@pytest.mark.parametrize(
    "body",
    [
        "A valid [[target]] link.\n",
        "An embed ![[image.png]] renders.\n",
        "Backticked `[[foo` never reaches the scan.\n",
        "An aliased [[target|alias]] link.\n",
    ],
)
def test_valid_and_code_wrapped_wikilinks_never_fire(tmp_path, body):
    assert one(tmp_path, "d", f"---\ntype: Note\ntitle: T\n---\n\n{body}") == []


def test_a_wikilink_broken_across_a_line_break_is_still_unbalanced(tmp_path):
    """A wikilink is a single-line construct: Obsidian renders neither of these
    lines as a link. Joining the paragraph's text children without their
    `softbreak` would splice `[[broken-here` to `and-then]]` into one
    apparently-valid `[[broken-hereand-then]]` and lose the finding entirely."""
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\nSee [[broken-here\nand-then]] more text.\n")
    assert len(found) == 1
    assert found[0].code == "render.wikilink"
    assert "Unbalanced" in found[0].message


def test_an_unbalanced_wikilink_excerpt_stops_at_the_line_end(tmp_path):
    """What follows the break is a different line and did not break this one.
    Guards against the garbled `for more.Third line` excerpt a break-dropping
    join produces."""
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\nSee [[dangling for more.\nThird line.\n")
    assert len(found) == 1
    assert "Third line" not in found[0].message


# --- Table pipes ------------------------------------------------------------


def test_a_row_wider_than_its_header_is_flagged(tmp_path):
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\n| a | b |\n|---|---|\n| 1 | 2 | 3 |\n")
    assert len(found) == 1
    assert found[0].code == "render.table-pipe"
    assert found[0].line == 8


@pytest.mark.parametrize(
    "body",
    [
        "| a | b |\n|---|---|\n| 1 |\n",
        "| a | b |\n|---|---|\n| 1 | 2 |\n",
        "| a | b |\n|---|---|\n| 1 | 2 \\| 3 |\n",
    ],
)
def test_padded_uniform_and_escaped_rows_never_fire(tmp_path, body):
    assert one(tmp_path, "d", f"---\ntype: Note\ntitle: T\n---\n\n{body}") == []


def test_a_row_with_a_leading_but_no_trailing_pipe_is_still_flagged(tmp_path):
    """A leading `|` with no trailing one is valid GFM. Covers `_split_pipes`'s
    `endswith("|")` false branch, which -- unlike the `startswith("|")` guard
    beside it -- is genuinely reachable from `_table_pipes`."""
    found = one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n\n| a | b |\n|---|---|\n| 1 | 2 | 3\n")
    assert len(found) == 1
    assert found[0].code == "render.table-pipe"


# --- Inline line precision ---------------------------------------------------
#
# markdown-it gives an inline token the whole *block's* map, so both inline
# codes must place a finding by walking the block's children and counting line
# breaks. Without that, every finding past a paragraph's first line points at
# the paragraph instead. `callout` and `table-pipe` read raw source lines and
# are exact by construction -- they are asserted above.


def test_an_inline_finding_names_its_own_line_not_the_paragraphs_first(tmp_path):
    found = one(
        tmp_path,
        "d",
        "---\ntype: Note\ntitle: T\n---\n\nFirst line of the paragraph.\nSecond line has <slug> in it.\nThird.\n",
    )
    assert len(found) == 1
    assert found[0].code == "render.angle-bracket"
    assert found[0].line == 7  # body line 2, not the paragraph's line 1


def test_a_wikilink_finding_names_its_own_line_too(tmp_path):
    found = one(
        tmp_path,
        "d",
        "---\ntype: Note\ntitle: T\n---\n\nFirst line here.\nSee [[dangling for more.\nThird line.\n",
    )
    assert len(found) == 1
    assert found[0].code == "render.wikilink"
    assert found[0].line == 7


def test_a_multi_line_raw_html_chunk_still_advances_the_line(tmp_path):
    """An `html_inline` child may itself span lines. Counting only `softbreak`
    children would leave everything after it a line short."""
    found = one(
        tmp_path,
        "d",
        "---\ntype: Note\ntitle: T\n---\n\nA <span\nclass='x'> tag, then <slug> here.\n",
    )
    assert [f.line for f in found] == [7]


def test_a_multi_line_code_span_still_costs_one_line(tmp_path):
    """The one imprecision left, pinned rather than hidden. CommonMark collapses
    the line ending inside a code span to a space, so `content` reports no
    newline and the walk cannot see it. Reporting the block's first line -- what
    this replaced -- was wrong for every child past line 1, not just these."""
    found = one(
        tmp_path,
        "d",
        "---\ntype: Note\ntitle: T\n---\n\nText with a `code\nspan` and then <slug>.\n",
    )
    assert [f.line for f in found] == [6]  # the truth is 7


# --- Severity ---------------------------------------------------------------


def test_every_code_defaults_to_warn():
    assert {f.severity for f in run(ext_helpers.unrendered_bundle())} == {"warn"}


def test_every_code_honours_the_severity_argument():
    found = run(ext_helpers.unrendered_bundle(), severity="error")
    assert {f.severity for f in found} == {"error"}
    assert {f.code for f in found} == set(CODES)


def test_the_default_never_changes_conformance():
    bundle = ext_helpers.unrendered_bundle()
    without = validate(bundle, today=TODAY)
    with_rule = validate(bundle, today=TODAY, extra_rules=[render_rule()])
    assert with_rule.ok == without.ok


def test_strict_promotes_the_house_rule_with_no_second_namespace():
    assert {f.severity for f in run(ext_helpers.unrendered_bundle(), strict=True)} == {"error"}


# --- The topic contract -----------------------------------------------------


def test_every_code_starts_with_the_topic_prefix():
    assert CODES
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)


def test_every_emitted_code_is_a_member_of_codes():
    assert {f.code for f in run(ext_helpers.unrendered_bundle())} == set(CODES)


def test_findings_are_shape_identical_to_built_ins():
    report = validate(ext_helpers.unrendered_bundle(), today=TODAY, extra_rules=[render_rule()])
    house = [f for f in report.findings if f.code.startswith("render.")]
    assert house
    assert all(isinstance(f, Finding) for f in house)
    assert all(f.path is not None and "." in f.code for f in house)
    assert report.findings == tuple(
        sorted(report.findings, key=lambda f: (f.path or "", f.line or 0, f.code, f.message))
    )


def test_a_document_with_an_empty_body_is_skipped(tmp_path):
    """Nothing to parse. Guards the `if not body` short-circuit."""
    assert one(tmp_path, "d", "---\ntype: Note\ntitle: T\n---\n") == []


def test_the_line_offset_survives_a_crlf_document(tmp_path):
    """`body_line_offset` is computed from the splitter's verbatim pieces, not
    from `raw_text.find(body)`. A CRLF document is where a search mis-locates."""
    text = "---\r\ntype: Note\r\ntitle: T\r\n---\r\n\r\nReplace <slug> here.\r\n"
    (tmp_path / "d.md").write_bytes(text.encode("utf-8"))
    found = run(load_bundle(tmp_path))
    assert len(found) == 1
    assert found[0].line == parse(text).body_line_offset + 1
