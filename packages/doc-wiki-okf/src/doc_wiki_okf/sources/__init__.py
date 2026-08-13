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

from doc_wiki_okf.sources.plan import (
    DEFAULT_SUFFIX,
    REFERENCES_DIRECTORY,
    SOURCE_TYPE,
    SOURCE_TYPES,
    copy_target,
    page_target,
    plan_ingest,
)

__all__ = [
    "DEFAULT_SUFFIX",
    "REFERENCES_DIRECTORY",
    "SOURCE_TYPE",
    "SOURCE_TYPES",
    "copy_target",
    "page_target",
    "plan_ingest",
]
