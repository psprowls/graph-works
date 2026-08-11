from dataclasses import replace
from pathlib import Path

import pytest
from work_tracker_okf.paths import artifact_path
from work_tracker_okf.results import ResultsFacts, render, write_results

_SLUG = "2026-03-02-epic-feature-filing-writer"

_FACTS = ResultsFacts(
    phase="execute",
    start_sha="abcdef1234567",
    end_sha="1234567890abc",
    files=("src/paths.py", "tests/test_paths.py"),
    commits=("abcdef1 feat: add the layout contract",),
)


def test_render_is_deterministic_and_pure() -> None:
    assert render(_FACTS) == render(_FACTS)


def test_render_carries_the_short_shas_the_counts_and_the_heading() -> None:
    text = render(_FACTS)
    assert text.startswith("## Execute — results\n")
    assert "`abcdef1`..`1234567`" in text
    assert "(1 commit)" in text
    assert "**Files changed:** 2" in text
    assert "- src/paths.py" in text
    assert "- abcdef1 feat: add the layout contract" in text


def test_the_footer_names_no_cli() -> None:
    """The stub does not know what dispatched it: `gw work advance` is tier-4
    knowledge and this package does not know it dispatches."""
    text = render(_FACTS)
    assert "gw " not in text
    assert "work-tracker-okf" not in text
    assert text.rstrip("\n").endswith("_Mechanical stub._")


def test_the_stub_carries_no_provenance_of_its_own() -> None:
    """It is a member, not a concept — the item page's `sources[]` entry records
    what produced it."""
    text = render(_FACTS)
    assert not text.startswith("---")
    assert "generated:" not in text


@pytest.mark.parametrize(
    ("commits", "expected"),
    [((), "(0 commits)"), (("a x",), "(1 commit)"), (("a x", "b y"), "(2 commits)")],
)
def test_the_plural_switches_on_the_commit_count(commits, expected) -> None:
    assert expected in render(replace(_FACTS, commits=commits))


def test_write_results_lands_exactly_at_the_artifact_path(tmp_path: Path) -> None:
    target = write_results(tmp_path, _SLUG, _FACTS)
    assert target == artifact_path(_SLUG, "execute", "results").path(tmp_path)
    assert target.read_text(encoding="utf-8") == render(_FACTS)


def test_write_results_creates_the_references_directory(tmp_path: Path) -> None:
    assert not (tmp_path / "work").exists()
    target = write_results(tmp_path, _SLUG, _FACTS)
    assert target.parent == tmp_path / "work" / _SLUG / "references"


def test_write_results_honours_archived(tmp_path: Path) -> None:
    target = write_results(tmp_path, _SLUG, _FACTS, archived=True)
    assert target == artifact_path(_SLUG, "execute", "results", archived=True).path(tmp_path)
    assert "_archive" in target.parts


def test_an_unknown_phase_raises_before_anything_is_written(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown phase"):
        write_results(tmp_path, _SLUG, replace(_FACTS, phase="done"))
    assert list(tmp_path.iterdir()) == []
