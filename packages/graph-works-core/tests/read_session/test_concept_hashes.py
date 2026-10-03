"""Stored hashes support derived caches without changing public member rows."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest
from graph_works_core.read_session import BundleSession, materialize, open_read_session
from okf_ext.readindex.view import IndexView
from work_tracker_okf.items import IGNORE


def test_index_backend_returns_stored_byte_hashes_in_concept_id_order(workspace):
    (workspace.bundle_dir / "a.md").write_bytes(b"\xef\xbb\xbf---\ntitle: A\n---\nx\r\n")
    (workspace.bundle_dir / "a-b.md").write_text("---\ntitle: AB\n---\ny\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        hashes = session.concept_hashes()
        rows = session.members(kind="concept")
    assert hashes is not None
    assert list(hashes) == sorted(row.concept_id for row in rows)
    assert hashes["a"] == hashlib.sha256((workspace.bundle_dir / "a.md").read_bytes()).hexdigest()
    assert all(row.sha256 is None for row in rows)
    with pytest.raises(TypeError):
        hashes["a"] = "changed"


def test_overlay_ignore_drops_hashes(workspace):
    with open_read_session(workspace) as session:
        full = session.concept_hashes()
        narrowed = session.concept_hashes(ignore=IGNORE)
        expected = {row.concept_id for row in session.members(kind="concept", ignore=IGNORE)}
        assert session.concept_hashes(ignore=("*",)) == {}
    assert full is not None and narrowed is not None
    assert set(narrowed) == expected and set(narrowed) < set(full)


def test_bundle_backend_has_no_hashes(workspace):
    assert BundleSession(workspace.bundle_dir, fallback="disabled").concept_hashes() is None


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_snapshot_preserves_hashes_after_source_closes_and_files_change(workspace, backend):
    path = workspace.bundle_dir / "docs/explanations/p.md"
    expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with open_read_session(workspace) as indexed:
        source = indexed if backend == "index" else BundleSession(workspace.bundle_dir, fallback="disabled")
        snap = materialize(source)
        expected = source.concept_hashes()
        narrowed = source.concept_hashes(ignore=IGNORE)
    path.write_bytes(b"changed")
    assert snap.concept_hashes() == expected
    assert snap.concept_hashes(ignore=IGNORE) == narrowed
    assert all(row.sha256 is None for row in snap.members())
    if backend == "index":
        assert snap.concept_hashes()["docs/explanations/p"] == expected_hash
        assert snap.concept_hashes(ignore=("docs/*",)).get("docs/explanations/p") is None
        assert snap.concept_hashes(ignore=("*",)) == {}
        with pytest.raises(TypeError):
            snap.concept_hashes()["docs/explanations/p"] = "changed"
    else:
        assert expected is None


def _fail_members(*args, **kwargs):
    raise sqlite3.OperationalError("injected hash failure")


def test_hash_query_sqlite_failure_before_result_switches_to_bundle(index_session, monkeypatch):
    monkeypatch.setattr(IndexView, "members", _fail_members)
    assert index_session.concept_hashes() is None
    assert index_session.backend == "bundle"
    assert index_session.fallback == "error"


def test_hash_query_sqlite_failure_after_result_propagates(index_session, monkeypatch):
    assert index_session.members()
    monkeypatch.setattr(IndexView, "members", _fail_members)
    with pytest.raises(sqlite3.OperationalError, match="injected hash failure"):
        index_session.concept_hashes()
    assert index_session.backend == "index"


@pytest.mark.parametrize("ignore", [(), ("*",)])
def test_hash_query_counts_as_public_result_even_when_empty(index_session, monkeypatch, ignore):
    assert index_session.concept_hashes(ignore=ignore) is not None
    monkeypatch.setattr(IndexView, "members", _fail_members)
    with pytest.raises(sqlite3.OperationalError, match="injected hash failure"):
        index_session.members()
    assert index_session.backend == "index"


def test_missing_hash_requests_oracle_unless_row_is_ignored(index_session, monkeypatch):
    members = IndexView.members

    def missing_stamp(self, **kwargs):
        return tuple(
            replace(row, sha256=None) if row.id == "docs/explanations/p.md" else row for row in members(self, **kwargs)
        )

    monkeypatch.setattr(IndexView, "members", missing_stamp)
    assert index_session.concept_hashes() is None
    assert index_session.concept_hashes(ignore=("docs/explanations/p.md",)) is not None
    assert index_session.backend == "index"
