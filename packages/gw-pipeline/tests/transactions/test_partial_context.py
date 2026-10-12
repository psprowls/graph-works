"""Independent partial-corpus engine checks using authored members and lower-band plans."""

from __future__ import annotations

from dataclasses import replace

import pytest
from gw_kernel.testing.transactions import snapshot, workspace
from gw_kernel.workspace import bundle as loaders
from gw_pipeline import transactions
from gw_pipeline.testing import forced_tier
from okf_io import Document
from okf_io.bundle import walk
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.reparent import plan_reparent_many


def write(layout, path, *, kind="Bug", target=None):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    dependency = "" if target is None else f"depends_on:\n- path: {target}\n  blocks: execute\n  needs: resolved\n"
    page.write_text(
        f"---\ntitle: {path}\ndescription: D\ntype: {kind}\nstatus: stable\n"
        "work_status: open\nphase: execute\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        f"{dependency}---\n\n## Summary\nD\n",
        encoding="utf-8",
        newline="\n",
    )


def scenario(tmp_path):
    layout = workspace(tmp_path)
    for path in ["work/left", "work/destination", "work/far"]:
        write(layout, path, kind="Epic")
    for path in ["work/left/children/bug-source", "work/left/children/bug-sibling", "work/far/children/bug-target"]:
        write(layout, path)
    full = loaders.load_workspace_bundle(layout, ignore=())
    plan = plan_reparent_many(full, load_items(full), ["work/left/children/bug-source"], "work/destination")
    # This fixture names its own admitted corpus; it does not exercise the work planner's touched scanner.
    admitted = frozenset(
        {
            "work/index.md",
            "work/left.md",
            "work/destination.md",
            "work/left/children/bug-source.md",
            "work/left/children/bug-sibling.md",
        }
    )
    baseline = loaders.load_partial_bundle(
        layout.bundle_dir,
        only=admitted,
        ignore=IGNORE,
        inventory=walk(layout.bundle_dir, prune=loaders.with_clone_prune()),
    )
    return layout, baseline, plan


@pytest.mark.parametrize("tier", ["linux", "win32"])
def test_partial_apply_reuses_clean_destination_and_parses_only_changed_members(tmp_path, monkeypatch, tier):
    layout, baseline, plan = scenario(tmp_path)
    parsed, observed = [], []
    original_parse, original_validate = Document.parse, transactions.validate

    def parse(cls, text, *, path=None):
        parsed.append(path.relative_to(layout.bundle_dir).as_posix())
        return original_parse(text, path=path)

    def validate(bundle, **options):
        observed.append(bundle)
        return original_validate(bundle, **options)

    monkeypatch.setattr(Document, "parse", classmethod(parse))
    monkeypatch.setattr(transactions, "validate", validate)
    with forced_tier(tier):
        application = transactions.apply_mutation(layout, plan, baseline_bundle=baseline, commit=None)
    assert application.ok, application.failures
    assert observed[0] is baseline
    assert observed[1].partial is not None
    assert observed[1].concepts["work/destination"] is baseline.concepts["work/destination"]
    expected = {write.member for write in plan.writes} | {f"{dest}.md" for dest in plan.path_mapping.values()}
    assert set(parsed) == expected
    assert len(parsed) == len(expected)


