from __future__ import annotations

import pytest
from ext_helpers import UNUSABLE, VOCABULARY, bundle_copy, tagged_bundle, write
from okf_ext.tags.rename import (
    plan_from_vocabulary,
    plan_merge,
    plan_normalize,
    plan_rename,
)
from okf_ext.tags.vocabulary import load_vocabulary
from okf_io import load_bundle


def edits_for(plan, concept_id):
    return sorted(
        ((e.index, e.old, e.new) for e in plan.edits if e.concept_id == concept_id),
    )


# --- Plan's own tests (Step 1), one assertion corrected --------------------
#
# `test_plan_from_vocabulary_drives_off_replaced_by` as written in the plan
# asserts `{e.new for e in plan.edits} == {"metric"}`. That cannot hold at
# the same time as the very next test (`..._collapses_where_the_replacement_
# is_present`), which correctly requires `merge_me`'s `kpi` to become a
# *removal* (`new=None`) because `metric` is already present in that
# document. The removal behaviour is the one the acceptance criteria and the
# shared `_plan_mapping` engine actually specify ("a rename onto a tag the
# document already carries becomes a removal"), so the first test's
# assertion is corrected here to allow for it rather than silently
# demanding a duplicate `metric` tag.


def test_plan_rename_targets_every_occurrence():
    plan = plan_rename(tagged_bundle(), "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")
    assert edits_for(plan, "block") == [(2, "kpi", "objective")]


def test_plan_rename_records_the_bundle_root():
    bundle = tagged_bundle()
    assert plan_rename(bundle, "kpi", "objective").root == bundle.root


def test_planning_never_dirties_a_document():
    """All four planners are pure reads. A planner that mutated would make
    `dry_run` semantics a lie and corrupt a bundle nobody agreed to change."""
    bundle = tagged_bundle()
    before = {cid: doc.serialize() for cid, doc in bundle.concepts.items()}
    plan_rename(bundle, "kpi", "objective")
    plan_normalize(bundle)
    plan_merge(bundle, ["metric", "metrics"], "metric")
    plan_from_vocabulary(bundle, load_vocabulary(VOCABULARY))
    assert {cid: doc.serialize() for cid, doc in bundle.concepts.items()} == before


def test_a_rename_onto_an_existing_tag_becomes_a_removal():
    """Otherwise the document ends up carrying the same tag twice."""
    plan = plan_rename(tagged_bundle(), "metrics", "metric")
    assert edits_for(plan, "duplicate") == [(1, "metrics", None)]
    assert edits_for(plan, "merge_me") == [(2, "metrics", None)]


def test_an_identity_rename_plans_nothing():
    assert plan_rename(tagged_bundle(), "kpi", "kpi").is_empty


def test_renaming_an_absent_tag_plans_nothing():
    assert plan_rename(tagged_bundle(), "not-a-tag", "whatever").is_empty


def test_plan_merge_keeps_the_first_and_removes_the_rest():
    """Two non-adjacent removals in one document — this is what makes
    descending-index deletion necessary rather than decorative."""
    plan = plan_merge(tagged_bundle(), ["kpi", "metrics"], "metric")
    assert edits_for(plan, "merge_me") == [(0, "kpi", None), (2, "metrics", None)]


def test_plan_merge_promotes_the_first_source_when_the_target_is_absent():
    plan = plan_merge(tagged_bundle(), ["ga4", "e-commerce"], "channel")
    assert edits_for(plan, "block") == [(1, "ga4", "channel")]
    assert edits_for(plan, "underscore") == [(1, "e-commerce", "channel")]


def test_plan_normalize_fixes_only_normalization_clusters():
    """The two-confidence split is load-bearing: `metrics` looks like `metric`
    but needs judgment, so a normalization plan must not touch it."""
    plan = plan_normalize(tagged_bundle())
    assert edits_for(plan, "block") == [(0, "Data Quality", "data-quality")]
    assert edits_for(plan, "underscore") == [(0, "data_quality", "data-quality")]
    assert "metrics" not in {e.old for e in plan.edits}


def test_plan_from_vocabulary_drives_off_replaced_by():
    plan = plan_from_vocabulary(tagged_bundle(), load_vocabulary(VOCABULARY))
    assert {e.old for e in plan.edits} == {"kpi"}
    # Corrected from the plan's literal `{e.new for e in plan.edits} ==
    # {"metric"}`: `merge_me` already carries `metric`, so its `kpi` edit is a
    # removal (`new=None`) per the very next test. Every *renamed-to* value
    # is still `metric` — only the removals are `None`.
    assert {e.new for e in plan.edits if e.new is not None} == {"metric"}
    assert None in {e.new for e in plan.edits}


