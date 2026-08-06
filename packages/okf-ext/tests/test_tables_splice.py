"""`splice_text`: every row of the §5.4 behaviour table, plus byte fidelity."""

from __future__ import annotations

import pytest
from okf_ext.tables import Column, TableSpec, splice_text
from okf_ext.tables.read import _split_cells
from okf_ext.tables.splice import _escape

PLAN = TableSpec(
    columns=(
        Column("action"),
        Column("done_when", synonyms=("done when",)),
        Column("rationale"),
    )
)
ROW = {"action": "port it", "done_when": "it works", "rationale": "because"}

OK = (
    "# T\n\nintro\n\n## Plan\n\n"
    "| Action | Done when | Rationale |\n| --- | --- | --- |\n"
    "| port it | already | prior |\n\n## Notes\n\ntail\n"
)
EMPTY = "# T\n\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n\n## Notes\n\ntail\n"
PROSE = "# T\n\n## Plan\n\nprose that must survive\n\nmore prose\n\n## Notes\n\ntail\n"
ABSENT = "# T\n\nintro only\n"


def _splice(body, **kwargs):
    return splice_text(body, "Plan", PLAN, ROW, key="action", **kwargs)


# --- the six rows of the behaviour table ---------------------------------


def test_a_present_key_under_skip_changes_nothing():
    result = _splice(OK)
    assert result.action is None
    assert result.line == 0
    assert not result.changed
    assert result.after == OK


def test_a_present_key_under_update_rewrites_only_the_non_key_cells():
    result = _splice(OK, on_conflict="update")
    assert result.action == "update"
    assert result.line == 9
    assert "| port it | it works | because |" in result.after
    assert "| port it | already | prior |" not in result.after
    assert "intro" in result.after and "tail" in result.after


def test_an_absent_key_in_an_ok_table_appends_after_the_last_table_line():
    result = splice_text(OK, "Plan", PLAN, {**ROW, "action": "new one"}, key="action")
    assert result.action == "append"
    assert result.line == 10
    lines = result.after.splitlines()
    assert lines[8] == "| port it | already | prior |"
    assert lines[9] == "| new one | it works | because |"
    assert lines[10] == ""  # the blank before ## Notes is still there


def test_an_empty_table_takes_an_append_too():
    result = _splice(EMPTY)
    assert result.action == "append"
    assert result.after.splitlines()[6] == "| port it | it works | because |"


def test_a_malformed_heading_gets_a_fresh_table_and_keeps_its_prose():
    result = _splice(PROSE)
    assert result.action == "create-table"
    assert result.line == 4
    lines = result.after.splitlines()
    assert lines[2] == "## Plan"
    assert lines[3] == "| action | done_when | rationale |"
    assert lines[4] == "| --- | --- | --- |"
    assert lines[5] == "| port it | it works | because |"
    assert lines[6] == ""  # nothing below can merge into the new table
    assert lines[7] == "prose that must survive"  # no second, redundant blank line
    assert "more prose" in result.after
    assert "tail" in result.after


def test_a_missing_heading_with_create_appends_a_whole_section():
    """`ABSENT`'s last line is non-blank, so a separating gap is inserted
    before the heading. `line` marks the start of the whole inserted block
    -- the gap, not the heading -- so a caller diffing from `line` sees a
    contiguous span with no untouched line above it."""
    result = _splice(ABSENT)
    assert result.action == "create-section"
    lines = result.after.splitlines()
    assert lines[-5:] == [
        "## Plan",
        "",
        "| action | done_when | rationale |",
        "| --- | --- | --- |",
        "| port it | it works | because |",
    ]
    assert lines[result.line - 1] == ""
    assert lines[result.line] == "## Plan"
    assert result.after.startswith("# T\n\nintro only\n\n")


