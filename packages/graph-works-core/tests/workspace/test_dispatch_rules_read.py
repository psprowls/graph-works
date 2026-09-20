"""`run_dispatch_rules`: the Config screen's rule read, in fold order."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.dispatch import packaged_rule, packaged_rules, rule_matches
from graph_works_core.workspace.dispatch_config import run_dispatch_rules
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.pipeline import PACKAGED_PIPELINE

TODAY = date(2026, 9, 19)


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Rules")).layout


def _rules(layout, shared: str, local: str | None = None) -> None:
    (layout.root / "dispatch.yaml").write_text(f"version: 1\npipeline:\n  rules:\n{shared}", encoding="utf-8")
    if local is not None:
        (layout.root / "dispatch.local.yaml").write_text(f"version: 1\npipeline:\n  rules:\n{local}", encoding="utf-8")


def test_packaged_rows_follow_the_packaged_pipeline() -> None:
    rows = packaged_rules()

    assert [row.origin.name for row in rows] == list(PACKAGED_PIPELINE)
    assert [row.origin.index for row in rows] == list(range(len(PACKAGED_PIPELINE)))
    assert all(row.origin.source == "packaged" for row in rows)
    assert rows == tuple(packaged_rule(variant) for variant in PACKAGED_PIPELINE)
    assert dict(rows[0].match) == {"variant": next(iter(PACKAGED_PIPELINE))}
    assert set(rows[0].fields) == {"skill", "mode", "prompt_tail", "agent", "model", "reasoning_effort"}
    assert rows[0].fields["agent"] == "claude"


def test_rules_are_in_fold_order_shared_then_local(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _rules(
        layout,
        "  - match: {stage: [plan, execute]}\n    model: opus\n",
        "  - name: mine\n    match: {has_spec: true}\n    agent: codex\n",
    )

    ruleset = run_dispatch_rules(layout)

    assert ruleset.attributes == ("blast_radius", "effort", "has_plan", "has_spec", "stage", "type", "variant")
    assert ruleset.packaged == packaged_rules()
    assert [(Path(r.origin.source).name, r.origin.index, r.origin.name) for r in ruleset.rules] == [
        ("dispatch.yaml", 0, None),
        ("dispatch.local.yaml", 0, "mine"),
    ]
    assert ruleset.rules[0].match["stage"] == ("plan", "execute")


def test_a_malformed_rule_names_its_file_and_index(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _rules(layout, "  - match: {}\n    model: opus\n", "  - match: {}\n    colour: red\n")

    with pytest.raises(WorkspaceError, match=r"dispatch\.local\.yaml: rule 0: unknown rule keys"):
        run_dispatch_rules(layout)


@pytest.mark.parametrize(
    ("constraints", "attributes", "expected"),
    [
        ({"stage": ("plan", "execute")}, {"stage": "plan"}, True),
        ({"stage": "design"}, {"stage": "plan"}, False),
        ({"has_spec": True}, {"has_spec": None}, False),
        ({"has_spec": True}, {"has_spec": True}, True),
        ({}, {"stage": "plan"}, True),
    ],
)
def test_rule_matches(constraints, attributes, expected) -> None:
    assert rule_matches(constraints, attributes) is expected