def test_plan_from_vocabulary_collapses_where_the_replacement_is_present():
    plan = plan_from_vocabulary(tagged_bundle(), load_vocabulary(VOCABULARY))
    assert edits_for(plan, "merge_me") == [(0, "kpi", None)]  # metric already at index 1
    assert edits_for(plan, "block") == [(2, "kpi", "metric")]


def test_a_deprecated_tag_with_no_replacement_plans_nothing(tmp_path):
    target = tmp_path / "tags.yaml"
    write(target, "version: 1\ntags:\n  - name: kpi\n    deprecated: true\n")
    assert plan_from_vocabulary(tagged_bundle(), load_vocabulary(target)).is_empty


def test_unreadable_concepts_are_skipped_not_edited():
    plan = plan_rename(tagged_bundle(), "finance", "money")
    assert {s.concept_id: s.reason for s in plan.skipped} == UNUSABLE
    assert not [e for e in plan.edits if e.concept_id in UNUSABLE]


def test_planning_the_same_bundle_twice_gives_identical_ordering():
    assert plan_normalize(tagged_bundle()) == plan_normalize(tagged_bundle())


def test_edits_are_ordered_by_concept_then_index():
    plan = plan_merge(tagged_bundle(), ["kpi", "metrics"], "metric")
    keys = [(e.concept_id, e.index) for e in plan.edits]
    assert keys == sorted(keys)


# --- Adversarial probes ------------------------------------------------
#
# The recurring shapes reference implementations in this plan have missed:
# a value type nobody considered, an empty collection, an invariant asserted
# in a docstring but not enforced in code. None of the below is expressible
# against the fixture corpus alone.


def test_a_tag_appearing_twice_in_one_document_gets_both_positions_handled(tmp_path):
    """The first occurrence becomes the rename; the document would then
    already carry the new spelling, so the second occurrence must become a
    removal rather than a second copy — and the positions recorded must be
    the raw ones (0 and 1), not a deduplicated view (which would only have
    one `kpi` and therefore only one candidate index)."""
    (tmp_path / "dup.md").write_bytes(
        b"---\ntype: Metric\ntitle: Dup\ndescription: D\ntags: [kpi, kpi, metric]\n---\n\n# Dup\n"
    )
    bundle = load_bundle(tmp_path)
    # `fm.tags` itself is not deduplicated (only `inventory()` dedupes it
    # explicitly via `dict.fromkeys`) -- both raw `kpi` positions are present
    # and must both be accounted for by index.
    assert bundle.concepts["dup"].fm.tags == ("kpi", "kpi", "metric")

    plan = plan_rename(bundle, "kpi", "objective")

    assert edits_for(plan, "dup") == [(0, "kpi", "objective"), (1, "kpi", None)]


def test_plan_merge_with_no_sources_plans_nothing():
    assert plan_merge(tagged_bundle(), [], "metric").is_empty


def test_plan_merge_with_an_empty_string_source_raises_type_error():
    """`""` is a `str`, not a sequence of tag names -- covers the empty-string
    shape `test_plan_merge_with_no_sources_plans_nothing` (an empty *list*)
    does not: an empty string must be rejected the same way a non-empty one
    is, not silently accepted as "no sources"."""
    with pytest.raises(TypeError, match="bare string"):
        plan_merge(tagged_bundle(), "", "metric")


def test_plan_merge_with_a_single_source_equal_to_the_target_plans_nothing():
    assert plan_merge(tagged_bundle(), ["metric"], "metric").is_empty


def test_plan_merge_given_a_bare_string_raises_instead_of_iterating_characters():
    """`Sequence[str]` accepts a `str` on its own terms, and `mypy --strict`
    raises no issue on `plan_merge(bundle, "metrics", "metric")` -- but a
    `str` iterates its own characters, so the natural-looking mistake would
    otherwise silently plan a merge of `"m"`, `"e"`, `"t"`, ... into
    `"metric"`, match nothing, and return an empty plan with no error."""
    with pytest.raises(TypeError, match="bare string"):
        plan_merge(tagged_bundle(), "metrics", "metric")


