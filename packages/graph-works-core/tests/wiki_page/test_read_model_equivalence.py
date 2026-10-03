"""`page_read` / `wiki_tree` answer the same on the index backend, the bundle backend and the pre-change full load."""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path

import pytest
from graph_works_core.read_session import BundleSession, open_read_session
from graph_works_core.wiki_page import page_read, run_page_read, run_wiki_tree, wiki_tree
from okf_ext import readindex
from read_model_helpers import (
    FIXTURE_BUNDLES,
    OKF_IO_FIXTURES,
    concept_ids,
    new_layout,
    oracle_page_read,
    oracle_wiki_tree,
    populate_fixture,
    populate_generated,
    write,
)


def _assert_equivalent(layout, *, backend="index", fallback=None) -> None:
    ids = concept_ids(layout)
    assert ids, "the bundle has no concepts to compare"
    bundle = BundleSession(layout.bundle_dir, fallback=None)
    with open_read_session(layout) as session:
        assert (session.backend, session.fallback) == (backend, fallback)
        for page_id in [*ids, "index", "no/such/page"]:
            expected = oracle_page_read(layout, page_id)
            assert page_read(session, layout, page_id) == expected, page_id
            assert page_read(bundle, layout, page_id) == expected, page_id
        expected_tree = oracle_wiki_tree(layout)
        assert wiki_tree(session, layout) == expected_tree
        assert wiki_tree(bundle, layout) == expected_tree


def test_fixture_paths_exist() -> None:
    assert OKF_IO_FIXTURES.is_dir()


@pytest.mark.parametrize("source", FIXTURE_BUNDLES, ids=lambda p: p.name)
def test_fixture_bundles(tmp_path: Path, source: Path) -> None:
    layout = new_layout(tmp_path)
    populate_fixture(layout, source)
    _assert_equivalent(layout)


def test_generated_bundle(tmp_path: Path) -> None:
    layout = new_layout(tmp_path)
    populate_generated(layout)
    _assert_equivalent(layout)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="chmod 000 has no effect")
def test_generated_bundle_with_an_unreadable_file(tmp_path: Path) -> None:
    layout = new_layout(tmp_path)
    populate_generated(layout)
    locked = write(layout, "docs/locked.md", "---\ntitle: L\n---\n[p](p.md)\n")
    locked.chmod(0)
    try:
        _assert_equivalent(layout)
    finally:
        locked.chmod(0o644)


def test_bundle_backend_gives_the_same_answers(tmp_path: Path) -> None:
    layout = new_layout(tmp_path)
    populate_generated(layout)
    index_answers = (run_page_read(layout, "docs/p"), run_wiki_tree(layout))
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    with open_read_session(layout) as session:
        assert (session.backend, session.fallback) == ("bundle", "disabled")
    assert (run_page_read(layout, "docs/p"), run_wiki_tree(layout)) == index_answers


def test_generated_bundle_with_nfc_collisions(tmp_path: Path) -> None:
    layout = new_layout(tmp_path)
    populate_generated(layout)
    nfc = "docs/collision/café.md"
    nfd = unicodedata.normalize("NFD", nfc)
    write(layout, nfc, "---\ntitle: First\n---\n[p](/docs/p.md)\n")
    write(layout, nfd, "---\ntitle: Second\n---\n[p](/docs/p.md)\n")
    names = {p.name for p in (layout.bundle_dir / "docs/collision").iterdir()}
    if len(names) != 2:
        pytest.skip("filesystem does not preserve distinct NFC/NFD names")
    write(layout, "index.md", f"# I\n\n## S\n\n- [A](/{nfc})\n- [B](/{nfd})\n")
    _assert_equivalent(
        layout,
        backend="bundle" if sys.platform == "win32" else "index",
        fallback="unavailable" if sys.platform == "win32" else None,
    )


def test_busy_bundle_backend_gives_the_same_answers(tmp_path: Path, monkeypatch) -> None:
    layout = new_layout(tmp_path)
    populate_generated(layout)
    expected = (oracle_page_read(layout, "docs/p"), oracle_wiki_tree(layout))

    def busy(index):
        raise readindex.IndexBusy("test lock contention")

    monkeypatch.setattr(readindex, "reconcile", busy)
    with open_read_session(layout) as session:
        assert (session.backend, session.fallback) == ("bundle", "busy")
        assert (page_read(session, layout, "docs/p"), wiki_tree(session, layout)) == expected
