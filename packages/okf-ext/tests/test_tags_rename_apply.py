"""`apply()`: byte-fidelity writes, atomicity, and the staleness guard.

The baseline tests (through `test_the_corpus_itself_was_never_touched`) are
the plan's own, with the corpus canary strengthened to a full-bundle byte
comparison per the review note: a single-line check would miss damage
anywhere else in the corpus.

Everything after that is adversarial: real defects have repeatedly needed
inputs the fixture corpus cannot express (a chmod'd file, a deleted file, a
hand-built plan, a non-string raw tag). Every mutating test goes through
`ext_helpers.bundle_copy` -- never `tagged_bundle()` -- so a failure here can
never corrupt the corpus for the rest of the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ext_helpers import TAGGED, bundle_copy, read, snapshot
from okf_ext.tags.model import RenamePlan, TagEdit
from okf_ext.tags.rename import apply, plan_merge, plan_normalize, plan_rename
from okf_io import load_bundle
from okf_io.document import Document
from ruamel.yaml.error import YAMLError

# Captured at import time -- before any test in this module, or any other
# module collected in the same run, has a chance to run and mutate anything.
_CORPUS_SNAPSHOT = snapshot(TAGGED)


def test_an_empty_plan_writes_nothing_and_changes_no_byte(tmp_path):
    """The round-trip property, inherited from okf-io -- and a true no-op:
    no writes, no failures, `ok` True."""
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "not-a-tag", "whatever")
    assert plan.is_empty
    result = apply(bundle, plan)
    assert result.written == ()
    assert result.failed == ()
    assert result.ok
    assert snapshot(root) == before


def test_a_flow_rename_stays_flow_and_stays_one_line(tmp_path):
    """Catches `set()`-instead-of-in-place, which drops the CommentedSeq and
    reflows the whole list."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, "revenue", "net-revenue"))
    assert "tags: [finance, net-revenue, headline-metric]" in read(root / "flow.md")


def test_a_block_rename_stays_block_and_leaves_neighbours_alone(tmp_path):
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, "ga4", "analytics"))
    after = read(root / "block.md")
    assert "tags:\n- Data Quality\n- analytics\n- kpi\n" in after
    assert "description: Count of completed orders." in after


def test_unaffected_files_are_byte_identical(tmp_path):
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    result = apply(bundle, plan_rename(bundle, "ga4", "analytics"))
    after = snapshot(root)
    assert result.written == ("block.md",)
    for name, content in before.items():
        if name != "block.md":
            assert after[name] == content, f"{name} changed and should not have"


def test_forgetting_mark_dirty_writes_the_pre_edit_bytes(tmp_path):
    """The worst footgun in the feature, and it fails *silently*.

    `serialize()` short-circuits to `raw_text` for a clean document, so an
    undeclared mutation is discarded with no error. This test exists so the
    behaviour is on the record rather than rediscovered.
    """
    root = bundle_copy(tmp_path)
    target = root / "flow.md"
    before = target.read_bytes()

    document = load_bundle(root).concepts["flow"]
    document.fm_raw["tags"][0] = "UNDECLARED"
    document.save()
    assert target.read_bytes() == before  # the edit vanished

    document.fm_raw["tags"][0] = "DECLARED"
    document.mark_dirty()
    document.save()
    assert target.read_bytes() != before
    assert "DECLARED" in read(target)


