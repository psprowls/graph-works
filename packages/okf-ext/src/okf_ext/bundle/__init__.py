"""Bundle setup -- the files every OKF bundle has, and one package's own files
added to it without clobbering another's.

    from datetime import date
    from okf_ext import bundle

    scaffold = bundle.plan_scaffold(root, today=date(2026, 8, 9))
    bundle.apply(scaffold)                       # index.md, log.md, _tags.yaml

    install = bundle.plan_install(root, {"_schema/Topic.schema.json": text})
    result = bundle.apply(install)
    if not result.ok:
        for failure in result.failed:
            print(failure.path, failure.kind, failure.error)

**The narrowed refusal.** A fresh-only installer refuses *"the directory is
non-empty"*. That is right for exactly as long as one package writes into a
bundle. Three are designed to share one, so the refusal here is *"a file I own
exists with content I did not write"* -- narrower, and it still protects
everything the wider one did: nothing this capability writes can clobber
anything, and a refusal names the file and writes nothing over it.

**Three regimes, keyed by who owns the file** -- see `plan.py` for the table.
The short version: the scaffold three are checked for *validity* (they change
by design, so byte-comparing them would report a difference on every real
bundle), owned templates for *byte-identity*, and human configuration is only
ever *seeded*.

**Declaration files are routed, not assumed.** `_schema/*`, `_sections/*` and
`_tags.yaml` resolve against `declarations_dir` and everything else against
the bundle root, so a caller who keeps one declaration set for several bundles
gets the same skip/refuse semantics with no second code path. Defaulting
`declarations_dir` to the root is today's behaviour, unchanged.

**Naming convention: no shared `_base`.** `load_schemas` keys the dispatch
table on type name and holds `_`-prefixed files as `$ref` targets only, so
several bases coexist in one `_schema/` and each stays reachable by filename.
That makes one shared `_base.schema.json` an unforced collision between
packages that have no business coupling: each lane names its own
`_base-<lane>.schema.json` (`_base-code-wiki.schema.json`,
`_base-diataxis.schema.json`) and `$ref`s it by that filename. This capability
does not enforce the convention -- there is nothing to enforce it against
until a lane authors a base -- it records it so the next lane is born
following it.

**No rule ships.** No `TOPIC`, no `CODES`, no `Finding` -- a primitive, as
`tables`, `generators` and `proposals` are. Adopting the capability adds no
finding to any bundle.

**No new dependency.** The fifth such capability: `okf_io.parse`,
`okf_io.append_log_entry` and `ruamel.yaml` are all already declared.

**This module imports no sibling capability**, and never the top-level
`okf_ext` package.
"""

from __future__ import annotations

from okf_ext.bundle.apply import apply
from okf_ext.bundle.model import (
    DECLARATION_MEMBERS,
    DECLARATION_PREFIXES,
    EMPTY_TAGS_YAML,
    SCAFFOLD_MEMBERS,
    InstallPlan,
    Plan,
    PlannedFile,
    ScaffoldPlan,
)
from okf_ext.bundle.plan import SCAFFOLD_LOG_ENTRY, plan_install, plan_scaffold
from okf_ext.writing import ApplyResult, FailureKind, Skipped, SkipReason, WriteFailure

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this, and
#: `test_ext_boundaries.py` asserts the same invariant independently.
__all__ = [
    "DECLARATION_MEMBERS",
    "DECLARATION_PREFIXES",
    "EMPTY_TAGS_YAML",
    "SCAFFOLD_LOG_ENTRY",
    "SCAFFOLD_MEMBERS",
    "ApplyResult",
    "FailureKind",
    "InstallPlan",
    "Plan",
    "PlannedFile",
    "ScaffoldPlan",
    "SkipReason",
    "Skipped",
    "WriteFailure",
    "apply",
    "plan_install",
    "plan_scaffold",
]