@pytest.mark.parametrize("change", ["dependency", "new-lane-member", "new-unrelated-member"])
def test_postcondition_context_completes_only_escaping_dependency_or_lane(tmp_path, monkeypatch, change):
    layout, baseline, plan = scenario(tmp_path)
    original = transactions._validate_postconditions
    original_validate = transactions.validate
    observed = []

    def changed(*args, **kwargs):
        if change == "dependency":
            page = layout.bundle_dir / "work/destination/children/bug-source.md"
            page.write_text(
                page.read_text(encoding="utf-8").replace(
                    "---\n",
                    "---\ndepends_on:\n- path: work/far/children/bug-target\n  blocks: execute\n  needs: resolved\n",
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
        elif change == "new-lane-member":
            write(layout, "work/destination/children/bug-new")
        else:
            write(layout, "work/far/children/bug-new")
        return original(*args, **kwargs)

    def validate(bundle, **options):
        observed.append(bundle)
        return original_validate(bundle, **options)

    monkeypatch.setattr(transactions, "_validate_postconditions", changed)
    monkeypatch.setattr(transactions, "validate", validate)
    application = transactions.apply_mutation(layout, plan, baseline_bundle=baseline, commit=None)
    assert application.ok == (change != "new-lane-member"), application.failures
    assert application.rolled_back == (change == "new-lane-member")
    assert (observed[1].partial is None) == (change != "new-unrelated-member")
    if change == "new-lane-member":
        assert transactions.only_stale_inventory(application)
    elif change == "dependency":
        assert "work/far/children/bug-target" in observed[1].concepts
    else:
        assert "work/far/children/bug-new.md" in observed[1].unloaded


def test_partial_baseline_missing_selected_source_refuses_before_writing(tmp_path):
    layout, baseline, plan = scenario(tmp_path)
    member = "work/left/children/bug-source.md"
    baseline = replace(baseline, partial=baseline.partial - {member}, unloaded=baseline.unloaded | {member})
    before = snapshot(layout.bundle_dir)
    with pytest.raises(ValueError, match="partial baseline does not cover the baseline scope"):
        transactions.apply_mutation(layout, plan, baseline_bundle=baseline, commit=None)
    assert snapshot(layout.bundle_dir) == before


def test_postcondition_selection_discards_dirty_written_and_deleted_seeds(tmp_path):
    _layout, baseline, plan = scenario(tmp_path)
    baseline.concepts["work/left"].mark_dirty()
    plan = replace(plan, deletes=("work/left/children/bug-sibling.md",))
    only, reuse = transactions._postcondition_load_set(plan, baseline)
    assert "work/destination/children/bug-source.md" in only
    assert "work/left/children/bug-source.md" not in only
    assert "work/left/children/bug-sibling.md" not in only
    assert not {w.member for w in plan.writes}.intersection(reuse)
    assert "work/left.md" not in reuse
    assert "work/left/children/bug-sibling.md" not in reuse
    assert reuse["work/destination.md"] is baseline.concepts["work/destination"]
    with pytest.raises(ValueError, match="requires a partial baseline"):
        transactions._postcondition_load_set(plan, replace(baseline, partial=None))


def test_partial_context_falls_back_to_full_for_directory_failure_or_new_inventory(tmp_path, monkeypatch):
    from contextlib import closing

    layout, baseline, _plan = scenario(tmp_path)
    with closing(transactions._open_root(layout.bundle_dir)) as root:
        unreadable = replace(baseline, unreadable_dirs={"docs": "denied"})
        assert transactions._complete_postcondition_context(root, layout.bundle_dir, unreadable).partial is None
        page = layout.bundle_dir / "work/left/children/bug-source.md"
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                "---\n",
                "---\ndepends_on:\n- path: work/far/children/bug-target\n  blocks: execute\n  needs: resolved\n",
                1,
            ),
            encoding="utf-8",
            newline="\n",
        )
        baseline = loaders.load_partial_bundle(
            layout.bundle_dir,
            only=baseline.partial,
            ignore=IGNORE,
            inventory=walk(layout.bundle_dir, prune=loaders.with_clone_prune()),
        )
        original = transactions._load_bundle_through
        calls = []

        def load(*args, **kwargs):
            calls.append(kwargs.get("only"))
            if kwargs.get("only") is not None:
                write(layout, "work/far/children/bug-new")
            return original(*args, **kwargs)

        monkeypatch.setattr(transactions, "_load_bundle_through", load)
        completed = transactions._complete_postcondition_context(root, layout.bundle_dir, baseline)
        assert completed.partial is None and not completed.unloaded
        assert "work/far/children/bug-new" in completed.concepts
        assert len(calls) == 2 and calls[1] is None


def test_owned_artifact_requires_fresh_lane_and_ancestor_context(tmp_path):
    from contextlib import closing

    from work_tracker_okf.mutation import PlannedWrite

    layout, baseline, _plan = scenario(tmp_path)
    sibling = "work/left/children/bug-sibling.md"
    baseline = loaders.load_partial_bundle(
        layout.bundle_dir,
        only=baseline.partial - {sibling},
        ignore=IGNORE,
        inventory=walk(layout.bundle_dir, prune=loaders.with_clone_prune()),
    )
    mutation = transactions.WorkMutationPlan(
        layout.bundle_dir,
        "file",
        {},
        None,
        (),
        (PlannedWrite("work/left/children/bug-source/references/01-design.md", None, b"authored"),),
        (),
        (),
        (),
        (),
        (),
    )
    with closing(transactions._open_root(layout.bundle_dir)) as root:
        completed = transactions._complete_postcondition_context(root, layout.bundle_dir, baseline, plan=mutation)
    assert completed.partial is None
    assert "work/left/children/bug-sibling" in completed.concepts


@pytest.mark.parametrize("fault", ["reload", "setup", "invalid-path", "index-read"])
def test_absolute_postcondition_failures_report_specific_unverified_condition(tmp_path, monkeypatch, fault):
    from gw_kernel.testing.transactions import plan as authored_plan
    from work_tracker_okf.mutation import PlannedWrite

    layout, _baseline, _plan = scenario(tmp_path)
    mutation = authored_plan(layout, validate_paths=("not/work", "work/missing"))
    if fault == "reload":
        monkeypatch.setattr(
            transactions,
            "_load_bundle_through",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("reload denied")),
        )
    elif fault == "setup":
        monkeypatch.setattr(
            transactions, "_extra_rules", lambda *args: (_ for _ in ()).throw(ValueError("schema denied"))
        )
    elif fault == "index-read":
        mutation = authored_plan(layout, writes=(PlannedWrite("work/index.md", None, b"index"),))
        monkeypatch.setattr(
            transactions, "_read_bytes_at", lambda *args: (_ for _ in ()).throw(OSError("index denied"))
        )
    failures = transactions._validate_postconditions(layout, mutation)
    expected = {
        "reload": "postcondition reload failed",
        "setup": "postcondition validation setup failed",
        "invalid-path": "validate path is not canonical",
        "index-read": "postcondition read failed",
    }[fault]
    assert any(expected in failure for failure in failures)
    if fault == "invalid-path":
        assert any("final work item did not reload" in failure for failure in failures)
