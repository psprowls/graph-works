from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

import repositories_okf
from okf_ext.schemas import DEFAULT_IGNORE as SCHEMA_IGNORE
from okf_ext.schemas import SchemaSet
from okf_ext.shape import DEFAULT_IGNORE as SECTIONS_IGNORE
from repositories_okf.lane import (
    CLONE_GLOB,
    GITIGNORE_PATTERN,
    IGNORE,
    LANE_DIR,
    OBSIDIAN_FILTER,
    PRUNE_GLOB,
    TYPES,
    placement_directories,
)
from repositories_okf.vocabulary import CONTRIBUTED_TAGS


def test_the_constants_are_the_design_values() -> None:
    assert PRUNE_GLOB == "repositories/*/references/git"
    assert repositories_okf.PRUNE_GLOB == PRUNE_GLOB
    assert LANE_DIR == "repositories"
    assert TYPES == ("ManagedRepository", "ReferenceRepository")
    assert CLONE_GLOB == "repositories/*/references/git/*"
    assert GITIGNORE_PATTERN == "repositories/*/references/git/"
    assert OBSIDIAN_FILTER == "repositories/*/references/git/"
    assert (CLONE_GLOB, "*/.DS_Store", *SCHEMA_IGNORE, *SECTIONS_IGNORE) == IGNORE


def test_the_clone_glob_covers_the_whole_clone_subtree_and_nothing_else() -> None:
    inside = (
        "repositories/demo/references/git/README.md",
        "repositories/demo/references/git/docs/deep/page.md",
        "repositories/demo/references/git/index.md",
    )
    outside = (
        "repositories/demo.md",
        "repositories/index.md",
        "repositories/demo/references/notes.md",
        "work/x/references/git/README.md",
    )
    assert all(fnmatchcase(path, CLONE_GLOB) for path in inside)
    assert not any(fnmatchcase(path, CLONE_GLOB) for path in outside)


def test_placement_directories_is_an_allow_list_of_this_lanes_types() -> None:
    schemas = {
        "ManagedRepository": {"x-okf-directory": "repositories/"},
        "ReferenceRepository": {"x-okf-directory": "repositories/"},
        "Feature": {"x-okf-directory": "work/"},
        "Package": {"x-okf-directory": "code-graph/"},
    }
    schema_set = SchemaSet(
        schemas=schemas,
        sources={name: f"{name}.schema.json" for name in schemas},
        documents={},
        root=Path(),
    )
    assert placement_directories(schema_set) == {
        "ManagedRepository": "repositories/",
        "ReferenceRepository": "repositories/",
    }


def test_contributed_tags_are_repository_and_upstream() -> None:
    assert tuple(tag.name for tag in CONTRIBUTED_TAGS) == ("repository", "upstream")
    assert all(tag.description.strip() for tag in CONTRIBUTED_TAGS)


def test_the_package_root_re_exports_the_lane_surface() -> None:
    names = ("LANE_DIR", "TYPES", "CLONE_GLOB", "GITIGNORE_PATTERN", "OBSIDIAN_FILTER", "IGNORE", "CONTRIBUTED_TAGS")
    for name in names:
        assert name in repositories_okf.__all__
        assert getattr(repositories_okf, name) is not None
