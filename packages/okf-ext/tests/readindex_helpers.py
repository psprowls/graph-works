"""Shared builders for the readindex suites."""

from __future__ import annotations

import os
from pathlib import Path

from ext_helpers import write
from okf_ext.readindex import ReadIndex, open_index, read, reconcile
from okf_io import build_link_graph, document_headings, effective_status, load_bundle

OLD_NS = 1_600_000_000 * 1_000_000_000  # 2020-09-13: far outside any racy window


def age(root: Path) -> None:
    """Push every file's mtime far into the past so no member is racy."""
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            os.utime(path, ns=(OLD_NS, OLD_NS))


def tree(root: Path, files: dict[str, str], *, aged: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        write(root / rel, text)
    if aged:
        age(root)
    return root


def assert_view_equivalent(index: ReadIndex) -> None:
    """Compare the current published view without reconciling it."""
    bundle = load_bundle(index.root, ignore=index.ignore, prune=index.prune)
    graph = build_link_graph(bundle)
    with read(index) as view:
        rows = {r.id: r for r in view.members()}
        assert {r.concept_id for r in rows.values() if r.kind == "concept"} == set(bundle.concepts)
        assert {i for i, r in rows.items() if r.kind == "asset"} == set(bundle.assets)
        assert {i for i, r in rows.items() if r.kind == "ignored"} == set(bundle.ignored)
        documents = {f"{cid}.md": doc for cid, doc in bundle.concepts.items()}
        for kind, collection, filename in [("index", bundle.indexes, "index.md"), ("log", bundle.logs, "log.md")]:
            reserved = {
                f"{directory}/{filename}" if directory else filename: doc for directory, doc in collection.items()
            }
            assert {i for i, r in rows.items() if r.kind == kind} == set(reserved)
            documents.update(reserved)
        for mid, doc in documents.items():
            row = rows[mid]
            assert row.type == doc.fm.type and row.title == doc.fm.title
            assert row.status == effective_status(doc.fm)
            assert row.tags == tuple(sorted(set(doc.fm.tags)))
            assert view.headings(mid) == document_headings(doc)
            if row.fm_exact:
                assert dict(row.fm or {}) == doc.fm_data(dates="iso")
            assert row.parse_error == doc.parse_error
            assert row.coercion_failures == doc.fm.coercion_failures
        for cid in bundle.concepts:
            assert view.outlinks(cid) == graph.out.get(cid, ())
            assert view.backlinks(cid) == graph.backlinks.get(cid, ())
        assert view.broken() == graph.broken
        diag = view.diagnostics()
        assert dict(diag.unreadable) == dict(bundle.unreadable)
        assert dict(diag.collisions) == dict(bundle.canonical_collisions)
        assert diag.pruned == bundle.pruned
        assert dict(diag.parse_errors) == {
            mid: doc.parse_error for mid, doc in documents.items() if doc.parse_error is not None
        }
        for path in ["ok.md", "café.md", "café.md", "vendor/v.md", "missing.md", "bad.md"]:
            assert view.resolve(path) == bundle.member_id(path)


def assert_equivalent(root: Path, db: Path, *, ignore=(), prune=()) -> None:
    with open_index(db, root, ignore=ignore, prune=prune) as index:
        reconcile(index)
        assert_view_equivalent(index)