def test_a_merge_with_two_removals_produces_the_right_sequence(tmp_path):
    """Proves descending-index deletion: removing index 0 first would shift
    index 2 and delete the wrong tag."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, plan_merge(bundle, ["kpi", "metrics"], "metric"))
    assert "tags: [metric]" in read(root / "merge_me.md")


def test_normalization_lands_in_both_dialects(tmp_path):
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    result = apply(bundle, plan_normalize(bundle))
    assert set(result.written) == {"block.md", "underscore.md"}
    assert "- data-quality\n" in read(root / "block.md")
    assert "tags: [data-quality, e-commerce, kpi]" in read(root / "underscore.md")


def test_the_in_memory_bundle_stays_coherent_after_apply(tmp_path):
    """`mark_dirty()` clears the memoized view and `by_tag` computes live from
    `doc.fm.tags`, so no reload is needed."""
    bundle = load_bundle(bundle_copy(tmp_path))
    apply(bundle, plan_rename(bundle, "ga4", "analytics"))
    assert bundle.by_tag("analytics") == ("block",)
    assert bundle.by_tag("ga4") == ()


def test_a_foreign_plan_raises(tmp_path):
    """The wrong-bundle guard. Applying positions computed against a different
    tree would rewrite arbitrary tags."""
    one = load_bundle(bundle_copy(tmp_path))
    other_root = tmp_path / "other"
    other_root.mkdir()
    (other_root / "a.md").write_text(
        "---\ntype: Metric\ntitle: T\ndescription: D\ntags: [ga4]\n---\n\n# T\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="different bundle"):
        apply(load_bundle(other_root), plan_rename(one, "ga4", "analytics"))


def test_a_stale_plan_is_refused_per_document_and_reported(tmp_path):
    """A plan records positions. If the file changed underneath, applying it
    would rewrite whatever now sits at that index."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")
    (root / "block.md").write_text(
        "---\ntype: Metric\ntitle: Orders\ndescription: D\ntags: [changed]\n---\n\n# Orders\n",
        encoding="utf-8",
    )
    result = apply(load_bundle(root), plan)
    assert result.written == ()
    assert [f.path for f in result.failed] == ["block.md"]
    assert result.failed[0].kind == "stale"
    assert "stale" in result.failed[0].error
    assert "changed" in read(root / "block.md")


