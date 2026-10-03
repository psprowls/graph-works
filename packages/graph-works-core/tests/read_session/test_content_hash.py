"""The index's change stamp, exposed without putting it on rows (D-003)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from graph_works_core.read_session import open_read_session
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_ext.readindex.view import IndexView


def _write(workspace: WorkspaceLayout, rel: str, text: str) -> bytes:
    path = workspace.bundle_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path.read_bytes()


def test_index_backend_returns_the_stored_hash(workspace) -> None:
    data = _write(workspace, "concepts/a.md", "---\ntype: Concept\ntitle: A\n---\nbody\n")
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        assert session.content_hash("concepts/a.md") == hashlib.sha256(data).hexdigest()
        assert session.member("concepts/a.md").sha256 is None
        assert session.content_hash("concepts/missing.md") is None


def test_bundle_backend_returns_none(workspace) -> None:
    _write(workspace, "concepts/a.md", "---\ntype: Concept\ntitle: A\n---\nbody\n")
    workspace.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as session:
        assert session.backend == "bundle"
        assert session.content_hash("concepts/a.md") is None


def test_hash_follows_an_edit_after_reconcile(workspace) -> None:
    _write(workspace, "concepts/a.md", "---\ntype: Concept\ntitle: A\n---\none\n")
    with open_read_session(workspace):
        pass
    data = _write(workspace, "concepts/a.md", "---\ntype: Concept\ntitle: A\n---\ntwo, longer\n")
    with open_read_session(workspace) as session:
        assert session.content_hash("concepts/a.md") == hashlib.sha256(data).hexdigest()


def _refuse_member(self, path):
    raise sqlite3.OperationalError("disk I/O error")


def test_hash_error_before_first_result_switches_to_bundle(workspace, monkeypatch) -> None:
    with open_read_session(workspace) as session:
        monkeypatch.setattr(IndexView, "member", _refuse_member)
        assert session.content_hash("docs/explanations/p.md") is None
        assert (session.backend, session.fallback) == ("bundle", "error")
        assert session.content_hash("docs/explanations/p.md") is None


def test_hash_result_prevents_later_backend_switch(workspace, monkeypatch) -> None:
    with open_read_session(workspace) as session:
        assert session.content_hash("docs/explanations/p.md") is not None
        monkeypatch.setattr(IndexView, "member", _refuse_member)
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            session.content_hash("docs/explanations/p.md")
        assert session.backend == "index"
