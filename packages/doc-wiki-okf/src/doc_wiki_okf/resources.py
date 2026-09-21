"""This package's own asset files, read out of package data.

The layout and the split are `work_tracker_okf/resources.py`'s, unchanged: a
module that names the assets without owning the installer, because the tests
want the seed text and C3's CLI wants the mapping.
"""

from __future__ import annotations

import importlib.resources
from importlib.resources.abc import Traversable

#: Every file this package owns, bundle-relative posix, in write order.
#:
#: Fourteen: `sections/_fragments.doc_wiki.yaml` is a member like any other, and
#: an installed `sections/` without it cannot resolve a single `placeholder_ref`;
#: `Source` and `Adr` ship a schema and a sections file like every other declared
#: type, even though neither sits in `RUBRIC` (`Adr` is the ADR lane's type, in
#: `adrs/`). `index.md`, `log.md` and `tags.yaml` are absent because they belong to
#: `okf_ext.bundle`'s scaffold, which any tier-3 package sharing a bundle may be
#: the first to run.
SEED_RELATIVE_PATHS: tuple[str, ...] = (
    "schema/_base-diataxis.schema.json",
    "schema/Tutorial.schema.json",
    "schema/HowTo.schema.json",
    "schema/Reference.schema.json",
    "schema/Explanation.schema.json",
    "schema/Source.schema.json",
    "schema/Adr.schema.json",
    "sections/_fragments.doc_wiki.yaml",
    "sections/Tutorial.yaml",
    "sections/HowTo.yaml",
    "sections/Reference.yaml",
    "sections/Explanation.yaml",
    "sections/Source.yaml",
    "sections/Adr.yaml",
)


def assets_root() -> Traversable:
    """The package-data directory the seeds are read from."""
    return importlib.resources.files("doc_wiki_okf") / "assets"


def seed_files() -> dict[str, str]:
    """Every file this package installs, keyed by bundle-relative posix path.

    Ready to hand to `okf_ext.bundle.plan_install`, which is C3's call to make.
    """
    assets = assets_root()
    return {relative: (assets / relative).read_text(encoding="utf-8") for relative in SEED_RELATIVE_PATHS}


__all__ = ["SEED_RELATIVE_PATHS", "assets_root", "seed_files"]
