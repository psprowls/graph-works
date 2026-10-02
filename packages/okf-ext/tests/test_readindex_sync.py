from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from ext_helpers import write
from okf_ext.readindex import IndexBusy, open_index, reconcile, sync
from readindex_helpers import age, tree


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = sync.read_member

    def counting(root: Path, member_id: str):
        calls.append(member_id)
        return real(root, member_id)

    monkeypatch.setattr(sync, "read_member", counting)
    return calls


def test_first_reconcile_parses_every_markdown_member(tmp_path: Path, counted: list[str]) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: A\n---\n", "index.md": "# I\n", "p.png": "x"})
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)
    assert sorted(counted) == ["a.md", "index.md"]
    assert result.parsed == 2 and result.generation == 1 and result.published
    assert sorted(result.added) == ["a.md", "index.md", "p.png"]


def test_unchanged_reconcile_reads_nothing_and_writes_nothing(
    tmp_path: Path, counted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tree(tmp_path / "b", {"a.md": "x", "b.md": "y"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        counted.clear()
        reads: list[str] = []
        real = sync._read_bytes

        def reading(root_: Path, member_id: str) -> bytes:
            reads.append(member_id)
            return real(root_, member_id)

        monkeypatch.setattr(sync, "_read_bytes", reading)
        statements: list[str] = []
        index.connection.set_trace_callback(statements.append)
        result = reconcile(index)
        assert reads == []
        assert not any(
            sql.upper().startswith(("BEGIN IMMEDIATE", "INSERT", "UPDATE", "DELETE", "COMMIT")) for sql in statements
        )
    assert counted == [] and result.parsed == 0 and not result.published and result.generation == 1


def test_one_edit_parses_one(tmp_path: Path, counted: list[str]) -> None:
    root = tree(tmp_path / "b", {"a.md": "x", "b.md": "y"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        counted.clear()
        write(root / "a.md", "changed")
        age(root)
        result = reconcile(index)
    assert counted == ["a.md"] and result.changed == ("a.md",) and result.generation == 2


def test_touch_parses_nothing_and_does_not_bump(tmp_path: Path, counted: list[str]) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        counted.clear()
        os.utime(root / "a.md", ns=(10**18, 10**18 + 5))
        result = reconcile(index)
    assert counted == [] and result.generation == 1 and result.published  # stat refresh only


def test_recent_file_is_racy_and_rehashed(tmp_path: Path, counted: list[str]) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"}, aged=False)
    with open_index(tmp_path / "i.db", root) as index:
        first = reconcile(index)
        assert first.racy == ("a.md",)
        counted.clear()
        age(root)
        second = reconcile(index)
    assert counted == [] and second.racy == () and second.generation == 1


def test_mid_write_file_is_unsettled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"})
    real = sync._read_bytes

    def racing(root_: Path, member_id: str) -> bytes:
        data = real(root_, member_id)
        write(root_ / member_id, "longer content now")
        return data

    monkeypatch.setattr(sync, "_read_bytes", racing)
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)
    assert result.unsettled == ("a.md",) and result.added == ()


def test_custom_yaml_tag_is_stored_inexact(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: A\nodd: !custom value\n---\n"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        row = index.connection.execute("SELECT fm_exact, title FROM members WHERE id = 'a.md'").fetchone()
    assert row == (0, "A")


def test_busy_write_raises_index_busy(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"})
    db = tmp_path / "i.db"
    open_index(db, root).close()
    blocker = sqlite3.connect(db, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with open_index(db, root, busy_timeout_ms=50) as index, pytest.raises(IndexBusy):
            reconcile(index)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_failure_before_commit_keeps_prior_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"})
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
        write(root / "a.md", "new")
        age(root)
        monkeypatch.setattr(sync, "_bump_generation", lambda conn, gen: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            reconcile(index)
        monkeypatch.undo()
        assert index.connection.execute("SELECT value FROM meta WHERE key='generation'").fetchone() == ("1",)
        assert reconcile(index).changed == ("a.md",)


def test_file_changed_during_parse_is_not_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"a.md": "old"})
    real = sync.read_member

    def racing(root_: Path, member_id: str):
        write(root_ / member_id, "a new projection")
        return real(root_, member_id)

    monkeypatch.setattr(sync, "read_member", racing)
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)
        assert index.connection.execute("SELECT * FROM members").fetchall() == []
    assert result.unsettled == ("a.md",) and result.added == ()


def test_generation_is_read_under_publish_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"a.md": "old"})
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        real = sync.read_member

        def publishing(root_: Path, member_id: str):
            with sqlite3.connect(db, isolation_level=None) as other:
                other.execute("BEGIN IMMEDIATE")
                other.execute("UPDATE meta SET value='7' WHERE key='generation'")
                other.execute("COMMIT")
            return real(root_, member_id)

        monkeypatch.setattr(sync, "read_member", publishing)
        result = reconcile(index)
        assert result.generation == 8
        assert index.connection.execute("SELECT value FROM meta WHERE key='generation'").fetchone() == ("8",)


def test_projection_duplicate_tags_and_removal(tmp_path: Path) -> None:
    import json

    root = tree(
        tmp_path / "b",
        {
            "a.md": "---\ntitle: A\ntype: Thing\ntags: [x, x]\n---\n# Heading\n[link](b.md)\n",
            "index.md": "# Index\n[link](a.md)\n",
            "b.md": "B",
        },
    )
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        conn = index.connection
        row = conn.execute("SELECT title, type, status, fm_json FROM members WHERE id='a.md'").fetchone()
        assert row[:3] == ("A", "Thing", "stable")
        assert json.loads(row[3])["tags"] == ["x", "x"]
        assert conn.execute("SELECT * FROM tags").fetchall() == [("a.md", "x")]
        assert conn.execute("SELECT source, target FROM links").fetchall() == [("a", "b.md")]
        assert conn.execute("SELECT member, text FROM headings ORDER BY member").fetchall() == [
            ("a.md", "Heading"),
            ("index.md", "Index"),
        ]
        (root / "a.md").unlink()
        result = reconcile(index)
        assert result.removed == ("a.md",) and result.generation == 2
        assert conn.execute("SELECT * FROM tags").fetchall() == []
        assert conn.execute("SELECT * FROM links").fetchall() == []
        assert conn.execute("SELECT member FROM headings").fetchall() == [("index.md",)]


def test_invalid_utf8_and_read_failure_are_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"a.md": "A", "b.md": "B"})
    (root / "a.md").write_bytes(b"\xff")
    age(root)
    real = sync._read_bytes

    def failing(root_: Path, member_id: str) -> bytes:
        if member_id == "b.md":
            raise OSError("denied")
        return real(root_, member_id)

    monkeypatch.setattr(sync, "_read_bytes", failing)
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)
        rows = index.connection.execute("SELECT id, unreadable, fm_json FROM members ORDER BY id").fetchall()
    assert result.parsed == 1
    assert rows[0][1].startswith("not valid UTF-8:") and rows[0][2] is None
    assert rows[1] == ("b.md", "could not be read: denied", None)


