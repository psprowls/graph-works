"""BM25 ranking and derived-signal filtering over any OKF v0.2 bundle.

    from okf_ext import search
    index = search.build_index(bundle)
    hits = search.search(index, "retention cohort", filters=search.Filters(type="Metric"))

Two steps, mirroring okf-io's own `load_bundle` -> `build_link_graph` ->
`validate` shape: one pass produces a derived structure and every consumer
shares it. Tokenization is the dominant per-query cost, so ten queries over one
index tokenize the corpus once rather than ten times. It also gives a persisted
index somewhere to live later without an API change.

**No `TOPIC`, no `CODES`.** This is the first capability that reports nothing.
`tags` and `schemas` both exist to emit `Finding`s through `extra_rules=`;
`search` answers questions instead of filing complaints, so it claims no topic
prefix and needs no entry in okf-io's code namespace.

**Shadowing note:** `okf_ext.search.search` is the function, not a submodule --
the same arrangement `okf_io.validate` has, and for the same reason: the
function is what callers want at the front door.

**This module imports no sibling capability** -- tag filtering goes through
`Bundle.by_tag()` in okf-io, never `okf_ext.tags` -- and never the top-level
`okf_ext` package. It does not import `okf_ext.context` either: `ExtContext`
has nothing to offer it, and a search-shaped field in the shared layer is
precisely the inversion `context.py`'s docstring warns about. Importing the
shared layer is permitted, not required, so the layers contract is satisfied
either way.
"""

from __future__ import annotations

from okf_ext.search.bm25 import bm25_scores
from okf_ext.search.index import build_index
from okf_ext.search.model import (
    DEFAULT_WEIGHTS,
    Filters,
    Hit,
    IndexedDocument,
    SearchIndex,
)
from okf_ext.search.query import search
from okf_ext.search.text import STOPWORDS, TOKEN_RE, snippet, tokenize

#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase functions,
#: each group alphabetical -- `RUF022` enforces it and `test_ext_boundaries.py`
#: asserts the same invariant independently.
__all__ = [
    "DEFAULT_WEIGHTS",
    "STOPWORDS",
    "TOKEN_RE",
    "Filters",
    "Hit",
    "IndexedDocument",
    "SearchIndex",
    "bm25_scores",
    "build_index",
    "search",
    "snippet",
    "tokenize",
]
