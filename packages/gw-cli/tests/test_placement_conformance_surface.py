"""Pins the landed placement surface the end-to-end conformance suite is written against.

A red test here is a stop signal: the suite holds the code to the epic designs, so it is
never adapted to a surface that drifted without a human deciding which side is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

from gw_cli.cli import app
from gw_cli.work_cli import main as work_main
from gw_pipeline.placement import branch_name
from typer.testing import CliRunner

runner = CliRunner()


def _commands() -> dict[str, set[str]]:
    result = runner.invoke(app, ["util", "describe-surface", "--json"])
    assert result.exit_code == 0, result.output
    surface = json.loads(result.stdout)
    return {c["name"]: {o for opt in c["options"] for o in opt["opts"]} for c in surface["commands"]}


def test_place_verb_has_four_exclusive_modes() -> None:
    options = _commands()["gw work place"]
    assert {"--ensure", "--start", "--remove", "--prune", "--dry-run", "--repo-name", "--json"} <= options


def test_retired_placement_verbs_are_gone() -> None:
    names = set(_commands())
    retired = {f"gw work {v}" for v in ("record-placement", "prepare-workspace", "record-baseline", "merge-workspace")}
    assert names & retired == set()


def test_advance_has_no_placement_flags() -> None:
    # `--no-infer-worktree` survives as a hidden, warned no-op until the legacy-placement
    # cleanup (feature-retire-placement-fields design §5); the other two are gone.
    options = _commands()["gw work advance"]
    assert options & {"--worktree", "--branch"} == set()


def test_base_schema_keeps_stored_placement_only_as_deprecated(workspace: Path) -> None:
    schema = json.loads((workspace / ".gw" / "schema" / "_base.schema.json").read_text(encoding="utf-8"))
    # The keys stay only as deprecated, so unmigrated pages keep validating (D-014).
    properties = schema.get("properties", {})
    for key in ("worktree", "branch", "repo_stamps"):
        assert key not in properties or properties[key].get("deprecated") is True, key


def test_branch_name_is_the_pipeline_function() -> None:
    assert branch_name("work/feature-x", "Feature").startswith("feature/")


def test_notifier_factory_is_patchable() -> None:
    assert callable(work_main.placement_notifier)
    assert callable(work_main.orca_port)
