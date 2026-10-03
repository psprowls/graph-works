"""`term_postings` and `bm25_from_stats`: the building blocks a persisted index scores through."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from types import MappingProxyType

import pytest
from ext_helpers import GA4, acme_retail_bundle
from okf_ext.search import (
    DEFAULT_WEIGHTS,
    SCORING_VERSION,
    STOPWORDS,
    TOKEN_RE,
    IndexedDocument,
    bm25_from_stats,
    bm25_scores,
    build_index,
    term_postings,
)
from okf_io import Document, load_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES / "bm25_golden.json"
VOCAB = ["alpha", "beta", "gamma", "delta", "token", "refresh", "storage", "retention", "zeta"]

#: Bump SCORING_VERSION and refresh this digest from the material in
#: test_scoring_version_tracks_the_tokenizer_stopwords_and_weights whenever
#: the tokenizer, stopwords or default weights change.
SCORING_FINGERPRINT = "94ff60806cdf0dabc610771e5ca5b19b482a40c8a1399eb410e08c18f510ccfa"


def _stats(documents):
    """The statistics a postings store would hold, derived from *documents*."""
    query_terms = {term for doc in documents for term in doc.tf}
    df = {term: sum(1 for doc in documents if term in doc.tf) for term in query_terms}
    return len(documents), sum(doc.length for doc in documents), df


def _via_stats(documents, query, **kw):
    total, total_length, df = _stats(documents)
    terms = set(query)
    postings = [(i, doc.tf, doc.length) for i, doc in enumerate(documents) if not terms.isdisjoint(doc.tf)]
    return bm25_from_stats(total=total, total_length=total_length, df=df, postings=postings, query=query, **kw)


def _doc(tokens, cid="c", length=None):
    counts = Counter(tokens)
    return IndexedDocument(
        concept_id=cid,
        tf=MappingProxyType(dict(counts)),
        length=len(tokens) if length is None else length,
        text=" ".join(tokens),
        data=MappingProxyType({}),
    )


def test_from_stats_reproduces_the_golden_exactly():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    documents = [_doc(tokens, f"d{i}") for i, tokens in enumerate(golden["documents"])]
    scored = _via_stats(documents, golden["query"], k1=golden["k1"], b=golden["b"])
    assert [list(pair) for pair in scored] == golden["scores"]


@pytest.mark.parametrize("seed", range(25))
def test_from_stats_equals_bm25_scores_on_generated_corpora(seed):
    rng = random.Random(seed)
    documents = [
        _doc([rng.choice(VOCAB) for _ in range(rng.randint(0, 12))], f"c{i}") for i in range(rng.randint(1, 30))
    ]
    query = [rng.choice(VOCAB) for _ in range(rng.randint(1, 4))]  # repeats included
    assert _via_stats(documents, query) == bm25_scores(documents, query)


def test_ties_keep_input_order_and_shared_ids_stay_distinct():
    documents = [_doc(["alpha"], "same"), _doc(["alpha"], "same"), _doc(["beta"], "other")]
    assert [i for i, _ in _via_stats(documents, ["alpha"])] == [0, 1]
    assert [i for i, _ in bm25_scores(documents, ["alpha"])] == [0, 1]


def test_zero_length_and_empty_inputs_keep_their_quirks():
    assert bm25_from_stats(total=0, total_length=0, df={}, postings=[], query=["x"]) == ()
    documents = [_doc(["alpha"], "a", length=0), _doc([], "b", length=0)]
    assert _via_stats(documents, ["alpha"]) == bm25_scores(documents, ["alpha"])


def test_term_postings_is_what_build_index_stores():
    for bundle in (acme_retail_bundle(), load_bundle(GA4)):
        index = build_index(bundle)
        for doc in index.documents:
            tf, length = term_postings(bundle.concepts[doc.concept_id])
            assert isinstance(tf, MappingProxyType)
            assert length == sum(tf.values())
            assert (dict(tf), length) == (dict(doc.tf), doc.length)
        zero = build_index(bundle, weights={"title": 0})
        for doc in zero.documents:
            tf, length = term_postings(bundle.concepts[doc.concept_id], weights={"title": 0})
            assert isinstance(tf, MappingProxyType)
            assert length == sum(tf.values())
            assert (dict(tf), length) == (dict(doc.tf), doc.length)


def test_term_postings_validates_weights():
    doc = Document.parse("---\ntitle: T\n---\nbody\n")
    with pytest.raises(ValueError, match="unknown weight"):
        term_postings(doc, weights={"titel": 1})


def test_scoring_version_tracks_the_tokenizer_stopwords_and_weights():
    material = json.dumps(
        {
            "version": SCORING_VERSION,
            "token_re": TOKEN_RE.pattern,
            "stopwords": sorted(STOPWORDS),
            "weights": sorted(DEFAULT_WEIGHTS.items()),
        },
        sort_keys=True,
    )
    assert hashlib.sha256(material.encode()).hexdigest() == SCORING_FINGERPRINT


def test_from_stats_preserves_arbitrary_posting_keys():
    key = ("page", 17)
    scored = bm25_from_stats(
        total=1, total_length=1, df={"alpha": 1}, postings=[(key, {"alpha": 1}, 1)], query=["alpha"]
    )
    assert scored[0][0] is key
    assert scored[0][1] > 0
