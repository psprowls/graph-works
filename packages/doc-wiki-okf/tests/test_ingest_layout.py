"""The layout is a value: today's directory names, and somebody else's."""

from pathlib import Path

from doc_wiki_okf.ingest.layout import (
    GRAPH_WIKI_LAYOUT,
    IngestLayout,
    archive_destination,
    guess_source_type,
    resolve_source_path,
)

OTHER = IngestLayout(
    raw_dir="inbox",
    archive_dir="done",
    source_types={"rfcs": "spec", "posts": "article"},
    batch_kinds=frozenset({"rfcs", "posts"}),
    source_page_template="pages/{slug}-{month}.md",
)


def test_the_shipped_layout_is_todays_values():
    assert GRAPH_WIKI_LAYOUT.raw_dir == "raw"
    assert GRAPH_WIKI_LAYOUT.archive_dir == "_archive"
    assert GRAPH_WIKI_LAYOUT.source_page_template == "sources/{month}-{slug}.md"
    assert GRAPH_WIKI_LAYOUT.batch_kinds == frozenset(
        {"specs", "articles", "prs", "tickets", "transcripts", "examples", "skills"}
    )


def test_the_layout_is_frozen():
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        GRAPH_WIKI_LAYOUT.raw_dir = "elsewhere"  # type: ignore[misc]


def test_every_raw_folder_guesses_its_source_type():
    cases = {
        "specs": "spec",
        "articles": "article",
        "prs": "pr",
        "tickets": "ticket",
        "transcripts": "transcript",
        "examples": "example",
    }
    for folder, expected in cases.items():
        assert guess_source_type(Path("raw") / folder / "x.md", None) == expected


def test_both_the_singular_and_plural_skill_folder_guess_skill():
    """Legacy checked the singular only; the plural was decided by the skill
    brief this port declines. Both answer `skill` now."""
    assert guess_source_type(Path("raw/skill/x.md"), None) == "skill"
    assert guess_source_type(Path("raw/skills/x.md"), None) == "skill"


def test_a_work_item_spec_guesses_spec():
    assert guess_source_type(Path("wiki/work/2026-01-01-thing-spec.md"), None) == "spec"
    assert guess_source_type(Path("wiki/work/2026-01-01-thing.md"), None) == "note"


def test_an_in_repo_doc_guesses_doc_and_a_stray_file_guesses_note():
    assert guess_source_type(None, Path("packages/x/README.md")) == "doc"
    assert guess_source_type(None, None) == "note"


def test_another_layout_answers_with_its_own_folder_names():
    assert guess_source_type(Path("inbox/rfcs/x.md"), None, layout=OTHER) == "spec"
    assert guess_source_type(Path("raw/specs/x.md"), None, layout=OTHER) == "note"


def test_archive_destination_maps_a_unit_under_raw():
    raw = Path("/w/raw")
    assert archive_destination(raw, raw / "specs" / "a.md") == raw / "_archive" / "specs" / "a.md"


def test_archive_destination_declines_the_cases_with_no_answer():
    raw = Path("/w/raw")
    assert archive_destination(raw, Path("/w/wiki/a.md")) is None
    assert archive_destination(raw, raw) is None
    assert archive_destination(raw, raw / "_archive" / "specs" / "a.md") is None


def test_archive_destination_follows_the_layout():
    raw = Path("/w/inbox")
    assert archive_destination(raw, raw / "rfcs" / "a.md", layout=OTHER) == raw / "done" / "rfcs" / "a.md"
    assert archive_destination(raw, raw / "done" / "a.md", layout=OTHER) is None


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
