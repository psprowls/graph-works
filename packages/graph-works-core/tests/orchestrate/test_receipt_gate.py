"""The execute -> finish receipt gate: a green full-gate receipt for the handed-over tree."""

from __future__ import annotations

from pathlib import Path

from _gate_helpers import TODAY, mint_receipt, mint_unit_receipt, raise_
from conftest import GateEnv, git, make_repo
from graph_works_core.orchestrate import gate_git, gate_units
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.workspace import gate_config, provenance
from work_tracker_okf import decisions as _decisions


def _ready(env: GateEnv) -> None:
    """A committed change under `affects`, after a recorded baseline."""
    env.state["start_sha"] = git(env.repo, "rev-parse", "HEAD")
    env._write_page()
    text = env.page_text.replace("owner: someone", f"owner: someone\nstart_sha: {env.state['start_sha']}")
    (env.layout.bundle_dir / f"{env.path}.md").write_text(text, encoding="utf-8", newline="\n")
    env.page_text = text
    (env.repo / "packages/a/y.py").write_text("y", encoding="utf-8", newline="\n")
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "work")


def advance(env: GateEnv, *, dry_run: bool = False, skip_gate: tuple[str, str] | None = None):
    return stage.run_stage_advance(
        env.layout,
        env.path,
        today=TODAY,
        repo=env.repo,
        dry_run=dry_run,
        skip_gate=skip_gate[0] if skip_gate else None,
        skip_reason=skip_gate[1] if skip_gate else None,
        actor="pat" if skip_gate else None,
    )


def ledger_text(env: GateEnv) -> str:
    return _decisions.ledger_ref(env.path).path(env.layout.bundle_dir).read_text(encoding="utf-8")


def test_no_receipt_refuses_and_names_the_verb_and_tree(env: GateEnv) -> None:
    _ready(env)
    result = advance(env)
    assert result.outcome.plan.refusal == "no-gate-receipt"
    assert f"gw work gate run {env.path}" in result.outcome.plan.detail
    assert env.tree in result.outcome.plan.detail
    assert env.page_unchanged()


def test_satisfying_receipt_passes_and_reports_its_source(env: GateEnv) -> None:
    _ready(env)
    mint_receipt(env, tree=env.tree, owner="work/other")
    result = advance(env)
    assert result.outcome.written and result.gate_receipt is not None
    assert result.gate_receipt.owner == "work/other"
    assert any(w.startswith("gate receipt: work/other run ") for w in result.warnings)


def test_no_gate_configured_refuses_and_names_the_key(env: GateEnv) -> None:
    _ready(env)
    env.set_manifest(gate="")
    result = advance(env)
    assert result.outcome.plan.refusal == "no-gate-configured"
    assert "repositories.code.gate.full" in result.outcome.plan.detail


def test_undeclared_repository_refuses_no_gate_configured(env: GateEnv, tmp_path: Path) -> None:
    _ready(env)
    other = make_repo(tmp_path / "other")
    result = stage.run_stage_advance(env.layout, env.path, today=TODAY, repo=other, dry_run=True)
    assert result.outcome.plan.refusal == "no-gate-configured"
    assert "not a declared repository" in result.outcome.plan.detail


def test_dry_run_reports_the_same_refusal_and_writes_nothing(env: GateEnv) -> None:
    _ready(env)
    result = advance(env, dry_run=True)
    assert result.outcome.plan.refusal == "no-gate-receipt" and env.page_unchanged()


def test_dry_run_with_receipt_reports_it(env: GateEnv) -> None:
    _ready(env)
    mint_receipt(env, tree=env.tree)
    result = advance(env, dry_run=True)
    assert result.outcome.plan.refusal is None and result.gate_receipt is not None and env.page_unchanged()


def test_red_or_scoped_receipt_does_not_satisfy(env: GateEnv) -> None:
    _ready(env)
    mint_receipt(env, tree=env.tree, exit=1)
    mint_receipt(env, tree=env.tree, scope="scoped", owner="work/b")
    assert advance(env).outcome.plan.refusal == "no-gate-receipt"


def test_skip_gate_bypasses_exactly_its_code(env: GateEnv) -> None:
    _ready(env)
    wrong = advance(env, skip_gate=("no-gate-configured", "x"))
    assert wrong.outcome.plan.refusal == "gate-bypass-mismatch"
    ok = advance(env, skip_gate=("no-gate-receipt", "gate run by hand, log attached"))
    assert ok.outcome.written
    assert "no-gate-receipt" in ledger_text(env)


def test_workspace_only_item_is_exempt(env: GateEnv) -> None:
    _ready(env)
    env.set_affects(["gw:workspace"])
    assert advance(env).outcome.plan.refusal not in {"no-gate-receipt", "no-gate-configured"}


