"""`build_index` over a purpose-built corpus."""

from __future__ import annotations

import pytest
from ext_helpers import write_bundle
from okf_ext.search import DEFAULT_WEIGHTS, build_index

WEIGHTED = """---
type: Metric
title: Revenue
description: Margin
tags: [cohort]
---

Retention
"""

BROKEN = """---
type: [unclosed
---

Broken body mentions revenue.
"""

DATED = """---
type: Metric
stale_after: 2026-12-31
enabled: true
---

Body.
"""

CORPUS = {
    "weighted.md": WEIGHTED,
    "broken.md": BROKEN,
    "dated.md": DATED,
    "index.md": "# Index\n",
    "log.md": "# Log\n",
    "data.csv": "a,b\n",
    "skip/hidden.md": "---\ntype: Metric\n---\n\nHidden.\n",
}


@pytest.fixture
def bundle(tmp_path):
    return write_bundle(tmp_path, CORPUS, ignore=("skip/*",))


def document(index, concept_id):
    return next(doc for doc in index.documents if doc.concept_id == concept_id)


def test_weights_are_applied_as_token_repetition(bundle):
    """Spec §4.1: a title word lands in `tf` three times, a description or tag
    twice, a body word once. `length` is the sum of the weighted counts, which
    is the accepted approximation the spec records."""
    doc = document(build_index(bundle), "weighted")
    assert dict(doc.tf) == {"revenue": 3, "margin": 2, "cohort": 2, "retention": 1}
    assert doc.length == 8


def test_a_zero_weight_excludes_the_field_entirely(bundle):
    """Not "counts it as zero". A term present in `tf` with a count of `0`
    would still count toward document frequency, changing idf for every other
    document — a silently wrong ranking rather than an excluded field. This is
    also the supported way to search bodies only."""
    doc = document(build_index(bundle, weights={"title": 0, "description": 0, "tags": 0}), "weighted")
    assert dict(doc.tf) == {"retention": 1}
    assert doc.length == 1


def test_a_supplied_weight_merges_over_the_defaults(bundle):
    doc = document(build_index(bundle, weights={"body": 5}), "weighted")
    assert doc.tf["retention"] == 5
    assert doc.tf["revenue"] == 3


def test_the_resolved_weights_ride_on_the_index(bundle):
    index = build_index(bundle, weights={"body": 5})
    assert index.weights == {"title": 3, "description": 2, "tags": 2, "body": 5}
    assert build_index(bundle).weights == DEFAULT_WEIGHTS


def test_an_unknown_weight_field_raises(bundle):
    """A typo'd `{"titel": 5}` that silently does nothing is the failure this
    prevents."""
    with pytest.raises(ValueError, match="titel"):
        build_index(bundle, weights={"titel": 5})


def test_a_negative_weight_raises(bundle):
    with pytest.raises(ValueError, match="negative"):
        build_index(bundle, weights={"title": -1})


def test_reserved_files_assets_and_ignored_members_are_absent(bundle):
    """Inherited from okf-io's own walk, never re-derived here (spec §4).
    The reference implementation's loader skipped `index.md`/`log.md` and
    dotted paths by hand; the exclusions survive, the hand-rolled walk does
    not."""
    ids = {doc.concept_id for doc in build_index(bundle).documents}
    assert ids == {"broken", "dated", "weighted"}


def test_documents_arrive_in_concept_id_order(bundle):
    """What makes the tie-break in `search` free (§5.2)."""
    ids = [doc.concept_id for doc in build_index(bundle).documents]
    assert ids == sorted(ids)


def test_a_malformed_document_is_indexed_on_its_body_alone(bundle):
    """Spec §4.2. `okf_ext.schemas` skips a `parse_error` document because
    okf-io already reported it; search does the opposite. Retrieval is not
    reporting, and hiding the document makes it unfindable precisely when
    someone is hunting for the thing that is broken."""
    doc = document(build_index(bundle), "broken")
    assert dict(doc.tf) == {"broken": 1, "body": 1, "mentions": 1, "revenue": 1}
    assert dict(doc.data) == {}


def test_the_snippet_source_is_the_body_not_the_raw_file(bundle):
    """Spec §7: a snippet source that included frontmatter is what the
    reference did by accident, and it produces snippets full of YAML."""
    doc = document(build_index(bundle), "weighted")
    assert doc.text == "Retention\n"


def test_data_is_the_iso_frontmatter_projection(bundle):
    """Extension point #2 acquiring its second consumer; `schemas` is the
    first. Dates arrive as ISO strings, which is what `where` compares."""
    doc = document(build_index(bundle), "dated")
    assert doc.data["stale_after"] == "2026-12-31"
    assert doc.data["enabled"] is True


def test_the_index_holds_the_bundle_rather_than_copying_out_of_it(bundle):
    """Filtering calls `by_type()` / `trust_tier()` on this, so a private copy
    is exactly how two consumers end up disagreeing about one document."""
    assert build_index(bundle).bundle is bundle
