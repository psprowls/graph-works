from __future__ import annotations

import importlib.resources

from okf_ext.shape import SectionSet, load_sections
from repositories_okf.lane import TYPES


def _section_set() -> SectionSet:
    return load_sections(str(importlib.resources.files("repositories_okf") / "assets" / "sections"))


def test_both_types_declare_exactly_a_required_summary() -> None:
    section_set = _section_set()
    for type_name in TYPES:
        declared = section_set.types[type_name]
        assert [section.heading for section in declared.sections] == ["Summary"]
        assert [section.heading for section in declared.sections if section.required] == ["Summary"]


def test_no_fragments_file_is_shipped() -> None:
    assets = importlib.resources.files("repositories_okf") / "assets" / "sections"
    assert sorted(entry.name for entry in assets.iterdir()) == [
        "ManagedRepository.yaml",
        "ReferenceRepository.yaml",
        "RepositoryChangelog.yaml",
        "RepositorySnapshot.yaml",
    ]
