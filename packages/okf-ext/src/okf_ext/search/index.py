"""The one reader of a `Bundle`. Builds a `SearchIndex` in a single pass.

`build_index` walks `bundle.concepts` and nothing else. Reserved files
(`index.md`, `log.md`), non-markdown `assets` and `ignore=`d members are
already excluded by okf-io's own walk, so nothing here re-derives what is or is
not a concept (spec §4). That matches the reference implementation's loader,
which skipped `index.md`/`log.md` and dotted paths by hand -- the exclusions
survive, the hand-rolled walk does not. The loader itself does not come across:
okf-io already walked.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType

from okf_io import Bundle

from okf_ext.search.model import DEFAULT_WEIGHTS, IndexedDocument, SearchIndex
from okf_ext.search.text import tokenize


def _resolve_weights(weights: Mapping[str, int]) -> Mapping[str, int]:
    """Merge *weights* over `DEFAULT_WEIGHTS`, refusing typos and negatives.

    An unrecognized field name raises rather than silently doing nothing: a
    typo'd `{"titel": 5}` that changes no ranking is the failure this prevents.
    A weight of `0` is legal and excludes that field, which is the supported
    way to search bodies only.

    Caller configuration is the one class of thing this workspace raises on --
    content never raises, configuration always does, as `load_vocabulary` and
    `load_schemas` already do.
    """
    unknown = sorted(set(weights) - set(DEFAULT_WEIGHTS))
    if unknown:
        raise ValueError(f"unknown weight field(s) {unknown}; the fields are {sorted(DEFAULT_WEIGHTS)}")
    negative = sorted(name for name, value in weights.items() if value < 0)
    if negative:
        raise ValueError(f"negative weight(s) for {negative}; a weight of 0 excludes a field instead")
    return MappingProxyType({**DEFAULT_WEIGHTS, **weights})


def build_index(bundle: Bundle, *, weights: Mapping[str, int] = DEFAULT_WEIGHTS) -> SearchIndex:
    """Index every concept in *bundle*.

    Field weighting is token repetition (§4.1): a title word lands in `tf`
    three times. The frontmatter fields come from the typed view, never from
    re-parsing, and tags are tokenized as text *in addition to* being exactly
    filterable through `Filters.tags`.

    A document whose frontmatter failed to parse is still indexed (§4.2). It
    carries an empty `Frontmatter` view, so it matches no frontmatter filter
    and ranks on body alone.
    """
    resolved = _resolve_weights(weights)
    documents: list[IndexedDocument] = []
    for concept_id, doc in bundle.concepts.items():
        fm = doc.fm
        fields = {
            "title": fm.title or "",
            "description": fm.description or "",
            "tags": " ".join(fm.tags),
            "body": doc.body,
        }
        counts: Counter[str] = Counter()
        for name, text in fields.items():
            weight = resolved[name]
            # A zero weight must skip the field, not add its terms at count
            # zero: a term present in `tf` still counts toward document
            # frequency, which would move idf for every other document.
            if weight:
                for token in tokenize(text):
                    counts[token] += weight
        documents.append(
            IndexedDocument(
                concept_id=concept_id,
                tf=MappingProxyType(dict(counts)),
                length=sum(counts.values()),
                text=doc.body,
                data=MappingProxyType(doc.fm_data(dates="iso")),
            )
        )
    return SearchIndex(bundle=bundle, documents=tuple(documents), weights=resolved)
