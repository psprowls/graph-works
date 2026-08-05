"""The corpus really carries the shapes every other module relies on.

Without this, a fixture edit degrades the suite silently: the dialect tests
still pass, they just stop testing two dialects.
"""

from __future__ import annotations

from ext_helpers import TAGGED, UNUSABLE, bundle_copy, read, snapshot, tagged_bundle


def test_the_bundle_loads_eight_concepts():
    assert sorted(tagged_bundle().concepts) == [
        "block",
        "broken",
        "duplicate",
        "flow",
        "merge_me",
        "scalar_tags",
        "underscore",
        "untagged",
    ]


def test_the_corpus_carries_both_yaml_dialects_in_one_bundle():
    """Style preservation is where this feature fails quietly, so the two
    dialects have to coexist in a single bundle, not in two."""
    assert "tags: [finance, revenue, headline-metric]" in read(TAGGED / "flow.md")
    assert "tags:\n- Data Quality\n" in read(TAGGED / "block.md")


def test_broken_is_a_parse_error_not_a_missing_file():
    doc = tagged_bundle().concepts["broken"]
    assert doc.parse_error is not None
    assert doc.fm_raw == {}


def test_scalar_tags_is_a_coercion_failure_not_a_parse_error():
    doc = tagged_bundle().concepts["scalar_tags"]
    assert doc.parse_error is None
    assert "tags" in doc.fm.coercion_failures
    assert doc.fm.tags == ()


def test_unusable_names_exactly_the_two_unreadable_concepts():
    bundle = tagged_bundle()
    readable = {
        cid
        for cid, doc in bundle.concepts.items()
        if doc.parse_error is None and "tags" not in doc.fm.coercion_failures
    }
    assert set(bundle.concepts) - readable == set(UNUSABLE)


def test_untagged_carries_no_tags_key():
    assert "tags" not in tagged_bundle().concepts["untagged"].fm_raw


def test_the_vocabulary_lives_inside_the_bundle():
    """So `DEFAULT_IGNORE` has something to be about, and so the corpus proves
    a bundle carrying one is an entirely ordinary OKF bundle."""
    assert "_tags.yaml" in tagged_bundle().assets


def test_a_copy_is_byte_identical(tmp_path):
    assert snapshot(bundle_copy(tmp_path)) == snapshot(TAGGED)
