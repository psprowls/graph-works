"""Real-reader and SQLite regressions for the final review findings."""

from __future__ import annotations

import os
import threading
from dataclasses import replace

import pytest
from okf_ext.readindex import IndexBusy, IndexUnavailable, open_index, read, reconcile, verify
from okf_io import load_bundle
from readindex_helpers import OLD_NS, assert_view_equivalent, tree


class BeforePublish:
    def __init__(self, connection, callback):
        self.connection = connection
        self.callback = callback

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE":
            callback, self.callback = self.callback, lambda: None
            callback()
        return self.connection.execute(sql, parameters)


def test_refresh_conflict_xyx_converges_without_touch(tmp_path):
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: X\n---\n"})
    db = tmp_path / "i.db"

    def write_title(title):
        (root / "a.md").write_bytes(f"---\ntitle: {title}\n---\n".encode())
        os.utime(root / "a.md", ns=(OLD_NS, OLD_NS))

    with open_index(db, root) as seed:
        reconcile(seed)
    with open_index(db, root) as a, open_index(db, root) as b:
        ready_a, ready_b, go_a, go_b = [threading.Event() for _ in range(4)]
        results = {}

        def run(name, index, ready, go):
            def pause():
                ready.set()
                assert go.wait(10)

            try:
                results[name] = reconcile(replace(index, connection=BeforePublish(index.connection, pause)))
            except BaseException as error:
                results[name] = error

        write_title("Y")
        tb = threading.Thread(target=run, args=("b", b, ready_b, go_b))
        ta = threading.Thread(target=run, args=("a", a, ready_a, go_a))
        tb.start()
        try:
            assert ready_b.wait(10)
            write_title("X")
            ta.start()
            assert ready_a.wait(10)
            go_b.set()
            tb.join(10)
            assert not tb.is_alive()
            go_a.set()
            ta.join(10)
            assert not ta.is_alive()
        finally:
            go_a.set()
            go_b.set()
            tb.join(10)
            if ta.ident is not None:
                ta.join(10)
        assert not isinstance(results["b"], BaseException)
        assert not isinstance(results["a"], BaseException) or isinstance(results["a"], IndexBusy)
        # No age(), write, or second repair hidden in the oracle.
        reconcile(a)
        assert verify(a) == ()
        assert_view_equivalent(a)


@pytest.mark.parametrize("in_flight", [False, True])
def test_stale_handle_cannot_publish_after_config_reset(tmp_path, in_flight):
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: X\n---\n"})
    db = tmp_path / "i.db"
    with open_index(db, root, fingerprint="old") as old:
        reconcile(old)
        (root / "a.md").write_bytes(b"---\ntitle: Y\n---\n")

        def reset():
            with open_index(db, root, fingerprint="new", ignore=("a.md",)) as new:
                reconcile(new)

        if in_flight:
            old = replace(old, connection=BeforePublish(old.connection, reset))
        else:
            reset()
        with pytest.raises(IndexUnavailable, match="identity"):
            reconcile(old)
        with open_index(db, root, fingerprint="new", ignore=("a.md",)) as new:
            assert_view_equivalent(new)
            with read(new) as view:
                assert view.member("a.md").kind == "ignored"


def test_prepared_deletion_cannot_remove_concurrent_replacement(tmp_path):
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: X\n---\n"})
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
        (root / "a.md").unlink()

        def replace_deleted():
            tree(root, {"a.md": "---\ntitle: New\n---\n"})
            with open_index(db, root) as other:
                reconcile(other)

        wrapped = replace(index, connection=BeforePublish(index.connection, replace_deleted))
        with pytest.raises(IndexBusy):
            reconcile(wrapped)
        assert_view_equivalent(index)


@pytest.mark.parametrize("escaped", [r"\uD800", r"\uDC00", r"\uD800\uDC00", r"text\\uD800", "café"])
def test_reader_surrogates_preserve_columns_json_tags_and_queries(tmp_path, escaped):
    text = (
        f'---\ntitle: "{escaped}"\ntype: "{escaped}"\nstatus: "{escaped}"\n'
        f'tags: ["{escaped}", plain, "{escaped}", "𐀀"]\nodd: {{nested: ["{escaped}"]}}\n'
        f'"{escaped}": unknown-key\n---\n# Heading\n[link](missing.md)\n'
    )
    root = tree(tmp_path / "b", {"a.md": text, "b.md": "---\ntitle: Normal\n---\n"})
    doc = load_bundle(root).concepts["a"]
    assert doc.parse_error is None
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
    with open_index(db, root) as index:
        assert_view_equivalent(index)
        with read(index) as view:
            row = view.member("a.md")
            assert row.fm_exact and dict(row.fm) == doc.fm_data(dates="iso")
            assert view.members(type=doc.fm.type) == (row,)
            assert view.members(type=doc.fm.type, prefix="a") == (row,)
            assert view.members(type=doc.fm.type + "other") == ()
            assert view.members(prefix="\ud800") == ()
            assert view.member("\ud800.md") is None
            assert view.outlinks("\ud800") == ()
            assert view.backlinks("\ud800") == ()
            assert view.broken("\ud800") == ()
            assert view.headings("\ud800.md") == ()
        # JSON remains directly queryable with the connection's text decoder.
        assert index.connection.execute(
            "SELECT json_extract(fm_json, '$.odd.nested[0]') FROM members WHERE id='a.md'"
        ).fetchone() == (doc.fm.title,)
        assert reconcile(index).parsed == 0


@pytest.mark.parametrize("count, explicit", [(1, True), (520, False), (520, True)])
def test_markdown_projection_materialized_before_writer_lock(tmp_path, monkeypatch, count, explicit):
    from okf_io import _md

    source = "sources: [{path: other.md}]\n" if explicit else ""
    root = tree(
        tmp_path / "b",
        {f"p{i:04}.md": f"---\ntitle: P{i}\n{source}---\n# Heading {i}\n[x](other.md)\n" for i in range(count)},
    )
    with open_index(tmp_path / "i.db", root) as index:
        real = _md.parse_body
        real.cache_clear()
        calls = []

        def observed(body):
            before = real.cache_info().misses
            result = real(body)
            calls.append((index.connection.in_transaction, real.cache_info().misses > before))
            return result

        monkeypatch.setattr(_md, "parse_body", observed)
        reconcile(index)
        assert sum(miss for _, miss in calls) >= count
        assert not any(locked for locked, _ in calls)
        assert_view_equivalent(index)
