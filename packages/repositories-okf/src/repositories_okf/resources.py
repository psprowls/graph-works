"""This package's own asset files, read out of package data. Same split as `work_tracker_okf.resources`."""

from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable

#: Every file this package owns, bundle-relative posix, in write order. The base is named `_base-repository`
#: because work-tracker already ships `schema/_base.schema.json` into the same shared `schema/`. No fragments
#: file: neither sections file uses `placeholder_ref`.
SEED_RELATIVE_PATHS: tuple[str, ...] = (
    "schema/_base-repository.schema.json",
    "schema/ManagedRepository.schema.json",
    "schema/ReferenceRepository.schema.json",
    "schema/RepositorySnapshot.schema.json",
    "schema/RepositoryChangelog.schema.json",
    "sections/ManagedRepository.yaml",
    "sections/ReferenceRepository.yaml",
    "sections/RepositorySnapshot.yaml",
    "sections/RepositoryChangelog.yaml",
)


def assets_root() -> Traversable:
    return importlib.resources.files("repositories_okf") / "assets"


def seed_files() -> dict[str, str]:
    assets = assets_root()
    return {relative: (assets / relative).read_text(encoding="utf-8") for relative in SEED_RELATIVE_PATHS}


__all__ = ["SEED_RELATIVE_PATHS", "assets_root", "seed_files"]
