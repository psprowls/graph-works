"""Okapi BM25

Two defensive quirks -- `avgdl = sum(...) / N or 1` and `denom or 1` -- are
**preserved deliberately rather than tidied**, because tidying them is exactly
what `tests/fixtures/bm25_golden.json` exists to catch (spec §5.3). Both are
reachable through this module's public signature, and both have a test.

Statistics are recomputed per call, on purpose (spec §5.4). Hoisting document
frequency, idf and average length into `SearchIndex` is the obvious
optimization and is declined here: it would leave this a pure function that
nothing calls, which is to say a golden test guarding dead code. At this corpus
size the recomputation is milliseconds, and precomputation later is an internal
change behind an unchanged public API.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

from okf_ext.search.model import IndexedDocument


def bm25_scores(
    documents: Sequence[IndexedDocument],
    query: Sequence[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> tuple[tuple[int, float], ...]:
    """Score *documents* against *query*. Positive scores only, descending.

    Returns `(index, score)` pairs indexing into *documents*. A document
    matching no query term scores zero and is dropped, which is why a text
    query returns only documents that match its terms however wide the filters
    are: filters narrow a text search, they do not widen it (spec §5.5).

    Ties keep input order — `list.sort` is stable — which is the whole of the
    tie-break in §5.2, given `build_index` emits documents in concept-id order.
    """
    total = len(documents)
    if total == 0:
        return ()
    avgdl = sum(doc.length for doc in documents) / total or 1
    df: dict[str, int] = defaultdict(int)
    for doc in documents:
        for term in doc.tf:
            df[term] += 1
    idf = {term: math.log(1 + (total - df_t + 0.5) / (df_t + 0.5)) for term, df_t in df.items()}
    scores: list[tuple[int, float]] = []
    for i, doc in enumerate(documents):
        score = 0.0
        for term in query:
            if term not in doc.tf:
                continue
            tf = doc.tf[term]
            denom = tf + k1 * (1 - b + b * doc.length / avgdl)
            score += idf.get(term, 0.0) * (tf * (k1 + 1)) / (denom or 1)
        if score > 0:
            scores.append((i, score))
    scores.sort(key=lambda pair: pair[1], reverse=True)
    return tuple(scores)