def test_a_write_failure_is_reported_not_raised(tmp_path, monkeypatch):
    """A caller halfway through a bulk edit needs the list of what landed far
    more than a traceback."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")

    def refuse(self, data):
        raise OSError("read-only file system")

    monkeypatch.setattr("pathlib.Path.write_bytes", refuse)
    result = apply(bundle, plan)
    assert result.written == ()
    assert [f.path for f in result.failed] == ["block.md"]
    assert result.failed[0].kind == "stage-error"
    assert "read-only" in result.failed[0].error
    assert not result.ok


def test_the_plans_skipped_list_is_carried_into_the_result(tmp_path):
    bundle = load_bundle(bundle_copy(tmp_path))
    plan = plan_rename(bundle, "ga4", "analytics")
    assert apply(bundle, plan).skipped == plan.skipped


def test_applying_a_plan_over_a_parse_error_concept_writes_nothing(tmp_path):
    """Direct `fm_raw` mutation bypasses `_require_mutable()`, so okf-ext does
    the check itself at plan time."""
    root = bundle_copy(tmp_path)
    before = (root / "broken.md").read_bytes()
    bundle = load_bundle(root)
    apply(bundle, plan_rename(bundle, "finance", "money"))
    assert (root / "broken.md").read_bytes() == before
    assert "broken" in {s.concept_id for s in plan_rename(bundle, "finance", "money").skipped}


def test_the_corpus_itself_was_never_touched():
    """Every mutating test goes through `bundle_copy`. If this fails, one did
    not, and the corpus is now lying to every other test.

    Strengthened from a single-line canary into a full-bundle byte
    comparison: a one-line check would miss damage anywhere else in the
    corpus (a different file, a different key, a trailing-byte change).
    """
    assert snapshot(TAGGED) == _CORPUS_SNAPSHOT


# --- Adversarial probes --------------------------------------------------
#
# None of the below is expressible against the fixture corpus alone: they
# need a chmod'd file, a deleted file, a hand-built plan, or a document shape
# (BOM, CRLF, a null tag position) the corpus does not carry.


def test_atomicity_one_unwritable_file_blocks_the_whole_batch(tmp_path):
    """The core guarantee. `plan_rename(..., "kpi", "objective")` touches
    three files (`block`, `merge_me`, `underscore`); chmod one read-only and
    assert that NONE of them changed -- not even the two that would have
    succeeded on their own."""
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    target = root / "underscore.md"
    original_mode = target.stat().st_mode
    target.chmod(0o444)
    try:
        result = apply(bundle, plan)
        assert result.written == ()
        assert "underscore.md" in [f.path for f in result.failed]
        assert {f.kind for f in result.failed} == {"unwritable"}
        assert snapshot(root) == before  # block.md and merge_me.md too, untouched
    finally:
        target.chmod(original_mode)


def test_atomicity_a_file_deleted_after_planning_blocks_the_whole_batch(tmp_path):
    """A concept still in the plan (and still in the in-memory Bundle) whose
    file vanished from disk between planning and apply must not let the
    other, perfectly fine, targets in the same batch get written."""
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    (root / "merge_me.md").unlink()

    result = apply(bundle, plan)
    assert result.written == ()
    assert "merge_me.md" in [f.path for f in result.failed]
    assert {f.kind for f in result.failed} == {"unwritable"}
    remaining = snapshot(root)
    assert remaining["block.md"] == before["block.md"]
    assert remaining["underscore.md"] == before["underscore.md"]


@pytest.mark.parametrize(
    ("fail_at", "failed_member"),
    [(1, "block.md"), (2, "merge_me.md"), (3, "underscore.md")],
)
def test_atomicity_a_mid_batch_write_failure_leaves_zero_files_changed(
    tmp_path, monkeypatch, fail_at, failed_member
):
    """The gap the pre-existing-unwritable-file test above cannot see: every
    target here passes the writability probe cleanly (nothing is chmod'd,
    nothing is deleted), and the failure only appears once staging actually
    starts writing bytes -- at each of the three positions in turn, not only
    the first. If staging wrote straight to the live targets, whichever
    document staged before the failure would already be changed on disk by
    the time the failing one is discovered. It must not be, regardless of
    which position fails: everything here lands in a same-directory temp
    file first, and nothing live is touched until every temp file has been
    written successfully.

    The mock also writes a truncated prefix before raising -- `write_bytes`
    opens for truncating write before it can fail, so a realistic disk-full
    error leaves partial bytes already on the temp file. A mock that raises
    without writing anything would never exercise the cleanup path against
    an actual partial file."""
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    original_write_bytes = Path.write_bytes
    calls = {"count": 0}

    def flaky_write_bytes(self, data):
        calls["count"] += 1
        if calls["count"] == fail_at:
            original_write_bytes(self, data[:20])  # a partial write really lands
            raise OSError("disk full")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)
    result = apply(bundle, plan)

    assert result.written == ()
    assert [f.path for f in result.failed] == [failed_member]
    assert result.failed[0].kind == "stage-error"
    assert "disk full" in result.failed[0].error
    assert snapshot(root) == before  # no live file changed, regardless of position

    leftover_temp_files = [p for p in root.rglob(".*.tmp") if p.is_file()]
    assert leftover_temp_files == []


def test_a_serialize_failure_for_one_document_does_not_block_its_siblings(tmp_path, monkeypatch):
    """`test_a_serialize_failure_is_reported_not_raised` only exercises a
    single-document plan, which cannot distinguish "this document's failure
    is isolated" from "the whole batch aborted for an unrelated reason".
    Here the plan touches three documents and only one fails to serialize:
    the other two -- a genuinely different content-failure isolation
    regime from the I/O atomicity proven above -- must still land."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    original_serialize = Document.serialize

    def flaky_serialize(self):
        if self.path is not None and self.path.name == "merge_me.md":
            raise YAMLError("boom")
        return original_serialize(self)

    monkeypatch.setattr(Document, "serialize", flaky_serialize)
    result = apply(bundle, plan)

    assert set(result.written) == {"block.md", "underscore.md"}
    assert [f.path for f in result.failed] == ["merge_me.md"]
    assert result.failed[0].kind == "serialize-error"
    assert "boom" in result.failed[0].error
    assert "objective" in read(root / "block.md")
    assert "objective" in read(root / "underscore.md")


