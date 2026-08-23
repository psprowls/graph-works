from dataclasses import replace
from pathlib import Path

import pytest
from okf_io import load
from work_helpers import write_item
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref
from work_tracker_okf.results import ResultsFacts, render, write_results

_PATH = "work/release-r1/children/epic-migration/children/feature-filing-writer"

_FACTS = ResultsFacts(
    phase="execute",
    start_sha="abcdef1234567",
    end_sha="1234567890abc",
    files=("src/paths.py", "tests/test_paths.py"),
    commits=("abcdef1 feat: add the layout contract",),
    scope=("src/", "tests/"),
    start_predates_item=False,
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


def test_render_names_its_own_scope() -> None:
    text = render(_FACTS)
    assert "src/" in text
    assert "tests/" in text


def test_an_empty_range_warns_instead_of_printing_a_bare_zero() -> None:
    facts = replace(_FACTS, start_sha="1234567890abc", commits=(), files=())
    text = render(facts)
    assert "Range is empty" in text


def test_a_normal_range_carries_no_empty_range_warning() -> None:
    assert "Range is empty" not in render(_FACTS)


def test_a_start_predating_the_item_warns() -> None:
    text = render(replace(_FACTS, start_predates_item=True))
    assert "before the item was opened" in text


def test_a_start_not_predating_the_item_carries_no_warning() -> None:
    assert "before the item was opened" not in render(_FACTS)


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
    write_item(tmp_path, _PATH, "type: Feature\nwork_status: in-progress\n")
    target = write_results(tmp_path, _PATH, _FACTS)
    assert target == artifact_ref(_PATH, MANAGED_ARTIFACTS["execute-results"]).path(tmp_path)
    assert target.read_text(encoding="utf-8") == render(_FACTS)
    sources = load(tmp_path / f"{_PATH}.md").fm_data()["sources"]
    assert sources[0]["resource"] == f"/{_PATH}/references/03-execute-results.md"


def test_write_results_creates_the_references_directory(tmp_path: Path) -> None:
    write_item(tmp_path, _PATH, "type: Feature\nwork_status: in-progress\n")
    target = write_results(tmp_path, _PATH, _FACTS)
    assert target.parent == tmp_path / _PATH / "references"


def test_write_results_preserves_archive_segments_in_identity(tmp_path: Path) -> None:
    path = "work/release-r1/children/epic-migration/children/_archive/bug-fixed"
    write_item(tmp_path, path, "type: Bug\nwork_status: resolved\n")
    target = write_results(tmp_path, path, _FACTS)
    assert target == artifact_ref(path, MANAGED_ARTIFACTS["execute-results"]).path(tmp_path)


def test_an_unknown_phase_raises_before_anything_is_written(tmp_path: Path) -> None:
    write_item(tmp_path, _PATH, "type: Feature\nwork_status: in-progress\n")
    with pytest.raises(ValueError, match="execute/finish"):
        write_results(tmp_path, _PATH, replace(_FACTS, phase="done"))
    assert not (tmp_path / _PATH / "references").exists()