def test_stat_only_members_refresh_without_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"p.png": "x", "skip.md": "x", "pruned/a.md": "x"})

    def forbidden(*args: object) -> bytes:
        raise AssertionError("stat-only member was read")

    monkeypatch.setattr(sync, "_read_bytes", forbidden)
    with open_index(tmp_path / "i.db", root, ignore=("skip.md",), prune=("pruned",)) as index:
        first = reconcile(index)
        write(root / "p.png", "larger asset")
        age(root)
        second = reconcile(index)
        assert index.connection.execute("SELECT * FROM pruned").fetchall() == [("pruned",)]
    assert first.added == ("p.png", "skip.md")
    assert second.published and second.changed == () and second.generation == 1


def test_asset_removal_does_not_delete_other_concept_links(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a..md": "[link](target.md)", "a.png": "asset"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        assert index.connection.execute("SELECT source FROM links").fetchall() == [("a.",)]
        (root / "a.png").unlink()
        reconcile(index)
        assert index.connection.execute("SELECT source FROM links").fetchall() == [("a.",)]


def test_walk_diagnostics_and_collisions_replace_only_when_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from okf_io.bundle import Member, Walk

    root = tree(tmp_path / "b", {})
    members = (Member("e\u0301.png", "asset", None), Member("é.png", "asset", None))
    monkeypatch.setattr(
        sync, "walk", lambda *args, **kwargs: Walk(members, {"denied": "could not be read"}, frozenset({"pruned"}))
    )
    with open_index(tmp_path / "i.db", root) as index:
        first = reconcile(index)
        conn = index.connection
        assert json.loads(conn.execute("SELECT ids_json FROM collisions").fetchone()[0]) == ["e\u0301.png", "é.png"]
        assert conn.execute("SELECT * FROM unreadable_dirs").fetchall() == [("denied", "could not be read")]
        second = reconcile(index)
        assert second.generation == first.generation  # unknown stats refresh, diagnostics unchanged
        monkeypatch.setattr(sync, "walk", lambda *args, **kwargs: Walk((), {}, frozenset()))
        third = reconcile(index)
        assert third.generation == 2
        for table in ("collisions", "unreadable_dirs", "pruned"):
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == []


def test_disappearing_during_read_retains_prior_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: Old\n---\n"})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        write(root / "a.md", "---\ntitle: New\n---\n")
        age(root)
        real = sync._read_bytes

        def disappearing(root_: Path, member_id: str) -> bytes:
            data = real(root_, member_id)
            (root_ / member_id).unlink()
            return data

        monkeypatch.setattr(sync, "_read_bytes", disappearing)
        result = reconcile(index)
        assert result.unsettled == ("a.md",) and not result.published
        assert index.connection.execute("SELECT title FROM members").fetchone() == ("Old",)
        assert result.generation == 1


def _strict_json(text: str) -> dict[str, object]:
    import json

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    result = json.loads(text, parse_constant=reject_constant)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize(
    "literal, expected, exact", [(".nan", "nan", 0), (".inf", "inf", 0), ("-.inf", "-inf", 0), ("1.25", 1.25, 1)]
)
def test_yaml_float_json_validity_and_exactness(tmp_path: Path, literal: str, expected: object, exact: int) -> None:
    root = tree(
        tmp_path / "b",
        {
            "a.md": (
                f"---\ntitle: A\ntype: Thing\nstatus: stable\ntags: [x]\nodd:\n  nested: [{literal}, 1.25]\n---\nbody\n"
            )
        },
    )
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        row = index.connection.execute("SELECT fm_json, fm_exact, title, type, status FROM members").fetchone()
        assert _strict_json(row[0])["odd"] == {"nested": [expected, 1.25]}
        assert row[1:] == (exact, "A", "Thing", "stable")
        assert index.connection.execute("SELECT * FROM tags").fetchall() == [("a.md", "x")]


@pytest.mark.parametrize(
    "unknown, expected",
    [
        ("odd: &loop [*loop]", [None]),
        ("odd: &loop {self: *loop}", {"self": None}),
        ("odd: &value [1, 2]\nalias: *value", [1, 2]),
    ],
)
def test_reader_flattened_alias_stays_exact(tmp_path: Path, unknown: str, expected: object) -> None:
    from okf_io import Document

    text = f"---\ntitle: A\ntags: [x]\n{unknown}\n---\nbody\n"
    doc = Document.parse(text)
    assert doc.parse_error is None and doc.fm_data()["odd"] == expected
    root = tree(tmp_path / "b", {"a.md": text})
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        row = index.connection.execute("SELECT fm_json, fm_exact FROM members").fetchone()
    assert _strict_json(row[0]) == doc.fm_data()
    assert row[1] == 1


def test_recursive_root_alias_uses_frontmatter_only_fallback(tmp_path: Path) -> None:
    from okf_io import Document

    text = (
        "---\n&root\ntitle: A\ntype: Thing\nstatus: stable\ntags: [x]\nodd: *root\n---\n# Heading\n[link](target.md)\n"
    )
    doc = Document.parse(text)
    assert doc.parse_error is None
    with pytest.raises(RecursionError):
        doc.fm_data()
    root = tree(tmp_path / "b", {"a.md": text})
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)
        row = index.connection.execute("SELECT fm_json, fm_exact, title, type, status FROM members").fetchone()
        data = _strict_json(row[0])
        assert data["title"] == "A" and data["tags"] == ["x"]
        assert isinstance(data["odd"], str) and "recursive" in data["odd"] and "Heading" not in data["odd"]
        assert row[1:] == (0, "A", "Thing", "stable")
        assert result.added == ("a.md",) and result.parsed == 1
        assert index.connection.execute("SELECT * FROM tags").fetchall() == [("a.md", "x")]
        assert index.connection.execute("SELECT text FROM headings").fetchall() == [("Heading",)]
        assert index.connection.execute("SELECT source, target FROM links").fetchall() == [("a", "target.md")]


