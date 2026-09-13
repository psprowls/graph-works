import pytest
from plugin_fork_io.config import ConfigError, resolve_settings


def test_config_relative_paths_and_explicit_overrides(tmp_path, monkeypatch):
    config_path = tmp_path / "settings/config.json"
    config = {"schema_version": 1, "defaults": {"state_dir": "records", "content_dir": "skills"}}
    monkeypatch.chdir(tmp_path)
    resolved = resolve_settings(
        {"project": tmp_path, "content_dir": tmp_path / "override"},
        config,
        config_path,
        home=tmp_path,
        platform="linux",
        environment={},
    )
    assert resolved.roots.state == tmp_path / "settings/records"
    assert resolved.roots.content == tmp_path / "override"
    assert not (tmp_path / "settings").exists()


def test_config_defaults_and_future_schema(tmp_path):
    resolved = resolve_settings({"project": tmp_path}, None, None, home=tmp_path, platform="linux", environment={})
    assert resolved.roots.state == tmp_path / ".plugin-fork"
    assert resolved.config_path == tmp_path / ".config/plugin-fork/config.json"
    with pytest.raises(ConfigError):
        resolve_settings(
            {"project": tmp_path}, {"schema_version": 2}, None, home=tmp_path, platform="linux", environment={}
        )


@pytest.mark.parametrize(
    "config",
    [
        {"schema_version": True},
        {"schema_version": 1, "defaults": []},
        {"schema_version": 1, "defaults": {"state_dir": 3}},
        {"schema_version": 1, "projects": [{"project": "client", "variants": "id"}]},
    ],
)
def test_malformed_config_refused(tmp_path, config):
    with pytest.raises(ConfigError):
        resolve_settings({"project": tmp_path}, config, None, home=tmp_path, platform="linux", environment={})


def test_windows_appdata_and_xdg_defaults(tmp_path):
    with pytest.raises(ConfigError, match="APPDATA"):
        resolve_settings({"project": tmp_path}, None, None, home=tmp_path, platform="win32", environment={})
    windows = resolve_settings(
        {"project": tmp_path},
        None,
        None,
        home=tmp_path,
        platform="win32",
        environment={"APPDATA": str(tmp_path / "roaming")},
    )
    assert windows.config_path == tmp_path / "roaming/plugin-fork/config.json"
    explicit = resolve_settings(
        {"project": tmp_path}, None, tmp_path / "explicit.json", home=tmp_path, platform="win32", environment={}
    )
    assert explicit.config_path == tmp_path / "explicit.json"
    xdg = resolve_settings(
        {"project": tmp_path},
        None,
        None,
        home=tmp_path,
        platform="linux",
        environment={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )
    assert xdg.config_path == tmp_path / "xdg/plugin-fork/config.json"


def test_ambiguous_stores_require_explicit_selection(tmp_path):
    config = {
        "schema_version": 1,
        "projects": [
            {"project": "client", "state_dir": "first", "variants": ["variant"]},
            {"project": "other", "state_dir": "second", "variants": ["variant"]},
        ],
    }
    options = {"project": tmp_path / "client", "variant": "variant"}
    with pytest.raises(ConfigError, match="Ambiguous"):
        resolve_settings(options, config, tmp_path / "config.json", home=tmp_path, platform="linux", environment={})
    resolved = resolve_settings(
        {**options, "state_dir": tmp_path / "second"},
        config,
        tmp_path / "config.json",
        home=tmp_path,
        platform="linux",
        environment={},
    )
    assert resolved.roots.state == tmp_path / "second"


def test_cross_volume_binding_is_explicit_not_a_fake_relative_path():
    from pathlib import Path

    from plugin_fork_io.config import portable_content_root

    assert portable_content_root(Path("D:/client/skills"), Path("C:/tracking"), platform="win32") is None
    assert portable_content_root(Path("C:/client/skills"), Path("C:/tracking"), platform="win32") == "../client/skills"


@pytest.mark.parametrize("document", [b"null", b"[]", b'"settings"', b"1"])
def test_present_non_object_config_is_a_structured_refusal(tmp_path, document):
    import json

    from plugin_fork_io.cli import app
    from typer.testing import CliRunner

    config = tmp_path / "config.json"
    config.write_bytes(document)
    result = CliRunner().invoke(app, ["status", "unused", "--config", str(config), "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["findings"][0]["code"] == "config.invalid"
