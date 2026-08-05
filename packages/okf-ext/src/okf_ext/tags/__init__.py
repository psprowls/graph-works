"""Tag management: inventory, normalization, controlled vocabulary, rename.

    from okf_ext import tags
    plan = tags.plan_rename(bundle, "kpi", "metric")
    result = tags.apply(bundle, plan)

Free functions over frozen data, matching okf-io exactly. A facade object
(`ExtBundle(bundle).tags.rename(...)`) was rejected: it breaks the core's
idiom, and a facade reaching across capabilities would silently couple them —
precisely what the internal boundaries exist to prevent, and what would make a
future graduation painful.

The four pieces are one capability because they are close to useless apart. A
vocabulary's `replaced_by` is simultaneously a validation fact and a rename
instruction: inventory finds the mess, normalization and vocabulary define what
is correct, rename fixes it.

**This module imports the shared `okf_ext.context` layer and nothing else from
its own package.** It never imports `okf_ext` itself — that would invert the
re-export direction and make every capability load every other.
"""

from __future__ import annotations

from okf_ext.context import DEFAULT_NORMALIZATION, ExtContext, NormalizationPolicy
from okf_ext.tags.inventory import DEFAULT_CUTOFF, clusters, inventory, scan
from okf_ext.tags.model import (
    ApplyResult,
    FailureKind,
    RenamePlan,
    Skipped,
    SkipReason,
    TagCluster,
    TagEdit,
    TagInventory,
    Vocabulary,
    WriteFailure,
)
from okf_ext.tags.normalize import canonical
from okf_ext.tags.rename import (
    apply,
    plan_from_vocabulary,
    plan_merge,
    plan_normalize,
    plan_rename,
)
from okf_ext.tags.vocabulary import (
    CODES,
    SUPPORTED_VERSION,
    TOPIC,
    VOCABULARY_FILENAME,
    VocabularyError,
    load_vocabulary,
    vocabulary_rule,
)

#: `_tags.yaml` is a **documented convention, not magic** — nothing here
#: discovers it. A caller who keeps a vocabulary inside its own bundle splices
#: this into `okf_io.load_bundle(root, ignore=...)`; no okf-ext function ever
#: changes behaviour because a file appeared. Two patterns because `fnmatch`
#: needs a literal `/` to match a nested path and the bare name to match root.
DEFAULT_IGNORE = ("_tags.yaml", "*/_tags.yaml")

#: Ordered UPPER_SNAKE_CASE constants, then CapWords classes, then lowercase
#: functions, each group alphabetical — `ruff`'s `RUF022` enforces exactly
#: this grouping via `just lint`, and `test_ext_boundaries.py` checks the
#: same invariant so a stale ordering fails the test suite too, not only an
#: opt-in lint pass.
__all__ = [
    "CODES",
    "DEFAULT_CUTOFF",
    "DEFAULT_IGNORE",
    "DEFAULT_NORMALIZATION",
    "SUPPORTED_VERSION",
    "TOPIC",
    "VOCABULARY_FILENAME",
    "ApplyResult",
    "ExtContext",
    "FailureKind",
    "NormalizationPolicy",
    "RenamePlan",
    "SkipReason",
    "Skipped",
    "TagCluster",
    "TagEdit",
    "TagInventory",
    "Vocabulary",
    "VocabularyError",
    "WriteFailure",
    "apply",
    "canonical",
    "clusters",
    "inventory",
    "load_vocabulary",
    "plan_from_vocabulary",
    "plan_merge",
    "plan_normalize",
    "plan_rename",
    "scan",
    "vocabulary_rule",
]
