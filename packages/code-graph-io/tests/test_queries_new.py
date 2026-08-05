"""Tests for the cli/core raw-SQL reader methods added to queries.py / handle.py.

Each method ports the SQL verbatim from a real call site; these tests build a
tiny graph via the testing seam and assert shape + known values.
"""

from __future__ import annotations

from pathlib import Path

from code_graph_io import testing as gtesting


def _seed(db: Path):
    store = gtesting.open_store(db, create=True)
    c = store._conn
    c.execute("BEGIN")
    c.execute(
        "INSERT INTO nodes (id, kind, name, path, attrs_json) "
        "VALUES (1,'file','a.py','pkg/a.py','{\"language\":\"python\"}')"
    )
    c.execute("INSERT INTO nodes (id, kind, name, path) VALUES (2,'package','pkg','pkg')")
    c.execute(
        "INSERT INTO nodes (id, kind, name, path, attrs_json) "
        "VALUES (3,'entry_point','myep','pkg/a.py','{\"callable\":\"app:main\"}')"
    )
    c.execute("INSERT INTO nodes (id, kind, name, path) VALUES (4,'class','MyClass','pkg/a.py')")
    c.execute("INSERT INTO nodes (id, kind, name, path) VALUES (5,'function','my_func','pkg/a.py')")
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (2,1,'contains')")
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (2,3,'declares_entry_point')")
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (1,4,'contains')")
    c.execute("INSERT INTO edges (src, dst, kind) VALUES (1,5,'contains')")
    c.execute("INSERT INTO metadata (key, value) VALUES ('last_indexed_commit','abc123')")
    c.commit()
    return store


def test_metadata(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.metadata("last_indexed_commit") == "abc123"
        assert store.metadata("missing") is None
    finally:
        store.close()


def test_counts(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.node_count() == 5
        assert store.node_counts_by_kind() == {
            "file": 1,
            "package": 1,
            "entry_point": 1,
            "class": 1,
            "function": 1,
        }
        assert store.edge_counts_by_kind() == {"contains": 3, "declares_entry_point": 1}
    finally:
        store.close()


def test_languages(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.languages() == ["python"]
    finally:
        store.close()


def test_file_paths_and_attrs(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.file_paths() == ["pkg/a.py"]
        assert store.file_paths_in_package("pkg") == ["pkg/a.py"]
        # case-insensitive (LOWER(p.name)=LOWER(?))
        assert store.file_paths_in_package("PKG") == ["pkg/a.py"]
        assert store.file_attrs("pkg/a.py") == {"language": "python"}
        assert store.file_attrs("nope.py") is None
    finally:
        store.close()


def test_files_in_node_and_symbols(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        rows = store.files_in_node(2)
        assert len(rows) == 1
        assert rows[0][0] == 1
        assert rows[0][1] == "pkg/a.py"
        syms = store.symbol_names_under_files([1])
        assert sorted(syms) == ["MyClass", "my_func"]
        # empty file_ids → no SQL, empty list
        assert store.symbol_names_under_files([]) == []
    finally:
        store.close()


def test_declared_entry_points(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.declared_entry_points() == [("pkg", "pkg/a.py", "app:main")]
    finally:
        store.close()


def test_node_exists(tmp_path: Path):
    store = _seed(tmp_path / "code.db")
    try:
        assert store.node_exists(kind="package", name="pkg") is True
        assert store.node_exists(kind="package", name="nonexistent-pkg") is False
    finally:
        store.close()
