"""`search`: filtering, ranking and the `where` escape hatch."""

from __future__ import annotations

from datetime import date

import pytest
from ext_helpers import write_bundle
from okf_ext.search import Filters, build_index, search

TODAY = date(2026, 8, 6)

#: `alpha` is the fully-populated document: every scalar shape §6.1's table
#: names is a frontmatter key on it, so each row of that table is one query.
ALPHA = """---
type: Metric
title: Alpha Revenue
description: The alpha revenue metric.
tags: [finance, headline-metric]
status: stable
stale_after: 2026-12-31
verified:
  - { by: human:jsmith@acme, at: 2026-07-01T09:00:00Z }
runtime: bigquery
retries: 3
enabled: true
ratio: 1.5
aliases: [primary, main]
window: { from: 2026-01-01 }
owner:
---

Alpha body mentions revenue and cohort.
"""

#: No `status` (so §5.4 defaults it to `stable`) and a non-human verifier (so
#: `trust_tier` is machine-confirmed). Both distinctions matter below.
BETA = """---
type: Metric
title: Beta Margin
description: The beta margin metric.
tags: [finance]
verified:
  - { by: pipeline/nightly, at: 2026-07-01T09:00:00Z }
---

Beta body mentions margin and cohort.
"""

#: Unverified, drafted, and stale as of TODAY.
GAMMA = """---
type: Policy
title: Gamma Policy
description: A policy about retention.
tags: [governance, headline-metric]
status: draft
stale_after: 2026-01-01
---

Gamma body mentions retention and cohort.
"""


@pytest.fixture
def index(tmp_path):
    return build_index(write_bundle(tmp_path, {"alpha.md": ALPHA, "beta.md": BETA, "gamma.md": GAMMA}))


def ids(hits):
    return [hit.concept_id for hit in hits]


# --- ranking -----------------------------------------------------------------


def test_a_text_query_returns_only_matching_documents(index):
    assert set(ids(search(index, "cohort"))) == {"alpha", "beta", "gamma"}
    assert search(index, "zzz") == ()


def test_filtering_does_not_move_a_score(index):
    """Spec §5.1. Scores are computed over the whole index and the filter drops
    documents from the ranked list afterwards. Filtering first would compute
    document frequency and average length over the *subset*, so a term common
    corpus-wide but rare within one type would score higher there — the same
    document getting a different score depending on what else the caller
    narrowed by."""
    unfiltered = {hit.concept_id: hit.score for hit in search(index, "cohort")}
    narrowed = {hit.concept_id: hit.score for hit in search(index, "cohort", filters=Filters(type="Metric"))}
    assert set(narrowed) == {"alpha", "beta"}
    assert all(unfiltered[cid] == score for cid, score in narrowed.items())


def test_filters_narrow_a_text_search_and_never_widen_it(index):
    """The asymmetry in §5.5, and it is correct: `bm25_scores` drops zero
    scores, so a query with terms returns only documents matching them."""
    assert search(index, "zzz", filters=Filters(type="Metric")) == ()


def test_an_empty_query_returns_the_filtered_set_in_concept_id_order(index):
    """Spec §5.5 and §5.2. "Everything of type Metric tagged finance" is a
    legitimate request — half of what both requesting lanes want — and
    returning nothing for it would be a worse answer than the obvious one."""
    hits = search(index, "")
    assert ids(hits) == ["alpha", "beta", "gamma"]
    assert [hit.score for hit in hits] == [0.0, 0.0, 0.0]


def test_a_query_of_only_stopwords_browses_too(index):
    assert ids(search(index, "the and of")) == ["alpha", "beta", "gamma"]


def test_an_untokenizable_query_browses_and_is_indistinguishable_from_one_that_meant_to(index):
    """The documented limitation in `search`'s docstring, pinned.

    Three inputs reach the browse path: empty, all-stopwords, and text
    `TOKEN_RE` cannot represent. The first two are the documented §5.5
    behaviour; the third arrives by accident and cannot be told apart from
    them afterwards. The asymmetry is the sharp edge — an ASCII query that
    genuinely matches nothing returns `()`, so the one input that comes back
    with *everything* is the one that was never understood.
    """
    browse = ids(search(index, ""))
    assert ids(search(index, "数据质量")) == browse
    assert ids(search(index, "the and of")) == browse
    assert all(hit.score == 0.0 for hit in search(index, "数据质量"))
    assert search(index, "zzznomatch") == ()


def test_a_non_latin_document_is_reachable_by_filters_and_by_nothing_else(tmp_path):
    """The other half of the same limitation, at bundle level: the document
    indexes to an empty `tf`, so no text query reaches it. It is not missing
    from the index — `Filters` and the browse still find it — it is only
    unfindable by the one thing search exists to do."""
    index = build_index(
        write_bundle(
            tmp_path,
            {
                "zh.md": "---\ntype: Metric\ntitle: 数据质量\ntags: [质量]\n---\n\n数据质量指标。\n",
                "en.md": "---\ntype: Metric\ntitle: Data Quality\n---\n\nData quality metrics.\n",
            },
        )
    )
    assert dict(next(doc for doc in index.documents if doc.concept_id == "zh").tf) == {}
    assert ids(search(index, "数据质量")) == ["en", "zh"]  # the browse path, not a match
    assert ids(search(index, "quality")) == ["en"]
    assert ids(search(index, "", filters=Filters(tags=("质量",)))) == ["zh"]


def test_a_hit_carries_the_bundle_document_and_a_body_snippet(index):
    """Spec §7: `document` saves the caller a second lookup, and the snippet is
    drawn from `doc.body`, never the raw file."""
    hit = next(h for h in search(index, "retention") if h.concept_id == "gamma")
    assert hit.document is index.bundle.concepts["gamma"]
    assert "retention" in hit.snippet
    assert "type:" not in hit.snippet


