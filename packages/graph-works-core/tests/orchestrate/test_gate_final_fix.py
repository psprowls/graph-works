"""Final-review regressions at manifest, runner, receipt, and Git-leaf boundaries."""

from __future__ import annotations

import json
import shlex
import sys

import pytest
from _gate_helpers import CLOCK, NOW, TODAY, mint_unit_receipt, no_sleep, receipt_text, ticks
from conftest import GateEnv, git
from graph_works_core.orchestrate import gate, gate_git, gate_runner, gate_units
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.orchestrate.gate_receipts import GateRecord, RepoWideEntry, parse_gate_receipt
from graph_works_core.workspace import provenance

MANIFEST = {
    "version": 1,
    "jobs": 2,
    "repo_wide": {"command": "true"},
    "units": [
        {"name": "a", "inputs": ["packages/a/**"], "command": "true"},
        {"name": "b", "inputs": ["units.json"], "command": "true"},
    ],
}


def units_env(env: GateEnv, *, mutation: str = "none") -> None:
    env.state["start_sha"] = git(env.repo, "rev-parse", "HEAD")
    env._write_page()
    env.page_text = env.page_text.replace("owner: someone", f"owner: someone\nstart_sha: {env.state['start_sha']}")
    (env.layout.bundle_dir / f"{env.path}.md").write_text(env.page_text, encoding="utf-8", newline="\n")
    (env.repo / "packages/a/y.py").write_text("work", encoding="utf-8", newline="\n")
    (env.repo / "units.json").write_text(json.dumps(MANIFEST), encoding="utf-8", newline="\n")
    (env.repo / "emit.py").write_text(
        "import subprocess, sys\nfrom pathlib import Path\n"
        "if sys.argv[1] in ('dirty', 'commit'):\n"
        "    Path('packages/a/x.py').write_text('changed', encoding='utf-8', newline='\\n')\n"
        "if sys.argv[1] == 'commit':\n"
        "    subprocess.run(['git', 'commit', '-qam', 'manifest changed tree'],\n"
        "                   check=True, stdout=subprocess.DEVNULL)\n"
        "print(Path('units.json').read_text(encoding='utf-8'))\n",
        encoding="utf-8",
        newline="\n",
    )
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-qm", "unit fixture and work")
    command = f"{shlex.quote(sys.executable)} emit.py {mutation}"
    env.set_manifest(gate=f"    gate:\n      full: 'true'\n      units: {json.dumps(command)}\n")


@pytest.mark.skipif(sys.platform == "win32", reason="fixture commands use sh quoting and true")
@pytest.mark.parametrize("completion", ["runner", "wait", "recovery"])
@pytest.mark.parametrize("later_tree", [False, True])
def test_scoped_runner_omits_skipped_stale_units_and_full_plans_remainder(env, monkeypatch, completion, later_tree):
    units_env(env)
    started = gate.run_gate_run(env.layout, env.path, scope="scoped", now=NOW, token="0a1b2c3d", spawn=env.spawn)
    assert started.status == "started" and started.names == ("a",)
    record = env.spawned[0]
    if completion != "runner":
        with monkeypatch.context() as patch:
            patch.setattr(
                gate,
                "record_gate_run",
                lambda *a, **k: GateRecord("transaction-refused", None, False, "blocked"),
            )
            gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(*range(100)), sleep=no_sleep)
        assert not json.loads(record.read_text(encoding="utf-8"))["recorded"]
    else:
        gate_runner.execute(record, wall=lambda: NOW, monotonic=ticks(*range(100)), sleep=no_sleep)
    if completion == "recovery":
        gate.recover_unrecorded(env.layout, env.path, now=NOW)
    waited = gate.run_gate_wait(
        env.layout, env.path, run_id=started.run_id, timeout=0, clock=CLOCK, sleep=no_sleep, today=TODAY
    )
    assert (waited.status, waited.exit, waited.recorded) == ("finished", 0, True)
    runs = parse_gate_receipt(receipt_text(env))[1]
    assert len(runs) == 1
    assert [(unit.name, unit.ran, unit.exit) for unit in runs[0].units] == [("a", True, 0)]
    assert gate.run_gate_check(env.layout, env.path).stale == ("b",)
    if later_tree:
        (env.repo / "unrelated.txt").write_text("other", encoding="utf-8", newline="\n")
        git(env.repo, "add", "-A")
        git(env.repo, "commit", "-qm", "unrelated tree change")
    full = gate.run_gate_run(env.layout, env.path, now=NOW, token="1a1b2c3d", spawn=env.spawn)
    assert full.status == "started" and full.names == ("b",)
    data = json.loads(env.spawned[-1].read_text(encoding="utf-8"))
    assert data["repo_wide"]["planned"] is later_tree
    assert data["units"][0]["reused_from"]["run_id"] == started.run_id