def test_git_failure_during_the_receipt_snapshot_refuses(env: GateEnv, monkeypatch) -> None:
    _ready(env)
    mint_receipt(env, tree=env.tree)
    monkeypatch.setattr(stage.gate_git, "snapshot", raise_(stage.gate_git.GitUnavailable("boom")))
    result = advance(env)
    assert result.outcome.plan.refusal == "git-unavailable" and "boom" in result.outcome.plan.detail


def test_other_transitions_never_consult_receipts(env: GateEnv) -> None:
    env.set_phase("plan")
    plan = env.layout.bundle_dir / env.path / "references" / "02-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("---\ntype: Plan\n---\n\nplan\n", encoding="utf-8", newline="\n")
    result = advance(env)
    assert result.outcome.plan.refusal is None and result.gate_receipt is None


def test_unusable_git_refuses_before_the_receipt_lookup(env: GateEnv, monkeypatch) -> None:
    _ready(env)
    real = stage.provenance.gate_git
    calls = iter([real(env.layout)])
    failure = stage.provenance.GitFailure("unresolved", "no git")
    monkeypatch.setattr(stage.provenance, "gate_git", lambda layout: next(calls, failure))
    result = advance(env)
    assert result.outcome.plan.refusal == "git-unavailable" and "no git" in result.outcome.plan.detail


def _dirty_env(env: GateEnv) -> None:
    _ready(env)
    (env.repo / "packages/a/z.py").write_text("z", encoding="utf-8", newline="\n")


def test_bypassing_a_commit_gate_code_still_requires_a_receipt(env: GateEnv) -> None:
    _dirty_env(env)
    result = advance(env, skip_gate=("uncommitted-work", "unrelated dirt"))
    assert result.outcome.plan.refusal == "no-gate-receipt"
    assert env.page_unchanged()


def test_bypassing_a_commit_gate_code_passes_with_a_receipt(env: GateEnv) -> None:
    _dirty_env(env)
    tree = gate_git.snapshot(env.repo, git=provenance.gate_git(env.layout)).tree
    mint_receipt(env, tree=tree)
    result = advance(env, skip_gate=("uncommitted-work", "unrelated dirt"))
    assert result.outcome.written and result.gate_bypass is not None
    assert result.gate_bypass.code == "uncommitted-work"


def test_bypassing_a_commit_gate_code_with_no_gate_configured_refuses(env: GateEnv) -> None:
    _dirty_env(env)
    env.set_manifest(gate="")
    result = advance(env, skip_gate=("uncommitted-work", "unrelated dirt"))
    assert result.outcome.plan.refusal == "no-gate-configured"


UNITS_GATE = "    gate:\n      full: 'true'\n      units: 'cat units.json'\n"


def _units_manifest(env: GateEnv) -> None:
    (env.repo / "units.json").write_text(
        '{"version": 1, "jobs": 1, "units": [{"name": "a", "inputs": ["packages/a/**"], "command": "true"}]}',
        encoding="utf-8",
        newline="\n",
    )
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "units")


def test_units_repo_with_green_unit_evidence_passes(env: GateEnv) -> None:
    env.set_manifest(gate=UNITS_GATE)
    _units_manifest(env)
    _ready(env)
    state = gate_units.resolve_unit_state(gate_config.repo_gate(env.layout, "code"), env.repo, env.tree, git=None)
    mint_unit_receipt(env, owner="work/other", hashes=state.hashes)
    result = advance(env)
    assert result.outcome.written and result.gate_receipt is not None
    assert [u.name for u in result.gate_receipt.units] == ["a"]


def test_units_repo_without_evidence_names_stale_units(env: GateEnv) -> None:
    env.set_manifest(gate=UNITS_GATE)
    _units_manifest(env)
    _ready(env)
    result = advance(env)
    assert result.outcome.plan.refusal == "no-gate-receipt"
    assert "stale units: a" in result.outcome.plan.detail


def test_broken_units_command_refuses_units_command_failed(env: GateEnv) -> None:
    env.set_manifest(gate="    gate:\n      full: 'true'\n      units: 'exit 4'\n")
    _ready(env)
    result = advance(env)
    assert result.outcome.plan.refusal == "units-command-failed"


def test_units_refusal_is_bypassable(env: GateEnv) -> None:
    env.set_manifest(gate="    gate:\n      full: 'true'\n      units: 'echo nope'\n")
    _ready(env)
    result = advance(env, skip_gate=("units-command-failed", "manifest broken on this host"))
    assert result.outcome.written


def test_invalid_units_manifest_refuses_units_invalid(env: GateEnv) -> None:
    env.set_manifest(gate="    gate:\n      full: 'true'\n      units: 'echo {}'\n")
    _ready(env)
    result = advance(env)
    assert result.outcome.plan.refusal == "units-invalid"


def test_git_failure_during_unit_resolution_refuses(env: GateEnv, monkeypatch) -> None:
    _ready(env)
    monkeypatch.setattr(stage.gate_units, "resolve_unit_state", raise_(gate_git.GitUnavailable("units git")))
    result = advance(env)
    assert result.outcome.plan.refusal == "git-unavailable" and "units git" in result.outcome.plan.detail
