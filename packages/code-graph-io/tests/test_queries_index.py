"""Tests for the entity-lookup / index-generation reader methods.

A small graph is seeded via the testing seam; one assertion per per-kind branch.
"""

from __future__ import annotations

from pathlib import Path

from code_graph_io import testing as gtesting


def _seed(db: Path):
    store = gtesting.open_store(db, create=True)
    c = store._conn
    c.execute("BEGIN")
    # nodes
    c.execute("INSERT INTO nodes (id, kind, name, path, uri) VALUES (1,'package','pkg','pkg','pkg-uri')")
    c.execute("INSERT INTO nodes (id, kind, name, path) VALUES (3,'file','a.py','pkg/a.py')")
    c.execute("INSERT INTO nodes (id, kind, name, uri) VALUES (4,'dependency','requests','dep-uri')")
    c.execute("INSERT INTO nodes (id, kind, name, uri) VALUES (5,'test_suite','pkg-tests','ts-uri')")
    c.execute("INSERT INTO nodes (id, kind, name, path, uri) VALUES (7,'app','myapp','apps/myapp','app-uri')")
    # edges
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (1,3,'contains')")  # pkg contains a.py
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (1,4,'used_by')")  # pkg used_by requests
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (5,1,'tests')")  # pkg-tests tests pkg
    c.commit()
    return store


def test_consumer_packages_includes_repository(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        c = store._conn
        c.execute("BEGIN")
        c.execute("INSERT INTO nodes (id, kind, name, path, uri) VALUES (8,'repository','repo','','repo-uri')")
        c.execute("INSERT INTO edges (src, dst, kind) VALUES (8,4,'used_by')")  # repo used_by requests
        c.commit()
        assert store.consumer_packages(kind="dependency", entity_name="requests") == ("pkg", "repo")
    finally:
        store.close()


def test_package_for_file(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        # (name, uri) order — callers wanting (uri, name) re-order
        assert store.package_for_file(path="pkg/a.py") == ("pkg", "pkg-uri")
        assert store.package_for_file(path="nope.py") is None
    finally:
        store.close()


def test_entity_by_name(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.entity_by_name(name="pkg") == [("pkg", "pkg-uri", "package")]
        assert store.entity_by_name(name="missing") == []
    finally:
        store.close()


def test_package_or_app_by_dir(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.package_or_app_by_dir(path="pkg") == ("pkg-uri", "pkg", 1)
        assert store.package_or_app_by_dir(path="apps/myapp") == ("app-uri", "myapp", 7)
        assert store.package_or_app_by_dir(path="nope") is None
    finally:
        store.close()


def test_consumer_packages(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.consumer_packages(kind="dependency", entity_name="requests") == ("pkg",)
        assert store.consumer_packages(kind="test_suite", entity_uri="ts-uri") == ("pkg",)
        assert store.consumer_packages(kind="package") == ()
    finally:
        store.close()
