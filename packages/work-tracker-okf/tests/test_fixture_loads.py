from pathlib import Path

from okf_io import Bundle

_EXPECTED_CONCEPTS = {
    "notes/scratch",
    "work/broken-eta",
    "work/bug-gamma",
    "work/epic-alpha",
    "work/feature-beta",
    "work/feature-beta/notes",
    "work/feature-beta/references/01-design-spec",
    "work/feature-beta/references/02-plan-plan",
    "work/spike-zeta",
    "work/tech-debt-delta",
    "work/test-gap-epsilon",
    "work/_archive/bug-theta",
}


def test_the_fixture_loads_cleanly(minimal_bundle: Bundle) -> None:
    assert dict(minimal_bundle.unreadable) == {}
    for concept_id, document in minimal_bundle.concepts.items():
        assert document.parse_error is None, concept_id


def test_the_fixture_holds_exactly_the_expected_concepts(minimal_bundle: Bundle) -> None:
    assert set(minimal_bundle.concepts) == _EXPECTED_CONCEPTS


def test_the_fixture_carries_both_index_files_and_a_non_markdown_artifact(minimal_bundle: Bundle) -> None:
    assert set(minimal_bundle.indexes) == {"", "work", "work/_archive"}
    assert "work/feature-beta/references/03-plan-transcript.txt" in minimal_bundle.assets


def test_the_malformed_page_still_parses(minimal_bundle: Bundle) -> None:
    """okf-io's tolerance rule: a malformed page is still a `Document`. The
    failures are in `coercion_failures`, not in an exception."""
    document = minimal_bundle.concepts["work/broken-eta"]
    assert document.parse_error is None
    assert "type" in document.fm.coercion_failures


def test_the_fixture_root_is_a_path(minimal_root: Path) -> None:
    assert minimal_root.is_dir()
