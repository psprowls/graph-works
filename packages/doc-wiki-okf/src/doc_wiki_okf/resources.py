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
#: Twelve, not ten: `_sections/_fragments.yaml` is a member like any other, and
#: an installed `_sections/` without it cannot resolve a single
#: `placeholder_ref`; and `Source` ships a schema and a sections file like every
#: other declared type, even though it sits outside `RUBRIC`. `index.md`,
#: `log.md` and `_tags.yaml` are absent because they belong to
#: `okf_ext.bundle`'s scaffold, which any tier-3 package sharing a bundle may be
#: the first to run.
SEED_RELATIVE_PATHS: tuple[str, ...] = (
    "_schema/_base-diataxis.schema.json",
    "_schema/Tutorial.schema.json",
    "_schema/HowTo.schema.json",
    "_schema/Reference.schema.json",
    "_schema/Explanation.schema.json",
    "_schema/Source.schema.json",
    "_sections/_fragments.yaml",
    "_sections/Tutorial.yaml",
    "_sections/HowTo.yaml",
    "_sections/Reference.yaml",
    "_sections/Explanation.yaml",
    "_sections/Source.yaml",
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
