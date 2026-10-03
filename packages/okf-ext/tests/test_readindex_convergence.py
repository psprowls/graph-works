from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from ext_helpers import write
from okf_ext.readindex import open_index, reconcile, verify
from readindex_helpers import OLD_NS, age, assert_equivalent, assert_view_equivalent, tree

BASE = {
    "a.md": "---\ntitle: A\n---\n[b](b.md)\n",
    "b.md": "---\ntitle: B\n---\n[a](a.md)\n",
    "dir/c.md": "---\ntitle: C\n---\n",
}


def _edit(root: Path) -> None:
    write(root / "a.md", "---\ntitle: A2\n---\n[c](dir/c.md)\n")
    age(root)


def _rename(root: Path) -> None:
    (root / "b.md").rename(root / "b2.md")
    age(root)


def _delete(root: Path) -> None:
    (root / "dir/c.md").unlink()


def _add(root: Path) -> None:
    write(root / "new.md", "---\ntitle: N\n---\n[a](a.md)\n")
    age(root)


def _bad_utf8(root: Path) -> None:
    (root / "a.md").write_bytes(b"\xff\xfe")
    age(root)


def _bad_yaml(root: Path) -> None:
    write(root / "a.md", "---\ntitle: [unclosed\n---\n")
    age(root)


def _bulk(root: Path) -> None:
    for i in range(50):
        write(root / f"bulk/{i}.md", f"---\ntitle: {i}\n---\n[a](../a.md)\n")
    age(root)


def _touch(root: Path) -> None:
    os.utime(root / "a.md", ns=(OLD_NS + 10, OLD_NS + 10))


@pytest.mark.parametrize(
    "mutate", [_edit, _rename, _delete, _add, _bad_utf8, _bad_yaml, _bulk, _touch], ids=lambda f: f.__name__
)
def test_one_reconcile_converges_to_a_fresh_rebuild(tmp_path: Path, mutate: Callable[[Path], None]) -> None:
    root = tree(tmp_path / "b", BASE)
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
    mutate(root)
    with open_index(db, root) as index:
        result = reconcile(index)
        assert_view_equivalent(index)
        if mutate is _touch:
            assert result.parsed == 0 and result.generation == 1
    assert_equivalent(root, db)
    fresh = tmp_path / "fresh.db"
    assert_equivalent(root, fresh)


def test_same_size_edit_inside_racy_window_converges(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: AAAA\n---\n"}, aged=False)
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        first = reconcile(index)
        assert first.racy == ("a.md",)
        stat = (root / "a.md").stat()
        write(root / "a.md", "---\ntitle: BBBB\n---\n")
        os.utime(root / "a.md", ns=(stat.st_atime_ns, stat.st_mtime_ns))  # same size, same mtime
        result = reconcile(index)
        assert result.parsed == 1
        assert_view_equivalent(index)
    assert_equivalent(root, db)


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0, reason="chmod not enforced")
def test_directory_turning_unreadable_converges(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {**BASE, "locked/x.md": "x"})
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
    (root / "locked").chmod(0)
    try:
        with open_index(db, root) as index:
            reconcile(index)
            assert_view_equivalent(index)
        assert_equivalent(root, db)
    finally:
        (root / "locked").chmod(0o755)
    with open_index(db, root) as index:
        reconcile(index)
        assert_view_equivalent(index)
    assert_equivalent(root, db)


def test_file_deleted_between_walk_and_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from okf_ext.readindex import sync

    root = tree(tmp_path / "b", BASE)
    real = sync._read_bytes

    def vanish(root_: Path, member_id: str) -> bytes:
        (root_ / member_id).unlink(missing_ok=True)
        return real(root_, member_id)

    monkeypatch.setattr(sync, "_read_bytes", vanish)
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)  # must not raise
        assert result.parsed == 0
        assert sorted(result.added) == sorted(BASE)
        assert len(index.connection.execute("SELECT id FROM members WHERE unreadable IS NOT NULL").fetchall()) == 3
    monkeypatch.undo()
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        assert_view_equivalent(index)
    assert_equivalent(root, tmp_path / "i.db")


def test_adding_n_unrelated_pages_parses_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from okf_ext.readindex import sync

    root = tree(tmp_path / "b", BASE)
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        tree(root, {f"extra/{i}.md": "x" for i in range(7)})
        calls: list[str] = []
        real = sync.read_member

        def counting(root_: Path, member_id: str):
            calls.append(member_id)
            return real(root_, member_id)

        monkeypatch.setattr(sync, "read_member", counting)
        assert reconcile(index).parsed == 7
        assert calls == [f"extra/{i}.md" for i in range(7)]
        assert_view_equivalent(index)


