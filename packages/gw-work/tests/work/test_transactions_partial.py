"""Real locked partial applications compared with the unchanged full-load gate."""

from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import replace
from datetime import date

import pytest
import test_reparent_fixture_equivalence as fixtures
from gw_kernel.testing.transactions import snapshot as _snapshot
from gw_kernel.testing.transactions import workspace as _workspace
from gw_kernel.workspace import bundle as loaders
from gw_pipeline import transactions
from gw_pipeline.testing import forced_tier as _forced_tier
from gw_work import commands
from okf_io import validate
from test_reparent_partial import _write
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.reparent import plan_reparent_many


def _layout(tmp_path):
    layout = _workspace(tmp_path)
    for name in ["left", "destination", "far", "third"]:
        _write(layout, f"work/{name}", kind="Epic")
    _write(layout, "work/left/children/bug-moved")
    _write(layout, "work/left/children/bug-sibling")
    _write(layout, "work/far/children/bug-target")
    _write(layout, "work/third/children/bug-third")
    return layout


def _add_edge(layout, path, target):
    page = layout.bundle_dir / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    edge = f"depends_on:\n- path: {target}\n  blocks: execute\n  needs: resolved\n"
    page.write_text(text.replace("---\n", f"---\n{edge}", 1), encoding="utf-8", newline="")


def _findings(layout, bundle, scope):
    report = validate(bundle, today=date.max, extra_rules=transactions._extra_rules(layout, None), scope=scope)
    return Counter((f.path, f.code, f.severity, f.message) for f in report.findings if f.path in scope)


def _outcome(application):
    # Full-context planning has one explicit informational fallback warning.
    warnings = tuple(w for w in application.warnings if not w.startswith("dependency context reaches unloaded"))
    return application.failures, application.rolled_back, warnings, application.moved, application.written


