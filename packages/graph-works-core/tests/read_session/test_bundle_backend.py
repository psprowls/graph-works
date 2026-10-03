"""The full-load backend's protocol, filtering and pinned-load behavior."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import CLONE_ROOT, FIXTURE_BUNDLES
from graph_works_core.read_session import BundleSession, ReadSession
from graph_works_core.workspace.bundle import load_bundle_at
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import Heading, build_link_graph
from okf_io.bundle import MemberKind
from repositories_okf import CLONE_GLOB, PRUNE_GLOB
from work_tracker_okf.items import IGNORE, load_items


def _session(workspace: WorkspaceLayout) -> ReadSession:
    return BundleSession(workspace.bundle_dir, fallback="disabled")


def test_full_load_rows_and_public_metadata(workspace: WorkspaceLayout) -> None:
    session = _session(workspace)
    assert (session.backend, session.fallback, session.generation) == ("bundle", "disabled", None)
    bundle = load_bundle_at(workspace.bundle_dir)
    rows = session.members()
    assert [row.id for row in rows] == sorted(row.id for row in rows)
    assert {r.concept_id for r in rows if r.kind == "concept"} == set(bundle.concepts)
    assert {r.id for r in rows if r.kind == "asset"} == set(bundle.assets)
    assert {r.id for r in rows if r.kind == "index"} == {f"{d}/index.md" if d else "index.md" for d in bundle.indexes}
    assert {r.id for r in rows if r.kind == "log"} == {f"{d}/log.md" if d else "log.md" for d in bundle.logs}
    assert all(r.sha256 is None for r in rows)
    row = session.member("docs/explanations/p.md")
    assert row is not None
    assert (row.type, row.title, row.tags, row.fm_exact) == ("Explanation", "P", ("a",), True)
    assert row.fm is not None and row.fm["title"] == "P"


@pytest.mark.parametrize(
    ("prefix", "kind", "type_", "expected"),
    [
        (
            "work/",
            None,
            None,
            [
                "work/epic-a.md",
                "work/epic-a/.DS_Store",
                "work/epic-a/children/bug-b.md",
                "work/epic-a/references/01-design.md",
            ],
        ),
        ("", "concept", "Epic", ["work/epic-a.md"]),
        ("work/epic-a/", "concept", "Bug", ["work/epic-a/children/bug-b.md"]),
        ("", "asset", None, ["pic.png", "work/epic-a/.DS_Store"]),
        ("docs/", "asset", None, []),
        ("work/", "concept", "Explanation", []),
        ("absent/", None, None, []),
        ("", None, "Absent", []),
    ],
)
def test_member_filters(
    workspace: WorkspaceLayout, prefix: str, kind: MemberKind | None, type_: str | None, expected: list[str]
) -> None:
    assert [r.id for r in _session(workspace).members(prefix=prefix, kind=kind, type=type_)] == expected


def test_member_resolution_and_ignored_rows(workspace: WorkspaceLayout) -> None:
    session = _session(workspace)
    assert session.member("docs/explanations/cafe\u0301.md") == session.member("docs/explanations/café.md")
    assert session.member(" absent.md ") is None
    assert session.member("docs/explanations/bad.md") is None
    assert session.member(f"{CLONE_ROOT}/README.md") is None
    ignored = session.member("work/epic-a/references/01-design.md", ignore=IGNORE)
    assert ignored is not None and (ignored.kind, ignored.title, ignored.sha256) == ("ignored", None, None)
    ids = {row.id for row in session.members(kind="ignored", ignore=IGNORE)}
    assert "work/epic-a/references/01-design.md" in ids
    assert "work/epic-a/references/undecodable.md" in ids
    assert "work/epic-a/.DS_Store" in ids
    assert not session.members(kind="ignored")


def test_link_queries_preserve_graph_order_and_ignore_policy(workspace: WorkspaceLayout) -> None:
    session = _session(workspace)
    graph = build_link_graph(load_bundle_at(workspace.bundle_dir))
    for cid in ("work/epic-a", "work/epic-a/references/01-design", "docs/explanations/p", "absent"):
        assert session.outlinks(cid) == graph.out.get(cid, ())
        assert session.backlinks(cid) == graph.backlinks.get(cid, ())
    assert session.broken() == graph.broken
    assert [(link.source, link.target) for link in session.broken("work/epic-a")] == [
        ("work/epic-a", "docs/missing.md")
    ]
    assert session.broken("absent") == ()
    assert session.outlinks("work/epic-a/references/01-design", ignore=IGNORE) == ()
    assert "work/epic-a/references/01-design" not in session.backlinks("work/epic-a", ignore=IGNORE)
    assert session.broken(ignore=IGNORE) == build_link_graph(load_bundle_at(workspace.bundle_dir, ignore=IGNORE)).broken


@pytest.mark.parametrize(
    ("mid", "expected"),
    [
        ("docs/explanations/p.md", (Heading(2, "H", 7, False),)),
        ("work/epic-a/references/01-design.md", (Heading(1, "Design", 5, False),)),
        ("index.md", (Heading(1, "Index", 1, False),)),
        ("log.md", (Heading(2, "2026-10-02", 1, False),)),
        ("pic.png", ()),
        ("missing.md", ()),
        (f"{CLONE_ROOT}/README.md", ()),
    ],
)
def test_headings_use_clone_only_load(workspace: WorkspaceLayout, mid: str, expected: tuple[Heading, ...]) -> None:
    session = _session(workspace)
    session.members(ignore=IGNORE)
    assert session.headings(mid) == expected


def test_diagnostics_cover_all_documents_and_ignores(workspace: WorkspaceLayout) -> None:
    for name in ("docs/index.md", "docs/log.md"):
        (workspace.bundle_dir / name).write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8", newline="\n")
    session = _session(workspace)
    diag = session.diagnostics()
    assert set(diag.unreadable) == {"docs/explanations/bad.md", "work/epic-a/references/undecodable.md"}
    assert set(diag.parse_errors) == {"docs/explanations/yaml.md", "docs/index.md", "docs/log.md"}
    for mid, error in diag.parse_errors.items():
        row = session.member(mid)
        assert row is not None and row.parse_error == error
    assert diag.pruned == frozenset({CLONE_ROOT})
    assert dict(diag.collisions) == {}
    assert isinstance(diag.unreadable, MappingProxyType)
    assert isinstance(diag.parse_errors, MappingProxyType)
    assert isinstance(diag.collisions, MappingProxyType)
    ignored = session.diagnostics(ignore=("docs/*", *IGNORE))
    assert not ignored.parse_errors and not ignored.unreadable


def test_work_snapshot_uses_work_ignore_by_default(workspace: WorkspaceLayout) -> None:
    session = _session(workspace)
    expected = load_items(load_bundle_at(workspace.bundle_dir, ignore=IGNORE))
    assert tuple(session.work_snapshot()) == tuple(expected)
    assert tuple(session.work_snapshot(ignore=())) == tuple(load_items(load_bundle_at(workspace.bundle_dir)))
    assert tuple(session.work_snapshot(ignore=("work/*",))) == ()


def test_one_lazy_load_per_ignore_tuple(workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    _session(workspace)
    from graph_works_core.read_session import bundle_backend

    calls: list[tuple[str, ...]] = []
    real = bundle_backend.load_bundle_at

    def tracked(root: Path, *, ignore=()):
        calls.append(tuple(ignore))
        return real(root, ignore=ignore)

    monkeypatch.setattr(bundle_backend, "load_bundle_at", tracked)
    session = BundleSession(workspace.bundle_dir, fallback=None)
    assert calls == []
    assert session.fallback is None
    session.members()
    session.broken()
    session.members(ignore=list(IGNORE))
    session.work_snapshot()
    session.headings("index.md")
    assert calls == [(), tuple(IGNORE)]
    (workspace.bundle_dir / "late.md").write_text("late", encoding="utf-8", newline="\n")
    assert session.member("late.md") is None


def test_missing_bundle_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="bundle root not found"):
        BundleSession(tmp_path / "absent", fallback=None).members()


def test_shared_fixture_paths_match_repository_policy() -> None:
    from fnmatch import fnmatchcase

    assert fnmatchcase(CLONE_ROOT, PRUNE_GLOB)
    assert fnmatchcase(f"{CLONE_ROOT}/README.md", CLONE_GLOB)
    assert all(path.is_dir() for path in FIXTURE_BUNDLES)
