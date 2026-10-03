"""`resolve_citations` resolves a body's `path:N` spans against declared repositories, with no page involved."""

from __future__ import annotations

import sqlite3

import pytest
from graph_works_core.workspace import display_cache, repo_files
from graph_works_core.workspace.citations import extract_citations, resolve_citations, resolve_spans


def test_resolve_citations_resolves_a_body_without_a_page(code_layout) -> None:
    body = "See `src/a.py:2` and `nope/missing.py:1`.\n"
    citations = resolve_citations(code_layout, body)
    assert [(c.path, c.status) for c in citations] == [("src/a.py", "resolved"), (None, "missing")]
    assert citations[0].repo == "code"


def test_resolve_citations_of_a_body_without_spans_is_empty(code_layout) -> None:
    assert resolve_citations(code_layout, "No citations here.\n") == ()


def test_resolve_spans_with_unavailable_cache_does_not_open_another(code_layout, monkeypatch) -> None:
    def unexpected_open(db):
        pytest.fail("an owned unavailable cache must not be opened again")

    monkeypatch.setattr(display_cache, "_connect", unexpected_open)
    citations = resolve_spans(code_layout, extract_citations("`src/a.py:2`"), cache=None)
    assert [(c.repo, c.path, c.status) for c in citations] == [("code", "src/a.py", "resolved")]


def test_shared_resolver_failed_cache_open_warns_once_and_resolves_fresh(code_layout, monkeypatch, caplog) -> None:
    attempts = 0

    def unavailable(db):
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(display_cache, "_connect", unavailable)
    citations = resolve_citations(code_layout, "`src/a.py:2`")
    assert [(c.repo, c.path, c.status) for c in citations] == [("code", "src/a.py", "resolved")]
    assert attempts == 1
    warnings = [record for record in caplog.records if record.name == display_cache.logger.name]
    assert len(warnings) == 1
    assert "display cache unavailable" in warnings[0].message


def test_empty_spans_do_not_open_cache_or_read_repositories(code_layout, monkeypatch) -> None:
    def unexpected_io(*args, **kwargs):
        pytest.fail("empty spans must not open the cache or read repositories")

    monkeypatch.setattr(display_cache, "_connect", unexpected_io)
    monkeypatch.setattr(repo_files, "declared_repos", unexpected_io)
    assert resolve_citations(code_layout, "No citations here.\n") == ()
    assert resolve_spans(code_layout, (), cache=None) == ()
