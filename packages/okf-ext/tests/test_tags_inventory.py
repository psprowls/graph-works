from __future__ import annotations

from types import MappingProxyType

from ext_helpers import UNUSABLE, tagged_bundle
from okf_ext.tags.inventory import clusters, inventory, scan
from okf_ext.tags.model import TagCluster, TagInventory
from okf_io import load_bundle


def _bare_inventory(counts: dict[str, int]) -> TagInventory:
    """A `TagInventory` built from nothing but `counts`, for `clusters()`
    cases the fixture corpus cannot produce. The other fields are unused by
    `clusters()`, so they are filled with the cheapest empty value."""
    return TagInventory(
        counts=MappingProxyType(counts),
        concepts=MappingProxyType({}),
        untagged=(),
        co_occurrence=MappingProxyType({}),
        skipped=(),
    )


def test_scan_names_the_two_unreadable_concepts_and_no_others():
    usable, skipped = scan(tagged_bundle())
    assert {s.concept_id: s.reason for s in skipped} == UNUSABLE
    assert set(usable).isdisjoint(UNUSABLE)


def test_scan_carries_a_detail_that_says_what_went_wrong():
    _, skipped = scan(tagged_bundle())
    by_id = {s.concept_id: s for s in skipped}
    assert by_id["broken"].detail
    assert by_id["scalar_tags"].path == "scalar_tags.md"


def test_scan_reports_a_non_utf8_member_as_unreadable(tmp_path):
    """`bundle.unreadable` is the one `SkipReason` no markdown fixture in the
    corpus can trigger: `okf_io.load` only populates it on an OS-level read
    failure or invalid UTF-8 (`packages/okf-io/src/okf_io/bundle.py`), and
    the corpus is all valid UTF-8 markdown. Built here as an ad hoc
    one-off bundle instead, following the pattern okf-io's own suite uses in
    `test_bundle.py::test_a_non_utf8_member_becomes_unreadable_not_an_exception`.
    """
    (tmp_path / "bad.md").write_bytes(b"---\ntype: Metric\n---\n\n\xff\xfe\n")

    usable, skipped = scan(load_bundle(tmp_path))

    assert usable == ()
    by_id = {s.concept_id: s for s in skipped}
    assert by_id["bad"].reason == "unreadable"
    assert by_id["bad"].path == "bad.md"
    assert by_id["bad"].detail


def test_an_unreadable_concept_is_not_reported_as_untagged():
    """`broken` has `fm.tags == ()` because nobody could read it, not because
    it carries no tags. Counting it as untagged asserts something false."""
    inv = inventory(tagged_bundle())
    assert inv.untagged == ("untagged",)


def test_counts_are_per_concept_not_per_occurrence():
    inv = inventory(tagged_bundle())
    assert inv.counts["kpi"] == 3  # block, underscore, merge_me
    assert inv.counts["finance"] == 1  # flow only; scalar_tags was skipped
    assert inv.counts["metric"] == 2  # duplicate, merge_me


def test_concepts_maps_each_tag_to_sorted_concept_ids():
    inv = inventory(tagged_bundle())
    assert inv.concepts["kpi"] == ("block", "merge_me", "underscore")


def test_skipped_rides_along_with_the_inventory():
    inv = inventory(tagged_bundle())
    assert {s.concept_id for s in inv.skipped} == set(UNUSABLE)


def test_co_occurrence_pairs_are_sorted_and_deterministic():
    inv = inventory(tagged_bundle())
    pairs = inv.pairs()
    assert ("kpi", "metric", 1) in pairs
    assert all(left < right for left, right, _ in pairs)
    assert pairs == inventory(tagged_bundle()).pairs()


def test_tags_lists_every_counted_tag_sorted():
    """Written against the literal expected tuple, not `sorted(inv.counts)` —
    the same expression the property is implemented with would pass whether
    or not `tags` was sorted or filtered correctly."""
    inv = inventory(tagged_bundle())
    assert inv.tags == (
        "Data Quality",
        "data_quality",
        "e-commerce",
        "finance",
        "ga4",
        "headline-metric",
        "kpi",
        "metric",
        "metrics",
        "revenue",
    )


