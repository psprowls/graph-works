"""repositories-okf: the repositories lane over OKF v0.2.

Band 2: depends on `okf-io` and `okf-ext[schemas]` only. It declares `ManagedRepository` and
`ReferenceRepository`, installs their schemas and sections, and names the clone glob every
graph-works bundle load hides. Git is handed its executable and environment by the caller.
"""

from __future__ import annotations

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

__version__ = "0.1.3"

__all__ = [
    "CLONE_GLOB",
    "CONTRIBUTED_TAGS",
    "GITIGNORE_PATTERN",
    "IGNORE",
    "LANE_DIR",
    "OBSIDIAN_FILTER",
    "PRUNE_GLOB",
    "TYPES",
    "__version__",
    "placement_directories",
]
