"""Stale-set planning: what `gw work gate run` decides to execute."""

from __future__ import annotations

import json
import shlex

from _gate_helpers import NOW, mint_unit_receipt
from conftest import GateEnv, git
from graph_works_core.orchestrate import gate as gate_mod
from graph_works_core.orchestrate import gate_units as gu
from graph_works_core.orchestrate.gate_receipts import GateEvaluation, Reuse, UnitEvidence

M = gu.parse_manifest(json.dumps({
    "version": 1, "jobs": 4, "repo_wide": {"command": "rw"},
    "units": [
        {"name": "lib", "inputs": ["packages/lib/**"], "command": "c lib"},
        {"name": "app", "inputs": ["packages/app/**"], "depends_on": ["lib"], "command": "c app"},
        {"name": "other", "inputs": ["packages/other/**"], "command": "c other"},
    ],
}))  # fmt: skip
STATE = gu.UnitState(M, {"lib": "1" * 64, "app": "2" * 64, "other": "3" * 64})


def evaluation(green: set[str], wide: bool) -> GateEvaluation:
    g = {n: UnitEvidence(n, STATE.hashes[n], "work/x", "r1") for n in green}
    stale = tuple(sorted(set(STATE.hashes) - green))
    return GateEvaluation(False, None, stale, wide, g, Reuse("work/x", "r1") if wide else None, ())


def test_full_plans_every_stale_unit_and_repo_wide_when_red() -> None:
    plan = gate_mod.plan_gate(
        STATE, evaluation({"lib"}, False), scope="full", fresh=False, affects_files=None, unit_jobs=None
    )
    assert isinstance(plan, gate_mod.GatePlan)
    assert plan.repo_wide and plan.planned_names == ("app", "other") and plan.jobs == 4
    assert next(u for u in plan.units if u.name == "lib").reused_from is not None


def test_full_skips_repo_wide_when_green_and_caps_jobs() -> None:
    plan = gate_mod.plan_gate(
        STATE, evaluation({"lib"}, True), scope="full", fresh=False, affects_files=None, unit_jobs=2
    )
    assert isinstance(plan, gate_mod.GatePlan) and not plan.repo_wide and plan.jobs == 2


def test_fresh_runs_everything() -> None:
    plan = gate_mod.plan_gate(
        STATE, evaluation({"lib", "app", "other"}, True), scope="full", fresh=True, affects_files=None, unit_jobs=None
    )
    assert isinstance(plan, gate_mod.GatePlan)
    assert plan.repo_wide and plan.planned_names == ("app", "lib", "other")
    assert all(u.reused_from is None for u in plan.units)


def test_scoped_narrows_to_affected_units_and_their_dependents() -> None:
    plan = gate_mod.plan_gate(STATE, evaluation(set(), True), scope="scoped", fresh=False,
                              affects_files=["packages/lib/x.py"], unit_jobs=None)  # fmt: skip
    assert isinstance(plan, gate_mod.GatePlan) and plan.planned_names == ("app", "lib")


def test_scoped_uncovered_path_refuses() -> None:
    refused = gate_mod.plan_gate(STATE, evaluation(set(), True), scope="scoped", fresh=False,
                                 affects_files=["docs/readme.md"], unit_jobs=None)  # fmt: skip
    assert refused == ("scope-uncovered", "no gate unit's inputs match: docs/readme.md; run the full gate")


def test_scoped_with_nothing_stale_plans_nothing() -> None:
    plan = gate_mod.plan_gate(STATE, evaluation({"lib", "app"}, True), scope="scoped", fresh=False,
                              affects_files=["packages/lib/x.py"], unit_jobs=None)  # fmt: skip
    assert isinstance(plan, gate_mod.GatePlan) and plan.planned_names == () and not plan.repo_wide


UNITS_GATE = "    gate:\n      full: 'true'\n      units: 'cat units.json'\n"
UNITS_JSON = (
    '{"version": 1, "jobs": 2, "repo_wide": {"command": "true"}, "units": ['
    '{"name": "a", "inputs": ["packages/a/**"], "command": "true"},'
    '{"name": "b", "inputs": ["units.json"], "command": "true"}]}'
)


