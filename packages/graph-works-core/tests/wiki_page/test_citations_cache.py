"""`run_wiki_citations` warm and cold: same answers, no parse, no page read, no git when nothing changed."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.proposals import review  # noqa: F401  (imported so the proposals path is loaded in this process)
from graph_works_core.wiki_page import citations as page_citations
from graph_works_core.wiki_page import run_wiki_citations
from graph_works_core.workspace import citations as shared
from graph_works_core.workspace import display_cache, provenance, repo_files
from graph_works_core.workspace.display_cache import open_display_cache

TODAY = date(2026, 9, 19)
BODY = "see `src/a.py:1` and `a.py:2-3` and `nope.py:1`\n\n```\n`src/a.py:9`\n```\n"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    repo_files._MEMO.clear()
    monkeypatch.setattr(repo_files, "RACY_NS", 0)


@pytest.fixture
def counters(monkeypatch):
    seen = {"git": 0, "extract": 0, "read": 0}
    real_git, real_extract, real_read = provenance.probe_git, shared.extract_citations, page_citations._read_page

    def git(cwd, *a, **kw):
        seen["git"] += 1
        return real_git(cwd, *a, **kw)

    def extract(body):
        seen["extract"] += 1
        return real_extract(body)

    def read(path):
        seen["read"] += 1
        return real_read(path)

    monkeypatch.setattr(provenance, "probe_git", git)
    monkeypatch.setattr(shared, "extract_citations", extract)
    monkeypatch.setattr(page_citations, "_read_page", read)
    return seen


@pytest.fixture
def layout(tmp_path, git_repo, declare_repos):
    host = tmp_path / "host"
    (host / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(host / ".works", today=TODAY, topic="Code")).layout
    code = git_repo(tmp_path / "code", {"src/a.py": "1\n2\n3\n"})
    other = git_repo(tmp_path / "other", {"lib/a.py": "x\n"})
    declare_repos(layout, {"code": (code, []), "other": (other, [])})
    _page(layout, BODY)
    return layout


def _page(layout, body: str) -> None:
    path = layout.bundle_dir / "concepts" / "a.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: Concept\ntitle: A\n---\n{body}", encoding="utf-8", newline="\n")


def _disabled(layout) -> None:
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")


def _enabled(layout) -> None:
    layout.local_manifest_path.unlink(missing_ok=True)


def test_cached_equals_uncached(layout):
    cold = run_wiki_citations(layout, "concepts/a")
    warm = run_wiki_citations(layout, "concepts/a")
    _disabled(layout)
    fresh = run_wiki_citations(layout, "concepts/a")
    assert cold == warm == fresh
    assert [c.status for c in fresh.citations] == ["resolved", "ambiguous", "missing"]


def test_warm_unchanged_page_runs_no_git_no_parse_no_read(layout, counters):
    run_wiki_citations(layout, "concepts/a")
    counters.update(git=0, extract=0, read=0)
    run_wiki_citations(layout, "concepts/a")
    assert counters == {"git": 0, "extract": 0, "read": 0}


def test_page_edit_reextracts_once_without_git(layout, counters):
    run_wiki_citations(layout, "concepts/a")
    _page(layout, BODY + "\nand `src/a.py:3`\n")
    counters.update(git=0, extract=0, read=0)
    result = run_wiki_citations(layout, "concepts/a")
    assert (counters["extract"], counters["git"]) == (1, 0)
    assert result.citations[-1].raw == "src/a.py:3"


def test_page_rewritten_after_reconcile_returns_read_bytes_and_stores_nothing(layout, monkeypatch):
    run_wiki_citations(layout, "concepts/a")
    _page(layout, BODY + "\nnew `src/a.py:2`\n")
    real = page_citations._read_page

    def read_after_concurrent_edit(path):
        _page(layout, "only `src/a.py:1`\n")
        return real(path)

    with open_display_cache(layout) as cache:
        before = cache._conn.execute("SELECT COUNT(*) FROM citation_spans").fetchone()[0]
    monkeypatch.setattr(page_citations, "_read_page", read_after_concurrent_edit)
    result = run_wiki_citations(layout, "concepts/a")
    assert [c.raw for c in result.citations] == ["src/a.py:1"]
    with open_display_cache(layout) as cache:
        assert cache._conn.execute("SELECT COUNT(*) FROM citation_spans").fetchone()[0] == before


def test_bundle_backend_skips_span_cache_but_uses_inventory(layout, counters, monkeypatch):
    import importlib

    from graph_works_core.read_session import bundle_backend

    session_open = importlib.import_module("graph_works_core.read_session.open")

    run_wiki_citations(layout, "concepts/a")
    monkeypatch.setattr(
        session_open,
        "_open",
        lambda layout, *, reconcile, stack: bundle_backend.BundleSession(layout.bundle_dir, fallback="busy"),
    )
    counters.update(git=0, extract=0, read=0)
    result = run_wiki_citations(layout, "concepts/a")
    assert counters["git"] == 0 and counters["extract"] == 1
    _disabled(layout)
    assert result == run_wiki_citations(layout, "concepts/a")


@pytest.mark.parametrize("enabled", [True, False])
def test_non_concepts_and_undecodable_pages_are_unknown(layout, enabled):
    if not enabled:
        _disabled(layout)
    (layout.bundle_dir / "concepts" / "bad.md").write_bytes(b"---\ntype: Concept\n---\n\xff\xfe\n")
    (layout.bundle_dir / "concepts" / "pic.png").write_bytes(b"\x89PNG")
    for page_id in ("concepts/bad", "index", "log", "concepts/pic", "concepts/missing"):
        assert run_wiki_citations(layout, page_id).refusal == "unknown-page", page_id


def test_a_git_add_in_one_repository_relists_only_it(layout, counters, tmp_path):
    import subprocess

    run_wiki_citations(layout, "concepts/a")
    (tmp_path / "other" / "nope.py").write_text("y\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "nope.py"], cwd=tmp_path / "other", check=True)
    counters.update(git=0, extract=0, read=0)
    result = run_wiki_citations(layout, "concepts/a")
    assert counters["extract"] == 0
    assert counters["git"] == 2  # rev-parse + ls-files for `other` only
    assert (result.citations[2].repo, result.citations[2].status) == ("other", "resolved")


def test_resolve_citations_signature_and_answers_unchanged(layout):
    first = shared.resolve_citations(layout, BODY)
    _disabled(layout)
    assert first == shared.resolve_citations(layout, BODY)


def test_extractor_version_is_pinned_to_the_grammar():
    # Changing _GRAMMAR without bumping EXTRACTOR_VERSION fails here; update both together.
    assert (shared.EXTRACTOR_VERSION, shared._GRAMMAR.pattern) == (
        1,
        r"(?P<path>[^\s:]+\.[A-Za-z0-9]+):(?P<start>[1-9]\d*)(?:-(?P<end>[1-9]\d*))?",
    )


def test_warm_reverse_ranges_preserve_extracted_values(layout, counters):
    _page(layout, "`src/a.py:3-1`\n")
    cold = run_wiki_citations(layout, "concepts/a")
    counters.update(git=0, extract=0, read=0)
    warm = run_wiki_citations(layout, "concepts/a")
    assert cold == warm
    assert [(c.raw, c.start, c.end, c.status) for c in warm.citations] == [("src/a.py:3-1", 3, 1, "resolved")]
    assert counters == {"git": 0, "extract": 0, "read": 0}


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("page_id", ["concepts/./a", "concepts/../concepts/a", "/concepts/a"])
def test_member_aliases_are_unknown_pages(layout, enabled, page_id):
    if not enabled:
        _disabled(layout)
    result = run_wiki_citations(layout, page_id)
    assert (result.id, result.citations, result.refusal) == (page_id, (), "unknown-page")


@pytest.mark.parametrize("replacement", [None, b"\xff"])
def test_page_unreadable_after_reconcile_is_unknown(layout, monkeypatch, replacement):
    real_read = page_citations._read_page

    def disappearing_page(path):
        if replacement is None:
            path.unlink()
        else:
            path.write_bytes(replacement)
        return real_read(path)

    monkeypatch.setattr(page_citations, "_read_page", disappearing_page)
    result = run_wiki_citations(layout, "concepts/a")
    assert (result.citations, result.refusal) == ((), "unknown-page")


def test_extractor_version_change_reextracts_cached_page(layout, counters, monkeypatch):
    import markdown_it

    run_wiki_citations(layout, "concepts/a")
    monkeypatch.setattr(markdown_it, "__version__", "future")
    counters.update(git=0, extract=0, read=0)
    result = run_wiki_citations(layout, "concepts/a")
    assert [c.status for c in result.citations] == ["resolved", "ambiguous", "missing"]
    assert counters == {"git": 0, "extract": 1, "read": 1}


def test_empty_spans_skip_repository_inventory(layout, monkeypatch):
    def unexpected_inventory(*args, **kwargs):
        pytest.fail("an empty page must not read repository inventory")

    _page(layout, "plain prose\n")
    monkeypatch.setattr(shared, "repo_file_sets_in", unexpected_inventory)
    assert run_wiki_citations(layout, "concepts/a").citations == ()
    assert run_wiki_citations(layout, "concepts/a").citations == ()


def test_failed_display_cache_open_is_not_retried_within_wiki_request(layout, monkeypatch, caplog):
    _disabled(layout)
    fresh = run_wiki_citations(layout, "concepts/a")
    _enabled(layout)
    attempts = 0

    def unavailable(db):
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(display_cache, "_connect", unavailable)
    result = run_wiki_citations(layout, "concepts/a")
    assert result == fresh
    assert [c.status for c in result.citations] == ["resolved", "ambiguous", "missing"]
    assert attempts == 1
    warnings = [record for record in caplog.records if record.name == display_cache.logger.name]
    assert len(warnings) == 1
    assert "display cache unavailable" in warnings[0].message
