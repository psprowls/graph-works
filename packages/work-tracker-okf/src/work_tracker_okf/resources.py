"""This package's own asset files, read out of package data.

Split from `init.py` -- which `code-wiki-okf` does not do -- because child 2's
tests want the seed text without wanting the installer, and because the
fragment file makes the asset set slightly more than a flat list of templates.

The name is reused from `code-wiki-okf/resources.py` for a different job. That
module is a find-by-resource index: the entity lane looks pages up by the code
path they describe. The work lane addresses items by canonical path, so there
is no such index here.
"""

from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable

#: Every file this package owns, bundle-relative posix, in write order.
#:
#: Sixteen, not thirteen: `sections/_fragments.work_tracker.yaml` is a
#: member like any other, and an installed `sections/` without it cannot
#: resolve a single `placeholder_ref`. `index.md`, `log.md` and `tags.yaml`
#: are absent because they belong to `okf_ext.bundle`'s scaffold, which any of
#: the three tier-3 packages sharing a bundle may be the first to run.
SEED_RELATIVE_PATHS: tuple[str, ...] = (
    "schema/_base.schema.json",
    "schema/Release.schema.json",
    "schema/Epic.schema.json",
    "schema/Feature.schema.json",
    "schema/Bug.schema.json",
    "schema/TechDebt.schema.json",
    "schema/TestGap.schema.json",
    "schema/Spike.schema.json",
    "sections/_fragments.work_tracker.yaml",
    "sections/Release.yaml",
    "sections/Epic.yaml",
    "sections/Feature.yaml",
    "sections/Bug.yaml",
    "sections/TechDebt.yaml",
    "sections/TestGap.yaml",
    "sections/Spike.yaml",
)


def assets_root() -> Traversable:
    """The package-data directory the seeds are read from."""
    return importlib.resources.files("work_tracker_okf") / "assets"


def seed_files() -> dict[str, str]:
    """Every file this package installs, keyed by bundle-relative posix path.

    Takes no `declarations_dir`: unlike `code-wiki-okf`, this package ships no
    configuration file to stamp one into (C1-H). Relocating the declarations is
    still supported -- it is `okf_ext.bundle`'s `declarations_dir=`, applied at
    plan time -- it is just not persisted anywhere.
    """
    assets = assets_root()
    return {relative: (assets / relative).read_text(encoding="utf-8") for relative in SEED_RELATIVE_PATHS}


__all__ = ["SEED_RELATIVE_PATHS", "assets_root", "seed_files"]
