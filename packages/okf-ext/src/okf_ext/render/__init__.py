"""Render correctness: does this document render the way its author meant?

    from okf_ext import render
    report = validate(bundle, today=today, extra_rules=[render.render_rule()])

Four codes over one markdown-it parse per document, plus
`escape_angle_brackets`, the preventive half of the first of them.

Split from `health` along a real seam: this asks about **one document's
markdown body** and needs `markdown-it-py`; `health` asks about **the whole
bundle** and needs the link graph, the log parser and `today`. They share no
code and no data.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.
"""

from __future__ import annotations

from okf_ext.render.escape import escape_angle_brackets
from okf_ext.render.rule import CODES, TOPIC, render_rule

#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase functions,
#: each group alphabetical -- `RUF022` enforces it and `test_ext_boundaries.py`
#: asserts the same invariant independently.
__all__ = [
    "CODES",
    "TOPIC",
    "escape_angle_brackets",
    "render_rule",
]