def test_plan_normalize_on_an_already_canonical_bundle_plans_nothing(tmp_path):
    (tmp_path / "clean.md").write_bytes(
        b"---\ntype: Metric\ntitle: Clean\ndescription: D\ntags: [finance, metric]\n---\n\n# Clean\n"
    )
    assert plan_normalize(load_bundle(tmp_path)).is_empty


def test_a_bundle_with_zero_concepts_plans_nothing_for_every_planner(tmp_path):
    bundle = load_bundle(tmp_path)
    assert bundle.concepts == {}
    assert plan_rename(bundle, "a", "b").is_empty
    assert plan_merge(bundle, ["a", "b"], "c").is_empty
    assert plan_normalize(bundle).is_empty
    assert plan_from_vocabulary(bundle, load_vocabulary(VOCABULARY)).is_empty


def test_all_three_skip_reasons_are_carried_and_never_produce_an_edit(tmp_path):
    """`broken` (parse-error) and `scalar_tags` (tags-not-a-sequence) come
    from the corpus; `unreadable` needs a non-UTF-8 file, which the corpus
    cannot express, built here as an ad hoc addition to a writable copy."""
    root = bundle_copy(tmp_path)
    (root / "bad.md").write_bytes(b"---\ntype: Metric\n---\n\n\xff\xfe\n")
    bundle = load_bundle(root)

    plan = plan_rename(bundle, "kpi", "objective")

    reasons = {s.concept_id: s.reason for s in plan.skipped}
    assert reasons == {**UNUSABLE, "bad": "unreadable"}
    assert not [e for e in plan.edits if e.concept_id in reasons]


def test_root_is_recorded_for_every_planner():
    bundle = tagged_bundle()
    vocab = load_vocabulary(VOCABULARY)
    assert plan_merge(bundle, ["kpi", "metrics"], "metric").root == bundle.root
    assert plan_normalize(bundle).root == bundle.root
    assert plan_from_vocabulary(bundle, vocab).root == bundle.root


def test_the_untagged_concept_never_appears_in_any_plan():
    """`untagged` carries no `tags` key at all -- readable, but nothing to
    rewrite. Every planner must pass over it without an edit or a crash."""
    bundle = tagged_bundle()
    vocab = load_vocabulary(VOCABULARY)
    plans = [
        plan_rename(bundle, "kpi", "objective"),
        plan_merge(bundle, ["kpi", "metrics"], "metric"),
        plan_normalize(bundle),
        plan_from_vocabulary(bundle, vocab),
    ]
    for plan in plans:
        assert "untagged" not in plan.concept_ids


# --- Non-string raw tag entries -----------------------------------------
#
# A blanket `str()` over the raw sequence would turn a YAML `null` into the
# literal string "None" and a nested mapping into a plausible-looking string
# with no trace it was ever a mapping -- indistinguishable afterwards from a
# real tag someone actually wrote. `okf_io.models._str_tuple` refuses that
# same conversion for `fm.tags`; `_raw_tags` must refuse it too, since
# whatever it produces here is exactly what `apply()` will use to
# decide what a document "already says" before overwriting it.


def test_a_null_entry_is_unmatchable_and_neighbouring_indices_stay_correct(tmp_path):
    (tmp_path / "withnull.md").write_bytes(
        b"---\ntype: Metric\ntitle: N\ndescription: D\ntags: [kpi, null, metric]\n---\n\n# N\n"
    )
    bundle = load_bundle(tmp_path)
    # fm.tags silently drops the null; fm_raw keeps every position, so the
    # two sequences differ in length -- edit indices must be valid against
    # the raw one, not the shorter typed view.
    assert bundle.concepts["withnull"].fm.tags == ("kpi", "metric")
    assert len(list(bundle.concepts["withnull"].fm_raw.get("tags"))) == 3

    # A blanket str() would turn the null into the literal string "None",
    # letting a rename of the tag "None" land on it. It must not.
    assert plan_rename(bundle, "None", "was-null").is_empty

    # The tags on either side of the null keep their raw-accurate indices.
    assert edits_for(plan_rename(bundle, "kpi", "objective"), "withnull") == [(0, "kpi", "objective")]
    assert edits_for(plan_rename(bundle, "metric", "measure"), "withnull") == [(2, "metric", "measure")]


