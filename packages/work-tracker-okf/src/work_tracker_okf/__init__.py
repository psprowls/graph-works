"""work-tracker-okf: work-item tracking as an OKF v0.2 lane.

Tier 3 per ADR-0005: depends on `okf-io` and `okf-ext[schemas]`, and nothing
depends on this package. Ships the fourteen declaration files a work lane
installs into a shared bundle, the closed vocabulary those declarations encode,
and the `WorkItem` projection every other module in the lane reads.

    from work_tracker_okf import IGNORE, load_items
    from okf_io import load_bundle

    bundle = load_bundle(root, ignore=IGNORE)
    items = load_items(bundle)

The decision layer is five submodules, importing strictly downward:

    items -> hierarchy -> workflow -> {advance, children, projection}

    from work_tracker_okf.workflow import route, state_for
    from work_tracker_okf.advance import advance, apply

They stay submodules for the same reason `vocabulary` does: the module name is
what says which `route`, which `apply` and which `rollup` is meant, and
hoisting them would put `apply` and `rollup` at the package's front door as
bare names.

The layout and writer surface is five more, on the same rule:

    paths -> {filing, sources, results, archive}

    from work_tracker_okf.paths import artifact_path, item_page
    from work_tracker_okf.filing import file_item
    from work_tracker_okf.sources import upsert
    from work_tracker_okf.archive import apply_archive, plan_archive

`paths.artifact_path` returns one frozen `ArtifactRef` carrying the bundle-relative
form, the root-absolute `sources[].resource` form, the filesystem form and the
matching `sources[].id` — so a caller cannot obtain a resource without its id.
They stay submodules for the same reason the decision layer does: `filing.apply`
and `advance.apply` are different things, and the module name is what says which.

`archive` takes a bundle loaded through `ARCHIVE_IGNORE` rather than `IGNORE`:
`okf_ext.moves` never reads `bundle.ignored`, so the narrow recipe would plan a
move covering only the item page. It is the one path that wants the wider lens.

`vocabulary` stays a submodule rather than being flattened into this namespace:
its fourteen constants are read as `vocabulary.TERMINAL_STATUSES`, where the
module name says which vocabulary is meant, and hoisting them would make
`TYPES` and `PHASES` bare names at the package's front door.
"""

from __future__ import annotations

__version__ = "0.1.0"

from work_tracker_okf.init import BundleInstall, InitError, install_bundle, plan_install
from work_tracker_okf.items import ARCHIVE_DIR, ARCHIVE_IGNORE, IGNORE, WORK_DIR, WorkItem, load_items
from work_tracker_okf.resources import SEED_RELATIVE_PATHS, seed_files

__all__ = [
    "ARCHIVE_DIR",
    "ARCHIVE_IGNORE",
    "IGNORE",
    "SEED_RELATIVE_PATHS",
    "WORK_DIR",
    "BundleInstall",
    "InitError",
    "WorkItem",
    "__version__",
    "install_bundle",
    "load_items",
    "plan_install",
    "seed_files",
]
