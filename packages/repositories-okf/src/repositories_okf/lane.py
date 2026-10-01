"""The repositories lane's constants: where it lives, what it declares, and what it hides.

`CLONE_GLOB` is the one pattern every graph-works bundle load appends (see
`graph_works_core.workspace.bundle`). okf-io's `*` crosses `/`, so it covers a clone's whole
subtree. `PRUNE_GLOB` separately matches the clone directory itself, keeping its foreign
content outside the bundle model.
"""

from __future__ import annotations

from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.schemas import SchemaSet, declared_directories
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE

LANE_DIR = "repositories"
TYPES: tuple[str, ...] = ("ManagedRepository", "ReferenceRepository")

#: Bundle-relative ignore glob for a materialized clone.
PRUNE_GLOB = "repositories/*/references/git"
CLONE_GLOB = "repositories/*/references/git/*"
#: Bundle-relative `.gitignore` pattern; core prefixes it with the bundle's path under the workspace root.
GITIGNORE_PATTERN = "repositories/*/references/git/"
#: Vault-relative Obsidian `userIgnoreFilters` entry; the vault root is the bundle directory.
OBSIDIAN_FILTER = "repositories/*/references/git/"

IGNORE: tuple[str, ...] = (CLONE_GLOB, "*/.DS_Store", *_SCHEMA_IGNORE, *_SECTIONS_IGNORE)


def placement_directories(schema_set: SchemaSet) -> dict[str, str]:
    """`declared_directories(schema_set)`, narrowed to this lane's `TYPES`.

    An allow-list for the same reason as `work_tracker_okf.items.placement_directories`: a composed
    workspace shares one `schema/`, and code-wiki's types are placed by code-wiki's own rule.
    """
    return {name: directory for name, directory in declared_directories(schema_set).items() if name in TYPES}


__all__ = [
    "CLONE_GLOB",
    "GITIGNORE_PATTERN",
    "IGNORE",
    "LANE_DIR",
    "OBSIDIAN_FILTER",
    "PRUNE_GLOB",
    "TYPES",
    "placement_directories",
]
