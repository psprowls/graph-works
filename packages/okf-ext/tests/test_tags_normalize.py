from __future__ import annotations

import pytest
from ext_helpers import tagged_bundle
from okf_ext import NormalizationPolicy
from okf_ext.tags.normalize import canonical


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Data Quality", "data-quality"),
        ("data_quality", "data-quality"),
        ("data-quality", "data-quality"),
        ("  finance  ", "finance"),
        ("head line   metric", "head-line-metric"),
        ("mixed_ separator", "mixed-separator"),
        ("ga4", "ga4"),
        ("", ""),
    ],
)
def test_canonical_under_the_default_policy(raw, expected):
    assert canonical(raw) == expected


def test_case_preserve_keeps_case_but_still_collapses_separators():
    policy = NormalizationPolicy(case="preserve")
    assert canonical("Data Quality", policy) == "Data-Quality"


def test_strip_false_keeps_the_padding_and_turns_it_into_separators():
    policy = NormalizationPolicy(strip=False)
    assert canonical(" finance ", policy) == "-finance-"


def test_the_separator_is_configurable():
    policy = NormalizationPolicy(separator="_")
    assert canonical("Data Quality", policy) == "data_quality"


def test_nfkc_folds_compatibility_characters_and_nfc_does_not():
    """A fullwidth 'k' is a different codepoint under NFC and the same one
    under NFKC. This is the whole difference between the two forms."""
    fullwidth_k_pi = "ｋpi"  # noqa: RUF001 # U+FF4B FULLWIDTH LATIN SMALL LETTER K + pi
    assert canonical(fullwidth_k_pi, NormalizationPolicy(unicode_form="NFKC")) == "kpi"
    assert canonical(fullwidth_k_pi, NormalizationPolicy(unicode_form="NFC")) != "kpi"


def test_unicode_form_none_skips_normalization_entirely():
    decomposed = "café"  # e + combining acute (U+0301)
    assert canonical(decomposed, NormalizationPolicy(unicode_form="none")) == decomposed
    assert canonical(decomposed, NormalizationPolicy(unicode_form="NFC")) == "café"


def test_canonical_is_idempotent_across_the_corpus():
    """A non-idempotent canonicaliser makes `plan_normalize` a plan that needs
    running twice, which is not a plan."""
    bundle = tagged_bundle()
    tags = {tag for doc in bundle.concepts.values() for tag in doc.fm.tags}
    assert tags, "corpus carries no tags"
    for tag in tags:
        assert canonical(canonical(tag)) == canonical(tag)


def test_backslash_separator_treated_literally():
    """Regression: separator is treated as a literal string, not a regex
    replacement. Backslashes must not cause re.error or group references."""
    policy_backslash = NormalizationPolicy(separator="\\")
    assert canonical("a b", policy_backslash) == "a\\b"
    assert canonical("a  b", policy_backslash) == "a\\b"
    assert canonical("a_b", policy_backslash) == "a\\b"


def test_underscore_padding_stripped_like_whitespace():
    """Regression: underscore padding at the start/end is stripped along with
    whitespace, not left to become dangling separators."""
    assert canonical("___finance") == "finance"
    assert canonical("finance___") == "finance"
    assert canonical("__legacy__") == "legacy"
    assert canonical("___") == ""


def test_whitespace_only_and_underscore_only_agree():
    """Regression: padding-only tags produce the same canonical form
    regardless of whether the padding is whitespace or underscores."""
    assert canonical("   ") == canonical("___")
    assert canonical("   ") == ""
    assert canonical("___") == ""


def test_strip_false_keeps_padding_and_turns_it_into_separators():
    """When strip=False, padding is explicitly kept and becomes separators.
    Verify this behavior is pinned."""
    policy = NormalizationPolicy(strip=False)
    assert canonical(" finance ", policy) == "-finance-"
    assert canonical("_finance_", policy) == "-finance-"
    assert canonical("  multiple  spaces  ", policy) == "-multiple-spaces-"


def test_idempotency_with_space_separator():
    """Regression: idempotency must hold for non-default separators.
    Space separator was a problem case for dangling separators."""
    policy = NormalizationPolicy(separator=" ")
    # Applying the same policy twice should equal applying it once
    tag1 = canonical(canonical("_finance_", policy), policy)
    assert tag1 == canonical("_finance_", policy)
    tag2 = canonical(canonical("Data Quality", policy), policy)
    assert tag2 == canonical("Data Quality", policy)


def test_idempotency_with_underscore_separator():
    """Regression: idempotency with underscore separator."""
    policy = NormalizationPolicy(separator="_")
    # Applying the same policy twice should equal applying it once
    tag1 = canonical(canonical("Data Quality", policy), policy)
    assert tag1 == canonical("Data Quality", policy)
    tag2 = canonical(canonical("  spaces  ", policy), policy)
    assert tag2 == canonical("  spaces  ", policy)
