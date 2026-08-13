"""The layout is a value: today's page template, and somebody else's."""

import dataclasses
from pathlib import Path

import pytest
from doc_wiki_okf.ingest.layout import GRAPH_WIKI_LAYOUT, IngestLayout, resolve_source_path

OTHER = IngestLayout(source_page_template="pages/{slug}-{month}.md")


def test_the_shipped_layout_is_todays_value():
    assert GRAPH_WIKI_LAYOUT.source_page_template == "sources/{month}-{slug}.md"


def test_the_layout_carries_exactly_one_field():
    """`raw_dir`, `archive_dir`, `source_types` and `batch_kinds` were `raw/`'s,
    and `raw/` is retired from the code."""
    assert [f.name for f in dataclasses.fields(IngestLayout)] == ["source_page_template"]


def test_the_layout_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        GRAPH_WIKI_LAYOUT.source_page_template = "elsewhere/{slug}.md"  # type: ignore[misc]


def test_another_layout_formats_with_its_own_template():
    assert OTHER.source_page_template.format(month="2026-08", slug="a") == "pages/a-2026-08.md"


def test_resolve_source_path_prefers_a_file_that_exists_under_the_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text("# A\n", encoding="utf-8")
    assert resolve_source_path(Path("docs/a.md"), repo) == repo / "docs" / "a.md"


def test_resolve_source_path_passes_an_absolute_path_through(tmp_path):
    assert resolve_source_path(tmp_path / "a.md", tmp_path / "repo") == tmp_path / "a.md"


def test_resolve_source_path_falls_back_to_cwd_resolution(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loose.md").write_text("# L\n", encoding="utf-8")
    assert resolve_source_path(Path("loose.md"), tmp_path / "nowhere") == tmp_path.resolve() / "loose.md"