def test_a_missing_heading_whose_body_already_ends_blank_needs_no_gap():
    """No separating blank is inserted when the body's last line is already
    blank, and `line` then points straight at the heading."""
    already_blank = "# T\n\nintro only\n\n"
    result = splice_text(already_blank, "Plan", PLAN, ROW, key="action")
    assert result.action == "create-section"
    lines = result.after.splitlines()
    assert lines[result.line - 1] == "## Plan"
    assert lines[-5:] == [
        "## Plan",
        "",
        "| action | done_when | rationale |",
        "| --- | --- | --- |",
        "| port it | it works | because |",
    ]


def test_a_missing_heading_without_create_changes_nothing():
    result = _splice(ABSENT, create=False)
    assert result.action is None
    assert result.after == ABSENT


# --- a delimiter-shaped data row (the Critical bug) -----------------------

_PLACEHOLDER_BODY = (
    "## Plan\n\n| action | done_when | rationale |\n| --- | --- | --- |\n"
    "| port it | already | prior |\n| - | - | - |\n| second | s2 | s2r |\n"
)


def test_a_delimiter_shaped_data_row_no_longer_crashes_the_splice():
    """The reproducer: `_row_lines` used to filter out *every*
    delimiter-shaped line in the table's range, while `_build` treats only
    the line directly under the header as the delimiter -- so a `| - | - |
    - |` "not filled in" placeholder further down was a data row to `_build`
    (and to `read_section`'s row count) but not to the old `_row_lines`. That
    mismatch indexed `_row_lines(...)` past its end: `IndexError: tuple
    index out of range`. Now both derive from the single
    `read._data_row_lines`, so the placeholder is a data row everywhere, and
    the row keyed `"second"` -- which sits after it -- is the one updated."""
    result = splice_text(
        _PLACEHOLDER_BODY,
        "Plan",
        PLAN,
        {"action": "second", "done_when": "UPDATED", "rationale": "s2r"},
        key="action",
        on_conflict="update",
    )
    assert result.action == "update"
    lines = result.after.splitlines()
    assert lines[6] == "| second | UPDATED | s2r |"


def test_a_further_row_after_the_placeholder_does_not_silently_corrupt_it():
    """With a real row *after* the placeholder too, the old buggy
    `_row_lines` didn't always crash -- it silently rewrote the wrong row,
    because its shorter, placeholder-filtered list of line numbers no longer
    lined up with `table.rows`'s indices past the placeholder. Updating the
    row keyed `"second"` must change only that row's line; the placeholder
    line, the `"third"` row, and everything else must come through
    byte-identical."""
    body = _PLACEHOLDER_BODY + "| third | s3 | s3r |\n"
    before = body.splitlines(keepends=True)
    result = splice_text(
        body,
        "Plan",
        PLAN,
        {"action": "second", "done_when": "UPDATED", "rationale": "s2r"},
        key="action",
        on_conflict="update",
    )
    after = result.after.splitlines(keepends=True)
    assert result.action == "update"
    changed = [i for i in range(len(before)) if before[i] != after[i]]
    assert changed == [6]  # line 7 (1-based): the "second" row, and nothing else
    assert after[6] == "| second | UPDATED | s2r |\n"
    assert after[5] == "| - | - | - |\n"  # the placeholder is untouched
    assert after[7] == "| third | s3 | s3r |\n"  # the row after it is untouched


def test_the_placeholder_row_itself_can_be_matched_and_updated():
    """The placeholder is a data row like any other: it can be the row a key
    matches, not just a row that must be stepped over to reach one."""
    result = splice_text(
        _PLACEHOLDER_BODY,
        "Plan",
        PLAN,
        {"action": "-", "done_when": "FILLED", "rationale": "now"},
        key="action",
        on_conflict="update",
    )
    assert result.action == "update"
    lines = result.after.splitlines()
    assert lines[5] == "| - | FILLED | now |"
    assert lines[6] == "| second | s2 | s2r |"  # the row after it is untouched


# --- idempotence ---------------------------------------------------------


@pytest.mark.parametrize("body", [OK, EMPTY, PROSE, ABSENT])
@pytest.mark.parametrize("on_conflict", ["skip", "update"])
def test_splicing_twice_equals_splicing_once(body, on_conflict):
    once = _splice(body, on_conflict=on_conflict).after
    twice = _splice(once, on_conflict=on_conflict).after
    assert twice == once