# --- filters -----------------------------------------------------------------


def test_type_filter_goes_through_the_bundle(index):
    assert ids(search(index, "", filters=Filters(type="Policy"))) == ["gamma"]


def test_tags_are_and_across_tags(index):
    """Narrowing is what a multi-tag query means."""
    assert ids(search(index, "", filters=Filters(tags=("finance", "headline-metric")))) == ["alpha"]
    assert ids(search(index, "", filters=Filters(tags=("finance",)))) == ["alpha", "beta"]


def test_status_filters_on_the_effective_status(index):
    """`by_status()` applies §5.4's default, so `beta` — which authors no
    `status` at all — answers to `stable`."""
    assert ids(search(index, "", filters=Filters(status="stable"))) == ["alpha", "beta"]
    assert ids(search(index, "", filters=Filters(status="draft"))) == ["gamma"]


def test_trust_is_or_within(index):
    """Its values are mutually exclusive per document, so AND would be
    unsatisfiable."""
    assert ids(search(index, "", filters=Filters(trust=("human-reviewed", "unverified")))) == ["alpha", "gamma"]
    assert ids(search(index, "", filters=Filters(trust=("machine-confirmed",)))) == ["beta"]


def test_stale_filters_both_ways(index):
    assert ids(search(index, "", filters=Filters(stale=True), today=TODAY)) == ["gamma"]
    assert ids(search(index, "", filters=Filters(stale=False), today=TODAY)) == ["alpha", "beta"]


def test_filters_combine(index):
    hits = search(index, "", filters=Filters(type="Metric", tags=("finance",), stale=False), today=TODAY)
    assert ids(hits) == ["alpha", "beta"]


def test_an_unset_filters_matches_everything(index):
    assert ids(search(index, "", filters=Filters())) == ids(search(index, ""))


# --- `where`, one test per row of §6.1's table --------------------------------


def where(index, key, value):
    return ids(search(index, "", filters=Filters(where={key: value})))


def test_where_matches_a_string(index):
    assert where(index, "runtime", "bigquery") == ["alpha"]


def test_where_matches_a_bool_as_it_would_be_written(index):
    assert where(index, "enabled", "true") == ["alpha"]


def test_where_matches_an_int(index):
    assert where(index, "retries", "3") == ["alpha"]


def test_where_matches_a_float(index):
    assert where(index, "ratio", "1.5") == ["alpha"]


def test_where_matches_a_date_as_an_iso_string(index):
    """`fm_data(dates="iso")` already rendered it; `where` does not re-render."""
    assert where(index, "stale_after", "2026-12-31") == ["alpha"]


def test_where_matches_membership_in_a_list_of_scalars(index):
    assert where(index, "aliases", "primary") == ["alpha"]
    assert where(index, "aliases", "absent") == []


def test_where_never_matches_a_mapping(index):
    assert where(index, "window", "2026-01-01") == []


def test_where_never_matches_a_nested_list(index):
    """`verified` is a list of mappings — no string a caller could write means
    it."""
    assert where(index, "verified", "human:jsmith@acme") == []


def test_where_never_matches_a_null_value(index):
    assert where(index, "owner", "None") == []
    assert where(index, "owner", "") == []


def test_where_never_matches_an_absent_key(index):
    assert where(index, "nope", "anything") == []


def test_where_is_case_sensitive(index):
    """`Explanation` and `explanation` are different type values everywhere
    else in the workspace, and `where` is not the place to introduce a second
    convention."""
    assert where(index, "runtime", "BigQuery") == []


def test_where_is_and_across_keys(index):
    assert ids(search(index, "", filters=Filters(where={"runtime": "bigquery", "retries": "3"}))) == ["alpha"]
    assert search(index, "", filters=Filters(where={"runtime": "bigquery", "retries": "9"})) == ()


def test_where_reaches_a_known_key_and_means_something_different_from_the_filter(index):
    """Spec §6.1. `where` matches an *authored* value and does not apply §5.4's
    default; the dedicated filters exist because they carry derived semantics.
    `beta` authors no `status`, so `Filters.status` finds it and `where` does
    not — which is the whole distinction, demonstrated."""
    assert where(index, "status", "stable") == ["alpha"]
    assert ids(search(index, "", filters=Filters(status="stable"))) == ["alpha", "beta"]


# --- caller configuration raises ---------------------------------------------


def test_filtering_on_staleness_without_today_raises(index):
    """Spec §6.2. Content never raises; configuration always does. There is no
    default, no fallback, and no code path in which a hidden `date.today()`
    could survive review."""
    with pytest.raises(ValueError, match="today"):
        search(index, "cohort", filters=Filters(stale=False))


def test_a_query_with_no_staleness_opinion_needs_no_today(index):
    """`okf_io.validate()` takes `today` unconditionally; search does not,
    because most queries have no opinion and a mandatory argument on all of
    them would be friction with no safety gained."""
    assert ids(search(index, "cohort", filters=Filters(type="Metric"))) != []


def test_a_negative_limit_raises(index):
    """Python's slice semantics would otherwise read `limit=-1` as "all but the
    last hit", which is not a thing anyone means to ask for."""
    with pytest.raises(ValueError, match="limit"):
        search(index, "cohort", limit=-1)


def test_limit_truncates_and_zero_returns_nothing(index):
    assert len(search(index, "cohort", limit=2)) == 2
    assert search(index, "cohort", limit=0) == ()


def test_limit_defaults_to_everything(index):
    """A library that silently truncates is one you debug twice."""
    assert len(search(index, "cohort")) == 3
