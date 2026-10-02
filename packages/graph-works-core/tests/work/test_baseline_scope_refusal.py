"""A work-scoped bundle cannot supply the mutation gate's whole-bundle baseline."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _transaction_helpers import _plan, _snapshot, _workspace
from graph_works_core.workspace import transactions
from graph_works_core.workspace.bundle import load_work_bundle, load_workspace_bundle
from work_tracker_okf.items import IGNORE


def test_apply_mutation_refuses_a_work_scoped_baseline(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "docs").mkdir()
    scoped = load_work_bundle(layout)
    assert "docs" in scoped.pruned
    before = _snapshot(layout.root)
    with pytest.raises(ValueError, match="pruned beyond the clone glob: docs"):
        transactions.apply_mutation(layout, _plan(layout), commit=None, baseline_bundle=scoped)
    assert _snapshot(layout.root) == before


def test_apply_mutation_refuses_before_lock_acquisition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "docs").mkdir()
    scoped = load_work_bundle(layout)

    def unexpected_lock(*args: object, **kwargs: object) -> None:
        pytest.fail("scoped baseline reached bundle lock acquisition")

    monkeypatch.setattr(transactions, "_bundle_root_lock", unexpected_lock)
    with pytest.raises(ValueError, match="pruned beyond the clone glob: docs"):
        transactions.apply_mutation(layout, _plan(layout), commit=None, baseline_bundle=scoped)


def test_capture_validation_state_refuses_a_supplied_work_scoped_bundle(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "docs").mkdir()
    scoped = load_work_bundle(layout)
    root = transactions._open_root(layout.bundle_dir)
    try:
        with pytest.raises(ValueError, match="pruned beyond the clone glob: docs"):
            transactions._capture_validation_state(layout, _plan(layout), root, repo_root=None, bundle=scoped)
    finally:
        root.close()


def test_apply_mutation_accepts_the_ignore_baseline(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "docs").mkdir()
    baseline = load_workspace_bundle(layout, ignore=IGNORE)
    application = transactions.apply_mutation(layout, _plan(layout), commit=None, baseline_bundle=baseline)
    assert application.ok


def test_refuse_scoped_baseline_allows_only_clone_roots(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    baseline = load_workspace_bundle(layout, ignore=IGNORE)
    transactions._refuse_scoped_baseline(baseline)
    transactions._refuse_scoped_baseline(replace(baseline, pruned=frozenset({"repositories/demo/references/git"})))
    with pytest.raises(ValueError, match=r"pruned beyond the clone glob: repositories$"):
        transactions._refuse_scoped_baseline(replace(baseline, pruned=frozenset({"repositories"})))
    with pytest.raises(ValueError, match=r"pruned beyond the clone glob: docs, repositories$"):
        transactions._refuse_scoped_baseline(
            replace(baseline, pruned=frozenset({"repositories/demo/references/git", "repositories", "docs"}))
        )