def test_a_replace_failure_is_reported_and_its_temp_file_is_cleaned_up(tmp_path, monkeypatch):
    """The third regime the docstring names explicitly: a *caught* `OSError`
    during the final rename loop is isolated per document, not batch-wide --
    unlike a staging failure (which aborts the whole batch because nothing
    live has changed yet), a replace failure happens after some siblings may
    already have committed, so partial application is possible here without
    any crash. It is reported for that one document rather than unwinding
    what already landed. Also pins that the losing document's temp file
    does not linger."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    original_replace = Path.replace

    def flaky_replace(self, target):
        if self.name.startswith(".merge_me.md."):
            raise OSError("permission changed after staging")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    result = apply(bundle, plan)

    assert set(result.written) == {"block.md", "underscore.md"}
    assert [f.path for f in result.failed] == ["merge_me.md"]
    assert result.failed[0].kind == "commit-error"
    assert "permission changed" in result.failed[0].error
    assert "objective" in read(root / "block.md")
    assert "objective" in read(root / "underscore.md")
    # merge_me.md itself was never replaced, and its temp file is gone.
    assert "kpi" in read(root / "merge_me.md")
    leftover_temp_files = [p for p in root.rglob(".*.tmp") if p.is_file()]
    assert leftover_temp_files == []


def test_a_staging_cleanup_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    """Both cleanup call sites in the staging phase -- for the document that
    just failed, and for the ones that had already staged successfully --
    wrap their `unlink` in a bare `except OSError: pass`. If the cleanup
    itself also fails (a permission race on top of the disk-full that
    caused the original failure, say), `apply()` must not raise and must
    not let the unlink failure overwrite the original, more informative
    error. A leftover temp file in this one degenerate case is the accepted,
    documented trade of best-effort cleanup -- not a second silent failure."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    original_write_bytes = Path.write_bytes
    original_unlink = Path.unlink
    calls = {"count": 0}

    def flaky_write_bytes(self, data):
        calls["count"] += 1
        if calls["count"] == 2:  # merge_me's staging write
            raise OSError("disk full")
        return original_write_bytes(self, data)

    def flaky_unlink(self, missing_ok=False):
        if self.name.endswith(".tmp"):
            raise OSError("cannot remove temp file either")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)
    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    result = apply(bundle, plan)  # must not raise despite the broken cleanup

    assert result.written == ()
    assert [f.path for f in result.failed] == ["merge_me.md"]
    assert result.failed[0].kind == "stage-error"
    assert "disk full" in result.failed[0].error  # not overwritten by the unlink failure
    # The degenerate outcome this test exists to prove is safe, not silent:
    # cleanup genuinely could not run, so a temp file really is left behind.
    assert any(p.name.endswith(".tmp") for p in root.rglob("*"))


def test_a_replace_cleanup_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    """The same defence in the commit loop's cleanup: a failed `replace`
    whose follow-up `unlink` also fails must still report the `replace`
    failure, not the `unlink` failure, and must not raise."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    original_replace = Path.replace
    original_unlink = Path.unlink

    def flaky_replace(self, target):
        if self.name.startswith(".merge_me.md."):
            raise OSError("permission changed after staging")
        return original_replace(self, target)

    def flaky_unlink(self, missing_ok=False):
        if self.name.endswith(".tmp"):
            raise OSError("cannot remove temp file either")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    result = apply(bundle, plan)  # must not raise despite the broken cleanup

    assert set(result.written) == {"block.md", "underscore.md"}
    assert [f.path for f in result.failed] == ["merge_me.md"]
    assert result.failed[0].kind == "commit-error"
    assert "permission changed" in result.failed[0].error  # not overwritten
    assert any(p.name.endswith(".tmp") for p in root.rglob("*"))


def test_two_edits_at_the_same_index_are_refused(tmp_path):
    """No planner can construct this (each raw index is visited once), but
    `apply()` already defends against other malformed hand-built plans --
    stale index, parse-error concept, non-sequence `tags` -- and applying a
    rename immediately followed by a removal at the same position would
    otherwise silently discard the rename with nothing reported."""
    (tmp_path / "dup.md").write_bytes(
        b"---\ntype: Metric\ntitle: D\ndescription: D\ntags: [kpi, metric]\n---\n\n# D\n"
    )
    bundle = load_bundle(tmp_path)
    before = (tmp_path / "dup.md").read_bytes()
    plan = RenamePlan(
        root=bundle.root,
        edits=(
            TagEdit(concept_id="dup", path="dup.md", index=0, old="kpi", new="objective"),
            TagEdit(concept_id="dup", path="dup.md", index=0, old="kpi", new=None),
        ),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ()
    assert result.failed[0].kind == "duplicate-edit"
    assert "index 0" in result.failed[0].error
    assert (tmp_path / "dup.md").read_bytes() == before


def test_in_memory_bundle_is_not_mutated_when_the_batch_aborts(tmp_path):
    """A stronger form of coherence: not only must the *files* be untouched
    when the batch aborts, the in-memory Bundle must not silently disagree
    with them either. A caller catching the abort and calling `by_tag` must
    see the pre-apply world, not a phantom rename that never reached disk."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")

    target = root / "underscore.md"
    original_mode = target.stat().st_mode
    target.chmod(0o444)
    try:
        result = apply(bundle, plan)
        assert result.written == ()
        assert bundle.by_tag("objective") == ()
        assert bundle.by_tag("kpi") == ("block", "merge_me", "underscore")
    finally:
        target.chmod(original_mode)


