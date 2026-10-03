from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys

import pytest
from graph_works_core.read_session import location, open_read_session
from okf_ext import readindex


def _edit(workspace) -> None:
    (workspace.bundle_dir / "docs/explanations/p.md").write_text(
        "---\ntitle: Fresh\n---\n", encoding="utf-8", newline="\n"
    )


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "serving the full bundle load" in r.getMessage()
    ]


def test_disabled_selects_the_bundle_backend_silently(workspace, caplog) -> None:
    workspace.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback) == ("bundle", "disabled")
    assert _warnings(caplog) == []
    assert not location.database_path(workspace).exists()


def test_busy_falls_back_with_fresh_content(workspace, monkeypatch, caplog) -> None:
    with open_read_session(workspace):
        pass
    _edit(workspace)
    monkeypatch.setattr(location, "BUSY_TIMEOUT_MS", 50)
    script = (
        "import sqlite3,sys; c=sqlite3.connect(sys.argv[1],isolation_level=None); "
        "c.execute('BEGIN IMMEDIATE'); print('locked',flush=True); "
        "sys.stdin.readline(); c.execute('ROLLBACK'); c.close()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, str(location.database_path(workspace))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        assert holder.poll() is None
        with open_read_session(workspace) as session:
            assert (session.backend, session.fallback) == ("bundle", "busy")
            assert session.member("docs/explanations/p.md").title == "Fresh"
    finally:
        try:
            _, stderr = holder.communicate("release\n", timeout=10)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.communicate()
            raise
    assert holder.returncode == 0, stderr
    assert len(_warnings(caplog)) == 1 and str(location.database_path(workspace)) in _warnings(caplog)[0]


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0, reason="chmod")
def test_read_only_cache_dir_is_unavailable(workspace, caplog) -> None:
    _edit(workspace)
    workspace.cache_dir.mkdir(parents=True, exist_ok=True)
    workspace.cache_dir.chmod(0o500)
    try:
        with open_read_session(workspace) as session:
            assert (session.backend, session.fallback) == ("bundle", "unavailable")
            assert session.member("docs/explanations/p.md").title == "Fresh"
    finally:
        workspace.cache_dir.chmod(0o755)
    assert len(_warnings(caplog)) == 1


def test_wal_unavailable_falls_back(workspace, monkeypatch, caplog) -> None:
    def refuse(*args, **kwargs):
        raise readindex.IndexUnavailable("journal_mode returned delete")

    _edit(workspace)
    monkeypatch.setattr(readindex, "open_index", refuse)
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback) == ("bundle", "unavailable")
        assert session.member("docs/explanations/p.md").title == "Fresh"
    assert len(_warnings(caplog)) == 1


def test_sqlite_error_before_the_first_result_switches_backend(workspace, monkeypatch, caplog) -> None:
    from okf_ext.readindex.view import IndexView

    def boom(self, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(IndexView, "members", boom)
    _edit(workspace)
    with open_read_session(workspace) as session:
        rows = session.members()
        assert (session.backend, session.fallback, session.generation) == ("bundle", "error", None)
        assert any(r.title == "Fresh" for r in rows)
    assert len(_warnings(caplog)) == 1


def test_sqlite_error_after_a_result_propagates(workspace, monkeypatch) -> None:
    from okf_ext.readindex.view import IndexView

    with open_read_session(workspace) as session:
        session.members()
        monkeypatch.setattr(
            IndexView, "broken", lambda self, source=None: (_ for _ in ()).throw(sqlite3.OperationalError("x"))
        )
        with pytest.raises(sqlite3.OperationalError):
            session.broken()


@pytest.mark.parametrize("error", [KeyError("caller"), sqlite3.OperationalError("caller"), OSError("caller")])
def test_a_caller_exception_is_not_a_fallback(workspace, caplog, error) -> None:
    with pytest.raises(type(error), match="caller"), open_read_session(workspace):
        raise error
    assert _warnings(caplog) == []


def test_actual_unsettled_reconcile_uses_fresh_bundle(workspace, monkeypatch, caplog):
    from okf_ext.readindex import sync

    with open_read_session(workspace):
        pass
    _edit(workspace)
    real_read = sync.read_member
    real_reconcile = readindex.reconcile
    results = []

    def racing_read(root, mid):
        result = real_read(root, mid)
        if mid == "docs/explanations/p.md":
            (root / mid).write_text("---\ntitle: Newest\n---\n", encoding="utf-8", newline="\n")
        return result

    def capture(index):
        result = real_reconcile(index)
        results.append(result)
        return result

    monkeypatch.setattr(sync, "read_member", racing_read)
    monkeypatch.setattr(readindex, "reconcile", capture)
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback, session.generation) == ("bundle", "unavailable", None)
        assert session.member("docs/explanations/p.md").title == "Newest"
    assert results[0].unsettled == ("docs/explanations/p.md",)
    assert len(_warnings(caplog)) == 1


