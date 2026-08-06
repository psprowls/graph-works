"""Filtering, ranking and snippets -- the query half of the capability.

**Filtering happens after scoring, never before** (spec §5.1). Scores are
computed over the whole index and the filter then drops non-matching documents
from the ranked list. Filtering first would compute document frequency and
average length over the *subset*, so a term common corpus-wide but rare within
one `type` would score higher there, and the same document would get a
different score depending on what else the caller narrowed by. A document's
relevance to a query is a fact about the corpus. The cost is scoring documents
that are then discarded, which is negligible at this scale.

Every filter delegates to okf-io -- `Bundle.by_type()` / `by_tag()` /
`by_status()` and `derive.trust_tier()` / `is_stale()`. Tag filtering goes
through `bundle.by_tag()`, never `okf_ext.tags`: capabilities may not import
each other, and this is the one place the temptation is real.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from okf_io import is_stale, trust_tier

from okf_ext.search.bm25 import bm25_scores
from okf_ext.search.model import Filters, Hit, SearchIndex
from okf_ext.search.text import snippet, tokenize


def _renders_as(value: Any) -> str | None:  # noqa: ANN401 -- compares an arbitrary projected YAML value
    """How *value* would be **written**, or None when no string can match it.

    Values are compared as they would be authored, not as Python renders them:
    a bool is `"true"` / `"false"`, a number is `str(value)`, and a date is
    already an ISO string because `fm_data(dates="iso")` rendered it at index
    time. Mappings, nested lists and `None` never match -- there is no string a
    caller could write that means them (spec §6.1).

    `bool` is checked before `int` because it is one.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _matches_where(data: Mapping[str, Any], where: Mapping[str, str]) -> bool:
    """AND across keys; membership for a list of scalars; case-sensitive."""
    for key, wanted in where.items():
        if key not in data:
            return False
        value = data[key]
        if isinstance(value, list):
            if not any(_renders_as(item) == wanted for item in value):
                return False
        elif _renders_as(value) != wanted:
            return False
    return True


def _selected(index: SearchIndex, filters: Filters, today: date | None) -> frozenset[str]:
    """The concept ids surviving *filters*. Every populated field narrows."""
    bundle = index.bundle
    selected = frozenset(bundle.concepts)
    if filters.type is not None:
        selected &= frozenset(bundle.by_type(filters.type))
    for tag in filters.tags:
        selected &= frozenset(bundle.by_tag(tag))
    if filters.status is not None:
        selected &= frozenset(bundle.by_status(filters.status))
    if filters.trust:
        wanted = frozenset(filters.trust)
        selected = frozenset(cid for cid in selected if trust_tier(bundle.concepts[cid].fm) in wanted)
    stale = filters.stale
    if stale is not None:
        if today is None:
            raise ValueError("filters.stale needs a `today=` date; search never reads the clock")
        selected = frozenset(cid for cid in selected if is_stale(bundle.concepts[cid].fm, today=today) is stale)
    if filters.where:
        by_id = {doc.concept_id: doc for doc in index.documents}
        selected = frozenset(cid for cid in selected if _matches_where(by_id[cid].data, filters.where))
    return selected


def search(
    index: SearchIndex,
    text: str,
    *,
    filters: Filters | None = None,
    today: date | None = None,
    limit: int | None = None,
) -> tuple[Hit, ...]:
    """Rank *index* against *text*, narrowed by *filters*.

    A query with no usable terms -- empty, or entirely stopwords -- returns the
    filtered set at score `0.0` in concept-id order rather than nothing (§5.5).
    Note the asymmetry, which is correct: a query *with* terms returns only
    documents matching those terms, because `bm25_scores` drops zero scores.
    Filters narrow a text search; they do not widen it.

    **Known limitation: "no usable terms" is wider than it looks, and the
    caller cannot see it happen.** A third case reaches the browse path
    alongside empty and all-stopwords: text `TOKEN_RE` cannot represent at all,
    which is any query in a non-Latin script (see `text.TOKEN_RE`). All three
    return the whole filtered set at `0.0`, so a query that could not be
    tokenized is indistinguishable from a deliberate browse -- while an ASCII
    query that genuinely matches nothing correctly returns `()`:

        search(index, "数据")       # every concept, all scored 0.0
        search(index, "zzznomatch")  # ()

    Failure shaped like success is the worst of the three outcomes, and it is
    the one a caller is least likely to notice. A caller that wants to tell
    them apart calls `tokenize(text)` first and treats an empty result as its
    own case; `search()` deliberately does not do this on their behalf,
    because "browse the filtered set" is a real request that the empty query
    is the natural way to spell (§5.5). Giving the two cases separate names is
    an API change, and belongs with `parse_query()` if that ever lands.

    *limit* defaults to `None`, meaning every hit. A library that silently
    truncates is a library you debug twice: the caller who wanted ten writes
    `limit=10`, and the caller who wanted everything does not have to discover
    a default that ate their tail. `limit=0` returns nothing; a negative
    *limit* raises, because Python's slice semantics would otherwise read
    `limit=-1` as "all but the last hit".

    *today* is required only when *filters* has an opinion about staleness
    (§6.2), and there is no default: no code path here can reach a clock.
    """
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be None or >= 0, got {limit}")
    active = Filters() if filters is None else filters
    selected = _selected(index, active, today)
    query = tokenize(text)
    if query:
        ranked = [
            (index.documents[i], score)
            for i, score in bm25_scores(index.documents, query)
            if index.documents[i].concept_id in selected
        ]
    else:
        ranked = [(doc, 0.0) for doc in index.documents if doc.concept_id in selected]
    hits = tuple(
        Hit(
            concept_id=doc.concept_id,
            score=score,
            snippet=snippet(doc.text, query),
            document=index.bundle.concepts[doc.concept_id],
        )
        for doc, score in ranked
    )
    return hits if limit is None else hits[:limit]
