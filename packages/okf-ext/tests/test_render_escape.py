"""`escape_angle_brackets` — the preventive half of `render.angle-bracket`.

The property that justifies co-locating it with the detective half (escaped
text never produces a finding) lives in `test_ext_acceptance.py`, which needs
the rule. These are the unit facts.
"""

from __future__ import annotations

import pytest
from okf_ext.render import escape_angle_brackets


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<slug>", "\\<slug\\>"),
        ("a < b > c", "a \\< b \\> c"),
        ("no brackets here", "no brackets here"),
        ("", ""),
        ("<<>>", "\\<\\<\\>\\>"),
    ],
)
def test_escapes_every_angle_bracket_and_nothing_else(text, expected):
    assert escape_angle_brackets(text) == expected


def test_is_not_idempotent_and_does_not_pretend_to_be():
    """Applying it twice double-escapes. The function is for splicing raw prose
    into a body once, at write time -- not for normalizing already-escaped
    text, which it cannot tell apart from prose containing a backslash."""
    assert escape_angle_brackets(escape_angle_brackets("<x>")) == "\\\\<x\\\\>"
