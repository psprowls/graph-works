"""The code graph arrives as two callables, or not at all."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from doc_wiki_okf.ingest.seams import NO_ENTITY, EntityMatch, EntityMatcher, StateGate, read_state_gate


def test_entity_match_defaults_to_two_nulls():
    assert EntityMatch() == NO_ENTITY
    assert NO_ENTITY.uri is None
    assert NO_ENTITY.entity_filename is None
    assert NO_ENTITY.as_data() == {"uri": None, "entity_filename": None}


def test_entity_match_carries_a_hit():
    match = EntityMatch(uri="pkg:o/r/graph-io", entity_filename="pkg_graph-io")
    assert match.as_data() == {"uri": "pkg:o/r/graph-io", "entity_filename": "pkg_graph-io"}


def test_an_absent_gate_reads_none():
    assert read_state_gate(None, Path("/repo"), Path("/w")) is None


def test_a_present_gate_is_called_with_the_repo_and_the_workspace():
    seen: list[tuple[Path, Path]] = []

    def gate(repo: Path, /, *, workspace: Path) -> Mapping[str, Any]:
        seen.append((repo, workspace))
        return {"scanned_at": "2026-08-12", "stale": False}

    checked: StateGate = gate
    value = read_state_gate(checked, Path("/repo"), Path("/w"))
    assert value == {"scanned_at": "2026-08-12", "stale": False}
    assert seen == [(Path("/repo"), Path("/w"))]


def test_a_matcher_stub_satisfies_the_protocol():
    def matcher(repo: Path, source: Path, title: str, /) -> EntityMatch:
        return EntityMatch(uri=f"pkg:{title}", entity_filename=source.stem)

    checked: EntityMatcher = matcher
    assert checked(Path("/repo"), Path("/repo/a.md"), "thing") == EntityMatch(uri="pkg:thing", entity_filename="a")