@pytest.mark.parametrize(
    "damage",
    ["delete", "truncate", "schema_version", "projection_version", "okf_io_version", "fingerprint", "patterns"],
)
def test_damaged_database_rebuilds_and_is_equivalent(tmp_path: Path, damage: str) -> None:
    root = tree(tmp_path / "b", BASE)
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
    if damage == "delete":
        db.unlink()
    elif damage == "truncate":
        db.write_bytes(db.read_bytes()[:64])
    else:
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE meta SET value='wrong' WHERE key=?", (damage,))
    reasons = {
        "delete": "created",
        "truncate": "corrupt",
        "schema_version": "schema",
        "projection_version": "projection",
        "okf_io_version": "okf-io",
        "fingerprint": "fingerprint",
        "patterns": "patterns",
    }
    with open_index(db, root) as index:
        assert index.rebuilt_reason == reasons[damage]
        result = reconcile(index)
        assert result.parsed == 3
        assert_view_equivalent(index)
    assert_equivalent(root, db)


def test_verify_reports_hidden_change(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", BASE)
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        stat = (root / "a.md").stat()
        write(root / "a.md", "---\ntitle: Z\n---\n[b](b.md)\n")  # same size
        os.utime(root / "a.md", ns=(stat.st_atime_ns, stat.st_mtime_ns))
        before = list(index.connection.iterdump())
        assert verify(index) == ("a.md",)
        assert verify(index) == ("a.md",)  # verify never writes
        assert list(index.connection.iterdump()) == before


@pytest.mark.parametrize("failure", ["deleted", "unreadable"])
def test_verify_reports_read_failure_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from okf_ext.readindex import sync

    root = tree(tmp_path / "b", {**BASE, "index.md": "# I", "log.md": "# L", "skip.md": "ignored", "p.png": "asset"})
    with open_index(tmp_path / "i.db", root, ignore=("skip.md",)) as index:
        reconcile(index)
        before = list(index.connection.iterdump())
        calls = []
        real = sync._read_bytes

        def failing(root_: Path, member_id: str) -> bytes:
            calls.append(member_id)
            if failure == "unreadable" and member_id in ("a.md", "log.md"):
                raise PermissionError("denied")
            return real(root_, member_id)

        if failure == "deleted":
            (root / "a.md").unlink()
            (root / "log.md").unlink()
        monkeypatch.setattr(sync, "_read_bytes", failing)
        assert verify(index) == ("a.md", "log.md")
        assert calls == sorted([*BASE, "index.md", "log.md"])
        assert list(index.connection.iterdump()) == before


def test_verify_empty_and_unchanged_index(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {})
    with open_index(tmp_path / "i.db", root) as index:
        assert verify(index) == ()
        tree(root, BASE)
        reconcile(index)
        assert verify(index) == ()


@pytest.mark.parametrize("broken", [b"\xff", b"---\ntitle: [unclosed\n---\n"])
def test_content_failure_recovers_equivalently(tmp_path: Path, broken: bytes) -> None:
    root = tree(tmp_path / "b", BASE)
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
        (root / "a.md").write_bytes(broken)
        age(root)
        reconcile(index)
        assert_view_equivalent(index)
        write(root / "a.md", BASE["a.md"])
        age(root)
        result = reconcile(index)
        assert result.changed == ("a.md",) and result.parsed == 1
        assert_view_equivalent(index)
    assert_equivalent(root, db)


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0, reason="chmod not enforced")
def test_unreadable_file_recovers_equivalently(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", BASE)
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
        (root / "a.md").chmod(0)
        try:
            reconcile(index)
            assert_view_equivalent(index)
        finally:
            (root / "a.md").chmod(0o644)
        result = reconcile(index)
        assert result.changed == ("a.md",) and result.parsed == 1
        assert_view_equivalent(index)
    assert_equivalent(root, db)


def test_verify_includes_hashed_undecodable_member_but_skips_unhashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from okf_ext.readindex import sync

    root = tree(tmp_path / "b", {"bad.md": "x", "denied.md": "y"})
    (root / "bad.md").write_bytes(b"\xff")
    age(root)
    real = sync._read_bytes

    def denied(root_: Path, member_id: str) -> bytes:
        if member_id == "denied.md":
            raise PermissionError("denied")
        return real(root_, member_id)

    monkeypatch.setattr(sync, "_read_bytes", denied)
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        assert verify(index) == ()
        (root / "bad.md").write_bytes(b"\xfe")
        assert verify(index) == ("bad.md",)