def test_pairs_can_be_filtered_by_a_minimum():
    inv = inventory(tagged_bundle())
    assert all(count >= 2 for _, _, count in inv.pairs(minimum=2))


def test_an_empty_or_whitespace_tag_is_counted_not_filtered(tmp_path):
    """Consistent with the module's "report the mess, never fix it"
    philosophy (see `inventory`'s docstring): an empty string or
    whitespace-only tag is a real, if malformed, value in `fm.tags`, and
    pinning that it survives into `counts` is what stops a future "helpful"
    filter from silently hiding the authoring mistake it represents."""
    (tmp_path / "a.md").write_bytes(
        b'---\ntype: Metric\ntitle: A\ndescription: D\ntags: ["", "kpi"]\n---\n\n# A\n'
    )
    (tmp_path / "b.md").write_bytes(
        b'---\ntype: Metric\ntitle: B\ndescription: D\ntags: ["", "   "]\n---\n\n# B\n'
    )

    inv = inventory(load_bundle(tmp_path))

    assert inv.counts[""] == 2
    assert inv.counts["kpi"] == 1
    assert inv.counts["   "] == 1
    found = [c for c in clusters(inv) if c.kind == "normalization" and c.canonical == ""]
    assert found and "" in found[0].members


def test_a_coerced_non_string_tag_is_counted_though_no_planner_can_touch_it(tmp_path):
    """The other half of a documented asymmetry (see `rename`'s module
    docstring and the package README's "Known limitations"). `okf_io` coerces
    a non-string scalar tag (`42`) to the string `'42'` with no
    `coercion_failures` entry -- `inventory()` has no way to tell it apart
    from a tag someone actually wrote, so it is counted like any other.
    `tags.rename.test_an_integer_entry_is_unmatchable_even_though_the_typed_view_coerces_it`
    pins the other side: `plan_rename(bundle, "42", ...)` against this same
    shape returns an empty plan, silently. Neither function is wrong in
    isolation; pinning both here is what keeps a future "fix" on one side
    from being made without noticing it changes the other's contract too.
    """
    (tmp_path / "withint.md").write_bytes(
        b"---\ntype: Metric\ntitle: I\ndescription: D\ntags: [kpi, 42, metric]\n---\n\n# I\n"
    )
    bundle = load_bundle(tmp_path)
    doc = bundle.concepts["withint"]
    assert "tags" not in doc.fm.coercion_failures  # no trace it was ever non-string

    inv = inventory(bundle)
    assert inv.counts["42"] == 1
    assert inv.concepts["42"] == ("withint",)


def test_the_same_bundle_inventories_identically_twice():
    """Determinism, matching the core's habit of sorting output so nothing
    depends on filesystem or dict order."""
    assert inventory(tagged_bundle()) == inventory(tagged_bundle())


def test_normalization_clusters_group_by_shared_canonical_form():
    found = [c for c in clusters(inventory(tagged_bundle())) if c.kind == "normalization"]
    assert len(found) == 1
    assert found[0].canonical == "data-quality"
    assert found[0].members == ("Data Quality", "data_quality")
    assert found[0].score is None


def test_similarity_clusters_carry_a_score_and_stay_separate():
    found = [c for c in clusters(inventory(tagged_bundle())) if c.kind == "similarity"]
    assert len(found) == 1
    assert found[0].members == ("metric", "metrics")
    assert found[0].score is not None and found[0].score > 0.8


