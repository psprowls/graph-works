from __future__ import annotations

import pytest
from okf_io import _edit, _md

BODY = "# Metric\n\n* [a](a.md)\n* [b](b.md)\n\n# Policy\n\n* [c](c.md)\n"


def items(body):
    return _md.parse_body(body).list_items


def test_no_edits_is_byte_identical():
    for body in (BODY, "a\r\nb\r\n", "no trailing newline", "", "\n\n"):
        assert _edit.apply(body, ()) == body


def test_a_deletion_removes_exactly_its_range():
    after = _edit.apply(BODY, (_edit.Edit(4, 4),))
    assert after == "# Metric\n\n* [a](a.md)\n\n# Policy\n\n* [c](c.md)\n"


def test_a_multi_line_deletion_removes_exactly_its_range():
    body = "* one\n  continued\n* two\n"
    assert _edit.apply(body, (_edit.Edit(1, 2),)) == "* two\n"


def test_insert_after_lands_before_the_next_line_and_removes_nothing():
    edit = _edit.insert_after(BODY, 4, ("* [d](d.md)\n",))
    after = _edit.apply(BODY, (edit,))
    assert after == "# Metric\n\n* [a](a.md)\n* [b](b.md)\n* [d](d.md)\n\n# Policy\n\n* [c](c.md)\n"


def test_insert_after_terminates_an_unterminated_final_line():
    body = "# Metric\n\n* [a](a.md)"
    edit = _edit.insert_after(body, 3, ("* [b](b.md)\n",))
    assert _edit.apply(body, (edit,)) == "# Metric\n\n* [a](a.md)\n* [b](b.md)\n"


def test_insert_after_zero_lands_at_the_top():
    edit = _edit.insert_after(BODY, 0, ("# Heading\n",))
    assert _edit.apply(BODY, (edit,)).startswith("# Heading\n# Metric\n")


def test_insert_into_an_empty_body():
    edit = _edit.insert_after("", 0, ("* [a](a.md)\n",))
    assert _edit.apply("", (edit,)) == "* [a](a.md)\n"


def test_crlf_line_endings_survive_an_edit():
    body = "# Metric\r\n\r\n* [a](a.md)\r\n* [b](b.md)\r\n"
    after = _edit.apply(body, (_edit.Edit(4, 4),))
    assert after == "# Metric\r\n\r\n* [a](a.md)\r\n"
    assert _edit.newline_of(body) == "\r\n"
    assert _edit.newline_of("a\nb\n") == "\n"


def test_edits_are_applied_in_line_order_regardless_of_argument_order():
    edits = (_edit.Edit(8, 8), _edit.Edit(3, 3))
    assert _edit.apply(BODY, edits) == "# Metric\n\n* [b](b.md)\n\n# Policy\n\n"


def test_overlapping_edits_raise():
    with pytest.raises(ValueError, match="Overlapping edit"):
        _edit.apply(BODY, (_edit.Edit(3, 4), _edit.Edit(4, 4)))


def test_bullet_marker_follows_the_file():
    dashed = "# H\n\n- one\n"
    assert _edit.bullet_marker(dashed, items(dashed), "*") == "-"
    assert _edit.bullet_marker(BODY, items(BODY), "-") == "*"


def test_bullet_marker_falls_back_to_the_default():
    body = "# H\n\nProse only.\n"
    assert _edit.bullet_marker(body, items(body), "*") == "*"


def test_bullet_marker_ignores_an_ordered_list():
    body = "# H\n\n1. one\n"
    assert _edit.bullet_marker(body, items(body), "*") == "*"


def test_line_count_and_line_at():
    assert _edit.line_count("a\nb\n") == 2
    assert _edit.line_count("a\nb") == 2
    assert _edit.line_count("") == 0
    assert _edit.line_at("a\nb\n", 2) == "b\n"


def test_malformed_range_raises():
    """A range with end < start - 1 is malformed."""
    with pytest.raises(ValueError, match="Invalid edit range"):
        _edit.apply(BODY, (_edit.Edit(5, 2),))


def test_out_of_range_edit_raises():
    """An edit outside the body's line range raises ValueError."""
    with pytest.raises(ValueError, match="Invalid edit range"):
        _edit.apply(BODY, (_edit.Edit(100, 100),))


def test_insert_after_with_out_of_range_line_raises():
    """insert_after with a line outside [0, line_count] raises ValueError."""
    with pytest.raises(ValueError, match="Invalid line number"):
        _edit.insert_after(BODY, 100, ("* [x](x.md)\n",))
    with pytest.raises(ValueError, match="Invalid line number"):
        _edit.insert_after(BODY, -1, ("* [x](x.md)\n",))


def test_insert_after_at_very_end_works():
    """insert_after(body, line_count(body), ...) appends at the very end."""
    end_line = _edit.line_count(BODY)
    edit = _edit.insert_after(BODY, end_line, ("* [end](end.md)\n",))
    after = _edit.apply(BODY, (edit,))
    assert after.endswith("* [c](c.md)\n* [end](end.md)\n")