def test_a_foreign_root_raises_even_when_the_directory_exists(tmp_path):
    """`apply()` with a plan from a bundle rooted somewhere else raises,
    even when that other root is a real, loadable bundle of its own --
    the check is about identity, not validity."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")

    elsewhere = bundle_copy(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="different bundle"):
        apply(load_bundle(elsewhere), plan)


def test_a_stale_plan_from_reordering_is_refused(tmp_path):
    """Not just a changed value at the position -- a *reordering* moves a
    different, but real, tag into the planned slot. The guard must not
    accept it just because the value happens to still look plausible."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")  # block.md, index 1

    (root / "block.md").write_text(
        "---\ntype: Metric\ntitle: Orders\ndescription: D\n"
        "tags:\n- ga4\n- Data Quality\n- kpi\n---\n\n# Orders\n",
        encoding="utf-8",
    )
    result = apply(load_bundle(root), plan)
    assert result.written == ()
    assert result.failed[0].kind == "stale"
    assert "stale" in result.failed[0].error
    assert "Data Quality" in read(root / "block.md")  # untouched, not overwritten


def test_a_stale_plan_from_a_shortened_sequence_is_refused(tmp_path):
    """The index the plan recorded no longer exists at all."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")  # block.md, index 1

    (root / "block.md").write_text(
        "---\ntype: Metric\ntitle: Orders\ndescription: D\n"
        "tags:\n- Data Quality\n---\n\n# Orders\n",
        encoding="utf-8",
    )
    result = apply(load_bundle(root), plan)
    assert result.written == ()
    assert result.failed[0].kind == "stale"
    assert "stale" in result.failed[0].error


def test_a_stale_document_does_not_block_its_siblings_in_the_same_plan(tmp_path):
    """The two atomicity regimes are deliberately different. An I/O write
    failure aborts the whole batch (proved above); a *content*-shaped
    failure -- staleness, a parse error, a non-sequence -- is refused only
    for the one document it applies to, per the acceptance criteria
    ("refused per document"). `plan_rename(..., "kpi", "objective")` touches
    three files; only `underscore` is made stale here, and `block` and
    `merge_me` must still land."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "kpi", "objective")
    assert plan.concept_ids == ("block", "merge_me", "underscore")

    (root / "underscore.md").write_text(
        "---\ntype: Reference\ntitle: Quality notes\ndescription: D\n"
        "tags: [changed]\n---\n\n# Quality notes\n",
        encoding="utf-8",
    )
    result = apply(load_bundle(root), plan)
    assert set(result.written) == {"block.md", "merge_me.md"}
    assert [f.path for f in result.failed] == ["underscore.md"]
    assert result.failed[0].kind == "stale"
    assert "stale" in result.failed[0].error
    assert "objective" in read(root / "block.md")
    assert "objective" in read(root / "merge_me.md")
    assert "changed" in read(root / "underscore.md")  # left exactly as found


def test_reapplying_the_same_plan_is_refused_as_stale_against_the_same_bundle(tmp_path):
    """This covers *reapplying the same plan object against the same
    in-memory bundle*, not re-planning against a reloaded one -- see
    `test_replanning_after_reload_converges_to_an_empty_plan` in
    `test_tags_roundtrip.py` for that other, genuinely different case.
    Decide and pin: applying the same plan twice, without a reload, is NOT a
    no-op. The first apply mutates the in-memory Bundle (`by_tag` reflects it
    immediately, with no reload -- see the fixture test above), so the
    second application of the identical plan finds `edit.old` no longer at
    its recorded index and is refused as stale, per document, rather than
    silently reapplying or silently succeeding a second time."""
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")

    first = apply(bundle, plan)
    assert first.written == ("block.md",)

    second = apply(bundle, plan)
    assert second.written == ()
    assert [f.path for f in second.failed] == ["block.md"]
    assert second.failed[0].kind == "stale"
    assert "stale" in second.failed[0].error
    assert "analytics" in read(root / "block.md")  # the first apply's result stands