def test_the_two_kinds_share_no_member_in_this_corpus():
    """Not a general guarantee — the two kinds *can* share a member, see
    `test_a_shared_member_is_the_intended_overlap_not_a_regression` below.
    The fixture corpus just never produces that shape: its one normalization
    cluster's canonical form (`data-quality`) is not itself a spelling either
    `Data Quality` or `data_quality` uses, so it never becomes a real tag
    eligible for the similarity pass in the first place."""
    found = clusters(inventory(tagged_bundle()))
    normalization = {m for c in found if c.kind == "normalization" for m in c.members}
    similarity = {m for c in found if c.kind == "similarity" for m in c.members}
    assert normalization.isdisjoint(similarity)


def test_an_unrelated_similarity_pair_survives_alongside_an_overlapping_cluster():
    """`data-quality` is a real, literal tag here (count 3) — not a canonical
    form that only exists on paper — and it is also one spelling of a
    multi-member normalization group alongside `data_quality`. Because it is
    real, it stays eligible for the similarity pass and legitimately matches
    the lexically close `data-qualityx`: a member can head one normalization
    cluster and one similarity cluster simultaneously when its own spelling
    genuinely is the canonical form, and that is not a bug — a phantom
    spelling nobody wrote would be (see the tests below). `metric`/`metrics`
    ride along, untouched by any of this, to prove the overlap is confined
    to the one shared shape rather than leaking into unrelated pairs.
    Hand-built rather than a bundle: the fixture corpus cannot produce this
    shape.
    """
    inv = TagInventory(
        counts=MappingProxyType(
            {
                "data-quality": 3,
                "data_quality": 1,
                "data-qualityx": 1,
                "metric": 2,
                "metrics": 1,
            }
        ),
        concepts=MappingProxyType({}),
        untagged=(),
        co_occurrence=MappingProxyType({}),
        skipped=(),
    )

    found = clusters(inv)

    assert found == (
        TagCluster(
            canonical="data-quality",
            members=("data-quality", "data_quality"),
            kind="normalization",
        ),
        TagCluster(
            canonical="data-quality",
            members=("data-quality", "data-qualityx"),
            kind="similarity",
            score=0.96,
        ),
        TagCluster(
            canonical="metric",
            members=("metric", "metrics"),
            kind="similarity",
            score=0.9231,
        ),
    )


def test_a_single_already_canonical_tag_produces_no_clusters():
    """`get_close_matches` rejects `n=0`. A bundle where every tag shares one
    canonical form leaves `forms` with exactly one element, so there is
    nothing left for that lone form to be compared against — the crash this
    guards against needs no exclusion at all, just an ordinary small bundle.
    """
    found = clusters(_bare_inventory({"finance": 3}))
    assert found == ()


def test_a_single_non_canonical_tag_normalizes_without_a_crash():
    found = clusters(_bare_inventory({"Finance": 3}))
    normalization = [c for c in found if c.kind == "normalization"]
    similarity = [c for c in found if c.kind == "similarity"]
    assert normalization == [
        TagCluster(canonical="finance", members=("Finance",), kind="normalization")
    ]
    assert similarity == []


def test_two_spellings_of_one_canonical_form_produce_no_similarity_crash():
    found = clusters(_bare_inventory({"Data Quality": 1, "data_quality": 2}))
    normalization = [c for c in found if c.kind == "normalization"]
    similarity = [c for c in found if c.kind == "similarity"]
    assert normalization == [
        TagCluster(
            canonical="data-quality",
            members=("Data Quality", "data_quality"),
            kind="normalization",
        )
    ]
    assert similarity == []


def test_a_shared_member_is_the_intended_overlap_not_a_regression():
    """The minimal reproduction of both things this dict pins at once: the
    `ValueError` crash this class of case used to raise (before the
    `if not others: continue` guard), and the one shape where a member is
    honestly shared between kinds. `data-quality` is a real tag (count 3),
    and it is the **target** `data_quality` collapses into, not something
    normalization changes — so `data-quality` still legitimately matches the
    lexically close `data-qualityx` under `similarity`, before and after
    that merge is ever applied. A future reader who finds `data-quality` in
    both `found[0].members` and `found[1].members` should read this test as
    confirmation, not conclude the overlap needs "fixing" back to disjoint.
    """
    found = clusters(_bare_inventory({"data-quality": 3, "data_quality": 1, "data-qualityx": 1}))
    assert found == (
        TagCluster(
            canonical="data-quality",
            members=("data-quality", "data_quality"),
            kind="normalization",
        ),
        TagCluster(
            canonical="data-quality",
            members=("data-quality", "data-qualityx"),
            kind="similarity",
            score=0.96,
        ),
    )
    shared = {m for c in found if c.kind == "normalization" for m in c.members} & {
        m for c in found if c.kind == "similarity" for m in c.members
    }
    assert shared == {"data-quality"}


