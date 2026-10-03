from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.read_session import location, open_read_session
from graph_works_core.workspace.init import apply_init, plan_init
from graph_works_core.workspace.layout import layout_for


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from okf_ext.readindex import sync

    seen: list[str] = []
    real = sync.read_member
    monkeypatch.setattr(sync, "read_member", lambda root, mid: seen.append(mid) or real(root, mid))
    return seen


def test_first_open_builds_then_an_unchanged_open_parses_nothing(workspace, parses, caplog) -> None:
    with open_read_session(workspace) as session:
        assert session.backend == "index" and session.fallback is None
    assert "rebuilt (created)" in caplog.text
    parses.clear()
    with open_read_session(workspace):
        pass
    assert parses == []


def test_one_edit_parses_one_file(workspace, parses) -> None:
    with open_read_session(workspace):
        pass
    parses.clear()
    path = workspace.bundle_dir / "docs/explanations/p.md"
    path.write_text("---\ntype: Explanation\ntitle: P2\n---\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as session:
        assert session.member("docs/explanations/p.md").title == "P2"
    assert parses == ["docs/explanations/p.md"]


def test_reconcile_false_parses_nothing(workspace, parses) -> None:
    with open_read_session(workspace):
        pass
    (workspace.bundle_dir / "docs/explanations/p.md").write_text(
        "---\ntitle: P3\n---\n", encoding="utf-8", newline="\n"
    )
    parses.clear()
    with open_read_session(workspace, reconcile=False) as session:
        assert session.member("docs/explanations/p.md").title == "P"
    assert parses == []


def test_session_format_bump_rebuilds_and_logs(workspace, monkeypatch, caplog) -> None:
    with open_read_session(workspace):
        pass
    monkeypatch.setattr(location, "SESSION_FORMAT", 2)
    caplog.clear()
    with open_read_session(workspace) as session:
        assert session.backend == "index"
    assert "rebuilt (fingerprint)" in caplog.text


def test_a_session_sees_one_generation(workspace) -> None:
    with open_read_session(workspace) as session:
        before = session.member("docs/explanations/p.md").title
        (workspace.bundle_dir / "docs/explanations/p.md").write_text(
            "---\ntitle: Later\n---\n", encoding="utf-8", newline="\n"
        )
        assert session.member("docs/explanations/p.md").title == before
    with open_read_session(workspace) as session:
        assert session.member("docs/explanations/p.md").title == "Later"


def test_database_follows_a_relocated_cache_dir(workspace, tmp_path: Path) -> None:
    moved = layout_for(workspace.root, cache_dir=str(tmp_path / "elsewhere"))
    with open_read_session(moved):
        pass
    assert (tmp_path / "elsewhere/read-index/bundle.db").exists()
    assert not location.database_path(workspace).exists()


def test_garbage_at_the_database_path_is_rebuilt(workspace, caplog) -> None:
    db = location.database_path(workspace)
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    with open_read_session(workspace) as session:
        assert session.backend == "index"
    assert "rebuilt (corrupt)" in caplog.text


def test_deleting_the_database_changes_no_result(workspace) -> None:
    with open_read_session(workspace) as session:
        first = (session.members(), session.broken(), tuple(session.work_snapshot()))
    for path in location.sidecar_paths(location.database_path(workspace)):
        path.unlink(missing_ok=True)
    with open_read_session(workspace) as session:
        assert (session.members(), session.broken(), tuple(session.work_snapshot())) == first


def test_missing_bundle_root_raises(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / ".works", today=date(2026, 10, 2), topic="t")).layout
    import shutil

    shutil.rmtree(layout.bundle_dir)
    with pytest.raises(FileNotFoundError), open_read_session(layout):
        pass


@pytest.mark.parametrize("caller_error", [None, sqlite3.OperationalError("caller"), OSError("caller")])
def test_exit_releases_transaction_and_connection(workspace, monkeypatch, caller_error):
    from okf_ext import readindex

    opened = []
    closes = []
    real_open = readindex.open_index
    real_close = readindex.ReadIndex.close

    def track(*args, **kwargs):
        index = real_open(*args, **kwargs)
        opened.append(index)
        return index

    def close(index):
        closes.append(index.connection.in_transaction)
        real_close(index)

    monkeypatch.setattr(readindex, "open_index", track)
    monkeypatch.setattr(readindex.ReadIndex, "close", close)

    def use():
        with open_read_session(workspace) as session:
            assert session.backend == "index"
            assert opened[0].connection.in_transaction
            session.members()
            if caller_error is not None:
                raise caller_error

    if caller_error is None:
        use()
    else:
        with pytest.raises(type(caller_error), match="caller"):
            use()
    assert closes == [False]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].connection.execute("SELECT 1")
    with open_read_session(workspace) as session:
        assert session.backend == "index"


def test_indexed_display_and_work_reads_do_not_load_bundle(workspace, monkeypatch):
    from graph_works_core.read_session import bundle_backend

    def refuse(*args, **kwargs):
        pytest.fail("indexed ordinary reads must not load bundle")

    monkeypatch.setattr(bundle_backend, "load_bundle_at", refuse)
    with open_read_session(workspace) as session:
        session.members()
        session.broken()
        session.work_snapshot()
        session.work_snapshot()
        assert session.backend == "index"


def test_disabled_repeated_reads_load_once_per_policy(workspace, monkeypatch):
    from graph_works_core.read_session import bundle_backend
    from work_tracker_okf.items import IGNORE

    workspace.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
    calls = []
    real_load = bundle_backend.load_bundle_at

    def track(root, *, ignore=()):
        calls.append(tuple(ignore))
        return real_load(root, ignore=ignore)

    monkeypatch.setattr(bundle_backend, "load_bundle_at", track)
    with open_read_session(workspace) as session:
        assert calls == []
        session.members()
        session.broken()
        session.work_snapshot()
        session.work_snapshot()
    assert calls == [(), tuple(IGNORE)]


@pytest.mark.parametrize("reset", ["absent", "deleted", "fingerprint", "corrupt"])
def test_reconcile_false_populates_rebuilt_index(workspace, monkeypatch, reset) -> None:
    db = location.database_path(workspace)
    if reset != "absent":
        with open_read_session(workspace):
            pass
    if reset == "deleted":
        for path in location.sidecar_paths(db):
            path.unlink(missing_ok=True)
    elif reset == "fingerprint":
        monkeypatch.setattr(location, "SESSION_FORMAT", 2)
    elif reset == "corrupt":
        db.write_bytes(b"not a database")
    with open_read_session(workspace, reconcile=False) as session:
        assert session.backend == "index"
        row = session.member("work/epic-a.md")
        assert row is not None and row.title == "A"
        assert "work/epic-a" in session.work_snapshot().by_path
