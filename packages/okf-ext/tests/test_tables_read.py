"""The generic read: tolerance, structural pipes, and the prose mask."""

from __future__ import annotations

from ext_helpers import tabled_bundle
from okf_ext.body import find_section, sections
from okf_ext.tables import Table, read, read_all

PLAIN = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"


def test_a_well_formed_table_reads_positionally():
    table = read(PLAIN)
    assert table == Table(
        headers=("A", "B"),
        rows=(("1", "2"), ("3", "4")),
        start=1,
        stop=4,
        delimiter=2,
    )


def test_a_header_with_no_delimiter_still_parses():
    table = read("| A | B |\n| 1 | 2 |\n")
    assert table is not None
    assert table.delimiter == 0
    assert table.rows == (("1", "2"),)


def test_a_header_and_delimiter_with_no_rows_is_an_empty_table():
    table = read("| A | B |\n| --- | --- |\n")
    assert table is not None
    assert table.rows == ()
    assert (table.start, table.stop, table.delimiter) == (1, 2, 2)


def test_alignment_markers_still_read_as_a_delimiter():
    table = read("| A | B | C |\n| :--- | ---: | :---: |\n| 1 | 2 | 3 |\n")
    assert table is not None
    assert table.delimiter == 2
    assert table.rows == (("1", "2", "3"),)


def test_an_escaped_pipe_is_decoded_and_does_not_split_a_cell():
    table = read("| A | B |\n| --- | --- |\n| a \\| b | c |\n")
    assert table is not None
    assert table.rows == (("a | b", "c"),)


def test_a_trailing_escaped_pipe_is_content_not_structure():
    table = read("| A |\n| --- |\n| a \\| |\n")
    assert table is not None
    assert table.rows == (("a |",),)


def test_an_empty_first_or_last_cell_survives_the_structural_strip():
    table = read("| A | B | C |\n| --- | --- | --- |\n|  | mid |  |\n")
    assert table is not None
    assert table.rows == (("", "mid", ""),)


def test_a_short_row_is_padded_and_a_long_row_keeps_its_extras():
    table = read("| A | B |\n| --- | --- |\n| 1 |\n| 1 | 2 | 3 |\n")
    assert table is not None
    assert table.rows == (("1", ""), ("1", "2", "3"))


def test_a_delimiter_shaped_line_after_the_first_data_row_is_a_data_row():
    """Only the line directly under the header can be `Table.delimiter` -- a
    `| - | - | - |` "not filled in yet" placeholder, or a copy-pasted second
    `| --- | --- | --- |`, appearing anywhere later in the table is a data
    row, not a second delimiter. This is `_build`'s rule; `okf_ext.tables.
    splice` must derive its own row line numbers from the same
    `_data_row_lines` rather than re-deriving the rule, which is what let the
    two disagree in the first place."""
    table = read("| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n| - | - | - |\n| 4 | 5 | 6 |\n")
    assert table is not None
    assert table.delimiter == 2
    assert table.rows == (("1", "2", "3"), ("-", "-", "-"), ("4", "5", "6"))


def test_a_pipe_table_inside_a_fence_is_not_a_table():
    body = "```\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```\n"
    assert read(body) is None
    assert read_all(body) == ()


def test_a_pipe_table_inside_an_indented_block_is_not_a_table():
    assert read("text\n\n    | A | B |\n    | 1 | 2 |\n") is None


def test_a_blockquoted_row_is_not_a_table_line():
    assert read("> | A | B |\n> | 1 | 2 |\n") is None


def test_two_tables_separated_by_prose_read_as_two():
    body = "| A |\n| --- |\n| 1 |\n\nbetween\n\n| B |\n| --- |\n| 2 |\n"
    found = read_all(body)
    assert len(found) == 2
    assert found[0].headers == ("A",)
    assert (found[0].start, found[0].stop) == (1, 3)
    assert found[1].headers == ("B",)
    assert (found[1].start, found[1].stop) == (7, 9)
    assert read(body) is found[0]


def test_within_restricts_the_search_to_one_section():
    body = "## First\n| A |\n| --- |\n| 1 |\n\n## Second\n| B |\n| --- |\n| 2 |\n"
    second = find_section(body, "Second")
    assert second is not None
    table = read(body, within=second)
    assert table is not None
    assert table.headers == ("B",)
    assert table.start == 7


def test_within_an_empty_section_finds_nothing():
    body = "## A\n## B\n| x |\n"
    first = find_section(body, "A")
    assert first is not None
    assert read(body, within=first) is None


def test_a_body_with_no_table_reads_none():
    assert read("just prose\n") is None
    assert read("") is None


def test_a_lone_pipe_line_is_a_header_only_table():
    table = read("| A |\n")
    assert table is not None
    assert (table.headers, table.rows, table.delimiter) == (("A",), (), 0)


def test_a_row_without_its_outer_pipes_still_splits():
    table = read("| A | B |\n| --- | --- |\n| 1 | 2\n")
    assert table is not None
    assert table.rows == (("1", "2"),)


def test_the_file_map_fixture_yields_one_table_per_h3_block():
    document = tabled_bundle().concepts["file_map"]
    blocks = [s for s in sections(document.body) if s.level == 3]
    assert len(blocks) == 2
    for block in blocks:
        table = read(document.body, within=block)
        assert table is not None
        assert table.headers[0].casefold() == "path"


def test_the_fenced_fixture_reads_only_its_real_table():
    document = tabled_bundle().concepts["fenced"]
    tables = read_all(document.body)
    assert len(tables) == 1
    assert "fake" not in {cell.casefold() for cell in tables[0].headers}


def test_the_reference_entries_fixture_decodes_an_escaped_pipe_and_pads_an_empty_cell():
    """The inline-string tests for escaped pipes and empty trailing cells cover
    the mechanism; this pins it against the real prose-shaped fixture the
    design spec (§8) uses as the Reference-mode sample, where both quirks show
    up in the same table rather than in isolation."""
    document = tabled_bundle().concepts["reference_entries"]
    table = read(document.body)
    assert table is not None
    assert ("`delimiter`", "field", "The `|---|---|` row, or 0") in table.rows
    assert ("`stop`", "field", "") in table.rows
