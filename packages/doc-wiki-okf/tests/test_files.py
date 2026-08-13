"""language_for / list_folder_files / pick_representative.

Nine ported from wiki-io's test_ingest_source.py, four new — the ported suite
never calls `list_folder_files` directly.
"""

from __future__ import annotations

from pathlib import Path

from doc_wiki_okf.reading import language_for, list_folder_files, pick_representative

# ---------------------------------------------------------------------------
# language_for — ported
# ---------------------------------------------------------------------------


def test_language_for_python() -> None:
    assert language_for(Path("foo.py")) == "python"


def test_language_for_typescript() -> None:
    assert language_for(Path("component.tsx")) == "typescript"


def test_language_for_rust() -> None:
    assert language_for(Path("main.rs")) == "rust"


def test_language_for_go() -> None:
    assert language_for(Path("server.go")) == "go"


def test_language_for_unknown() -> None:
    assert language_for(Path("file.xyz")) == "unknown"


# ---------------------------------------------------------------------------
# pick_representative — ported
# ---------------------------------------------------------------------------


def test_pick_representative_readme_wins(tmp_path: Path) -> None:
    entries = [("README.md", 100), ("index.ts", 200), ("utils.ts", 50)]
    assert pick_representative(tmp_path, entries) == "README.md"


def test_pick_representative_index_ts(tmp_path: Path) -> None:
    entries = [("index.ts", 200), ("utils.ts", 50)]
    assert pick_representative(tmp_path, entries) == "index.ts"


def test_pick_representative_falls_back_to_largest(tmp_path: Path) -> None:
    entries = [("small.go", 10), ("large.go", 500)]
    rep = pick_representative(tmp_path, entries)
    assert rep == "large.go"


def test_pick_representative_empty(tmp_path: Path) -> None:
    assert pick_representative(tmp_path, []) is None


# --- new coverage, not ported --------------------------------------------


def test_pick_representative_readme_match_is_case_insensitive(tmp_path: Path) -> None:
    """The lookup lowercases; the returned value keeps the on-disk casing."""
    entries = [("ReadMe.MD", 10), ("other.ts", 900)]
    assert pick_representative(tmp_path, entries) == "ReadMe.MD"


def test_list_folder_files_returns_sorted_relative_paths_and_sizes(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "b.py").write_text("bb\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "pkg" / "c.ts").write_text("ccc\n", encoding="utf-8")

    assert list_folder_files(tmp_path) == [("a.md", 2), ("b.py", 3), ("pkg/c.ts", 4)]


def test_list_folder_files_skips_directories(tmp_path: Path) -> None:
    """`rglob("*")` yields directories too; only regular files are reported."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "only.md").write_text("x\n", encoding="utf-8")

    assert list_folder_files(tmp_path) == [("only.md", 2)]


def test_list_folder_files_on_an_empty_directory_is_empty(tmp_path: Path) -> None:
    assert list_folder_files(tmp_path) == []
