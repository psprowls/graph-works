"""Unit tests for gw graph sync-wiki logic.

The wiki-layout conventions live in wiki_io.package_pages (tested there);
these tests inject stub resolvers and pin the graph side: upserts, the drift
report, stale cleanup, and the ambiguous-skip warning.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import code_graph_io
import pytest
from code_graph_io import store, sync_wiki, upsert
from code_parser.projections.graph import GraphNode, GraphRecords


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "agent-workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("registered_plugins: []\n")
    (ws / "wiki").mkdir()
    return ws


@pytest.fixture()
def conn(workspace: Path) -> sqlite3.Connection:
    db = workspace / ".agent-workspace" / "code.db"
    c = store.connect(db, create=True)
    yield c
    c.close()


def _seed_package(conn: sqlite3.Connection, name: str, path: str) -> None:
    upsert.upsert_records(
        conn,
        GraphRecords(
            nodes=[GraphNode(kind="package", name=name, path=path, line=None, attrs={"language": "python"})],
            edges=[],
        ),
    )


def _make_overview(workspace: Path, rel: str) -> None:
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {p.stem}\n")


def _resolver(mapping: dict[str, str], ambiguous: frozenset[str] = frozenset()):
    """Stub resolve_page: a static name->path mapping plus explicit ambiguous names."""

    def resolve_page(name: str) -> tuple[str | None, bool]:
        if name in ambiguous:
            return None, True
        return mapping.get(name), False

    return resolve_page


def test_links_package_via_injected_resolver(workspace: Path, conn: sqlite3.Connection) -> None:
    # A deliberately NON-conventional path: code-graph-io must not care about layout.
    _seed_package(conn, "alpha", "packages/alpha")
    _make_overview(workspace, "wiki/anywhere/alpha-page.md")

    report = sync_wiki.run(
        workspace=workspace, conn=conn, resolve_page=_resolver({"alpha": "wiki/anywhere/alpha-page.md"})
    )

    assert report.newly_linked == (("alpha", "wiki/anywhere/alpha-page.md"),)
    assert report.undocumented == ()
    page = conn.execute("SELECT name, path FROM nodes WHERE kind='wiki_page'").fetchone()
    assert page == ("wiki/anywhere/alpha-page.md", "wiki/anywhere/alpha-page.md")
    edge_count = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='documents'").fetchone()[0]
    assert edge_count == 1


def test_undocumented_package_is_reported(workspace: Path, conn: sqlite3.Connection) -> None:
    _seed_package(conn, "alpha", "packages/alpha")

    report = sync_wiki.run(workspace=workspace, conn=conn, resolve_page=_resolver({}))

    assert report.undocumented == ("alpha",)
    assert report.newly_linked == ()


def test_ambiguous_package_is_skipped_with_warning(workspace: Path, conn: sqlite3.Connection, capsys) -> None:
    _seed_package(conn, "core", "domains/a/packages/core")

    report = sync_wiki.run(workspace=workspace, conn=conn, resolve_page=_resolver({}, ambiguous=frozenset({"core"})))

    assert report.ambiguous == ("core",)
    assert report.undocumented == ("core",)
    assert report.newly_linked == ()
    assert "core" in capsys.readouterr().err
    edge_count = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='documents'").fetchone()[0]
    assert edge_count == 0


def test_cleanup_removes_stale_wiki_page_and_edges(workspace: Path, conn: sqlite3.Connection) -> None:
    _seed_package(conn, "alpha", "packages/alpha")
    _make_overview(workspace, "wiki/packages/alpha/alpha.md")
    resolve_page = _resolver({"alpha": "wiki/packages/alpha/alpha.md"})
    sync_wiki.run(workspace=workspace, conn=conn, resolve_page=resolve_page)

    (workspace / "wiki/packages/alpha/alpha.md").unlink()
    report = sync_wiki.run(workspace=workspace, conn=conn, resolve_page=_resolver({}))

    assert report.stale == ("wiki/packages/alpha/alpha.md",)
    assert report.undocumented == ("alpha",)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='wiki_page'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE kind='documents'").fetchone()[0] == 0


def test_run_is_idempotent(workspace: Path, conn: sqlite3.Connection) -> None:
    _seed_package(conn, "alpha", "packages/alpha")
    _make_overview(workspace, "wiki/packages/alpha/alpha.md")
    resolve_page = _resolver({"alpha": "wiki/packages/alpha/alpha.md"})

    first = sync_wiki.run(workspace=workspace, conn=conn, resolve_page=resolve_page)
    second = sync_wiki.run(workspace=workspace, conn=conn, resolve_page=resolve_page)

    assert first.newly_linked == (("alpha", "wiki/packages/alpha/alpha.md"),)
    assert second.newly_linked == ()
    assert second.stale == ()
    page_count = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='wiki_page'").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='documents'").fetchone()[0]
    assert page_count == 1
    assert edge_count == 1


def test_run_sync_wiki_opens_workspace(workspace: Path) -> None:
    # run_sync_wiki resolves the workspace's code.db and opens its own writer —
    # seed through a separate connection (committed + closed) to prove it.
    db = workspace / ".agent-workspace" / "code.db"
    seed = store.connect(db, create=True)
    _seed_package(seed, "alpha", "packages/alpha")
    seed.commit()
    seed.close()
    _make_overview(workspace, "wiki/packages/alpha/alpha.md")

    report = code_graph_io.run_sync_wiki(workspace, _resolver({"alpha": "wiki/packages/alpha/alpha.md"}))

    assert isinstance(report, code_graph_io.DriftReport)
    assert report.newly_linked == (("alpha", "wiki/packages/alpha/alpha.md"),)
    assert report.undocumented == ()


def test_run_sync_wiki_missing_graph_raises(tmp_path: Path) -> None:
    with pytest.raises(code_graph_io.GraphNotInitializedError):
        code_graph_io.run_sync_wiki(tmp_path, _resolver({}))