def test_partial_baseline_missing_scope_is_refused(tmp_path):
    layout = _layout(tmp_path)
    baseline, plan = commands._plan_reparent_partial(layout, ["work/left/children/bug-moved"], "work/destination")
    baseline = loaders.reclassify_ignored(baseline, IGNORE)
    member = "work/left/children/bug-moved.md"
    starved = replace(
        baseline,
        concepts={k: d for k, d in baseline.concepts.items() if f"{k}.md" != member},
        partial=baseline.partial - {member},
        unloaded=baseline.unloaded | {member},
    )
    with pytest.raises(ValueError, match="partial baseline does not cover the baseline scope"):
        transactions.apply_mutation(layout, plan, baseline_bundle=starved, commit=None)


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_live_batch_partial_apply_matches_full_and_retains_fresh_document_identity(tmp_path, monkeypatch, tier):
    applications, snapshots = [], []
    for variant in ["full", "partial"]:
        layout = _layout(tmp_path / variant)
        sources = ["work/left/children/bug-moved", "work/left/children/bug-sibling"]
        bundle, _plan = commands._plan_reparent_partial(layout, sources, "work/destination")
        baseline = loaders.reclassify_ignored(bundle, IGNORE)
        observed = []
        original = transactions.validate

        def record(bundle, observed=observed, original=original, **kwargs):
            observed.append(bundle)
            return original(bundle, **kwargs)

        with monkeypatch.context() as patch, _forced_tier(tier):
            patch.setattr(transactions, "validate", record)
            if variant == "partial":
                # Avoid unrelated Git integration; commit=None still exercises
                # the durable transaction with real anchored reads and writes.
                original_apply = commands.apply_mutation
                patch.setattr(
                    commands,
                    "apply_mutation",
                    lambda *a, original_apply=original_apply, **kw: original_apply(*a, **(kw | {"commit": None})),
                )
                result = commands.run_reparent(layout, sources, "work/destination", dry_run=False)
                application = result.application
            else:
                full = loaders.load_workspace_bundle(layout, ignore=())
                oracle = plan_reparent_many(full, load_items(full), sources, "work/destination")
                application = transactions.apply_mutation(layout, oracle, commit=None)
        assert application.ok, application.failures
        if variant == "partial":
            assert observed[0].partial is not None and observed[1].partial is not None
            assert "work/far/children/bug-target.md" in observed[1].unloaded
            assert observed[1].concepts["work/destination"] is observed[0].concepts["work/destination"]
            assert (
                observed[1].concepts["work/destination/children/bug-moved"]
                is not baseline.concepts["work/left/children/bug-moved"]
            )
        applications.append(_outcome(application))
        snapshots.append(_snapshot(layout.bundle_dir))
    assert applications[0] == applications[1]
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize("variant", ["remote", "cycle", "terminal", "phase-compatible", "absent", "unreadable"])
def test_dependency_application_matches_full_findings_notes_and_bytes(tmp_path, variant):
    results, snapshots = [], []
    for mode in ["full", "partial"]:
        layout = _layout(tmp_path / mode)
        source = "work/left/children/bug-moved"
        target = "work/far/children/bug-target"
        third = "work/third/children/bug-third"
        _write(layout, source, target=target)
        if variant in {"cycle", "terminal", "phase-compatible"}:
            _write(layout, target, target=third, terminal=variant == "terminal")
            _write(layout, third, target=source, needs="design" if variant == "phase-compatible" else "resolved")
        if variant == "absent":
            (layout.bundle_dir / f"{target}.md").unlink()
        if variant == "unreadable":
            (layout.bundle_dir / f"{target}.md").write_bytes(b"\xffnot UTF-8")
        bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
        baseline = loaders.reclassify_ignored(bundle, IGNORE)
        full = loaders.load_workspace_bundle(layout, ignore=IGNORE)
        assert _findings(layout, baseline, transactions._baseline_scope(plan)) == _findings(
            layout, full, transactions._baseline_scope(plan)
        )
        application = transactions.apply_mutation(
            layout,
            plan,
            baseline_bundle=baseline if mode == "partial" and baseline.partial is not None else None,
            commit=None,
        )
        assert application.ok == (variant != "unreadable"), application.failures
        assert application.rolled_back == (variant == "unreadable")
        results.append(_outcome(application))
        snapshots.append(_snapshot(layout.bundle_dir))
    assert results[0] == results[1]
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize("edit", ["title", "new-edge", "new-cycle", "unreadable-target"])
@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_concurrent_context_edit_matches_full_locked_oracle(tmp_path, monkeypatch, edit, tier):
    results, snapshots, findings = [], [], []
    for variant in ["full", "partial"]:
        layout = _layout(tmp_path / variant)
        source = "work/left/children/bug-moved"
        sibling = "work/left/children/bug-sibling"
        target = "work/far/children/bug-target"
        if edit == "new-cycle":
            _write(layout, target, target=sibling)
        if edit == "unreadable-target":
            (layout.bundle_dir / f"{target}.md").write_bytes(b"\xffnot UTF-8")
        bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
        baseline = loaders.reclassify_ignored(bundle, IGNORE)
        assert baseline.partial is not None
        original = transactions._apply_mutation_locked
        original_validate = transactions.validate
        original_load = transactions._load_bundle_through
        observed, loads = [], []

        def record_load(*args, original_load=original_load, loads=loads, **kwargs):
            loaded = original_load(*args, **kwargs)
            loads.append(loaded)
            return loaded

        def record(bundle, observed=observed, original_validate=original_validate, **kwargs):
            observed.append(bundle)
            return original_validate(bundle, **kwargs)

        def edit_then_apply(*args, layout=layout, sibling=sibling, target=target, original=original, **kwargs):
            if edit == "title":
                page = layout.bundle_dir / f"{sibling}.md"
                page.write_text(
                    page.read_text(encoding="utf-8").replace(f"title: {sibling}", "title: Renamed"),
                    encoding="utf-8",
                    newline="",
                )
            else:
                _write(layout, sibling, target=target)
            return original(*args, **kwargs)

        with monkeypatch.context() as patch, _forced_tier(tier):
            patch.setattr(transactions, "_apply_mutation_locked", edit_then_apply)
            patch.setattr(transactions, "validate", record)
            patch.setattr(transactions, "_load_bundle_through", record_load)
            application = transactions.apply_mutation(
                layout,
                plan,
                baseline_bundle=baseline if variant == "partial" else None,
                commit=None,
            )
        # A reparent no longer writes lane indexes, so a renamed sibling is not a stale inventory.
        assert application.ok, application.failures
        assert len(observed) == 2
        if variant == "partial":
            assert observed[0] is baseline
            assert loads[0].partial is not None
            assert observed[1].concepts[sibling] is not baseline.concepts[sibling]
            if edit in {"title", "unreadable-target"}:
                assert observed[1].partial is not None
                if edit == "unreadable-target":
                    by_path = {item.path: item for item in load_items(observed[1])}
                    assert any(
                        kind == "dependency-missing"
                        for kind, _ in transactions._item_conditions(by_path[sibling], by_path, observed[1])
                    )
            else:
                assert observed[1].partial is None and not observed[1].unloaded
                assert len(loads) == 2
                assert observed[1].concepts["work/destination"] is loads[0].concepts["work/destination"]
        findings.append(_findings(layout, observed[1], transactions._gate_scope(plan)))
        results.append(_outcome(application))
        snapshots.append(_snapshot(layout.bundle_dir))
    assert results[0] == results[1]
    assert snapshots[0] == snapshots[1]
    assert findings[0] == findings[1]


