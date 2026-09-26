"""Source pages and their in-bundle reference copies.

    from doc_wiki_okf.sources import plan_ingest

A sibling of `proposals/`, not a member of `ingest/`. `ingest/` is boundary-
locked to the standard library plus `reading/` by
`tests/test_reading_boundaries.py`, and a writer needs `okf_io` and `okf_ext`;
keeping the write here leaves that contract untouched. The dependency runs one
way -- this subpackage imports `ingest.layout` for the page template, and
nothing in `ingest/` may import this one.
"""

from __future__ import annotations

from doc_wiki_okf.sources.drain import (
    DRAIN_CODES,
    DROP_REASONS,
    KEY_CLAIMS_HEADING,
    DrainProblem,
    DrainStatus,
    drain_rule,
    drain_status,
    drain_statuses,
    drop_only_ledger,
    entry_keys_from,
    is_source_member,
)
from doc_wiki_okf.sources.plan import (
    DEFAULT_SOURCE_KIND,
    DEFAULT_SUFFIX,
    REFERENCES_DIRECTORY,
    SOURCE_TYPE,
    IngestPreflight,
    copy_target,
    normalize_origin,
    page_target,
    plan_ingest,
    preflight_ingest,
    seed_source_kinds,
    source_kinds,
)

__all__ = [
    "DEFAULT_SOURCE_KIND",
    "DEFAULT_SUFFIX",
    "DRAIN_CODES",
    "DROP_REASONS",
    "KEY_CLAIMS_HEADING",
    "REFERENCES_DIRECTORY",
    "SOURCE_TYPE",
    "DrainProblem",
    "DrainStatus",
    "IngestPreflight",
    "copy_target",
    "drain_rule",
    "drain_status",
    "drain_statuses",
    "drop_only_ledger",
    "entry_keys_from",
    "is_source_member",
    "normalize_origin",
    "page_target",
    "plan_ingest",
    "preflight_ingest",
    "seed_source_kinds",
    "source_kinds",
]