@pytest.mark.parametrize("stage", ["open", "reconcile", "read", "construct"])
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (readindex.IndexBusy("locked"), "busy"),
        (readindex.IndexUnavailable("no WAL"), "unavailable"),
        (OSError("cache refused"), "unavailable"),
        (sqlite3.OperationalError("disk I/O error"), "error"),
    ],
)
def test_opener_failure_table_closes_resources(workspace, monkeypatch, caplog, stage, error, reason):
    import importlib

    opener = importlib.import_module("graph_works_core.read_session.open")
    with open_read_session(workspace):
        pass
    _edit(workspace)
    opened = []
    real_open = readindex.open_index

    def track(*args, **kwargs):
        index = real_open(*args, **kwargs)
        opened.append(index)
        return index

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(readindex, "open_index", track)
    if stage == "construct":
        monkeypatch.setattr(opener, "IndexSession", refuse)
    else:
        monkeypatch.setattr(readindex, {"open": "open_index", "reconcile": "reconcile", "read": "read"}[stage], refuse)
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback) == ("bundle", reason)
        assert session.member("docs/explanations/p.md").title == "Fresh"
    assert len(_warnings(caplog)) == 1
    for index in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            index.connection.execute("SELECT 1")


def test_real_read_entry_failure_closes_connection(workspace, monkeypatch, caplog):
    from okf_ext.readindex import view

    opened = []
    real_open = readindex.open_index

    def track(*args, **kwargs):
        index = real_open(*args, **kwargs)
        opened.append(index)
        return index

    def refuse(*args, **kwargs):
        raise sqlite3.OperationalError("view construction")

    monkeypatch.setattr(readindex, "open_index", track)
    monkeypatch.setattr(view, "IndexView", refuse)
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback) == ("bundle", "error")
    assert len(_warnings(caplog)) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].connection.execute("SELECT 1")


def test_cache_parent_is_a_file_uses_fresh_bundle(workspace, caplog):
    _edit(workspace)
    workspace.cache_dir.mkdir(parents=True, exist_ok=True)
    location.database_path(workspace).parent.write_bytes(b"cache obstruction")
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback) == ("bundle", "unavailable")
        assert session.member("docs/explanations/p.md").title == "Fresh"
    assert len(_warnings(caplog)) == 1


def test_opener_preserves_inexact_work_fidelity_and_pinning(workspace, caplog):
    path = workspace.bundle_dir / "work/epic-a.md"
    path.write_text(
        "---\ntype: Epic\ntitle: Original\nsources:\n"
        "  - id: s\n    resource: /docs/explanations/p.md\n    payload: !!binary YWJj\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    with open_read_session(workspace) as session:
        assert (session.backend, session.fallback, session.generation) == ("bundle", "unavailable", None)
        path.write_text("---\ntype: Epic\ntitle: Changed\n---\n", encoding="utf-8", newline="\n")
        item = session.work_snapshot().by_path["work/epic-a"]
        assert item.title == "Original"
        assert item.sources[0].extra["payload"] == b"abc"
        assert session.member("work/epic-a.md").title == "Original"
    assert len(_warnings(caplog)) == 1
