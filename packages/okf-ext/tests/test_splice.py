"""The hoisted primitives, directly.

`tables` and `sections` both exercise these through their own splices; this
module is what stops a behaviour change here from being discoverable only as
a confusing failure two layers up.
"""

from __future__ import annotations

import pytest
from okf_ext.splice import (
    CR,
    CRLF,
    LF,
    assemble,
    bare_lines,
    dominant_newline,
    has_trailing_newline,
    insert,
    needs_gap,
    replace,
)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("a\nb\n", LF),
        ("a\r\nb\r\n", CRLF),
        ("a\rb\r", CR),
        ("a\r\nb\n", CRLF),  # tie on count: CRLF wins
        ("no terminator at all", LF),  # nothing to count: LF is the default
        ("", LF),
    ],
)
def test_dominant_newline_prefers_crlf_then_lf_then_cr(body, expected):
    assert dominant_newline(body) == expected


def test_a_crlf_body_is_not_counted_twice_as_lf():
    """`"\\r\\n".count("\\n")` is 1, so a naive count would make LF tie CRLF on
    every CRLF body and the tie-break would never be reached honestly."""
    assert dominant_newline("a\r\n" * 5) == CRLF


@pytest.mark.parametrize(
    ("lines", "expected"),
    [([], True), (["a\n"], True), (["a\r\n"], True), (["a\r"], True), (["a"], False), (["a\n", "b"], False)],
)
def test_has_trailing_newline(lines, expected):
    assert has_trailing_newline(lines) is expected


def test_assemble_restores_an_absent_trailing_terminator():
    assert assemble(["a\n", "b\n"], LF, trailing=False) == "a\nb"
    assert assemble(["a\n", "b\n"], LF, trailing=True) == "a\nb\n"


def test_assemble_only_strips_the_bodys_own_newline():
    """A CRLF body assembled with `newline="\\r\\n"` must lose both bytes, not
    just the `\\n` -- a bare `rstrip("\\n")` would leave a stray `\\r`."""
    assert assemble(["a\r\n"], CRLF, trailing=False) == "a"


def test_insert_terminates_each_new_line_and_keeps_the_rest():
    assert insert(["a\n", "c\n"], 2, ["b"], LF) == ["a\n", "b\n", "c\n"]


def test_insert_at_the_start_pushes_everything_down():
    assert insert(["a\n"], 1, ["z"], LF) == ["z\n", "a\n"]


def test_insert_past_the_end_terminates_the_previously_last_line():
    """Otherwise the first inserted line runs onto it."""
    assert insert(["a"], 2, ["b"], LF) == ["a\n", "b\n"]


def test_insert_past_the_end_leaves_an_already_terminated_line_alone():
    assert insert(["a\n"], 2, ["b"], LF) == ["a\n", "b\n"]


def test_insert_into_an_empty_line_list():
    assert insert([], 1, ["a"], LF) == ["a\n"]


@pytest.mark.parametrize(
    ("lines", "at", "expected"),
    [
        (["a\n"], 2, True),  # appending under non-blank prose
        (["a\n", "\n"], 3, False),  # the line above is already blank
        (["a\n"], 1, False),  # nothing above to separate from
        ([], 1, False),  # empty body
        (["## H\n", "x\n"], 2, True),  # inserting between two non-blank lines
    ],
)
def test_needs_gap(lines, at, expected):
    assert needs_gap(lines, at) is expected


def test_replace_over_an_empty_range_is_exactly_an_insert():
    """`replace(lines, at, at - 1, ...)` claims no existing line, which is
    what `insert` means. Stated as a property rather than a worked example
    because a section with a heading and no body has `body_start > stop`, and
    that is the path it takes."""
    lines = ["a\n", "b\n", "c\n"]
    for at in range(1, len(lines) + 2):
        assert replace(lines, at, at - 1, ["x"], "\n") == insert(lines, at, ["x"], "\n")


def test_replace_claims_the_inclusive_range_it_names():
    lines = ["a\n", "b\n", "c\n", "d\n"]
    assert replace(lines, 2, 3, ["x", "y"], "\n") == ["a\n", "x\n", "y\n", "d\n"]


def test_replace_can_empty_a_range():
    lines = ["a\n", "b\n", "c\n"]
    assert replace(lines, 2, 2, [], "\n") == ["a\n", "c\n"]


def test_replace_terminates_a_previous_last_line_that_lacked_one():
    """Same discipline `insert` carries: otherwise the new content would run
    onto it."""
    lines = ["a\n", "b"]
    assert replace(lines, 3, 2, ["c"], "\n") == ["a\n", "b\n", "c\n"]


def test_replace_uses_the_newline_it_is_given():
    lines = ["a\r\n", "b\r\n"]
    assert replace(lines, 1, 1, ["x"], "\r\n") == ["x\r\n", "b\r\n"]


def test_bare_lines_drops_blank_ends_and_normalises_terminators():
    assert bare_lines("\n\nfirst\r\nsecond\r\n\n") == ["first", "second"]


def test_bare_lines_keeps_interior_blanks():
    assert bare_lines("a\n\nb\n") == ["a", "", "b"]


def test_bare_lines_of_blank_text_is_empty():
    assert bare_lines("") == []
    assert bare_lines("\n  \n") == []
