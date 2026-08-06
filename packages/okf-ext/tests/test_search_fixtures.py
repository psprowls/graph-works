"""A run over okf-io's vendored `acme_retail` bundle.

Spec §10.2. The corpus is vendored verbatim from
`GoogleCloudPlatform/knowledge-catalog` and has nothing to do with the vault
this capability was written for, which is the point: it proves search is not
quietly shaped by its origin. Do not edit the fixture — see
`packages/okf-io/tests/fixtures/FIXTURES.md` to re-vendor.
"""

from __future__ import annotations

from datetime import date

from ext_helpers import acme_retail_bundle
from okf_ext.search import Filters, build_index, search

TODAY = date(2027, 1, 1)

#: Every concept in the bundle. `index.md`, `log.md`, `viz.html` and
#: `attesters/sql_equality.py` are not concepts, and okf-io's walk — not this
#: capability — is what excludes them.
CONCEPTS = (
    "computations/gross-margin-period",
    "computations/revenue-ytd",
    "metrics/gross-margin",
    "metrics/gross-margin-legacy",
    "metrics/revenue",
    "policies/margin-standard",
    "policies/revenue-recognition",
    "skills/run-on-bq",
    "tables/orders",
)


def ids(hits):
    return [hit.concept_id for hit in hits]


def test_the_index_covers_exactly_the_bundle_concepts():
    index = build_index(acme_retail_bundle())
    assert tuple(doc.concept_id for doc in index.documents) == CONCEPTS


def test_a_text_query_ranks_the_obvious_document_first():
    index = build_index(acme_retail_bundle())
    hits = search(index, "gross margin")
    assert hits[0].concept_id == "metrics/gross-margin"
    assert hits[0].score > 0
    assert "margin" in hits[0].snippet.lower()


def test_a_type_filter_narrows_without_moving_scores():
    index = build_index(acme_retail_bundle())
    unfiltered = {hit.concept_id: hit.score for hit in search(index, "gross margin")}
    metrics = search(index, "gross margin", filters=Filters(type="Metric"))
    assert set(ids(metrics)) == {"metrics/gross-margin", "metrics/gross-margin-legacy"}
    assert all(unfiltered[hit.concept_id] == hit.score for hit in metrics)


def test_browsing_by_tag_returns_the_headline_metrics():
    index = build_index(acme_retail_bundle())
    assert ids(search(index, "", filters=Filters(tags=("headline-metric",)))) == [
        "metrics/gross-margin",
        "metrics/revenue",
    ]


def test_the_derived_signals_are_all_load_bearing():
    """The stated point of the item: `by_type`, `by_tag`, `by_status`,
    `trust_tier` and `is_stale` were built, tested, and consumed by nothing but
    the validation catalog. One capability makes all five load-bearing."""
    index = build_index(acme_retail_bundle())
    assert ids(search(index, "", filters=Filters(status="deprecated"))) == ["metrics/gross-margin-legacy"]
    assert ids(search(index, "", filters=Filters(trust=("unverified",)))) == ["skills/run-on-bq"]
    fresh = ids(search(index, "", filters=Filters(stale=False), today=TODAY))
    assert fresh == ["metrics/gross-margin-legacy", "skills/run-on-bq"]


def test_where_reaches_an_extension_key_the_typed_view_does_not_expose():
    """`runtime` is a spec field, but `where` reads the whole `fm_data`
    projection rather than the typed view's named attributes — which is what
    makes it the escape hatch for the extension keys both requesting lanes
    plan to add."""
    index = build_index(acme_retail_bundle())
    assert ids(search(index, "", filters=Filters(where={"runtime": "bigquery"}))) == [
        "computations/gross-margin-period",
        "computations/revenue-ytd",
    ]


def test_the_vendored_bundle_is_never_written_to():
    """`build_index` and `search` read; nothing in this capability writes."""
    bundle = acme_retail_bundle()
    before = {path: path.read_bytes() for path in sorted(bundle.root.rglob("*")) if path.is_file()}
    search(build_index(bundle), "gross margin", filters=Filters(type="Metric"))
    assert {path: path.read_bytes() for path in sorted(bundle.root.rglob("*")) if path.is_file()} == before
