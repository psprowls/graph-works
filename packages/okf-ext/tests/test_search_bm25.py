"""The ported BM25, frozen against the reference's captured output."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import MappingProxyType

from okf_ext.search import IndexedDocument, bm25_scores

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "bm25_golden.json"


def indexed(tokens, concept_id="c"):
    """An `IndexedDocument` whose `tf` and `length` come from a token list —
    the shape the reference's document dicts had."""
    counts = Counter(tokens)
    return IndexedDocument(
        concept_id=concept_id,
        tf=MappingProxyType(dict(counts)),
        length=len(tokens),
        text=" ".join(tokens),
        data=MappingProxyType({}),
    )


def test_the_ported_math_reproduces_the_reference_scores():
    """Spec §10.1, the parity golden.

    End-to-end ranking parity with the old tool is impossible by construction
    — `load_docs` is deleted, so the text being scored is okf-io's parsed body
    plus selected fields rather than the whole raw file including frontmatter
    YAML, and field weighting changes term frequencies. What is testable, and
    what actually matters, is that the *math* survived the port. Exact float
    equality: the operations and their order are unchanged, so the results are
    bit-identical, and a tolerance here would hide the tidy-up this guards.
    """
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    documents = [indexed(tokens, f"d{i}") for i, tokens in enumerate(golden["documents"])]
    scored = bm25_scores(documents, golden["query"], k1=golden["k1"], b=golden["b"])
    assert [list(pair) for pair in scored] == golden["scores"]


def test_an_empty_corpus_scores_nothing():
    assert bm25_scores([], ["revenue"]) == ()


def test_a_single_document_scores():
    assert bm25_scores([indexed(["revenue"])], ["revenue"]) == ((0, 0.28768207245178085),)


def test_a_term_absent_from_every_document_scores_nothing():
    assert bm25_scores([indexed(["alpha"]), indexed(["beta"])], ["zzz"]) == ()


def test_the_avgdl_guard_survives_a_zero_length_corpus():
    """`avgdl = sum(...) / N or 1`. Unreachable through `build_index`, where
    `length` is derived from the tokens — but reachable here, because
    `IndexedDocument.length` is a field. That is why the guard is preserved
    rather than argued away as dead code; deleting it makes this ZeroDivisionError.
    """
    doc = IndexedDocument(
        concept_id="c",
        tf=MappingProxyType({"revenue": 1}),
        length=0,
        text="",
        data=MappingProxyType({}),
    )
    assert bm25_scores([doc], ["revenue"]) == ((0, 0.5230583135486925),)


def test_the_denom_guard_survives_a_zero_denominator():
    """`denom or 1`. Zero needs `k1` strictly between -1 and 0 and a matching
    document exactly twice the average length: k1=-0.5, b=1, dl=4, avgdl=2
    gives `1 + (-0.5)(0 + 1 * 2) == 0`. Synthetic, and preserved verbatim from
    the reference rather than argued away — deleting it makes this
    ZeroDivisionError too.
    """
    long_doc = IndexedDocument("a", MappingProxyType({"revenue": 1}), 4, "", MappingProxyType({}))
    empty_doc = IndexedDocument("b", MappingProxyType({}), 0, "", MappingProxyType({}))
    assert bm25_scores([long_doc, empty_doc], ["revenue"], k1=-0.5, b=1) == ((0, 0.34657359027997264),)


def test_scores_come_back_descending_with_ties_in_input_order():
    """Spec §5.2. The tie-break is free: `list.sort` is stable, so equal scores
    keep the order they arrived in — and `build_index` builds documents in
    `bundle.concepts` order, which okf-io sorts by id."""
    docs = [indexed(["revenue"], "a"), indexed(["revenue"], "b"), indexed(["revenue", "revenue"], "c")]
    scored = bm25_scores(docs, ["revenue"])
    assert [i for i, _ in scored] == [2, 0, 1]
    assert scored[1][1] == scored[2][1]


def test_document_frequency_reads_tf_not_a_token_list():
    """The one mechanical adaptation to the arithmetic's inputs (§5.3): the
    reference used `set(d["tokens"])`, this uses `tf.keys()`. Same set — which
    is why the token list need not be stored at all. A document repeating a
    term must still count once toward df, so its idf must match a corpus where
    it appears once."""
    repeated = bm25_scores([indexed(["revenue", "revenue"]), indexed(["other"])], ["revenue"])
    single = bm25_scores([indexed(["revenue"]), indexed(["other"])], ["revenue"])
    assert len(repeated) == len(single) == 1
