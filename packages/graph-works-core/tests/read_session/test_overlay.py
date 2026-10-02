"""Overlay membership, aliases and fallback must preserve observable results."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from graph_works_core.read_session import BundleSession
from graph_works_core.workspace.bundle import CLONE_IGNORE, CLONE_PRUNE
from okf_ext.readindex import open_index, read, reconcile
from okf_ext.readindex.view import IndexView
from test_equivalence import compare, make_session
from work_tracker_okf.items import IGNORE


def test_overlay_ignored_unreadable_file_is_a_member(index_session) -> None:
    mid = "work/epic-a/references/undecodable.md"
    row = index_session.member(mid, ignore=IGNORE)
    assert row is not None and row.kind == "ignored" and row.sha256 is None
    assert mid not in index_session.diagnostics(ignore=IGNORE).unreadable
    assert "docs/explanations/bad.md" in index_session.diagnostics(ignore=IGNORE).unreadable
    assert index_session.members(prefix=mid, kind="concept", ignore=IGNORE) == ()
    assert index_session.members(prefix=mid, type="Bug", ignore=IGNORE) == ()
    assert index_session.member(mid) is None
    assert index_session.member(mid + "x", ignore=IGNORE) is None


def test_ignored_members_are_not_link_sources(index_session) -> None:
    design = "work/epic-a/references/01-design"
    assert index_session.outlinks(design, ignore=IGNORE) == ()
    assert design not in index_session.backlinks("work/epic-a", ignore=IGNORE)
    assert index_session.backlinks(design, ignore=IGNORE) == ()
    assert index_session.broken(design, ignore=IGNORE) == ()
    assert index_session.member(design + ".md").kind == "concept"


def test_links_into_ignored_unreadable_targets_are_resolved(workspace, tmp_path: Path) -> None:
    root = workspace.bundle_dir
    target = "work/epic-a/references/undecodable.md"
    (root / "source.md").write_text(
        f"[x]({target}) ![image]({target}) [bad](docs/explanations/bad.md) "
        "[clone](repositories/clone/references/git/README.md)\n",
        encoding="utf-8",
        newline="\n",
    )
    compare(root, tmp_path / "i.db", IGNORE)


def test_ignored_unreadable_alias_is_a_member(workspace, tmp_path: Path) -> None:
    root = workspace.bundle_dir
    raw = "work/epic-a/references/A\u030a.md"
    (root / raw).write_bytes(b"\xff\xfe bad")
    (root / "source.md").write_text("[alias](work/epic-a/references/Å.md)\n", encoding="utf-8", newline="\n")
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            oracle = BundleSession(root, fallback=None)
            for alias in (raw, "work/epic-a/references/Å.md", " work/epic-a/references/Å.md "):
                assert session.member(alias, ignore=IGNORE) == oracle.member(alias, ignore=IGNORE)
                assert session.member(alias, ignore=IGNORE).kind == "ignored"
            assert session.broken("source", ignore=IGNORE) == ()
            assert session.broken("source") != ()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ignore", [(), ("Å.md",), ("A\u030a.md",), ("*.md",)])
def test_collision_order_and_links_include_ignored_unreadable(
    workspace, tmp_path, monkeypatch, reverse, ignore
) -> None:
    # APFS merges NFC/NFD names. Virtualize only the file boundary, keeping
    # real documents, loaders, SQLite projections and overlay derivation.
    import okf_io.bundle as bundle_module
    from okf_ext.readindex import sync
    from okf_io import Document
    from okf_io.bundle import Member, MemberStat, Unreadable, Walk

    root = workspace.bundle_dir
    names = ["A\u030a.md", "Å.md"]
    unreadable = names[int(reverse)]
    data = {
        "source.md": b"[exact](A%CC%8A.md) [alias](%E2%84%AB.md) [missing](no.md) ![image](%E2%84%AB.md)\n",
        "Å.md": b"---\ntitle: readable\n---\n[back](source.md)\n",
        "A\u030a.md": b"---\ntitle: readable\n---\n[back](source.md)\n",
    }
    data[unreadable] = b"\xff"

    def walk(root, *, ignore=(), prune=()):
        from fnmatch import fnmatchcase

        return Walk(
            tuple(
                Member(
                    name,
                    "ignored" if any(fnmatchcase(name, p) for p in ignore) else "concept",
                    MemberStat(len(data[name]), 1, 1, 1),
                )
                for name in ["source.md", *names]
            ),
            {"locked": "denied"},
            frozenset(),
        )

    def read_document(root, name):
        if name == unreadable:
            return Unreadable("invalid UTF-8")
        return Document.parse(data[name].decode("utf-8"), path=root / name)

    monkeypatch.setattr(bundle_module, "walk", walk)
    monkeypatch.setattr(sync, "walk", walk)
    monkeypatch.setattr(bundle_module, "read_member", read_document)
    monkeypatch.setattr(sync, "read_member", read_document)
    monkeypatch.setattr(sync, "_read_bytes", lambda root, name: data[name])
    monkeypatch.setattr(sync, "_stable", lambda root, member: True)
    db = tmp_path / "i.db"
    compare(root, db, ignore)
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index, read(index) as view:
        session = make_session(index, view, root, db)
        oracle = BundleSession(root, fallback=None)
        for path in ("Å.md", "A\u030a.md", "Å.md"):
            assert session.member(path, ignore=ignore) == oracle.member(path, ignore=ignore)
        expected = {"Å.md": tuple(names)} if unreadable in ignore or "*.md" in ignore else {}
        assert dict(session.diagnostics(ignore=ignore).collisions) == expected


def test_inexact_work_falls_back_before_results_and_pins_snapshot(workspace, tmp_path, caplog) -> None:
    root = workspace.bundle_dir
    path = root / "work/epic-a.md"
    path.write_text(
        "---\ntype: Epic\ntitle: Original\nsources:\n"
        "  - id: s\n    resource: /docs/explanations/p.md\n    payload: !!binary YWJj\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            assert view.member("work/epic-a.md").fm_exact is False
            session = make_session(index, view, root, db)
            assert (session.backend, session.fallback, session.generation) == ("bundle", "unavailable", None)
            path.write_text("---\ntype: Epic\ntitle: Changed\n---\n", encoding="utf-8", newline="\n")
            item = session.work_snapshot().by_path["work/epic-a"]
            assert item.title == "Original"
            assert item.sources[0].extra["payload"] == b"abc"
            assert session.member("work/epic-a.md").title == "Original"
    assert len(caplog.records) == 1 and "unavailable" in caplog.text


def test_inexact_nonwork_remains_indexed(workspace, tmp_path) -> None:
    root = workspace.bundle_dir
    (root / "docs/explanations/inexact.md").write_text(
        "---\ncustom: !!binary YWJj\n---\n", encoding="utf-8", newline="\n"
    )
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            assert session.backend == "index"
            assert tuple(session.work_snapshot()) == tuple(BundleSession(root, fallback=None).work_snapshot())


@pytest.mark.parametrize("directory", [False, True], ids=["files", "directories"])
@pytest.mark.parametrize("reverse", [False, True], ids=["enumeration", "reverse-enumeration"])
@pytest.mark.parametrize("unreadable_index", [None, 0, 1], ids=["readable", "first-unreadable", "second-unreadable"])
@pytest.mark.parametrize("overlay", [False, True], ids=["no-ignore", "ignore"])
def test_windows_comparison_ties_use_authoritative_bundle(
    workspace, tmp_path, monkeypatch, caplog, directory, reverse, unreadable_index, overlay
) -> None:
    # APFS cannot preserve these distinct names. Model native Windows sorting
    # and stack popping at the walk boundary; keep real loaders and SQLite.
    from fnmatch import fnmatchcase
    from pathlib import PureWindowsPath
    from urllib.parse import quote

    import graph_works_core.read_session.index_backend as backend_module
    import okf_io.bundle as bundle_module
    from okf_ext.readindex import sync
    from okf_io import Document
    from okf_io.bundle import Member, MemberStat, Unreadable, Walk, canonical_id

    suffix = "/target.md" if directory else ".md"
    names = ["Å" + suffix, "Å" + suffix]
    alias = "A\u030a" + suffix
    assert PureWindowsPath(names[0]) == PureWindowsPath(names[1])
    assert canonical_id(names[0]) == canonical_id(names[1]) == canonical_id(alias)
    enumeration = list(reversed(names)) if reverse else names
    # Stable reverse sort preserves comparison ties; the loader then pops
    # its stack, reversing their original enumeration order.
    traversal = list(reversed(sorted(enumeration, key=PureWindowsPath, reverse=True)))
    assert traversal == list(reversed(enumeration))
    unreadable = names[unreadable_index] if unreadable_index is not None else None
    ignored = unreadable if unreadable is not None else names[0]
    ignore = (ignored,) if overlay else ()
    data = {
        "source.md": (
            f"[exact]({quote(names[0])}) [alias]({quote(alias)}) ![image]({quote(alias)}) [missing](no.md)\n"
        ).encode(),
        **{name: b"---\ntitle: Target\n---\n# Target\n[back](/source.md)\n" for name in names},
    }
    if unreadable is not None:
        data[unreadable] = b"\xff"

    def walk(root, *, ignore=(), prune=()):
        return Walk(
            tuple(
                Member(
                    name,
                    "ignored" if any(fnmatchcase(name, pattern) for pattern in ignore) else "concept",
                    MemberStat(len(data[name]), 1, 1, 1),
                )
                for name in ["source.md", *traversal]
            ),
            {"locked": "denied"},
            frozenset(),
        )

    def read_document(root, name):
        if name == unreadable:
            return Unreadable("invalid UTF-8")
        return Document.parse(data[name].decode("utf-8"), path=root / name)

    monkeypatch.setattr(bundle_module, "walk", walk)
    monkeypatch.setattr(sync, "walk", walk)
    monkeypatch.setattr(bundle_module, "read_member", read_document)
    monkeypatch.setattr(sync, "read_member", read_document)
    monkeypatch.setattr(sync, "_read_bytes", lambda root, name: data[name])
    monkeypatch.setattr(sync, "_stable", lambda root, member: True)
    # Patch only the backend's platform value, never global sys/os/pathlib.
    monkeypatch.setattr(backend_module, "platform", "win32", raising=False)
    root = workspace.bundle_dir
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            selection = (session.backend, session.fallback, session.generation)
            oracle = BundleSession(root, fallback=None)
            # Even an unrelated empty first result must not permit a later
            # transition from a pinned generation to a collision fallback.
            assert session.members(prefix="absent/") == ()
            for path in (*names, alias):
                assert session.member(path, ignore=ignore) == oracle.member(path, ignore=ignore)
            assert session.members(ignore=ignore) == oracle.members(ignore=ignore)
            assert session.diagnostics(ignore=ignore) == oracle.diagnostics(ignore=ignore)
            expected = {canonical_id(alias): tuple(traversal)} if unreadable is None or overlay else {}
            assert dict(session.diagnostics(ignore=ignore).collisions) == expected
            for cid in ("source", *(name[:-3] for name in names)):
                assert session.outlinks(cid, ignore=ignore) == oracle.outlinks(cid, ignore=ignore)
                assert session.backlinks(cid, ignore=ignore) == oracle.backlinks(cid, ignore=ignore)
                assert session.broken(cid, ignore=ignore) == oracle.broken(cid, ignore=ignore)
            assert session.broken(ignore=ignore) == oracle.broken(ignore=ignore)
            assert selection == ("bundle", "unavailable", None)
            assert len(caplog.records) == 1 and "unavailable" in caplog.text
            # Already loaded policies and graph queries must not reread files.
            monkeypatch.setattr(bundle_module, "read_member", fail)
            assert session.member(alias, ignore=ignore) == oracle.member(alias, ignore=ignore)
            assert session.broken(ignore=ignore) == oracle.broken(ignore=ignore)


def test_windows_without_canonical_collisions_stays_pinned(workspace, tmp_path, monkeypatch, caplog) -> None:
    import graph_works_core.read_session.index_backend as backend_module

    monkeypatch.setattr(backend_module, "platform", "win32", raising=False)
    root = workspace.bundle_dir
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            session = make_session(index, view, root, db)
            generation = view.generation
            assert (session.backend, session.fallback, session.generation) == ("index", None, generation)
            (root / "work/epic-a.md").write_text("---\ntitle: Changed\n---\n", encoding="utf-8", newline="\n")
            assert session.member("work/epic-a.md").title == "A"
            assert session.work_snapshot().by_path["work/epic-a"].title == "A"
            assert not caplog.records


def fail(*args, **kwargs):
    raise sqlite3.OperationalError("injected failure")


@pytest.mark.parametrize(
    "query", ["members", "member", "outlinks", "backlinks", "broken", "headings", "diagnostics", "work_snapshot"]
)
def test_sqlite_error_before_result_falls_back(index_session, workspace, monkeypatch, caplog, query) -> None:
    monkeypatch.setattr(IndexView, "members", fail)
    monkeypatch.setattr(IndexView, "headings", fail)
    monkeypatch.setattr(IndexView, "outlinks", fail)
    monkeypatch.setattr(IndexView, "member", fail)
    monkeypatch.setattr(IndexView, "backlinks", fail)
    monkeypatch.setattr(IndexView, "broken", fail)
    monkeypatch.setattr(IndexView, "diagnostics", fail)
    args = {
        "member": ("work/epic-a.md",),
        "outlinks": ("work/epic-a",),
        "backlinks": ("work/epic-a",),
        "headings": ("index.md",),
    }.get(query, ())
    kwargs = {} if query == "headings" else {"ignore": IGNORE}
    got = getattr(index_session, query)(*args, **kwargs)
    expected = getattr(BundleSession(workspace.bundle_dir, fallback=None), query)(*args, **kwargs)
    assert tuple(got) == tuple(expected) if query == "work_snapshot" else got == expected
    assert (index_session.backend, index_session.fallback, index_session.generation) == ("bundle", "error", None)
    index_session.members()
    assert len(caplog.records) == 1


@pytest.mark.parametrize("first", ["empty", "none", "members"])
def test_sqlite_error_after_result_propagates(index_session, monkeypatch, caplog, first) -> None:
    generation = index_session.generation
    if first == "empty":
        assert index_session.members(prefix="absent/") == ()
    elif first == "none":
        assert index_session.member("absent.md") is None
    else:
        assert index_session.members()
    monkeypatch.setattr(IndexView, "headings", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        index_session.headings("index.md")
    assert (index_session.backend, index_session.fallback, index_session.generation) == ("index", None, generation)
    assert not caplog.records


@pytest.mark.parametrize("failing_helper", ["outlinks", "diagnostics"])
def test_nested_query_failure_does_not_mark_result_served(
    index_session, workspace, monkeypatch, failing_helper
) -> None:
    # Membership succeeds inside the graph query, but no public result escapes.
    monkeypatch.setattr(IndexView, failing_helper, fail)
    got = index_session.broken(ignore=IGNORE)
    assert got == BundleSession(workspace.bundle_dir, fallback=None).broken(ignore=IGNORE)
    assert index_session.fallback == "error"


def test_constructor_sqlite_failure_falls_back(workspace, tmp_path, monkeypatch, caplog) -> None:
    root = workspace.bundle_dir
    db = tmp_path / "i.db"
    with open_index(db, root, ignore=CLONE_IGNORE, prune=CLONE_PRUNE) as index:
        reconcile(index)
        with read(index) as view:
            index.connection.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
            session = make_session(index, view, root, db)
            index.connection.set_authorizer(None)
            assert session.members() == BundleSession(root, fallback=None).members()
            assert session.fallback == "error"
    assert len(caplog.records) == 1


def test_snapshot_projection_failure_does_not_mark_members_served(index_session, workspace, monkeypatch) -> None:
    from work_tracker_okf.snapshot import WorkSnapshot

    monkeypatch.setattr(WorkSnapshot, "from_rows", fail)
    assert tuple(index_session.work_snapshot()) == tuple(
        BundleSession(workspace.bundle_dir, fallback=None).work_snapshot()
    )
    assert index_session.fallback == "error"