@pytest.mark.skipif(sys.platform == "win32", reason="fixture commands use sh quoting and true")
@pytest.mark.parametrize("operation", ["run", "check", "advance"])
@pytest.mark.parametrize("mutation", ["dirty", "commit", "unavailable"])
@pytest.mark.parametrize("cached", [False, True])
def test_manifest_command_state_change_refuses_before_acceptance_or_planning(
    env, monkeypatch, operation, mutation, cached
):
    units_env(env, mutation=mutation)
    tree = env.tree
    manifest = gate_units.parse_manifest(json.dumps(MANIFEST))
    if cached:
        hashes = gate_units.unit_hashes(manifest, gate_git.ls_tree(env.repo, tree), ())
        mint_unit_receipt(env, owner="work/other", hashes=hashes, repo_wide=RepoWideEntry(tree, "true", True, 0, 1.0))
    if mutation == "unavailable":
        real = gate_units.load_manifest

        def load(*args, **kwargs):
            result = real(*args, **kwargs)

            def unavailable(*args, **kwargs):
                raise gate_git.GitUnavailable("post-manifest Git unavailable")

            monkeypatch.setattr(gate_git, "snapshot", unavailable)
            return result

        monkeypatch.setattr(gate_units, "load_manifest", load)
    expected = (
        "git-unavailable" if mutation != "dirty" else ("uncommitted-work" if operation == "advance" else "dirty-tree")
    )
    if operation == "run":
        result = gate.run_gate_run(env.layout, env.path, now=NOW, token="0a1b2c3d", spawn=env.spawn)
        assert result.refusal == expected, result
        assert env.spawned == []
        assert not list(gate.runs_dir(env.layout).glob("*.json"))
    elif operation == "check":
        result = gate.run_gate_check(env.layout, env.path)
        assert result.refusal == expected, result
    else:
        result = stage.run_stage_advance(env.layout, env.path, today=TODAY, repo=env.repo, dry_run=False)
        assert result.outcome.plan.refusal == expected, result.outcome.plan
        assert not result.outcome.written and env.page_unchanged()
    if mutation == "commit":
        assert tree != env.tree and gate_git.snapshot(env.repo).dirty == ()
        detail = result.outcome.plan.detail if operation == "advance" else result.detail
        assert "manifest" in detail and tree in detail and env.tree in detail


@pytest.mark.parametrize("modes", [("100755", "100644"), ("100644", "120000"), ("100644", "160000")])
def test_unit_hash_changes_with_leaf_mode_even_for_same_object(tmp_path, monkeypatch, modes):
    manifest = gate_units.parse_manifest(
        json.dumps({"version": 1, "jobs": 1, "units": [{"name": "a", "inputs": ["test.sh"], "command": "./test.sh"}]})
    )
    hashes = []
    for mode in modes:
        kind = "commit" if mode == "160000" else "blob"
        monkeypatch.setattr(
            provenance, "strict_git", lambda *a, mode=mode, kind=kind, **k: f"{mode} {kind} {'a' * 40}\ttest.sh\0"
        )
        listing = gate_git.ls_tree(tmp_path, "HEAD", git=provenance.GitExecutable("git", "path", "git version x"))
        assert listing == {"test.sh": gate_git.GitLeaf(mode, "a" * 40)}
        hashes.append(gate_units.unit_hashes(manifest, listing, ())["a"])
    assert hashes[0] != hashes[1]


def test_unit_hash_version_invalidates_previous_recipe(tmp_path, monkeypatch):
    manifest = gate_units.parse_manifest(
        json.dumps({"version": 1, "jobs": 1, "units": [{"name": "a", "inputs": ["test.sh"], "command": "./test.sh"}]})
    )
    monkeypatch.setattr(provenance, "strict_git", lambda *a, **k: f"100644 blob {'a' * 40}\ttest.sh\0")
    listing = gate_git.ls_tree(tmp_path, "HEAD", git=provenance.GitExecutable("git", "path", "git version x"))
    current = gate_units.unit_hashes(manifest, listing, ())["a"]
    old = gate_units._digest(
        {"tag": "gw-gate-unit/1", "files": [("test.sh", "a" * 40)], "command": "./test.sh", "env": []}
    )
    assert current != old
    assert gate_units._UNIT_TAG == "gw-gate-unit/2"
