"""Okapi BM25

Two defensive quirks -- `avgdl = sum(...) / N or 1` and `denom or 1` -- are
**preserved deliberately rather than tidied**, because tidying them is exactly
what `tests/fixtures/bm25_golden.json` exists to catch (spec §5.3). Both are
reachable through this module's public signature, and both have a test.

Statistics are recomputed per call inside `bm25_scores` (spec §5.4).
`bm25_from_stats` supplies the same arithmetic for a persisted index whose
corpus statistics and query-term postings are already stored. Both entry
points share one scoring implementation and preserve the defensive quirks.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from okf_ext.search.model import IndexedDocument


def bm25_from_stats[K](
    *,
    total: int,
    total_length: int,
    df: Mapping[str, int],
    postings: Sequence[tuple[K, Mapping[str, int], int]],
    query: Sequence[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> tuple[tuple[K, float], ...]:
    """BM25 from corpus statistics: the arithmetic `bm25_scores` runs, without the corpus.

    *total* and *total_length* describe the whole corpus. *df* gives the
    document frequency of at least every query term. *postings* holds only the
    documents that carry at least one query term, in the order ties should
    keep: `(key, tf, length)`. A persisted index can therefore score from the
    postings of the query's own terms. Positive scores only, stable descending.
    """
    if total == 0:
        return ()
    avgdl = total_length / total or 1
    idf = {term: math.log(1 + (total - df_t + 0.5) / (df_t + 0.5)) for term, df_t in df.items()}
    scores: list[tuple[K, float]] = []
    for key, tf_by_term, length in postings:
        score = 0.0
        for term in query:
            if term not in tf_by_term:
                continue
            tf = tf_by_term[term]
            denom = tf + k1 * (1 - b + b * length / avgdl)
            score += idf.get(term, 0.0) * (tf * (k1 + 1)) / (denom or 1)
        if score > 0:
            scores.append((key, score))
    scores.sort(key=lambda pair: pair[1], reverse=True)
    return tuple(scores)


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
    terms = frozenset(query)
    df = {term: sum(1 for doc in documents if term in doc.tf) for term in terms}
    postings = [(i, doc.tf, doc.length) for i, doc in enumerate(documents) if not terms.isdisjoint(doc.tf)]
    return bm25_from_stats(
        total=total,
        total_length=sum(doc.length for doc in documents),
        df=df,
        postings=postings,
        query=query,
        k1=k1,
        b=b,
    )
