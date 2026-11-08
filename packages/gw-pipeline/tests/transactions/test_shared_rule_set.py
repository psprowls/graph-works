"""One rule set serves the baseline and the postcondition of a single mutation."""

from __future__ import annotations

from pathlib import Path

import pytest
from gw_kernel.testing.transactions import plan as _plan
from gw_kernel.testing.transactions import workspace as _workspace
from gw_pipeline import transactions
from gw_pipeline.transactions import apply_mutation
from work_tracker_okf.mutation import PlannedWrite


def _write_plan(layout):
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    return _plan(layout, writes=(PlannedWrite("work/final.bin", None, b"final"),))


def test_apply_mutation_builds_one_rule_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    calls: list[int] = []
    real = transactions.rule_set

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(transactions, "rule_set", counting)
    result = apply_mutation(layout, _write_plan(layout), commit=None)
    assert result.ok is True
    assert len(calls) == 1


def test_failed_first_build_is_rebuilt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    calls: list[int] = []

    def failing(*args):
        calls.append(1)
        raise ValueError("schema denied")

    monkeypatch.setattr(transactions, "_extra_rules", failing)
    result = apply_mutation(layout, _write_plan(layout), commit=None)
    assert len(calls) == 2
    text = " ".join(map(str, (*result.warnings, *getattr(result, "excused", ()), *getattr(result, "failures", ()))))
    assert "schema denied" in text or not result.ok


def test_shared_rule_set_caches_success_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    calls: list[int] = []

    def flaky(*args):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("once")
        return ()

    monkeypatch.setattr(transactions, "_extra_rules", flaky)
    shared = transactions._SharedRuleSet(layout, None, ())
    with pytest.raises(ValueError, match="once"):
        shared.get()
    first = shared.get()
    assert shared.get() is first
    assert len(calls) == 2