def test_a_second_update_reports_no_change_at_all():
    once = _splice(OK, on_conflict="update")
    twice = _splice(once.after, on_conflict="update")
    assert twice.action is None
    assert not twice.changed


# --- byte fidelity -------------------------------------------------------


def test_a_crlf_body_stays_uniformly_crlf():
    body = OK.replace("\n", "\r\n")
    after = splice_text(body, "Plan", PLAN, {**ROW, "action": "new one"}, key="action").after
    assert "\r\n" in after
    assert "\n" not in after.replace("\r\n", "")


def test_a_crlf_body_with_a_missing_section_stays_crlf():
    body = ABSENT.replace("\n", "\r\n")
    after = _splice(body).after
    assert "\n" not in after.replace("\r\n", "")


def test_a_body_with_no_trailing_newline_gains_none():
    body = ABSENT.rstrip("\n")
    after = _splice(body).after
    assert not after.endswith("\n")


def test_a_body_with_a_trailing_newline_keeps_exactly_one():
    after = _splice(ABSENT).after
    assert after.endswith("\n")
    assert not after.endswith("\n\n")


def test_a_crlf_body_stays_uniformly_crlf_on_update():
    body = OK.replace("\n", "\r\n")
    after = _splice(body, on_conflict="update").after
    assert "\r\n" in after
    assert "\n" not in after.replace("\r\n", "")


def test_a_body_with_no_trailing_newline_keeps_none_on_update():
    body = OK.rstrip("\n")
    after = _splice(body, on_conflict="update").after
    assert not after.endswith("\n")


def test_a_crlf_body_stays_uniformly_crlf_on_create_table():
    body = PROSE.replace("\n", "\r\n")
    after = _splice(body).after
    assert "\r\n" in after
    assert "\n" not in after.replace("\r\n", "")


def test_a_body_with_no_trailing_newline_keeps_none_on_create_table():
    body = PROSE.rstrip("\n")
    after = _splice(body).after
    assert not after.endswith("\n")


def test_a_heading_at_the_end_of_the_body_with_no_trailing_newline_gains_none():
    """The `_create_table` reproducer: when the heading is the body's last
    line, `at = section.body_start` is one past the end of `lines`, so
    `already_blank` is `False` on the old code and a separator blank got
    appended regardless -- which, on a body with no trailing newline, forces
    one onto the result. There is nothing below the new table for the blank
    to guard against merging into, so none is needed here."""
    body = "# T\n\n## Plan"
    after = _splice(body).after
    assert not after.endswith("\n")
    expected = (
        "# T\n\n## Plan\n| action | done_when | rationale |\n| --- | --- | --- |\n| port it | it works | because |"
    )
    assert after == expected


def test_a_heading_at_the_end_of_the_body_with_a_trailing_newline_keeps_exactly_one():
    """Same shape, with the body's trailing newline present: the old code
    left a superfluous blank line at end of file; there is nothing after the
    heading for a separator to protect."""
    body = "# T\n\n## Plan\n"
    after = _splice(body).after
    assert after.endswith("\n")
    assert not after.endswith("\n\n")
    expected = (
        "# T\n\n## Plan\n| action | done_when | rationale |\n| --- | --- | --- |\n| port it | it works | because |\n"
    )
    assert after == expected


def test_only_the_claimed_lines_change():
    before = OK.splitlines(keepends=True)
    result = splice_text(OK, "Plan", PLAN, {**ROW, "action": "new one"}, key="action")
    after = result.after.splitlines(keepends=True)
    assert len(after) == len(before) + 1
    inserted = result.line - 1
    assert after[:inserted] == before[:inserted]
    assert after[inserted + 1 :] == before[inserted:]