def test_an_empty_inventory_clusters_to_nothing():
    assert clusters(_bare_inventory({})) == ()


def test_a_multi_member_normalization_group_with_no_canonical_spelling_never_phantom_matches():
    """Neither `Data Quality` nor `DATA_QUALITY` is spelled exactly
    `data-quality` — the canonical form is not a tag anyone wrote. Comparing
    canonical forms alone would still offer it as a `similarity` match
    against the lexically close `data-qualityx`, naming a tag that appears
    nowhere in the bundle and that `rename.plan_merge` could never act on.
    The `form in inv.counts` check excludes it.
    """
    found = clusters(_bare_inventory({"Data Quality": 3, "DATA_QUALITY": 1, "data-qualityx": 1}))
    assert found == (
        TagCluster(
            canonical="data-quality",
            members=("DATA_QUALITY", "Data Quality"),
            kind="normalization",
        ),
    )


def test_a_single_member_normalization_group_with_no_canonical_spelling_never_phantom_matches():
    """`Data Quality` alone still normalizes to `data-quality`, a spelling
    nobody wrote — the same phantom-match risk as the multi-member case,
    here with a normalization cluster of one."""
    found = clusters(_bare_inventory({"Data Quality": 3, "data-qualityx": 1}))
    assert found == (
        TagCluster(canonical="data-quality", members=("Data Quality",), kind="normalization"),
    )


def test_every_cluster_member_is_a_real_tag():
    """The invariant the phantom-member bug violated: a cluster may only
    name a spelling that appears in `inv.counts`. `rename.plan_merge` takes
    tag names, and a member nothing carries could never be merged. Checked
    against the corpus and every hand-built inventory this suite
    constructs, not just the cases built to prove the point."""
    inventories = [
        inventory(tagged_bundle()),
        _bare_inventory({}),
        _bare_inventory({"finance": 3}),
        _bare_inventory({"Finance": 3}),
        _bare_inventory({"Data Quality": 1, "data_quality": 2}),
        _bare_inventory({"data-quality": 3, "data_quality": 1, "data-qualityx": 1}),
        _bare_inventory(
            {
                "data-quality": 3,
                "data_quality": 1,
                "data-qualityx": 1,
                "metric": 2,
                "metrics": 1,
            }
        ),
        _bare_inventory({"Data Quality": 3, "DATA_QUALITY": 1, "data-qualityx": 1}),
        _bare_inventory({"Data Quality": 3, "data-qualityx": 1}),
    ]
    for inv in inventories:
        for cluster in clusters(inv):
            for member in cluster.members:
                assert member in inv.counts, (inv.counts, cluster)


def test_a_lone_non_canonical_tag_still_clusters():
    """Otherwise `plan_normalize` could not fix it — it only acts on clusters.

    Depends on the corpus carrying `Data Quality` (title case, never its own
    canonical form) alongside its normalized sibling `data_quality`.
    """
    inv = inventory(tagged_bundle())
    assert any(c.kind == "normalization" and "Data Quality" in c.members for c in clusters(inv))


def test_a_high_cutoff_suppresses_similarity_clusters():
    found = clusters(inventory(tagged_bundle()), cutoff=0.99)
    assert not [c for c in found if c.kind == "similarity"]


def test_clusters_are_deterministic():
    inv = inventory(tagged_bundle())
    assert clusters(inv) == clusters(inv)
