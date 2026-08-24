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

The layout and writer surface is six more, on the same rule:

    paths -> {filing, sources, results, mutation, reparent, archive, decisions}

    from work_tracker_okf.paths import artifact_ref, item_page
    from work_tracker_okf.filing import plan_filing
    from work_tracker_okf.sources import upsert
    from work_tracker_okf.reparent import plan_reparent
    from work_tracker_okf.archive import plan_archive

`paths.artifact_ref` returns one frozen `ArtifactRef` carrying the bundle-relative
form, the root-absolute `sources[].resource` form, the filesystem form and the
matching `sources[].id` — so a caller cannot obtain a resource without its id.
They stay submodules for the same reason the decision layer does: `filing.apply`
and `advance.apply` are different things, and the module name is what says which.

Path mutations treat `Bundle.ignored` files beneath an owned subtree as members:
unregistered `references/` content stays opaque while registered and canonical
Markdown is promoted for repair. `mutation.WorkMutationPlan` is the single
immutable effect vocabulary for reparenting, Release adoption, and local
archival.

Decision mutations use an immutable plan/apply pair. Path mutation planning
captures every expected refusal without writing or applying filesystem effects;
decision apply rechecks its ledger snapshot under the existing exclusive lock.

`vocabulary` stays a submodule rather than being flattened into this namespace:
its fourteen constants are read as `vocabulary.TERMINAL_STATUSES`, where the
module name says which vocabulary is meant, and hoisting them would make
`TYPES` and `PHASES` bare names at the package's front door.
"""

from __future__ import annotations

__version__ = "0.5.0"

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
