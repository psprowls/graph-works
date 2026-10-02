"""`resolve_citations` resolves a body's `path:N` spans against declared repositories, with no page involved."""

from __future__ import annotations

from graph_works_core.workspace.citations import resolve_citations


def test_resolve_citations_resolves_a_body_without_a_page(code_layout) -> None:
    body = "See `src/a.py:2` and `nope/missing.py:1`.\n"
    citations = resolve_citations(code_layout, body)
    assert [(c.path, c.status) for c in citations] == [("src/a.py", "resolved"), (None, "missing")]
    assert citations[0].repo == "code"


def test_resolve_citations_of_a_body_without_spans_is_empty(code_layout) -> None:
    assert resolve_citations(code_layout, "No citations here.\n") == ()
