from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core.tag_policy.commands import (
    apply_phase,
    draft_disposition,
    plan_phase,
    undeclared,
)
from graph_works_core.tag_policy.disposition import loads
from okf_io import load_bundle

TODAY = date(2026, 9, 8)


def build(root: Path, pages: dict[str, list[str]]) -> Path:
    for concept_id, tags in pages.items():
        target = root / f"{concept_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = "".join(f"- {tag}\n" for tag in tags)
        target.write_text(
            f"---\ntype: Reference\ntitle: {concept_id}\ntags:\n{rendered}---\n\n# {concept_id}\n",
            encoding="utf-8",
            newline="",
        )
    return root


CORPUS = {
    "a": ["lint", "wiki-lint", "okf-io"],
    "b": ["lint", "okf-io"],
    "c": ["lint", "wiki-lint"],
    "d": ["lint"],
    "e": ["lint", "singleton"],
}

DISPOSITION = """\
generated: 2026-09-08
tagged_pages: 5
keep:
  - {tag: lint, uses: 5, reason: survivor}
merge:
  - {tag: wiki-lint, uses: 2, reason: semantic, into: lint}
strip:
  - {tag: okf-io, uses: 2, reason: entity-dup}
  - {tag: singleton, uses: 1, reason: below-floor}
"""


def test_draft_disposition_judges_the_whole_bundle(tmp_path):
    bundle = load_bundle(build(tmp_path, CORPUS))
    result = draft_disposition(bundle, generated=TODAY)
    assert {v.tag for v in result.verdicts} >= {"lint", "wiki-lint", "okf-io", "singleton"}
    assert result.generated == TODAY


def test_plan_phase_merge_only_touches_merge_entries(tmp_path):
    bundle = load_bundle(build(tmp_path, CORPUS))
    plan = plan_phase(bundle, loads(DISPOSITION, "d.yaml"), "merge")
    assert {e.old for e in plan.edits} == {"wiki-lint"}


def test_plan_phase_strip_only_touches_strip_entries(tmp_path):
    bundle = load_bundle(build(tmp_path, CORPUS))
    plan = plan_phase(bundle, loads(DISPOSITION, "d.yaml"), "strip")
    assert {e.old for e in plan.edits} == {"okf-io", "singleton"}
    assert all(e.new is None for e in plan.edits)


MULTI_TARGET_CORPUS = {
    "a": ["foo", "bar"],
    "b": ["foo"],
    "c": ["bar"],
}

MULTI_TARGET_DISPOSITION = """\
generated: 2026-09-08
tagged_pages: 3
keep:
  - {tag: keep-one, uses: 2, reason: survivor}
  - {tag: keep-two, uses: 2, reason: survivor}
merge:
  - {tag: foo, uses: 2, reason: semantic, into: keep-one}
  - {tag: bar, uses: 2, reason: semantic, into: keep-two}
"""


def test_plan_phase_merge_groups_sources_by_distinct_targets(tmp_path):
    """`plan_phase`'s merge grouping loop has only ever run with one merge
    target across the existing suite. With two distinct targets, each source
    tag must land on its own target and the edits must stay disjoint --
    every source position rewritten exactly once, to the right target."""
    bundle = load_bundle(build(tmp_path, MULTI_TARGET_CORPUS))
    plan = plan_phase(bundle, loads(MULTI_TARGET_DISPOSITION, "d.yaml"), "merge")

    by_old = {(e.concept_id, e.old): e.new for e in plan.edits}
    assert by_old == {
        ("a", "foo"): "keep-one",
        ("a", "bar"): "keep-two",
        ("b", "foo"): "keep-one",
        ("c", "bar"): "keep-two",
    }
    # Disjoint: one edit per (concept_id, index) position, no overlap.
    positions = [(e.concept_id, e.index) for e in plan.edits]
    assert len(positions) == len(set(positions))


def test_plan_phase_refuses_an_unknown_phase(tmp_path):
    bundle = load_bundle(build(tmp_path, CORPUS))
    with pytest.raises(ValueError, match="phase"):
        plan_phase(bundle, loads(DISPOSITION, "d.yaml"), "everything")  # type: ignore[arg-type]


def test_apply_merge_collapses_onto_the_target(tmp_path):
    root = build(tmp_path, CORPUS)
    bundle = load_bundle(root)
    result = apply_phase(bundle, loads(DISPOSITION, "d.yaml"), "merge")
    assert result.ok, result.failed
    assert load_bundle(root).concepts["a"].fm.tags == ("lint", "okf-io")
    assert load_bundle(root).concepts["c"].fm.tags == ("lint",)


def test_apply_strip_after_merge_is_planned_against_the_moved_positions(tmp_path):
    """The merge shifts `okf-io` in `a` from index 2 to index 1. Planning the
    strip before the merge lands would make it stale; `apply_phase` re-plans."""
    root = build(tmp_path, CORPUS)
    bundle = load_bundle(root)
    assert apply_phase(bundle, loads(DISPOSITION, "d.yaml"), "merge").ok
    result = apply_phase(bundle, loads(DISPOSITION, "d.yaml"), "strip")
    assert result.ok, result.failed
    assert load_bundle(root).concepts["a"].fm.tags == ("lint",)
    assert load_bundle(root).concepts["e"].fm.tags == ("lint",)


def test_undeclared_names_every_tag_missing_from_the_vocabulary(tmp_path):
    root = build(tmp_path / "b", CORPUS)
    vocab = tmp_path / "tags.yaml"
    vocab.write_text(
        "version: 1\ntags:\n  - name: lint\n    description: The lint machinery.\n",
        encoding="utf-8",
        newline="",
    )
    assert undeclared(load_bundle(root), vocab) == ("okf-io", "singleton", "wiki-lint")


def test_undeclared_is_empty_for_a_complete_vocabulary(tmp_path):
    root = build(tmp_path / "b", {"a": ["lint"]})
    vocab = tmp_path / "tags.yaml"
    vocab.write_text(
        "version: 1\ntags:\n  - name: lint\n    description: The lint machinery.\n",
        encoding="utf-8",
        newline="",
    )
    assert undeclared(load_bundle(root), vocab) == ()


def test_undeclared_counts_a_deprecated_tag_as_declared(tmp_path):
    """`gate` answers "is this tag known", not "is it approved". A deprecated
    entry is known, and `tags.deprecated` is the finding that reports it."""
    root = build(tmp_path / "b", {"a": ["kpi"]})
    vocab = tmp_path / "tags.yaml"
    vocab.write_text(
        "version: 1\ntags:\n"
        "  - name: metric\n    description: A measure.\n"
        "  - name: kpi\n    description: Retired.\n    deprecated: true\n    replaced_by: metric\n",
        encoding="utf-8",
        newline="",
    )
    assert undeclared(load_bundle(root), vocab) == ()
