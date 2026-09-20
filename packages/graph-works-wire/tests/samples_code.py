"""Samples for `graph_works_wire.code` projections."""

from __future__ import annotations

from collections.abc import Callable

from graph_works_core.code_read import CodeExcerpt
from graph_works_wire import code

CODE: dict[str, tuple[Callable[[], object], ...]] = {
    "code.excerpt_payload": (
        lambda: code.excerpt_payload(CodeExcerpt("gw", "a.py", 3, 3, 1, 8, 10, "python", ("x", "y"), None)),
        lambda: code.excerpt_payload(CodeExcerpt("gw", "a.py", 20, 20, None, None, 10, None, (), "out-of-range")),
        lambda: code.excerpt_payload(
            CodeExcerpt("nope", "a.py", 1, 1, None, None, None, None, (), "unknown-repository")
        ),
    ),
}
