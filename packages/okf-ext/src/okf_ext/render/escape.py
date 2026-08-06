"""The preventive half of `render.angle-bracket`.

A bare `<name>` token in prose spliced into a markdown body is indistinguishable
from an unclosed HTML tag to a CommonMark renderer: it becomes raw HTML and
vanishes. `render.angle-bracket` detects that after the fact; this escapes it
before the fact.

The two halves live in one capability because the property that ties them --
**the output of `escape_angle_brackets` never produces a `render.angle-bracket`
finding** -- is testable only when they are co-located. That test is in
`tests/test_ext_acceptance.py` and is the reason this function is here rather
than beside its eventual consumer.
"""

from __future__ import annotations


def escape_angle_brackets(text: str) -> str:
    """Backslash-escape literal ``<``/``>`` so CommonMark renders them literally.

    Safe on plain prose that carries no intentional markdown of its own --
    frontmatter values, docstring-derived descriptions. **Not idempotent:**
    applying it twice double-escapes, because a backslash in the input is
    indistinguishable from one this function wrote.
    """
    return text.replace("<", "\\<").replace(">", "\\>")