def test_recursive_root_alias_with_sequence_mapping_key_is_stored_inexact(tmp_path: Path) -> None:
    from okf_io import Document

    text = (
        "---\n&root\ntitle: A\ntype: Thing\nstatus: stable\ntags: [x]\nodd: *root\n"
        "other:\n  ? [a, b]\n  : value\n---\n# Heading\n[link](target.md)\nBODY_ONLY_SENTINEL\n"
    )
    doc = Document.parse(text)
    assert doc.parse_error is None
    with pytest.raises(RecursionError):
        doc.fm_data()
    root = tree(tmp_path / "b", {"a.md": text})
    with open_index(tmp_path / "i.db", root) as index:
        result = reconcile(index)
        row = index.connection.execute("SELECT fm_json, fm_exact, title, type, status FROM members").fetchone()
        data = _strict_json(row[0])
        assert data["title"] == "A" and data["tags"] == ["x"]
        assert isinstance(data["odd"], str) and "recursive" in data["odd"]
        assert isinstance(data["other"], str) and "value" in data["other"]
        assert "Heading" not in row[0] and "BODY_ONLY_SENTINEL" not in row[0]
        assert row[1:] == (0, "A", "Thing", "stable")
        assert result.added == ("a.md",) and result.parsed == 1 and result.published
        assert index.connection.execute("SELECT * FROM tags").fetchall() == [("a.md", "x")]
        assert index.connection.execute("SELECT text FROM headings").fetchall() == [("Heading",)]
        assert index.connection.execute("SELECT source, target FROM links").fetchall() == [("a", "target.md")]