def test_postcondition_selection_excludes_moved_written_deleted_and_dirty_reuse(tmp_path):
    layout = _layout(tmp_path)
    baseline, plan = commands._plan_reparent_partial(layout, ["work/left/children/bug-moved"], "work/destination")
    baseline = loaders.reclassify_ignored(baseline, IGNORE)
    baseline.concepts["work/left"].mark_dirty()
    plan = replace(plan, deletes=("work/left/children/bug-sibling.md",))
    only, reuse = transactions._postcondition_load_set(plan, baseline)
    assert "work/destination/children/bug-moved.md" in only
    assert "work/left/children/bug-moved.md" not in only
    assert "work/left/children/bug-sibling.md" not in only
    assert set(write.member for write in plan.writes) <= only
    assert "work/left/children/bug-moved.md" not in reuse
    assert "work/destination/children/bug-moved.md" not in reuse
    assert "work/left.md" not in reuse
    assert "work/left/children/bug-sibling.md" not in reuse
    assert not set(write.member for write in plan.writes).intersection(reuse)
    assert reuse["work/destination.md"] is baseline.concepts["work/destination"]


@pytest.mark.parametrize(
    "name,kwargs", fixtures.CASES, ids=[f"{name}-{i}" for i, (name, _) in enumerate(fixtures.CASES)]
)
def test_shipped_fixtures_match_real_partial_transaction(tmp_path, monkeypatch, name, kwargs):
    """Pin F2 against actual writes, failures, notes and both scoped reports."""
    original_compare = fixtures._postcondition_comparison

    def durable_comparison(layout, partial, plan, scratch):
        original_compare(layout, partial, plan, scratch)
        if not plan.ok:
            return
        outcomes, snapshots, findings = [], [], []
        for variant in ["full", "partial"]:
            copied_root = scratch.parent / f"{scratch.name}-transaction-{variant}"
            shutil.copytree(plan.root, copied_root, symlinks=True)
            copied_layout = replace(layout, bundle_dir=copied_root)
            copied_plan = replace(plan, root=copied_root)
            baseline = replace(loaders.reclassify_ignored(partial, IGNORE), root=copied_root)
            reports = []
            original_validate = transactions.validate

            def record(bundle, original_validate=original_validate, reports=reports, **options):
                report = original_validate(bundle, **options)
                scope = options["scope"]
                reports.append(
                    Counter((f.path, f.code, f.severity, f.message) for f in report.findings if f.path in scope)
                )
                return report

            with monkeypatch.context() as patch:
                patch.setattr(transactions, "validate", record)
                application = transactions.apply_mutation(
                    copied_layout,
                    copied_plan,
                    baseline_bundle=baseline if variant == "partial" and baseline.partial is not None else None,
                    commit=None,
                )
            outcomes.append(_outcome(application))
            snapshots.append(_snapshot(copied_root))
            findings.append(reports)
            if application.ok:
                assert all((copied_root / write.member).read_bytes() == write.after for write in copied_plan.writes)
        assert outcomes[0] == outcomes[1]
        assert snapshots[0] == snapshots[1]
        assert findings[0] == findings[1]

    monkeypatch.setattr(fixtures, "_postcondition_comparison", durable_comparison)
    fixtures.test_existing_fixtures_match_partial_planning_and_validation(tmp_path, monkeypatch, name, kwargs)
    # Refused fixtures intentionally have no durable application.


