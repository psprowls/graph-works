import json

import pytest
from plugin_fork_io.cli import app
from test_acceptance import prepared
from typer.testing import CliRunner


@pytest.mark.parametrize(
    "command,option,payload",
    [
        ("fork", "--selection", []),
        ("fork", "--selection", {}),
        ("fork", "--intent", [7]),
        ("adopt", "--evidence", []),
        ("adopt", "--intent", {}),
        ("adopt", "--intent", [False]),
        ("update", "--selection", []),
        ("update", "--selection", {}),
        ("update", "--mapping", []),
        ("update", "--mapping", {"old": 7}),
        ("accept", "--review", {}),
        ("accept", "--resolutions", {}),
        ("accept", "--intent", [False]),
    ],
)
def test_supplied_json_errors_preserve_command_and_refuse_before_mutation(tmp_path, command, option, payload):
    roots, variant, update = prepared(tmp_path)
    valid = tmp_path / "selection.json"
    valid.write_bytes(
        json.dumps(
            {
                "skills": [{"source": "skills/review", "name": "review"}],
                "resources": [],
                "dependencies": [],
                "adaptations": [],
                "source_links": [],
            }
        ).encode()
    )
    bad = tmp_path / "bad.json"
    bad.write_bytes(json.dumps(payload).encode())
    args = {
        "fork": [str(tmp_path / "source"), "--selection", str(valid)],
        "adopt": ["--selection", str(valid), "--content-dir", str(tmp_path / "source")],
        "update": [variant, "--source", str(tmp_path / "source")],
        "accept": [variant, "--candidate", update.preview_id],
    }[command]
    if option == "--selection" and "--selection" in args:
        args = args[: args.index("--selection")]
    response = CliRunner().invoke(app, [command, *args, option, str(bad), "--state-dir", str(roots.state), "--json"])
    assert response.exit_code == 2, response.stdout
    result = json.loads(response.stdout)
    assert result["operation"] == command and not result["applied"]


@pytest.mark.parametrize(
    "command,args",
    [
        ("fork", []),
        ("adopt", []),
        ("accept", []),
        ("rollback", []),
        ("install", []),
        ("install", ["variant", "--agent", "codex", "--scope", "global"]),
        ("install", ["variant", "--agent", "codex", "--mode", "fallback"]),
    ],
)
def test_incomplete_preparation_and_unknown_modes_are_usage_refusals(tmp_path, command, args):
    response = CliRunner().invoke(app, [command, *args, "--state-dir", str(tmp_path / "state"), "--json"])
    assert response.exit_code == 2, response.stdout
    assert json.loads(response.stdout)["operation"] == command


@pytest.mark.parametrize("command", ["fork", "adopt", "install", "accept", "rollback"])
def test_apply_rejects_new_content_resolution_and_invalid_identifier(tmp_path, command):
    for args in (["--apply", "id", "--content-dir", str(tmp_path / "other")], ["--apply", "nested/id"]):
        response = CliRunner().invoke(app, [command, *args, "--state-dir", str(tmp_path / "state"), "--json"])
        assert response.exit_code in (2, 3), response.stdout
        result = json.loads(response.stdout)
        assert result["operation"] == command and not result["applied"]


@pytest.mark.parametrize(
    "command,module,function",
    [("fork", "plugin_fork_io.fork", "plan_fork"), ("update", "plugin_fork_io.updates", "plan_update")],
)
def test_execution_exceptions_are_not_empty_success(tmp_path, monkeypatch, command, module, function):
    from importlib import import_module

    roots, variant, _update = prepared(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("injected acquisition failure")

    monkeypatch.setattr(import_module(module), function, fail)
    args = [variant, "--source", str(tmp_path / "source")]
    if command == "fork":
        selected = tmp_path / "selection.json"
        selected.write_bytes(
            b'{"skills":[{"source":"skills/review","name":"review"}],"resources":[],"dependencies":[],"adaptations":[],"source_links":[]}'
        )
        args = [str(tmp_path / "source"), "--selection", str(selected)]
    response = CliRunner().invoke(app, [command, *args, "--state-dir", str(roots.state), "--json"])
    assert response.exit_code == 4
    result = json.loads(response.stdout)
    assert result["operation"] == command and result["findings"][0]["code"] == "internal.error"
