"""`tokenize` and `snippet`, ported from the reference with behaviour unchanged."""

from __future__ import annotations

from okf_ext.search import STOPWORDS, TOKEN_RE, snippet, tokenize


def test_tokenize_folds_case_and_drops_stopwords():
    assert tokenize("The Revenue and Margin") == ("revenue", "margin")


def test_tokenize_keeps_hyphens_and_apostrophes_inside_a_token():
    """Spec §4.3, a known limitation: `headline-metric` is one token, so a
    *text* search for `metric` does not reach it. `Filters.tags` is the
    exact-match path, and is what both requesting lanes actually use."""
    assert tokenize("headline-metric it's") == ("headline-metric", "it's")
    assert "metric" not in tokenize("headline-metric")


def test_tokenize_needs_two_characters():
    """`TOKEN_RE` already guarantees two, so the `len(t) > 1` guard is
    redundant. It is kept anyway (§5.3): a redundant guard is not a bug, and
    removing it is not this item's work."""
    assert tokenize("a b 9 ok") == ("ok",)


def test_tokenize_of_empty_text_is_empty():
    assert tokenize("") == ()


def test_tokenize_drops_non_latin_script_entirely():
    """`TOKEN_RE`'s known ASCII-only limitation, first consequence: a document
    written in a non-Latin script indexes to an empty `tf` and is unreachable
    by any text query. Inherited from the reference, not chosen here."""
    assert tokenize("数据质量") == ()
    assert tokenize("данные") == ()
    assert tokenize("数据质量 metrics") == ("metrics",)


def test_tokenize_mangles_accented_latin_symmetrically():
    """Second consequence, and the one that bites a reader rather than a
    corpus: the mangling is applied at index time *and* at query time, so the
    accented spelling matches itself and the unaccented spelling — which is
    what someone actually types — matches nothing."""
    assert tokenize("Müller") == ("ller",)
    assert tokenize("muller") == ("muller",)
    assert tokenize("Müller") != tokenize("muller")


def test_tokenize_leaves_junk_behind_when_it_splits_on_an_accent():
    """Third consequence: the fragments are real tokens a query can match."""
    assert tokenize("naïve") == ("na", "ve")


def test_tokenize_returns_a_tuple():
    """The reference returned a list. Frozen data throughout is the house
    style, and `IndexedDocument` is frozen."""
    assert isinstance(tokenize("revenue"), tuple)


def test_the_pattern_and_stopwords_are_the_ported_values():
    assert TOKEN_RE.pattern == r"[a-zA-Z0-9][a-zA-Z0-9_\-']+"
    assert isinstance(STOPWORDS, frozenset)
    assert len(STOPWORDS) == 59
    assert {"the", "also", "under", "i", "a"} <= STOPWORDS


def test_snippet_centres_on_the_matching_term():
    assert snippet("alpha beta\ngamma revenue delta", ["revenue"], width=20) == "…gamma revenue delta"


def test_snippet_collapses_newlines():
    assert snippet("one\ntwo\nthree", ["two"], width=40) == "one two three"


def test_snippet_skips_terms_that_are_absent():
    """The first *matching* term wins, not the first term."""
    assert snippet("alpha beta gamma", ["zzz", "gamma"], width=40) == "alpha beta gamma"


def test_snippet_falls_back_to_the_head_when_no_term_matches():
    assert snippet("alpha beta gamma delta", ["nope"], width=10) == "alpha beta…"


def test_snippet_of_an_empty_query_is_the_head():
    """What the empty-query browse (§5.5) shows: `snippet`'s own existing
    behaviour when no term is found, not a special case bolted on."""
    assert snippet("alpha beta", (), width=5) == "alpha…"


def test_snippet_shorter_than_the_width_gets_no_ellipsis():
    assert snippet("short", ["nope"], width=100) == "short"
