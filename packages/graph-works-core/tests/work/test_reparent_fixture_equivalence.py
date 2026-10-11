"""Replay shipped reparent/adopt fixtures through partial planning and both lenses.

These are the existing fixture functions, with their original behavioral
assertions intact. Planner interception adds full/partial plan, rendered-byte,
and scoped finding comparisons rather than copying those fixture builders.
"""

from __future__ import annotations

import importlib.util
import shutil
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from _transaction_helpers import _workspace
from graph_works_core.work import commands
from graph_works_core.workspace import bundle as loaders
from graph_works_core.workspace import transactions
from okf_io import validate
from okf_io.bundle import walk
from work_tracker_okf import load_items
from work_tracker_okf.cli import _apply_mutation
from work_tracker_okf.items import ARCHIVE_IGNORE, IGNORE

FIXTURES = Path(__file__).resolve().parents[3] / "work-tracker-okf" / "tests"
CASES = [
    (name, {})
    for name in [
        "maps_every_descendant_and_registered_reference",
        "preserves_opaque_bytes_and_warns_only_for_old_paths",
        "refuses_descendant_parent_nested_release_and_leaf_parent",
        "refuses_a_nested_release_destination_parent",
        "refuses_destination_collisions_without_writes",
        "refuses_an_existing_owned_destination_directory",
        "preserves_empty_nested_directories_and_cleans_the_source",
        "preconditions_an_absent_implicit_mkdir_ancestor",
        "refuses_registered_malformed_markdown_but_moves_unregistered_malformed_opaque",
        "merges_source_destination_and_owned_lane_indexes",
        "moves_the_execute_coverage_artifact",
        "rebases_a_dangling_stamped_resource",
        "unchanged_lane_index_retains_digest_without_naming_itself_as_source",
        "registered_and_canonical_markdown_are_repaired_not_made_opaque",
        "external_registered_and_canonical_artifacts_are_repaired_under_ignore",
        "external_unregistered_artifact_stays_opaque_under_archive_ignore",
        "reparenting_a_relocating_child_lane_index_does_not_duplicate_its_entry",
        "symlink_escape_refuses_the_whole_subtree",
        "reparenting_a_parent_creates_no_archive_lane",
    ]
]
CASES += [
    (
        "registered_reserved_markdown_moves_with_structured_repair_under_every_lens",
        {"filename": filename, "ignore": ignore},
    )
    for filename in ["index.md", "log.md"]
    for ignore in [IGNORE, ARCHIVE_IGNORE]
]
CASES += [
    ("registers_every_canonical_equivalent_unreadable_reserved_member", {"filename": filename, "ambiguous": ambiguous})
    for filename in ["index.md", "log.md"]
    for ambiguous in [False, True]
]
CASES += [
    ("registered_reserved_aliases_resolve_canonical_equivalent_paths", {"ignore": ignore})
    for ignore in [IGNORE, ARCHIVE_IGNORE]
]
CASES += [
    ("refuses_invalid_utf8_registered_reserved_markdown_without_effects", {"filename": filename, "ignore": ignore})
    for filename in ["index.md", "log.md"]
    for ignore in [IGNORE, ARCHIVE_IGNORE]
]
CASES += [
    ("adoption_moves_one_root_subtree_and_keeps_release_at_root", {}),
    ("adoption_refuses_non_release_and_non_root_sources", {}),
]


def _scoped_findings(layout, bundle, scope):
    report = validate(bundle, today=date.max, extra_rules=transactions._extra_rules(layout, None), scope=scope)
    return Counter((f.path, f.code, f.severity, f.message) for f in report.findings if f.path in scope)


def _postcondition_comparison(layout, partial, plan, scratch):
    if not plan.ok:
        return
    shutil.copytree(plan.root, scratch)
    copied_plan = replace(plan, root=scratch)
    _apply_mutation(copied_plan)
    copied_layout = replace(layout, bundle_dir=scratch)
    full = loaders.load_workspace_bundle(copied_layout, ignore=IGNORE)
    if partial.partial is None:
        fresh = loaders.load_workspace_bundle(copied_layout, ignore=IGNORE)
    else:
        mapped = {transactions._map_member(plan.path_mapping, member) for member in partial.partial}
        mapped.update(write.member for write in plan.writes)
        inventory = walk(scratch, prune=loaders.with_clone_prune())
        fresh = loaders.load_partial_bundle(scratch, only=mapped, inventory=inventory, ignore=IGNORE)
    scope = transactions._gate_scope(plan)
    assert _scoped_findings(copied_layout, fresh, scope) == _scoped_findings(copied_layout, full, scope)
    for write in plan.writes:
        assert (scratch / write.member).read_bytes() == write.after


@pytest.mark.parametrize("name,kwargs", CASES, ids=[f"{name}-{i}" for i, (name, _) in enumerate(CASES)])
def test_existing_fixtures_match_partial_planning_and_validation(tmp_path, monkeypatch, name, kwargs):
    monkeypatch.syspath_prepend(str(FIXTURES))
    spec = importlib.util.spec_from_file_location("reparent_oracle_fixtures", FIXTURES / "test_reparent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    validation_layout = _workspace(tmp_path / "metadata")
    original_load = module.load_bundle
    lens = []
    metadata_overrides = {}
    comparisons = []

    def record_load(root, **options):
        lens[:] = options.get("ignore", ())
        return original_load(root, **options)

    monkeypatch.setattr(module, "load_bundle", record_load)
    if name == "registers_every_canonical_equivalent_unreadable_reserved_member":
        # The shipped ambiguity fixture injects a second raw spelling even on
        # APFS. Replay that same metadata transformation on both corpora.
        def record_replace(value, **changes):
            metadata_overrides.update(changes)
            return replace(value, **changes)

        monkeypatch.setattr(module, "replace", record_replace)

    def intercept(planner):
        def compare(bundle, items, source_path, parent_path):
            source, parent = source_path, parent_path
            oracle = planner(bundle, items, source, parent)
            layout = replace(validation_layout, bundle_dir=bundle.root)
            partial, _ = commands._plan_reparent_partial(layout, [source], parent)
            selected = loaders.reclassify_ignored(partial, lens)
            selected = replace(selected, **metadata_overrides)
            plan = planner(selected, load_items(selected), source, parent)
            assert plan == oracle
            baseline_scope = transactions._baseline_scope(plan)
            baseline = loaders.reclassify_ignored(selected, IGNORE)
            full = loaders.load_workspace_bundle(layout, ignore=IGNORE)
            assert _scoped_findings(layout, baseline, baseline_scope) == _scoped_findings(layout, full, baseline_scope)
            scratch = tmp_path / f"postcondition-{len(comparisons)}"
            _postcondition_comparison(layout, selected, plan, scratch)
            comparisons.append(plan)
            return plan

        return compare

    monkeypatch.setattr(module, "plan_reparent", intercept(module.plan_reparent))
    monkeypatch.setattr(module, "plan_release_adoption", intercept(module.plan_release_adoption))
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    prefix = "test_release_" if name.startswith("adoption_") else "test_reparent_"
    # A few shipped fixture names start with registered_ rather than reparent_.
    function_name = f"{prefix}{name}"
    if not hasattr(module, function_name):
        function_name = f"test_{name}"
    getattr(module, function_name)(fixture_root, **kwargs)
    assert comparisons
