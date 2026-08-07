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
    dominant_newline,
    has_trailing_newline,
    insert,
    needs_gap,
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
