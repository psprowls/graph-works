"""Frozen values the search capability returns and consumes.

All frozen and slotted, matching the core and both sibling capabilities.

`SearchIndex` holds the `Bundle` rather than copying out of it. Filtering then
calls `bundle.by_type()` / `by_tag()` / `by_status()` and `derive.trust_tier()`
/ `is_stale()` directly, which is the stated point of this capability -- a
private copy of those derivations is exactly how two consumers end up
disagreeing about the same document.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from okf_io import Bundle, Document, TrustTier

#: A shared empty mapping, the same shape `okf_io.models.Frontmatter.extra`
#: uses. `mappingproxy` became hashable in 3.12, which is why the workspace
#: floor is 3.12: before that the dataclass machinery rejects it as a mutable
#: default.
_EMPTY_WHERE: Mapping[str, str] = MappingProxyType({})

#: Field weights, applied as **token repetition** at index time (spec §4.1). A
#: title word lands in `tf` three times.
#:
#: The accepted approximation: repetition inflates `length` as well as the term
#: counts, so a weighted title makes a document marginally "longer" and
#: slightly penalizes it under BM25's length normalization. Noise on a
#: 2,000-token body; measurable on a three-line stub. Per-field length
#: normalization (BM25F) is the correct fix and costs roughly triple the code
#: for a difference that does not show on a corpus of a few hundred documents.
DEFAULT_WEIGHTS: Mapping[str, int] = MappingProxyType({"title": 3, "description": 2, "tags": 2, "body": 1})


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """One concept, reduced to what scoring and `where` need.

    The token list is deliberately not stored: document frequency reads
    `tf.keys()`, which is the same set, and `length` is the sum of the weighted
    counts rather than a raw token count.
    """

    concept_id: str
    tf: Mapping[str, int]  # term -> weighted frequency
    length: int  # sum of tf.values()
    text: str  # doc.body -- the snippet source
    data: Mapping[str, Any]  # doc.fm_data(dates="iso") -- what `where` reads


@dataclass(frozen=True, slots=True)
class SearchIndex:
    """One pass over a bundle. Share it across queries.

    `documents` is in `bundle.concepts` order, which okf-io sorts by id. That
    is what makes the tie-break in `search()` free (spec §5.2): Python's sort
    is stable, so equal scores come back in concept-id order without anyone
    asking for it.
    """

    bundle: Bundle
    documents: tuple[IndexedDocument, ...]
    weights: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Filters:
    """Every populated field narrows; an unset `Filters` matches everything.

    `tags` is AND because narrowing is what a multi-tag query means; `trust` is
    OR because its values are mutually exclusive per document, so AND would be
    unsatisfiable.

    `status` goes through `Bundle.by_status()`, so it filters on the §5.4
    **effective** status: a document with no `status` answers to `"stable"`.
    `where` does not -- it matches an *authored* value (spec §6.1).
    """

    type: str | None = None  # Bundle.by_type()
    tags: tuple[str, ...] = ()  # Bundle.by_tag(), AND across tags
    status: str | None = None  # Bundle.by_status() -- effective status
    trust: tuple[TrustTier, ...] = ()  # derive.trust_tier(), OR within
    stale: bool | None = None  # derive.is_stale(); None means "don't care"
    where: Mapping[str, str] = _EMPTY_WHERE  # AND across keys


@dataclass(frozen=True, slots=True)
class Hit:
    """One ranked result.

    `document` is carried so a caller can render a title or a path without a
    second lookup through the bundle. `Document` is mutable, which is the same
    arrangement `Bundle` already has with its own concepts -- the frozen
    wrapper is about the result set, not about deep immutability.
    """

    concept_id: str
    score: float
    snippet: str
    document: Document
