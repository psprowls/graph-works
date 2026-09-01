"""`apply()`: byte-fidelity writes, the digest guard, and the three regimes.

Every mutating test goes through `ext_helpers.tabled_copy` -- never
`tabled_bundle()` -- so a failure here can never corrupt the corpus for the
rest of the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ext_helpers import TABLED, read, snapshot, tabled_copy, write
from okf_ext.tables import Column, RowSplice, SplicePlan, TableSpec, apply, plan_row
from okf_io import load_bundle
from okf_io.document import Document
from ruamel.yaml.error import YAMLError

_CORPUS_SNAPSHOT = snapshot(TABLED)

PLAN = TableSpec(columns=(Column("action"), Column("done_when", synonyms=("done when",)), Column("rationale")))
ROW = {"action": "a new action", "done_when": "it lands", "rationale": "because"}
TARGETS = ["plan_absent", "plan_empty", "plan_ok", "plan_prose"]


def _plan(bundle, ids=None, **kwargs):
    return plan_row(bundle, ids or TARGETS, "Plan", PLAN, ROW, key="action", **kwargs)


def test_an_empty_plan_writes_nothing_and_changes_no_byte(tmp_path):
    root = tabled_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    existing = {"action": "Port the tolerant reader", "done_when": "x", "rationale": "y"}
    plan = plan_row(bundle, ["plan_ok"], "Plan", PLAN, existing, key="action")
    assert plan.is_empty
    result = apply(bundle, plan)
    assert (result.written, result.failed, result.ok) == ((), (), True)
    assert snapshot(root) == before


def test_the_row_lands_and_the_rest_of_each_file_is_untouched(tmp_path):
    root = tabled_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    result = apply(bundle, _plan(bundle))
    assert set(result.written) == {f"{name}.md" for name in TARGETS}
    for name in TARGETS:
        after = read(root / f"{name}.md")
        assert "| a new action | it lands | because |" in after
        assert after.startswith("---\n")  # frontmatter is byte-identical
    for name, content in before.items():
        if name.removesuffix(".md") not in TARGETS:
            assert snapshot(root)[name] == content


def test_frontmatter_survives_a_body_only_edit_byte_for_byte(tmp_path):
    root = tabled_copy(tmp_path)
    original = read(root / "plan_ok.md").split("---\n")[1]
    bundle = load_bundle(root)
    apply(bundle, _plan(bundle, ["plan_ok"]))
    assert read(root / "plan_ok.md").split("---\n")[1] == original


def test_prose_under_a_malformed_heading_survives(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, _plan(bundle, ["plan_prose"]))
    after = read(root / "plan_prose.md")
    assert "wholesale would lose them" in after
    assert "| a new action | it lands | because |" in after


def test_the_in_memory_bundle_stays_coherent_after_apply(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, _plan(bundle, ["plan_ok"]))
    assert "a new action" in bundle.concepts["plan_ok"].body
    assert bundle.concepts["plan_ok"].body == load_bundle(root).concepts["plan_ok"].body


def test_only_written_documents_are_updated_in_memory(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    before = bundle.concepts["file_map"].body
    apply(bundle, _plan(bundle, ["plan_ok"]))
    assert bundle.concepts["file_map"].body == before


def test_applying_twice_is_a_no_op_the_second_time(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    apply(bundle, _plan(bundle, ["plan_ok"]))
    after_first = snapshot(root)
    reloaded = load_bundle(root)
    second = _plan(reloaded, ["plan_ok"])
    assert second.is_empty
    assert apply(reloaded, second).ok
    assert snapshot(root) == after_first


def test_a_foreign_plan_raises(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    plan = _plan(bundle, ["plan_ok"])
    elsewhere = tabled_copy(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="different bundle"):
        apply(load_bundle(elsewhere), plan)


def test_a_stale_body_is_refused_per_document_and_its_siblings_still_land(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    plan = _plan(bundle, ["plan_ok", "plan_empty"])
    write(root / "plan_ok.md", "---\ntype: Reference\ntitle: T\ndescription: D\n---\n\n## Plan\n\nchanged\n")
    result = apply(load_bundle(root), plan)
    assert result.written == ("plan_empty.md",)
    assert [(f.path, f.kind) for f in result.failed] == [("plan_ok.md", "stale")]
    assert "changed" in read(root / "plan_ok.md")


def test_reapplying_the_same_plan_against_the_same_bundle_is_refused_as_stale(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    plan = _plan(bundle, ["plan_ok"])
    assert apply(bundle, plan).written == ("plan_ok.md",)
    second = apply(bundle, plan)
    assert second.written == ()
    assert second.failed[0].kind == "stale"


def test_a_plan_naming_one_concept_twice_is_refused(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    one = _plan(bundle, ["plan_ok"]).splices[0]
    plan = SplicePlan(root=bundle.root, splices=(one, one), skipped=())
    result = apply(bundle, plan)
    assert result.written == ()
    assert result.failed[0].kind == "duplicate-edit"


def test_a_plan_naming_a_concept_absent_from_this_bundle_is_reported(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    ghost = RowSplice(
        concept_id="ghost",
        path="ghost.md",
        heading="Plan",
        row=ROW,
        action="append",
        line=1,
        digest="d",
        after="x",
    )
    result = apply(bundle, SplicePlan(root=bundle.root, splices=(ghost,), skipped=()))
    assert [(f.path, f.kind) for f in result.failed] == [("ghost.md", "not-a-member")]


def test_a_hand_built_plan_over_a_parse_error_concept_is_refused(tmp_path):
    root = tabled_copy(tmp_path)
    write(root / "broken.md", "---\ntype: [\n---\n\n# Broken\n")
    before = (root / "broken.md").read_bytes()
    bundle = load_bundle(root)
    splice = RowSplice(
        concept_id="broken",
        path="broken.md",
        heading="Plan",
        row=ROW,
        action="create-section",
        line=1,
        digest="d",
        after="x",
    )
    result = apply(bundle, SplicePlan(root=bundle.root, splices=(splice,), skipped=()))
    assert result.failed[0].kind == "parse-error"
    assert (root / "broken.md").read_bytes() == before


def test_one_unwritable_file_blocks_the_whole_batch(tmp_path):
    root = tabled_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = _plan(bundle)
    target = root / "plan_ok.md"
    mode = target.stat().st_mode
    target.chmod(0o444)
    try:
        result = apply(bundle, plan)
        assert result.written == ()
        assert {f.kind for f in result.failed} == {"unwritable"}
        assert snapshot(root) == before
    finally:
        target.chmod(mode)


def test_a_mid_batch_staging_failure_leaves_zero_files_changed(tmp_path, monkeypatch):
    root = tabled_copy(tmp_path)
    before = snapshot(root)
    bundle = load_bundle(root)
    plan = _plan(bundle)
    original = Path.write_bytes
    calls = {"count": 0}

    def flaky(self, data):
        calls["count"] += 1
        if calls["count"] == 3:
            original(self, data[:20])
            raise OSError("disk full")
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    result = apply(bundle, plan)
    assert result.written == ()
    assert {f.kind for f in result.failed} == {"stage-error"}
    assert snapshot(root) == before
    assert [p for p in root.rglob(".*.tmp") if p.is_file()] == []


def test_a_commit_failure_is_isolated_per_document(tmp_path, monkeypatch):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    plan = _plan(bundle)
    original = Path.replace

    def flaky(self, target):
        if self.name.startswith(".plan_ok.md."):
            raise OSError("permission changed after staging")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    result = apply(bundle, plan)
    assert set(result.written) == {"plan_absent.md", "plan_empty.md", "plan_prose.md"}
    assert [(f.path, f.kind) for f in result.failed] == [("plan_ok.md", "commit-error")]
    assert "a new action" not in read(root / "plan_ok.md")
    assert [p for p in root.rglob(".*.tmp") if p.is_file()] == []


def test_a_serialize_failure_for_one_document_does_not_block_its_siblings(tmp_path, monkeypatch):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    plan = _plan(bundle)
    original = Document.serialize

    def flaky(self):
        if self.path is not None and self.path.name == "plan_ok.md":
            raise YAMLError("boom")
        return original(self)

    monkeypatch.setattr(Document, "serialize", flaky)
    result = apply(bundle, plan)
    assert "plan_ok.md" not in result.written
    assert [(f.path, f.kind) for f in result.failed] == [("plan_ok.md", "serialize-error")]


def test_the_plans_skipped_list_is_carried_into_the_result(tmp_path):
    root = tabled_copy(tmp_path)
    bundle = load_bundle(root)
    plan = plan_row(bundle, ["ghost"], "Plan", PLAN, ROW, key="action")
    assert apply(bundle, plan).skipped == plan.skipped


def test_the_corpus_itself_was_never_touched():
    """Every mutating test goes through `tabled_copy`. If this fails, one did
    not, and the corpus is now lying to every other test."""
    assert snapshot(TABLED) == _CORPUS_SNAPSHOT
