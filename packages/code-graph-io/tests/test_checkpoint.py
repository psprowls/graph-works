from __future__ import annotations

from pathlib import Path

import pytest
from code_graph_io import checkpoint as checkpoint_module
from code_graph_io import open_reader
from code_graph_io.testing import raw_conn


def _stamp(graph: Path, value: str) -> None:
    with raw_conn(graph / "code.db", create=True) as conn:
        conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('review', ?)", (value,))
        conn.commit()


def test_checkpoint_restores_committed_wal_data_for_existing_readers(tmp_path: Path) -> None:
    _stamp(tmp_path, "before")
    with checkpoint_module.graph_checkpoint(tmp_path) as restore:
        _stamp(tmp_path, "after")
        with open_reader(graph_dir=tmp_path) as reader:
            assert reader.metadata("review") == "after"
            restore()
            assert reader.metadata("review") == "before"
    assert not list(tmp_path.glob("graph-checkpoint-*"))


def test_checkpoint_keeps_changes_on_success(tmp_path: Path) -> None:
    _stamp(tmp_path, "before")
    with checkpoint_module.graph_checkpoint(tmp_path):
        _stamp(tmp_path, "after")
    with open_reader(graph_dir=tmp_path) as reader:
        assert reader.metadata("review") == "after"


def test_absent_graph_restores_to_empty_queryable_cache(tmp_path: Path) -> None:
    with checkpoint_module.graph_checkpoint(tmp_path) as restore:
        assert not (tmp_path / "code.db").exists()
        _stamp(tmp_path, "after")
        restore()
    with open_reader(graph_dir=tmp_path) as reader:
        assert reader.metadata("review") is None and reader.node_count() == 0


def test_invalid_database_is_reported_as_os_error(tmp_path: Path) -> None:
    (tmp_path / "code.db").write_bytes(b"invalid database")
    with pytest.raises(OSError, match="graph checkpoint"), checkpoint_module.graph_checkpoint(tmp_path):
        pytest.fail("must refuse before mutation")


def test_restore_failure_is_reported_as_os_error(tmp_path: Path) -> None:
    _stamp(tmp_path, "before")
    with checkpoint_module.graph_checkpoint(tmp_path) as restore:
        database = tmp_path / "code.db"
        database.unlink()
        database.mkdir()
        with pytest.raises(OSError, match="cannot restore graph checkpoint"):
            restore()