def test_a_nested_mapping_entry_is_unmatchable_and_still_lets_the_document_plan(tmp_path):
    """A per-item coercion failure (`"tags.1"`) is not the exact string
    `"tags"`, so `scan()` still calls this concept usable -- it is the
    planner, not `scan`, that must keep the mapping from ever being matched
    or mistaken for a string tag."""
    (tmp_path / "withmap.md").write_bytes(
        b"---\ntype: Metric\ntitle: M\ndescription: D\ntags: [kpi, {a: 1}, metric]\n---\n\n# M\n"
    )
    bundle = load_bundle(tmp_path)
    doc = bundle.concepts["withmap"]
    assert "tags" not in doc.fm.coercion_failures

    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.skipped == ()  # usable, not skipped -- scan() never flagged it
    assert edits_for(plan, "withmap") == [(0, "kpi", "objective")]
    # The tag after the mapping keeps index 2, proving the mapping's
    # position was preserved (as an unmatchable sentinel) rather than
    # dropped, which would have shifted this tag to index 1.
    assert edits_for(plan_rename(bundle, "metric", "measure"), "withmap") == [(2, "metric", "measure")]


def test_an_integer_entry_is_unmatchable_even_though_the_typed_view_coerces_it(tmp_path):
    """`fm.tags` coerces `42` to the string `"42"` (`okf_io.models._as_str`
    treats `bool | int | float` as coercible, unlike `null` or a mapping).
    The raw value is still the Python `int` 42, not a string anyone
    actually wrote, so a rename of the *tag* `"42"` must not silently match
    a numeric position just because it renders the same after coercion."""
    (tmp_path / "withint.md").write_bytes(
        b"---\ntype: Metric\ntitle: I\ndescription: D\ntags: [kpi, 42, metric]\n---\n\n# I\n"
    )
    bundle = load_bundle(tmp_path)
    assert bundle.concepts["withint"].fm.tags == ("kpi", "42", "metric")

    assert plan_rename(bundle, "42", "forty-two").is_empty

    assert edits_for(plan_rename(bundle, "metric", "measure"), "withint") == [(2, "metric", "measure")]


def test_edit_indices_are_valid_against_fm_raw_when_fm_tags_is_shorter(tmp_path):
    """Combines a null, a nested mapping, and a repeated real tag in one
    document: `fm.tags` drops the first two, so it is strictly shorter than
    `fm_raw`. Every edit's index must still resolve to the correct raw
    element -- the property `apply()` depends on completely."""
    (tmp_path / "mixed.md").write_bytes(
        b"---\ntype: Metric\ntitle: Mixed\ndescription: D\ntags: [kpi, null, metric, {a: 1}, kpi]\n---\n\n# Mixed\n"
    )
    bundle = load_bundle(tmp_path)
    doc = bundle.concepts["mixed"]
    raw = list(doc.fm_raw.get("tags"))
    assert len(doc.fm.tags) < len(raw)

    plan = plan_rename(bundle, "kpi", "objective")
    for edit in plan.edits:
        if edit.concept_id == "mixed":
            assert str(raw[edit.index]) == edit.old

    # kpi appears at raw indices 0 and 4: the first is renamed, the second
    # becomes a removal, and both indices must be exactly these.
    assert edits_for(plan, "mixed") == [(0, "kpi", "objective"), (4, "kpi", None)]


def test_three_occurrences_of_one_tag_rename_the_first_and_remove_the_rest(tmp_path):
    (tmp_path / "triple.md").write_bytes(
        b"---\ntype: Metric\ntitle: T\ndescription: D\ntags: [kpi, kpi, metric, kpi]\n---\n\n# T\n"
    )
    bundle = load_bundle(tmp_path)
    plan = plan_rename(bundle, "kpi", "objective")
    assert edits_for(plan, "triple") == [
        (0, "kpi", "objective"),
        (1, "kpi", None),
        (3, "kpi", None),
    ]


def test_every_edit_index_is_valid_against_the_raw_sequence_across_the_corpus():
    """A general regression guard, not just for the hand-built cases above:
    every edit any of the four planners produces against the fixture corpus
    must resolve to the exact raw value it claims as `old`."""
    bundle = tagged_bundle()
    vocab = load_vocabulary(VOCABULARY)
    plans = [
        plan_rename(bundle, "kpi", "objective"),
        plan_merge(bundle, ["kpi", "metrics"], "metric"),
        plan_normalize(bundle),
        plan_from_vocabulary(bundle, vocab),
    ]
    for plan in plans:
        for edit in plan.edits:
            raw = list(bundle.concepts[edit.concept_id].fm_raw.get("tags"))
            assert raw[edit.index] == edit.old