@pytest.mark.parametrize("cycle", [False, True])
@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_new_edge_on_moved_item_after_effects_matches_full_oracle(tmp_path, monkeypatch, cycle, tier):
    """A new in-scope cycle must fail exactly as the complete graph does."""
    outcomes, snapshots, findings = [], [], []
    for variant in ["full", "partial"]:
        layout = _layout(tmp_path / variant)
        source, destination = "work/left/children/bug-moved", "work/destination/children/bug-moved"
        target = "work/far/children/bug-target"
        bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
        baseline = loaders.reclassify_ignored(bundle, IGNORE)
        original = transactions._validate_postconditions
        original_validate = transactions.validate
        observed = []

        def edit_then_validate(
            *args, layout=layout, destination=destination, target=target, original=original, **kwargs
        ):
            _add_edge(layout, destination, target)
            if cycle:
                _add_edge(layout, target, destination)
            return original(*args, **kwargs)

        def record(bundle, observed=observed, original_validate=original_validate, **kwargs):
            observed.append(bundle)
            return original_validate(bundle, **kwargs)

        with monkeypatch.context() as patch, _forced_tier(tier):
            patch.setattr(transactions, "_validate_postconditions", edit_then_validate)
            patch.setattr(transactions, "validate", record)
            application = transactions.apply_mutation(
                layout, plan, baseline_bundle=baseline if variant == "partial" else None, commit=None
            )
        assert application.ok == (not cycle), application.failures
        assert application.rolled_back == cycle
        if cycle:
            assert any("graph.depends-on-cycle" in failure for failure in application.failures)
        if variant == "partial":
            assert observed[1].partial is None and not observed[1].unloaded
        outcomes.append(_outcome(application))
        snapshots.append(_snapshot(layout.bundle_dir))
        findings.append(_findings(layout, observed[1], transactions._gate_scope(plan)))
    assert outcomes[0] == outcomes[1]
    assert snapshots[0] == snapshots[1]
    assert findings[0] == findings[1]


def test_partial_descriptor_reload_cannot_be_redirected_by_root_replacement(tmp_path):
    import sys

    if sys.platform == "win32":
        pytest.skip("descriptor-anchor security requires POSIX")
    layout = _layout(tmp_path)
    bundle, plan = commands._plan_reparent_partial(layout, ["work/left/children/bug-moved"], "work/destination")
    baseline = loaders.reclassify_ignored(bundle, IGNORE)
    only, reuse = transactions._postcondition_load_set(replace(plan, path_mapping={}, writes=(), deletes=()), baseline)
    anchor = transactions._open_root(layout.bundle_dir)
    try:
        retained = layout.bundle_dir.with_name("original-okf")
        layout.bundle_dir.rename(retained)
        layout.bundle_dir.mkdir()
        _write(layout, "work/destination", kind="Epic", target="work/attacker")
        fresh = transactions._load_bundle_through(anchor, layout.bundle_dir, ignore=IGNORE, only=only, reuse=reuse)
        assert fresh.concepts["work/destination"] is baseline.concepts["work/destination"]
        assert not fresh.concepts["work/destination"].fm_data(dates="iso").get("depends_on")
        with pytest.raises(ValueError, match="bundle root changed"):
            transactions._assert_root_identity(layout.bundle_dir, anchor)
    finally:
        anchor.close()


def test_dependency_completion_does_not_stamp_a_changed_inventory_full(tmp_path):
    layout = _layout(tmp_path)
    bundle, plan = commands._plan_reparent_partial(layout, ["work/left/children/bug-moved"], "work/destination")
    baseline = loaders.reclassify_ignored(bundle, IGNORE)
    _add_edge(layout, "work/left/children/bug-sibling", "work/far/children/bug-target")
    anchor = transactions._open_root(layout.bundle_dir)
    try:
        fresh = transactions._load_bundle_through(anchor, layout.bundle_dir, ignore=IGNORE, only=baseline.partial)
        assert "work/far/children/bug-target.md" in fresh.unloaded
        # This file appears after the initial locked reload inventoried members.
        _write(layout, "work/third/children/bug-new", target="work/far/children/bug-target")
        complete = transactions._complete_postcondition_context(anchor, layout.bundle_dir, fresh)
        full = loaders.load_workspace_bundle(layout, ignore=IGNORE)
        assert complete.partial is None and not complete.unloaded
        assert "work/third/children/bug-new" in complete.concepts
        assert _findings(layout, complete, transactions._baseline_scope(plan)) == _findings(
            layout, full, transactions._baseline_scope(plan)
        )
    finally:
        anchor.close()