def test_a_hand_built_plan_targeting_a_null_position_is_refused_as_stale(tmp_path):
    """The type-aware guard. `str(None) == "None"` would make a naive
    `str(sequence[edit.index]) != edit.old` guard accept `old="None"` as a
    match for a null position -- exactly the coercion `_raw_tags` (Task 7)
    exists to refuse. No planner can build this edit (a `None` sentinel is
    never a mapping key), so it is hand-built here to prove `apply()` itself
    refuses it, not just that the planners never offer it up."""
    (tmp_path / "withnull.md").write_bytes(
        b"---\ntype: Metric\ntitle: N\ndescription: D\ntags: [kpi, null, metric]\n---\n\n# N\n"
    )
    bundle = load_bundle(tmp_path)
    before = (tmp_path / "withnull.md").read_bytes()

    plan = RenamePlan(
        root=bundle.root,
        edits=(
            TagEdit(concept_id="withnull", path="withnull.md", index=1, old="None", new="renamed"),
        ),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ()
    assert result.failed[0].kind == "stale"
    assert "stale" in result.failed[0].error
    assert (tmp_path / "withnull.md").read_bytes() == before


def test_an_index_beyond_the_end_of_the_sequence_is_refused_as_stale(tmp_path):
    (tmp_path / "short.md").write_bytes(
        b"---\ntype: Metric\ntitle: S\ndescription: D\ntags: [kpi]\n---\n\n# S\n"
    )
    bundle = load_bundle(tmp_path)
    plan = RenamePlan(
        root=bundle.root,
        edits=(TagEdit(concept_id="short", path="short.md", index=5, old="kpi", new="objective"),),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ()
    assert result.failed[0].kind == "stale"
    assert "stale" in result.failed[0].error


def test_a_hand_built_plan_over_a_parse_error_concept_is_refused(tmp_path):
    """Defence in depth for the `_require_mutable()` bypass: even a plan that
    did not come from `plan_rename` (which would have excluded `broken` via
    `scan()`) must not be able to mutate it through `apply()` directly."""
    root = bundle_copy(tmp_path)
    before = (root / "broken.md").read_bytes()
    bundle = load_bundle(root)
    plan = RenamePlan(
        root=bundle.root,
        edits=(
            TagEdit(concept_id="broken", path="broken.md", index=0, old="finance", new="money"),
        ),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ()
    assert (root / "broken.md").read_bytes() == before
    assert result.failed[0].kind == "parse-error"
    assert "parse" in result.failed[0].error


def test_a_plan_naming_a_concept_absent_from_this_bundle_is_reported(tmp_path):
    (tmp_path / "solo.md").write_bytes(
        b"---\ntype: Metric\ntitle: S\ndescription: D\ntags: [kpi]\n---\n\n# S\n"
    )
    bundle = load_bundle(tmp_path)
    plan = RenamePlan(
        root=bundle.root,
        edits=(TagEdit(concept_id="ghost", path="ghost.md", index=0, old="kpi", new="objective"),),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ()
    assert [f.path for f in result.failed] == ["ghost.md"]
    assert result.failed[0].kind == "not-a-member"


def test_emptying_a_tag_list_writes_an_empty_sequence_not_a_removed_key(tmp_path):
    """Decide and pin: removing a document's only tag leaves `tags: []` on
    disk. The key is not deleted -- `apply()` only ever mutates the sequence
    in place; nothing in this module removes a frontmatter key.

    None of the four planners can actually construct this plan on their own:
    `_plan_mapping` always leaves exactly one survivor per collapsed target
    group, so a rename or merge can shrink a list but never empty it
    completely. `apply()` itself has no such restriction -- it only obeys
    `edits` -- so this is hand-built directly to exercise the behaviour in
    isolation.
    """
    (tmp_path / "solo.md").write_bytes(
        b"---\ntype: Metric\ntitle: Solo\ndescription: D\ntags: [onlytag]\n---\n\n# Solo\n"
    )
    bundle = load_bundle(tmp_path)
    plan = RenamePlan(
        root=bundle.root,
        edits=(TagEdit(concept_id="solo", path="solo.md", index=0, old="onlytag", new=None),),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ("solo.md",)
    after = (tmp_path / "solo.md").read_text()
    assert "tags: []" in after
    assert "tags:" in after


def test_a_bom_document_survives_a_rename(tmp_path):
    (tmp_path / "bom.md").write_bytes(
        "﻿---\ntype: Metric\ntitle: Bom\ndescription: D\ntags: [alpha, beta]\n"
        "---\n\n# Bom\n".encode()
    )
    bundle = load_bundle(tmp_path)
    result = apply(bundle, plan_rename(bundle, "alpha", "gamma"))
    assert result.written == ("bom.md",)
    raw = (tmp_path / "bom.md").read_bytes()
    assert raw.startswith("﻿".encode())
    assert "tags: [gamma, beta]" in raw.decode("utf-8")

    reloaded = Document.load(tmp_path / "bom.md")
    assert reloaded.parse_error is None
    assert reloaded.fm.tags == ("gamma", "beta")


def test_a_crlf_document_survives_a_rename(tmp_path):
    text = (
        "---\r\ntype: Metric\r\ntitle: Crlf\r\ndescription: D\r\n"
        "tags:\r\n- alpha\r\n- beta\r\n---\r\n\r\n# Crlf\r\n"
    )
    (tmp_path / "crlf.md").write_bytes(text.encode("utf-8"))
    bundle = load_bundle(tmp_path)
    result = apply(bundle, plan_rename(bundle, "alpha", "gamma"))
    assert result.written == ("crlf.md",)
    raw = (tmp_path / "crlf.md").read_bytes().decode("utf-8")
    assert "\r\n" in raw
    assert "\n" not in raw.replace("\r\n", "")  # no bare LF anywhere
    assert "- gamma\r\n" in raw


def test_trailing_whitespace_blank_lines_and_comments_in_frontmatter_survive(tmp_path):
    """The whitespace/comment/blank-line sits on an *unrelated* key, not the
    line being edited -- a rename legitimately changes the tags line itself,
    so trailing whitespace *there* is not expected to survive."""
    text = (
        "---\ntype: Metric  \ntitle: Commented\ndescription: D\n"
        "# a comment about tags\ntags: [alpha, beta]\n\n"
        "status: stable\n---\n\n# Commented\n"
    )
    (tmp_path / "commented.md").write_bytes(text.encode("utf-8"))
    bundle = load_bundle(tmp_path)
    result = apply(bundle, plan_rename(bundle, "alpha", "gamma"))
    assert result.written == ("commented.md",)
    after = (tmp_path / "commented.md").read_text()
    assert "type: Metric  \n" in after  # trailing whitespace on an untouched line
    assert "# a comment about tags\n" in after
    assert "\n\nstatus: stable\n" in after  # the blank line before `status`
    assert "tags: [gamma, beta]" in after


def test_result_paths_match_the_edits_and_skips_they_came_from(tmp_path):
    root = bundle_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "finance", "money")
    result = apply(bundle, plan)
    written_stems = {name[: -len(".md")] for name in result.written}
    edited_concepts = {edit.concept_id for edit in plan.edits}
    assert written_stems <= edited_concepts
    assert {s.path for s in result.skipped} == {s.path for s in plan.skipped}
    assert {s.concept_id for s in result.skipped} == {s.concept_id for s in plan.skipped}


def test_a_plan_targeting_a_document_whose_tags_are_no_longer_a_sequence_is_reported(tmp_path):
    """`scalar_tags` carries `tags: finance`, a scalar. No planner would ever
    build this edit (`scan()` excludes it), so it is hand-built here to
    exercise `apply()`'s own defence directly."""
    root = bundle_copy(tmp_path)
    before = (root / "scalar_tags.md").read_bytes()
    bundle = load_bundle(root)
    plan = RenamePlan(
        root=bundle.root,
        edits=(
            TagEdit(
                concept_id="scalar_tags",
                path="scalar_tags.md",
                index=0,
                old="finance",
                new="money",
            ),
        ),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert result.written == ()
    assert result.failed[0].kind == "tags-not-a-sequence"
    assert "sequence" in result.failed[0].error
    assert (root / "scalar_tags.md").read_bytes() == before


def test_a_serialize_failure_is_reported_not_raised(tmp_path, monkeypatch):
    """The other half of the "serialize before a byte lands" guarantee: a
    document that fails to render is reported, not raised, and nothing from
    that document (or, per the atomicity guarantee, from the batch) writes."""
    root = bundle_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = plan_rename(bundle, "ga4", "analytics")

    def explode(self):
        raise YAMLError("boom")

    monkeypatch.setattr("okf_io.document.Document.serialize", explode)
    result = apply(bundle, plan)
    assert result.written == ()
    assert [f.path for f in result.failed] == ["block.md"]
    assert result.failed[0].kind == "serialize-error"
    assert "boom" in result.failed[0].error
    assert snapshot(root) == before
