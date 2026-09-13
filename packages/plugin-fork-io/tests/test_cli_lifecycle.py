import json
from pathlib import Path

import pytest
from plugin_fork_io.cli import app
from test_updates import forked
from typer.testing import CliRunner


def test_status_json_is_read_only_and_uses_stable_envelope(tmp_path):
    roots, variant = forked(tmp_path)
    before = {p.relative_to(roots.state): p.read_bytes() for p in roots.state.rglob("*") if p.is_file()}
    response = CliRunner().invoke(
        app, ["status", variant, "--state-dir", str(roots.state), "--content-dir", str(roots.content), "--json"]
    )
    assert response.exit_code == 0
    data = json.loads(response.stdout)
    assert data["operation"] == "status" and data["applied"] is False
    assert data["variant_id"] == variant and "findings" in data
    assert before == {p.relative_to(roots.state): p.read_bytes() for p in roots.state.rglob("*") if p.is_file()}


@pytest.mark.parametrize("command", ["inspect", "fork", "adopt", "install", "status", "update", "accept", "rollback"])
def test_all_commands_have_common_resolution_options_and_json_usage_refusals(command):
    help_result = CliRunner().invoke(app, [command, "--help"], color=False, terminal_width=160)
    assert help_result.exit_code == 0
    for option in ("--json", "--config", "--state-dir", "--content-dir", "--project"):
        assert option in help_result.output
    refusal = CliRunner().invoke(app, [command, "--not-a-supported-option", "--json"])
    assert refusal.exit_code == 2
    data = json.loads(refusal.stdout)
    assert data["operation"] == command and not data["allowed"]
    assert set(data) == {
        "schema_version",
        "operation",
        "variant_id",
        "preview_id",
        "applied",
        "allowed",
        "findings",
        "affected_paths",
        "data",
    }


def test_real_external_cli_lifecycle_with_ordinary_git_commits(tmp_path):
    import os
    import subprocess
    import sys

    from helpers import write_skill

    executable = Path(sys.executable).with_name("plugin-fork")
    assert executable.exists()
    client, source, state = tmp_path / "client", tmp_path / "upstream", tmp_path / "external-state"
    home = tmp_path / "home"
    client.mkdir()
    home.mkdir()
    environment = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    selection = tmp_path / "selection.json"
    selection.write_bytes(
        json.dumps(
            {
                "skills": [{"source": "skills/review", "name": "local-review"}],
                "resources": [],
                "dependencies": [],
                "adaptations": [],
                "source_links": [],
            }
        ).encode()
    )
    write_skill(source, "review")
    content = client / "agent-skills"

    def git(*args):
        return subprocess.run(
            [
                "git",
                "-C",
                str(client),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgSign=false",
                *args,
            ],
            env=environment,
            check=True,
            capture_output=True,
        ).stdout

    def cli(command, *args, expected=0):
        before_head = git("rev-parse", "HEAD")
        before_index = git("write-tree")
        completed = subprocess.run(
            [str(executable), command, *map(str, args), "--state-dir", str(state), "--project", str(client), "--json"],
            cwd=client,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert completed.returncode == expected, completed.stdout + completed.stderr
        assert git("rev-parse", "HEAD") == before_head
        assert git("write-tree") == before_index
        data = json.loads(completed.stdout)
        assert data["operation"] == command
        assert data["schema_version"] == 1 and isinstance(data["findings"], list)
        return data

    git("init", "--quiet")
    git("commit", "--allow-empty", "--quiet", "-m", "Client root")
    inspected = cli("inspect", source)
    assert inspected["allowed"] and not state.exists()
    prepared = cli("fork", source, "--selection", selection, "--content-dir", content)
    assert not prepared["applied"] and not content.exists()
    wrong = cli("adopt", "--apply", prepared["preview_id"], expected=3)
    assert not wrong["applied"]
    created = cli("fork", "--apply", prepared["preview_id"])
    variant = created["variant_id"]
    assert created["applied"]
    git("add", ".")
    git("commit", "--quiet", "-m", "Track customized fork")
    definition = content / "local-review/SKILL.md"
    definition.write_bytes(definition.read_bytes() + b"\nPreserve the local human approval gate.\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "Ordinary local customization")
    before_accept = definition.read_bytes()
    assert "content.modified" in {f["code"] for f in cli("status", variant)["findings"]}
    installation = cli("install", variant, "--agent", "claude", "--mode", "shared")
    cli("install", "--apply", installation["preview_id"])
    assert (client / ".claude/skills/local-review").is_symlink()
    (source / "skills/review/new.txt").write_bytes(b"upstream resource\n")
    update = cli("update", variant, "--source", source)
    assert not update["applied"]
    assert {"upstream_diff", "candidate_diff"} <= update["data"].keys()
    acceptance = cli("accept", variant, "--candidate", update["preview_id"])
    assert acceptance["data"]["review_state"] == "not_requested"
    cli("accept", "--apply", acceptance["preview_id"])
    git("add", ".")
    git("commit", "--quiet", "-m", "Track explicitly accepted update")
    rollback = cli("rollback", variant)
    cli("rollback", "--apply", rollback["preview_id"])
    assert definition.read_bytes() == before_accept
    assert not (content / "local-review/new.txt").exists()
    assert cli("status", variant)["data"]["generation"] == 3
    # Unknown adoption is separate ownership; no replacement source invents a base.
    adopted_root = tmp_path / "unknown-copy"
    write_skill(adopted_root, "legacy")
    adopted_selection = tmp_path / "adopt.json"
    adopted_selection.write_bytes(
        json.dumps(
            {
                "skills": [{"source": "skills/legacy", "name": "legacy"}],
                "resources": [],
                "dependencies": [],
                "adaptations": [],
                "source_links": [],
            }
        ).encode()
    )
    adopted = cli("adopt", "--selection", adopted_selection, "--content-dir", adopted_root)
    adopted = cli("adopt", "--apply", adopted["preview_id"])
    unknown = cli("update", adopted["variant_id"], "--source", source, expected=3)
    assert "origin.unknown" in {f["code"] for f in unknown["findings"]}
    assert not (client / ".plugin-fork").exists()
    assert not (client / ".gitignore").exists()
    assert not (client / "workspace.yaml").exists()
    assert not (home / ".config/plugin-fork").exists()


def test_blocked_text_candidate_is_locatable(tmp_path):
    roots, variant = forked(tmp_path)
    source = tmp_path / "source/skills/review/SKILL.md"
    source.write_bytes(source.read_bytes() + b"[required helper](missing.py)\n")
    response = CliRunner().invoke(
        app, ["update", variant, "--source", str(tmp_path / "source"), "--state-dir", str(roots.state)]
    )
    assert response.exit_code == 3
    assert "preview:" in response.stdout and '"candidate_path"' in response.stdout


def test_unexpected_failure_keeps_operation_and_json_exit_four(tmp_path, monkeypatch):
    from plugin_fork_io import installation

    roots, variant = forked(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("injected unexpected failure")

    monkeypatch.setattr(installation, "plan_install", fail)
    response = CliRunner().invoke(
        app, ["install", variant, "--agent", "codex", "--state-dir", str(roots.state), "--json"]
    )
    assert response.exit_code == 4
    data = json.loads(response.stdout)
    assert data["operation"] == "install" and not data["allowed"]
    assert data["findings"][0]["code"] == "internal.error"
