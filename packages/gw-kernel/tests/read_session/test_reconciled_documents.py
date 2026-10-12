"""The read session retains only this reconcile's Documents, by exact key, for its lifetime."""

from __future__ import annotations

import hashlib

from gw_kernel.read_session import BundleSession, open_read_session, reconciled_document
from okf_ext import readindex

MID = "docs/explanations/p.md"


def _sha(layout, mid=MID) -> str:
    return hashlib.sha256((layout.bundle_dir / mid).read_bytes()).hexdigest()


def _edit(layout, text="---\ntitle: Edited\n---\nnew body\n") -> None:
    (layout.bundle_dir / MID).write_text(text, encoding="utf-8", newline="\n")


def test_edited_member_is_retained_by_exact_key(workspace) -> None:
    with open_read_session(workspace):
        pass
    _edit(workspace)
    with open_read_session(workspace) as session:
        doc = reconciled_document(session, MID, _sha(workspace))
        assert doc is not None and doc.fm.title == "Edited"
        assert reconciled_document(session, MID, "0" * 64) is None
        assert reconciled_document(session, MID.upper(), _sha(workspace)) is None
        assert reconciled_document(session, MID.removesuffix(".md"), _sha(workspace)) is None


def test_warm_session_retains_nothing(workspace) -> None:
    with open_read_session(workspace):
        pass
    with open_read_session(workspace) as session:
        assert reconciled_document(session, MID, _sha(workspace)) is None


def test_reconcile_false_retains_nothing(workspace) -> None:
    with open_read_session(workspace):
        pass
    _edit(workspace)
    with open_read_session(workspace, reconcile=False) as session:
        assert reconciled_document(session, MID, _sha(workspace)) is None


def test_close_releases_and_sessions_do_not_share(workspace) -> None:
    with open_read_session(workspace):
        pass
    _edit(workspace)
    with open_read_session(workspace) as first:
        assert reconciled_document(first, MID, _sha(workspace)) is not None
    assert reconciled_document(first, MID, _sha(workspace)) is None
    with open_read_session(workspace) as second:
        assert reconciled_document(second, MID, _sha(workspace)) is None


def test_session_without_accessor_returns_none(workspace) -> None:
    session = BundleSession(workspace.bundle_dir, fallback="disabled")
    assert reconciled_document(session, MID, _sha(workspace)) is None


def test_bundle_fallback_serves_no_documents(workspace, monkeypatch) -> None:
    with open_read_session(workspace):
        pass
    _edit(workspace)

    def busy(index, **kwargs):
        raise readindex.IndexBusy("locked")

    monkeypatch.setattr(readindex, "reconcile", busy)
    with open_read_session(workspace) as session:
        assert session.backend == "bundle"
        assert reconciled_document(session, MID, _sha(workspace)) is None


def test_unsettled_reconcile_hands_off_nothing(workspace, monkeypatch) -> None:
    from okf_ext.readindex import sync

    with open_read_session(workspace):
        pass
    _edit(workspace)
    other = "docs/explanations/q.md"
    (workspace.bundle_dir / other).write_text("---\ntitle: Q\n---\n", encoding="utf-8", newline="\n")
    real = sync.read_member

    def racing(root, mid):
        result = real(root, mid)
        if mid == other:
            (root / mid).write_text("---\ntitle: Q2\n---\n", encoding="utf-8", newline="\n")
        return result

    monkeypatch.setattr(sync, "read_member", racing)
    with open_read_session(workspace) as session:
        assert session.backend == "bundle"
        assert reconciled_document(session, MID, _sha(workspace)) is None


def test_bundle_switch_serves_no_documents(workspace, monkeypatch) -> None:
    from gw_kernel.read_session import index_backend

    with open_read_session(workspace):
        pass
    _edit(workspace)
    monkeypatch.setattr(index_backend.IndexSession, "_ambiguous_collision_order", lambda self: True)
    with open_read_session(workspace) as session:
        assert session.backend == "bundle"
        assert reconciled_document(session, MID, _sha(workspace)) is None


def test_edit_after_reconcile_keeps_hash_label(workspace) -> None:
    with open_read_session(workspace):
        pass
    _edit(workspace)
    reconciled = _sha(workspace)
    with open_read_session(workspace) as session:
        _edit(workspace, "---\ntitle: Later\n---\n")
        assert reconciled_document(session, MID, _sha(workspace)) is None
        doc = reconciled_document(session, MID, reconciled)
        assert doc is not None and doc.fm.title == "Edited"
