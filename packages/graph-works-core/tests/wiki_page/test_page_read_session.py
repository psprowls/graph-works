"""`page_read` over a read session: exact-id parity, one-file parse, stored edges."""

from __future__ import annotations

import os
import sys
import unicodedata

import pytest
from graph_works_core.read_session import bundle_backend, open_read_session
from graph_works_core.wiki_page import commands as wiki_commands
from graph_works_core.wiki_page import page_read, run_page_read
from okf_ext import readindex
from okf_ext.readindex import sync
from read_model_helpers import age_bundle, new_layout, oracle_page_read, populate_generated, write


@pytest.fixture
def layout(tmp_path):
    layout = new_layout(tmp_path)
    populate_generated(layout)
    age_bundle(layout)
    return layout


@pytest.fixture
def counters(monkeypatch):
    seen = {"core_parse": [], "index_parse": [], "graphs": 0, "loads": 0, "reconciles": 0}
    real_core, real_sync = wiki_commands.read_member, sync.read_member
    monkeypatch.setattr(
        wiki_commands, "read_member", lambda root, mid: seen["core_parse"].append(mid) or real_core(root, mid)
    )
    monkeypatch.setattr(sync, "read_member", lambda root, mid: seen["index_parse"].append(mid) or real_sync(root, mid))
    real_graph, real_load = bundle_backend.build_link_graph, bundle_backend.load_bundle_at

    def graph(*a, **k):
        seen["graphs"] += 1
        return real_graph(*a, **k)

    def load(*a, **k):
        seen["loads"] += 1
        return real_load(*a, **k)

    real_reconcile = readindex.reconcile

    def reconcile(index):
        seen["reconciles"] += 1
        return real_reconcile(index)

    monkeypatch.setattr(bundle_backend, "build_link_graph", graph)
    monkeypatch.setattr(bundle_backend, "load_bundle_at", load)
    monkeypatch.setattr(readindex, "reconcile", reconcile)
    return seen


def _reset(seen) -> None:
    seen["core_parse"].clear()
    seen["index_parse"].clear()
    seen["graphs"] = seen["loads"] = seen["reconciles"] = 0


def test_page_read_matches_the_full_load(layout) -> None:
    for page_id in ("docs/p", "work/feature-a", "work/feature-a/references/01-design", "docs/yaml"):
        assert run_page_read(layout, page_id) == oracle_page_read(layout, page_id)


def test_warm_page_read_parses_one_file_and_builds_no_graph(layout, counters) -> None:
    run_page_read(layout, "docs/p")  # warm-up: builds the index
    _reset(counters)
    run_page_read(layout, "docs/p")
    assert counters["core_parse"] == ["docs/p.md"]
    assert counters["index_parse"] == []
    assert (counters["graphs"], counters["loads"]) == (0, 0)


def test_unrelated_pages_do_not_change_the_cost(layout, counters) -> None:
    run_page_read(layout, "docs/p")
    for n in range(50):
        write(layout, f"docs/extra/e{n}.md", f"---\ntitle: E{n}\n---\n[p](/docs/p.md)\n")
    age_bundle(layout)
    run_page_read(layout, "docs/p")  # warm again after the additions
    _reset(counters)
    result = run_page_read(layout, "docs/p")
    assert counters["core_parse"] == ["docs/p.md"]
    assert counters["index_parse"] == []
    assert (counters["graphs"], counters["loads"]) == (0, 0)
    assert len([b for b in result.backlinks if b.startswith("docs/extra/")]) == 50


@pytest.mark.parametrize(
    "page_id", ["index", "log", "pic", "docs/gone", "repositories/clone/references/git/README", "docs/bad"]
)
def test_non_concepts_and_unknowns_are_unknown(layout, page_id) -> None:
    result = run_page_read(layout, page_id)
    assert result.refusal == "unknown-page"
    assert result == oracle_page_read(layout, page_id)


def test_nfc_variant_is_unknown(layout) -> None:
    nfd = unicodedata.normalize("NFD", "docs/café")
    assert nfd != "docs/café"
    assert run_page_read(layout, nfd).refusal == "unknown-page"
    assert oracle_page_read(layout, nfd).refusal == "unknown-page"
    assert run_page_read(layout, "docs/café").refusal is None


def test_page_deleted_after_reconcile_is_unknown(layout) -> None:
    with open_read_session(layout) as session:
        (layout.bundle_dir / "docs/p.md").unlink()
        result = page_read(session, layout, "docs/p")
    assert result.refusal == "unknown-page"
    assert result.body == "" and result.outlinks == () and result.backlinks == ()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="chmod 000 has no effect")
def test_page_made_unreadable_after_reconcile_is_unknown(layout) -> None:
    path = layout.bundle_dir / "docs/p.md"
    with open_read_session(layout) as session:
        path.chmod(0)
        try:
            result = page_read(session, layout, "docs/p")
        finally:
            path.chmod(0o644)
    assert result.refusal == "unknown-page"


def test_yaml_error_page_reads_with_parse_error(layout) -> None:
    result = run_page_read(layout, "docs/yaml")
    assert result.refusal is None
    assert result.parse_error is not None and ": " in result.parse_error
    assert "body survives" in result.body
    assert result == oracle_page_read(layout, "docs/yaml")


def test_one_session_serves_two_reads_without_reconciling_again(layout, counters) -> None:
    run_page_read(layout, "docs/p")
    _reset(counters)
    with open_read_session(layout) as session:
        first = page_read(session, layout, "docs/p")
        second = page_read(session, layout, "docs/p")
    assert first == second
    assert counters["reconciles"] == 1
