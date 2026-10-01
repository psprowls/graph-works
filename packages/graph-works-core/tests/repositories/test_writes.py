from __future__ import annotations

from pathlib import Path

from graph_works_core.repositories.writes import WriteLog


def test_rollback_restores_prior_bytes_and_removes_new_files(tmp_path: Path) -> None:
    (tmp_path / "log.md").write_bytes(b"before\r\n")
    log = WriteLog(tmp_path)
    log.write("log.md", "after\n")
    log.write("repositories/demo/snapshots/s.md", "new\n")
    log.write("log.md", "after again\n")  # the first `before` is the one kept
    assert log.paths == ("log.md", "repositories/demo/snapshots/s.md")
    log.rollback()
    assert (tmp_path / "log.md").read_bytes() == b"before\r\n"
    assert not (tmp_path / "repositories/demo/snapshots/s.md").exists()


def test_remember_records_a_file_another_writer_changes(tmp_path: Path) -> None:
    log = WriteLog(tmp_path)
    log.remember("proposals/p.md")
    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "p.md").write_bytes(b"written by okf-ext\n")
    assert log.paths == ("proposals/p.md",)
    log.rollback()
    assert not (tmp_path / "proposals" / "p.md").exists()


def test_writes_are_utf8_bytes_with_no_newline_translation(tmp_path: Path) -> None:
    log = WriteLog(tmp_path)
    log.write("a.md", "é\n")
    assert (tmp_path / "a.md").read_bytes() == "é\n".encode()