def test_partial_reload_with_an_unreadable_directory_retains_full_loader_diagnostics(tmp_path):
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("POSIX unreadable-directory fixture")
    layout = _layout(tmp_path)
    bundle, plan = commands._plan_reparent_partial(layout, ["work/left/children/bug-moved"], "work/destination")
    baseline = loaders.reclassify_ignored(bundle, IGNORE)
    hidden = layout.bundle_dir / "docs" / "locked.md"
    hidden.mkdir(parents=True)
    hidden.chmod(0)
    anchor = transactions._open_root(layout.bundle_dir)
    try:
        fresh = transactions._load_bundle_through(anchor, layout.bundle_dir, ignore=IGNORE, only=baseline.partial)
        if os.access(hidden, os.R_OK):
            pytest.skip("host grants read access to mode-000 directories")
        assert "docs/locked.md" in fresh.unreadable_dirs
        complete = transactions._complete_postcondition_context(anchor, layout.bundle_dir, fresh)
        assert complete.partial is None and not complete.unloaded
        assert "docs/locked.md" in complete.unreadable_dirs
        full = transactions._load_bundle_through(anchor, layout.bundle_dir, ignore=IGNORE)
        assert complete.unreadable == full.unreadable
        assert _findings(layout, complete, transactions._baseline_scope(plan)) == _findings(
            layout, full, transactions._baseline_scope(plan)
        )
    finally:
        anchor.close()
        hidden.chmod(0o755)


@pytest.mark.parametrize("addition", ["source-sibling", "destination-sibling", "ancestor-child", "unrelated"])
@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_new_context_member_after_effects_matches_full_snapshot_oracle(tmp_path, monkeypatch, addition, tier):
    outcomes, snapshots, findings = [], [], []
    for variant in ["full", "partial"]:
        layout = _layout(tmp_path / variant)
        source = "work/left/children/bug-moved"
        if addition == "ancestor-child":
            # The grandparent's direct-child lane is required context even
            # though this move does not write that lane's index.
            _write(layout, "work/outer", kind="Epic")
            _write(layout, "work/outer/children/inner", kind="Epic")
            source = "work/outer/children/inner/children/bug-moved"
            _write(layout, source)
        new_path = {
            "source-sibling": "work/left/children/bug-new",
            "destination-sibling": "work/destination/children/bug-new",
            "ancestor-child": "work/outer/children/bug-new",
            "unrelated": "work/third/children/bug-new",
        }[addition]
        bundle, plan = commands._plan_reparent_partial(layout, [source], "work/destination")
        baseline = loaders.reclassify_ignored(bundle, IGNORE)
        assert baseline.partial is not None
        original_postcondition = transactions._validate_postconditions
        original_validate = transactions.validate
        original_load = transactions._load_bundle_through
        observed, loads = [], []

        def add_then_validate(*args, layout=layout, new_path=new_path, original=original_postcondition, **kwargs):
            _write(layout, new_path)
            return original(*args, **kwargs)

        def record(bundle, observed=observed, original=original_validate, **kwargs):
            observed.append(bundle)
            return original(bundle, **kwargs)

        def record_load(*args, loads=loads, original=original_load, **kwargs):
            loaded = original(*args, **kwargs)
            loads.append(loaded)
            return loaded

        with monkeypatch.context() as patch, _forced_tier(tier):
            patch.setattr(transactions, "_validate_postconditions", add_then_validate)
            patch.setattr(transactions, "validate", record)
            patch.setattr(transactions, "_load_bundle_through", record_load)
            application = transactions.apply_mutation(
                layout, plan, baseline_bundle=baseline if variant == "partial" else None, commit=None
            )
        # A reparent no longer writes lane indexes, so a new sibling is never a stale inventory.
        assert application.ok, application.failures
        assert len(observed) == 2
        if variant == "partial":
            assert loads[0].partial is not None
            assert f"{new_path}.md" in loads[0].unloaded
            if addition == "unrelated":
                assert observed[1].partial is not None
                assert f"{new_path}.md" in observed[1].unloaded
                assert new_path not in observed[1].concepts
                assert len(loads) == 1
            else:
                assert observed[1].partial is None and not observed[1].unloaded
                assert new_path in observed[1].concepts
                assert len(loads) == 2
                assert observed[1].concepts["work/destination"] is loads[0].concepts["work/destination"]
        outcomes.append(_outcome(application))
        snapshots.append(_snapshot(layout.bundle_dir))
        findings.append(_findings(layout, observed[1], transactions._gate_scope(plan)))
    assert outcomes[0] == outcomes[1]
    assert snapshots[0] == snapshots[1]
    assert findings[0] == findings[1]