def _units_env(env: GateEnv) -> None:
    env.set_manifest(gate=UNITS_GATE)
    (env.repo / "units.json").write_text(UNITS_JSON, encoding="utf-8", newline="\n")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "units")


def test_run_writes_a_v2_pending_record(env: GateEnv) -> None:
    _units_env(env)
    result = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert result.status == "started" and result.stale == ("a", "b")
    record = json.loads(env.spawned[0].read_text(encoding="utf-8"))
    assert record["version"] == 2 and record["jobs"] == 2 and not record["implicit"]
    assert [u["name"] for u in record["units"] if u["planned"]] == ["a", "b"]
    assert record["repo_wide"]["planned"] is True


def test_run_is_satisfied_by_another_items_unit_evidence(env: GateEnv) -> None:
    _units_env(env)
    state = gu.resolve_unit_state(gate_mod.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    from graph_works_core.orchestrate.gate_receipts import RepoWideEntry

    mint_unit_receipt(env, owner="work/other", hashes=state.hashes,
                      repo_wide=RepoWideEntry(env.tree, "true", True, 0, 1.0))  # fmt: skip
    result = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert result.status == "satisfied" and env.spawned == []
    assert result.match is not None and {u.name for u in result.match.units} == {"a", "b"}


def test_fresh_runs_even_when_satisfied(env: GateEnv) -> None:
    _units_env(env)
    state = gu.resolve_unit_state(gate_mod.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    from graph_works_core.orchestrate.gate_receipts import RepoWideEntry

    mint_unit_receipt(
        env, owner="work/other", hashes=state.hashes, repo_wide=RepoWideEntry(env.tree, "true", True, 0, 1.0)
    )
    result = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn, fresh=True)
    assert result.status == "started" and len(env.spawned) == 1


def test_broken_manifest_refuses_gate_run(env: GateEnv) -> None:
    env.set_manifest(gate="    gate:\n      full: 'true'\n      units: 'echo {'\n")
    result = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert result.refusal == "units-command-failed" and env.spawned == []


def test_same_plan_joins_the_live_run(env: GateEnv) -> None:
    _units_env(env)
    first = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    # the runner never started; within START_GRACE the record counts as live
    second = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert second.status == "running" and second.run_id == first.run_id


def test_check_reports_stale_units(env: GateEnv) -> None:
    _units_env(env)
    result = gate_mod.run_gate_check(env.layout, env.path)
    assert result.status == "unsatisfied" and result.stale == ("a", "b")


def test_implicit_repo_record_runs_gate_full(env: GateEnv) -> None:
    env.set_manifest(gate="    gate:\n      full: 'true'\n")
    gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    record = json.loads(env.spawned[0].read_text(encoding="utf-8"))
    assert record["implicit"] is True and record["command"] == "true"
    assert [u["name"] for u in record["units"]] == ["tree"] and record["repo_wide"] is None


def test_scoped_empty_affects_refuses() -> None:
    assert gate_mod.plan_gate(
        STATE, evaluation(set(), False), scope="scoped", fresh=False, affects_files=[], unit_jobs=None
    ) == ("scope-uncovered", "no code `affects` to scope; run the full gate")


def test_scoped_current_keeps_unrelated_units_stale(env: GateEnv) -> None:
    _units_env(env)
    state = gu.resolve_unit_state(gate_mod.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    from graph_works_core.orchestrate.gate_receipts import RepoWideEntry

    mint_unit_receipt(
        env,
        owner="work/other",
        hashes={"a": state.hashes["a"]},
        repo_wide=RepoWideEntry(env.tree, "true", True, 0, 1.0),
    )
    result = gate_mod.run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert result.status == "current" and result.stale == ("b",) and env.spawned == []


def test_scoped_expands_affects_and_preserves_missing_entries(env: GateEnv) -> None:
    _units_env(env)
    env.set_affects(["packages/a", "missing"])
    result = gate_mod.run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert result.refusal == "scope-uncovered" and "missing" in result.detail
    assert env.spawned == []


def test_manifest_change_does_not_join_old_plan(env: GateEnv) -> None:
    _units_env(env)
    first = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    # Workspace gate config is outside the code tree; the unit env changes without a new code tree.
    changed = UNITS_JSON.replace(
        '"inputs": ["packages/a/**"], "command": "true"}',
        '"inputs": ["packages/a/**"], "command": "true", "env": {"X": "1"}}',
    )
    env.set_manifest(
        gate="    gate:\n      full: 'true'\n      units: " + json.dumps("printf %s " + shlex.quote(changed)) + "\n"
    )
    second = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert second.status == "started" and second.run_id != first.run_id and len(env.spawned) == 2


def test_reused_evidence_is_carried_in_pending_plan(env: GateEnv) -> None:
    _units_env(env)
    state = gu.resolve_unit_state(gate_mod.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    from graph_works_core.orchestrate.gate_receipts import RepoWideEntry

    mint_unit_receipt(
        env,
        owner="work/other",
        hashes={"a": state.hashes["a"]},
        repo_wide=RepoWideEntry(env.tree, "true", True, 0, 1.0),
    )
    result = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    record = json.loads(env.spawned[0].read_text(encoding="utf-8"))
    assert result.names == ("b",) and result.command == "units: b"
    assert record["repo_wide"]["planned"] is False
    assert record["repo_wide"]["reused_from"] == {"owner": "work/other", "run_id": "20260928T130000Z-10000000"}
    assert record["units"][0]["reused_from"] == record["repo_wide"]["reused_from"]


def test_check_broken_manifest_refuses(env: GateEnv) -> None:
    env.set_manifest(gate="    gate:\n      full: 'true'\n      units: 'echo {}'\n")
    assert gate_mod.run_gate_check(env.layout, env.path).refusal == "units-invalid"


def test_changed_hash_plan_does_not_join_even_with_same_manifest(env: GateEnv) -> None:
    _units_env(env)
    first = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    env.set_manifest(gate=UNITS_GATE + "      extra_inputs: ['units.json']\n")
    second = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert second.status == "started" and second.run_id != first.run_id


def test_changed_job_cap_does_not_join_old_plan(env: GateEnv) -> None:
    _units_env(env)
    first = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    manifest = env.layout.manifest_path
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "workflow: {dispatch_rules: dispatch.yaml}",
            "workflow: {dispatch_rules: dispatch.yaml, gate: {unit_jobs: 1}}",
        ),
        encoding="utf-8",
        newline="\n",
    )
    second = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert second.status == "started" and second.run_id != first.run_id
    assert json.loads(env.spawned[-1].read_text(encoding="utf-8"))["jobs"] == 1


def test_fresh_does_not_join_a_plan_reusing_green_units(env: GateEnv) -> None:
    _units_env(env)
    state = gu.resolve_unit_state(gate_mod.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    mint_unit_receipt(env, owner="work/other", hashes={"a": state.hashes["a"]})
    first = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    fresh = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn, fresh=True)
    assert fresh.status == "started" and fresh.run_id != first.run_id
    assert fresh.names == ("a", "b")


def test_equivalent_reuse_from_another_receipt_still_joins(env: GateEnv) -> None:
    _units_env(env)
    state = gu.resolve_unit_state(gate_mod.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    mint_unit_receipt(env, owner="work/other", hashes={"a": state.hashes["a"]})
    first = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
    mint_unit_receipt(env, owner="work/newer", hashes={"a": state.hashes["a"]})
    receipt = env.layout.bundle_dir / "work/newer/references/03-gate-receipts.md"
    receipt.write_text(
        receipt.read_text(encoding="utf-8").replace("13:00:00Z", "14:00:00Z"), encoding="utf-8", newline="\n"
    )
    second = gate_mod.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert second.status == "running" and second.run_id == first.run_id and len(env.spawned) == 1
