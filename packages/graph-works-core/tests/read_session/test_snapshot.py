"""Materialized reads retain backend behavior after the source has closed."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from conftest import FIXTURE_BUNDLES
from graph_works_core.read_session import BundleSession, materialize, open_read_session
from work_tracker_okf.items import IGNORE

OVERLAYS = [pytest.param((), id="no-overlay"), pytest.param(IGNORE, id="work-ignore")]


def _assert_equivalent(source, snap, ignore) -> None:
    assert snap.members(ignore=ignore) == source.members(ignore=ignore)
    for kind in ("concept", "index", "log", "asset", "ignored"):
        assert snap.members(kind=kind, ignore=ignore) == source.members(kind=kind, ignore=ignore)
    for prefix in ("work/", "docs/", "absent/"):
        for type_ in (None, "Epic", "Bug", "Explanation", "Absent"):
            assert snap.members(prefix=prefix, type=type_, ignore=ignore) == source.members(
                prefix=prefix, type=type_, ignore=ignore
            )
    ids = [row.id for row in source.members()] + [row.id for row in source.members(ignore=("*",))]
    for member_id in sorted(set(ids)):
        assert snap.member(member_id, ignore=ignore) == source.member(member_id, ignore=ignore)
        assert snap.member(f" {member_id} ", ignore=ignore) == source.member(member_id, ignore=ignore)
        assert snap.headings(member_id) == source.headings(member_id)
        if member_id.endswith(".md"):
            cid = member_id[:-3]
            assert snap.outlinks(cid, ignore=ignore) == source.outlinks(cid, ignore=ignore)
            assert snap.backlinks(cid, ignore=ignore) == source.backlinks(cid, ignore=ignore)
            assert snap.broken(cid, ignore=ignore) == source.broken(cid, ignore=ignore)
    for row in source.members():
        assert snap.content_hash(row.id) == source.content_hash(row.id)
    assert snap.content_hash("absent.md") is None
    assert snap.member("absent.md", ignore=ignore) is None
    assert snap.member(" ", ignore=ignore) is None
    assert snap.broken(ignore=ignore) == source.broken(ignore=ignore)
    assert snap.diagnostics(ignore=ignore) == source.diagnostics(ignore=ignore)
    assert tuple(snap.work_snapshot(ignore=ignore)) == tuple(source.work_snapshot(ignore=ignore))
    assert (snap.backend, snap.fallback, snap.generation) == (source.backend, source.fallback, source.generation)


@pytest.mark.parametrize("ignore", OVERLAYS)
def test_snapshot_of_the_index_backend_equals_it(workspace, ignore) -> None:
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        snap = materialize(session)
        _assert_equivalent(session, snap, ignore)
        assert tuple(snap.work_snapshot()) == tuple(session.work_snapshot())


@pytest.mark.parametrize("ignore", OVERLAYS)
def test_snapshot_of_the_bundle_backend_equals_it(workspace, ignore) -> None:
    source = BundleSession(workspace.bundle_dir, fallback="disabled")
    snap = materialize(source)
    _assert_equivalent(source, snap, ignore)
    assert tuple(snap.work_snapshot()) == tuple(source.work_snapshot())
    assert (snap.backend, snap.fallback) == ("bundle", "disabled")


@pytest.mark.parametrize("root", FIXTURE_BUNDLES, ids=lambda p: p.name)
def test_snapshot_of_fixture_bundle_equals_it(root: Path) -> None:
    source = BundleSession(root, fallback=None)
    _assert_equivalent(source, materialize(source), ())


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_snapshot_outlives_its_session_without_rereading(workspace, monkeypatch, backend) -> None:
    import okf_io.bundle as bundle_module

    with open_read_session(workspace) as indexed:
        source = indexed if backend == "index" else BundleSession(workspace.bundle_dir, fallback="disabled")
        snap = materialize(source)
        expected = source.members()
        work = tuple(source.work_snapshot())
    (workspace.bundle_dir / "work/late.md").write_text("---\ntitle: late\n---\n", encoding="utf-8", newline="\n")

    def fail(*args, **kwargs):
        raise AssertionError("snapshot reread the bundle")

    monkeypatch.setattr(bundle_module, "read_member", fail)
    assert snap.members() == expected
    assert tuple(snap.work_snapshot()) == work
    assert "work/epic-a/children/bug-b" in snap.work_snapshot(ignore=()).by_path


def test_work_snapshot_for_another_ignore_set_is_memoized(workspace) -> None:
    with open_read_session(workspace) as session:
        snap = materialize(session)
    custom = (*IGNORE, "work/epic-a/children/*")
    results: list[object] = []
    threads = [threading.Thread(target=lambda: results.append(snap.work_snapshot(ignore=custom))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 8
    assert all(result is results[0] for result in results)
    assert "work/epic-a/children/bug-b" not in snap.work_snapshot(ignore=custom).by_path
    assert snap.work_snapshot(ignore=custom).by_path["work/epic-a"].child_paths == ()


def test_rows_carry_no_hash(workspace) -> None:
    with open_read_session(workspace) as session:
        snap = materialize(session)
    assert all(row.sha256 is None for row in snap.members())


@pytest.mark.parametrize("backend", ["index", "bundle"])
def test_aliases_and_links_resolve_newly_ignored_unreadable_targets(workspace, backend) -> None:
    root = workspace.bundle_dir
    raw = "work/epic-a/references/A\u030a.md"
    (root / raw).write_bytes(b"\xff")
    (root / "source.md").write_text(
        "[alias](work/epic-a/references/Å.md) ![image](work/epic-a/references/Å.md) "
        "[clone](repositories/clone/references/git/README.md) [external](https://example.com) "
        "[escape](../outside.md)\n",
        encoding="utf-8",
        newline="\n",
    )
    with open_read_session(workspace) as indexed:
        source = indexed if backend == "index" else BundleSession(root, fallback=None)
        snap = materialize(source)
        for ignore in ((), IGNORE, ("*",)):
            _assert_equivalent(source, snap, ignore)
            assert snap.member(" work/epic-a/references/Å.md ", ignore=ignore) == source.member(
                " work/epic-a/references/Å.md ", ignore=ignore
            )
        assert [link.raw for link in snap.broken("source", ignore=IGNORE)] == ["../outside.md"]


def test_snapshot_owns_nested_frontmatter_and_work_values(workspace) -> None:
    (workspace.bundle_dir / "work/nested.md").write_text(
        "---\ntype: Bug\ntitle: Nested\ntags: [a]\ncustom: {nested: [one]}\n"
        "sources: [{id: s, resource: /index.md, payload: {nested: [one]},\n"
        "  usage_window: {from: 2026-10-02, payload: {nested: [one]}}}]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    source = BundleSession(workspace.bundle_dir, fallback=None)
    source_row = source.member("work/nested.md")
    source_work = source.work_snapshot().by_path["work/nested"]
    snap = materialize(source)
    source_row.fm["custom"]["nested"].append("changed")
    source_work.sources[0].extra["payload"]["nested"].append("changed")
    source_work.sources[0].usage_window.extra["payload"]["nested"].append("changed")
    assert snap.member("work/nested.md").fm["custom"] == {"nested": ["one"]}
    assert snap.work_snapshot().by_path["work/nested"].sources[0].extra["payload"] == {"nested": ["one"]}
    assert snap.work_snapshot().by_path["work/nested"].sources[0].usage_window.extra["payload"] == {"nested": ["one"]}
    with pytest.raises(TypeError):
        snap.member("work/nested.md").fm["custom"]["new"] = "value"
    with pytest.raises(TypeError):
        snap.work_snapshot().by_path["work/nested"].sources[0].extra["payload"]["new"] = "value"


def test_overlay_broken_links_keep_source_order(workspace) -> None:
    for name in ("a.md", "a!.md"):
        (workspace.bundle_dir / name).write_text("[missing](missing.md)\n", encoding="utf-8", newline="\n")
    with open_read_session(workspace) as source:
        snap = materialize(source)
        assert [link.source for link in snap.broken(ignore=IGNORE)][:2] == ["a", "a!"]
        assert snap.broken(ignore=IGNORE) == source.broken(ignore=IGNORE)


@pytest.mark.parametrize("backend", ["index", "bundle"])
@pytest.mark.parametrize("unreadable", ["Å.md", "A\u030a.md"])
def test_overlay_collision_order_recomputes_alias_backlinks(workspace, monkeypatch, backend, unreadable) -> None:
    from fnmatch import fnmatchcase

    import okf_io.bundle as bundle_module
    from okf_ext.readindex import sync
    from okf_io import Document
    from okf_io.bundle import Member, MemberStat, Unreadable, Walk

    names = ("A\u030a.md", "Å.md")
    data = {
        "source.md": b"[alias](%E2%84%AB.md) ![image](%E2%84%AB.md)\n",
        **{name: b"[back](source.md)\n" for name in names},
    }
    data[unreadable] = b"\xff"

    def walk(root, *, ignore=(), prune=()):
        return Walk(
            tuple(
                Member(
                    name,
                    "ignored" if any(fnmatchcase(name, p) for p in ignore) else "concept",
                    MemberStat(len(data[name]), 1, 1, 1),
                )
                for name in ("source.md", *names)
            ),
            {"locked": "denied"},
            frozenset(),
        )

    def read_document(root, name):
        return (
            Unreadable("invalid UTF-8") if name == unreadable else Document.parse(data[name].decode(), path=root / name)
        )

    monkeypatch.setattr(bundle_module, "walk", walk)
    monkeypatch.setattr(sync, "walk", walk)
    monkeypatch.setattr(bundle_module, "read_member", read_document)
    monkeypatch.setattr(sync, "read_member", read_document)
    monkeypatch.setattr(sync, "_read_bytes", lambda root, name: data[name])
    monkeypatch.setattr(sync, "_stable", lambda root, member: True)
    with open_read_session(workspace) as indexed:
        source = indexed if backend == "index" else BundleSession(workspace.bundle_dir, fallback=None)
        snap = materialize(source)
        for ignore in ((), ("Å.md",), ("A\u030a.md",), ("*.md",)):
            _assert_equivalent(source, snap, ignore)
            assert snap.member("Å.md", ignore=ignore) == source.member("Å.md", ignore=ignore)
            assert "locked" in snap.diagnostics(ignore=ignore).unreadable


def test_custom_work_projection_retains_mapping_fields(workspace) -> None:
    (workspace.bundle_dir / "work/mappings.md").write_text(
        "---\ntype: Bug\ntitle: Mappings\nrepo_stamps:\n"
        "  code: {worktree: /checkout, branch: topic}\nspec_baseline:\n"
        f"  code: {'a' * 40}\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    with open_read_session(workspace) as source:
        expected = source.work_snapshot(ignore=()).by_path["work/mappings"]
        snap = materialize(source)
    actual = snap.work_snapshot(ignore=()).by_path["work/mappings"]
    assert actual == expected
    assert actual.repo_stamps["code"].worktree == "/checkout"
    assert actual.spec_baseline.code == "a" * 40


def test_snapshot_owns_native_yaml_sets_in_source_extras(workspace) -> None:
    (workspace.bundle_dir / "work/sets.md").write_text(
        "---\ntype: Bug\ntitle: Set extras\nsources: [{id: s, resource: /index.md, payload: !!set {one: null}}]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    source = BundleSession(workspace.bundle_dir, fallback=None)
    item = source.work_snapshot().by_path["work/sets"]
    snap = materialize(source)
    copied = snap.work_snapshot().by_path["work/sets"].sources[0].extra["payload"]
    item.sources[0].extra["payload"].add("changed")
    assert set(copied) == {"one"}


@pytest.mark.parametrize("ignore", [(), ("work/epic-a/children/*",)])
def test_custom_ignore_keeps_bundle_work_fidelity(workspace, ignore) -> None:
    (workspace.bundle_dir / "work/legacy.md").write_text(
        "---\ntype: Bug\ntitle: Legacy\nwork_status: open\n---\n# Citations\n\n- [source](/docs/explanations/p.md)\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace.bundle_dir / "work/native.md").write_text(
        "---\ntype: Bug\ntitle: Native\nsources: [{id: s, resource: /index.md, extra_date: 2026-10-02}]\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    source = BundleSession(workspace.bundle_dir, fallback=None)
    expected = source.work_snapshot(ignore=ignore)
    snap = materialize(source)
    assert tuple(snap.work_snapshot(ignore=ignore)) == tuple(expected)


def test_index_revision_survives_materialization_and_changes_after_cache_loss(workspace) -> None:
    from graph_works_core.read_session import database_path, index_revision

    with open_read_session(workspace) as source:
        before = index_revision(source)
        assert before is not None and before[0] is not None
        snap = materialize(source)
        assert index_revision(snap) == before
    database_path(workspace).unlink()
    with open_read_session(workspace, reconcile=False) as source:
        after = index_revision(source)
        assert after is not None and after[0] != before[0]
        assert after[1] == before[1]
        assert source.members()
    assert index_revision(BundleSession(workspace.bundle_dir, fallback=None)) is None


def test_snapshot_content_hash_is_captured_and_survives_close(workspace) -> None:
    with open_read_session(workspace) as session:
        assert session.backend == "index"
        snap = materialize(session)
        expected = {row.id: session.content_hash(row.id) for row in session.members()}
    assert any(value is not None for value in expected.values())
    assert {mid: snap.content_hash(mid) for mid in expected} == expected