# --- the escape/decode inverse --------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "a|b",
        "a\\|b",  # a literal backslash directly followed by a literal pipe
        "|||",
        "\\|\\|\\|",
        "trailing\\",
        "",
    ],
)
def test_escape_is_split_cells_exact_inverse(value):
    """`_escape` (here) and `read._split_cells`'s decode must stay exact
    inverses of each other -- idempotence depends on a value surviving
    write-then-read unchanged, and nothing else in the suite pins the pair
    directly. The two end-to-end tests that exercise pipes and backslashes
    (`test_cells_are_stripped_flattened_and_escaped`,
    `test_a_pipe_in_the_key_still_matches_on_a_second_run`) each happen to
    cover only one shape; this is every shape verified by hand in `_escape`'s
    docstring."""
    line = f"| {_escape(value)} |"
    assert _split_cells(line) == (value,)


def test_a_hand_written_double_backslash_before_a_pipe_reads_as_one_escaped_pipe():
    """Pins the one shape `_escape` and `_split_cells` are *not* mutual
    inverses over: disk text carrying two backslashes directly before a
    pipe. `read.py`'s comment on `_UNESCAPED_PIPE_RE` calls this undefined --
    "neither reference implementation handles it either, and no corpus row
    carries one" -- so this test records the current behaviour rather than
    asserting it is correct. `_split_cells` treats the pipe as escaped
    (content, not a separator) and collapses both backslashes down to one:
    `a\\\\|b` on disk decodes exactly like `a\\|b` would, so the two disk
    spellings are indistinguishable after a read. `_escape` itself never
    produces this shape -- its own one-backslash-then-pipe case is covered by
    `test_escape_is_split_cells_exact_inverse` and does round-trip."""
    line = "| a\\\\|b |"
    assert _split_cells(line) == ("a\\|b",)


# --- rendering -----------------------------------------------------------


def test_an_appended_row_uses_the_column_count_of_the_table_on_disk():
    """A four-column table gets four cells, with the unmapped one empty."""
    body = "## Plan\n\n| Action | Done when | Rationale | Owner |\n| --- | --- | --- | --- |\n| prior | x | y | me |\n"
    after = splice_text(body, "Plan", PLAN, {**ROW, "action": "new"}, key="action").after
    assert after.splitlines()[5] == "| new | it works | because |  |"


def test_an_appended_row_respects_the_disk_column_order():
    body = "## Plan\n\n| Rationale | Action |\n| --- | --- |\n| prior | old |\n"
    after = splice_text(body, "Plan", PLAN, {**ROW, "action": "new"}, key="action").after
    assert after.splitlines()[5] == "| because | new |"


def test_cells_are_stripped_flattened_and_escaped():
    row = {"action": "  a | b  ", "done_when": "one\ntwo", "rationale": ""}
    after = splice_text(EMPTY, "Plan", PLAN, row, key="action").after
    assert "| a \\| b | one two |  |" in after


def test_a_pipe_in_the_key_still_matches_on_a_second_run():
    row = {"action": "a | b", "done_when": "x", "rationale": "y"}
    once = splice_text(EMPTY, "Plan", PLAN, row, key="action").after
    assert splice_text(once, "Plan", PLAN, row, key="action").after == once


def test_a_key_not_in_the_spec_raises():
    with pytest.raises(ValueError, match="not a column"):
        splice_text(OK, "Plan", PLAN, ROW, key="owner")


def test_a_row_carrying_a_column_the_spec_does_not_know_is_ignored():
    row = {**ROW, "action": "new", "owner": "me"}
    after = splice_text(OK, "Plan", PLAN, row, key="action").after
    assert "me" not in after


def test_a_table_missing_the_key_column_appends_rather_than_matching():
    """Idempotence needs the key column present on disk; without it nothing
    can match, so every call appends. Pinned so the limitation is on the
    record rather than rediscovered."""
    body = "## Plan\n\n| Done when | Rationale |\n| --- | --- |\n| x | y |\n"
    first = splice_text(body, "Plan", PLAN, ROW, key="action").after
    assert first != body
    assert splice_text(first, "Plan", PLAN, ROW, key="action").after != first
