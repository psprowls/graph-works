from pathlib import Path

import pytest
from code_wiki_okf.config import ConfigError, RepoConfig, StateGateConfig, load_config


def _write(root: Path, text: str) -> None:
    (root / "_repositories.yaml").write_text(text, encoding="utf-8")


def test_load_config_good_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "graph_dir: ../graphs/code\n"
        "repositories:\n"
        "  agent-workspace:\n"
        "    path: ../../agent-workspace\n"
        "    ignore:\n"
        '      - "packages/*/tests/fixtures/**"\n'
        "ignore:\n"
        '  - "*.lock"\n'
        "state_gate:\n"
        "  enabled: true\n"
        "  branches: [main]\n",
    )
    config = load_config(tmp_path)
    assert config.graph_dir == (tmp_path / "../graphs/code").resolve()
    assert config.repos == (
        RepoConfig(
            name="agent-workspace",
            path=(tmp_path / "../../agent-workspace").resolve(),
            ignore=("*.lock", "packages/*/tests/fixtures/**"),
        ),
    )
    assert config.state_gate == StateGateConfig(enabled=True, branches=("main",))


def test_load_config_state_gate_defaults_when_absent(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\n")
    config = load_config(tmp_path)
    assert config.state_gate == StateGateConfig(enabled=True, branches=("main",))


def test_load_config_missing_graph_dir_raises(tmp_path: Path) -> None:
    _write(tmp_path, "repositories: {}\n")
    with pytest.raises(ConfigError, match="graph_dir"):
        load_config(tmp_path)


def test_load_config_unknown_top_level_key_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nbogus: 1\n")
    with pytest.raises(ConfigError, match="bogus"):
        load_config(tmp_path)


def test_load_config_repo_missing_path_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nrepositories:\n  agent-workspace: {}\n")
    with pytest.raises(ConfigError, match="path"):
        load_config(tmp_path)


def test_load_config_not_valid_yaml_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(tmp_path)


def test_load_config_tilde_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path, "graph_dir: ~/graphs/code\n")
    config = load_config(tmp_path)
    assert config.graph_dir == (tmp_path / "graphs/code")


def test_load_config_missing_file_propagates_os_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        load_config(tmp_path)


def test_load_config_repo_entry_not_mapping_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nrepositories:\n  agent-workspace: 123\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(tmp_path)


def test_load_config_state_gate_not_mapping_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nstate_gate: hello\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(tmp_path)


def test_load_config_global_ignore_not_list_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nignore: hello\n")
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load_config(tmp_path)


def test_load_config_repo_ignore_not_list_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "graph_dir: ../graphs/code\nrepositories:\n  agent-workspace:\n"
        "    path: ../../agent-workspace\n    ignore: 123\n",
    )
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load_config(tmp_path)


def test_load_config_repo_unknown_key_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "graph_dir: ../graphs/code\nrepositories:\n  agent-workspace:\n    path: ../../agent-workspace\n    bogus: 1\n",
    )
    with pytest.raises(ConfigError, match="bogus"):
        load_config(tmp_path)


def test_load_config_state_gate_unknown_key_raises(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nstate_gate:\n  bogus: 1\n")
    with pytest.raises(ConfigError, match="bogus"):
        load_config(tmp_path)


def test_load_config_state_gate_enabled_not_bool_raises(tmp_path: Path) -> None:
    _write(tmp_path, 'graph_dir: ../graphs/code\nstate_gate:\n  enabled: "yes"\n')
    with pytest.raises(ConfigError, match="must be a boolean"):
        load_config(tmp_path)


def test_load_config_state_gate_empty_branches_is_valid(tmp_path: Path) -> None:
    _write(tmp_path, "graph_dir: ../graphs/code\nstate_gate:\n  branches: []\n")
    config = load_config(tmp_path)
    assert config.state_gate == StateGateConfig(enabled=True, branches=())


def test_declarations_dir_defaults_to_the_bundle_root(tmp_path: Path) -> None:
    """The default is today's layout: a bundle describes itself, and the
    question of relocating declarations does not arise until someone opts in."""
    (tmp_path / "_repositories.yaml").write_text("graph_dir: ../graphs/code\n", encoding="utf-8")
    config = load_config(tmp_path)
    assert config.declarations_dir == tmp_path


def test_declarations_dir_resolves_relative_to_the_bundle_root(tmp_path: Path) -> None:
    """Resolved exactly as `graph_dir` is -- one rule for every path key."""
    (tmp_path / "_repositories.yaml").write_text(
        "graph_dir: ../graphs/code\ndeclarations_dir: ../shared-declarations\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.declarations_dir == (tmp_path / ".." / "shared-declarations").resolve()


def test_declarations_dir_accepts_an_absolute_path(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    (tmp_path / "_repositories.yaml").write_text(
        f"graph_dir: ../graphs/code\ndeclarations_dir: {elsewhere}\n", encoding="utf-8"
    )
    assert load_config(tmp_path).declarations_dir == elsewhere


def test_declarations_dir_tilde_expands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "_repositories.yaml").write_text(
        "graph_dir: ../graphs/code\ndeclarations_dir: ~/shared-declarations\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config.declarations_dir == (tmp_path / "shared-declarations")


def test_a_blank_declarations_dir_is_a_config_error(tmp_path: Path) -> None:
    """Present-but-empty is a typo, not a request for the default."""
    (tmp_path / "_repositories.yaml").write_text(
        'graph_dir: ../graphs/code\ndeclarations_dir: "  "\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="declarations_dir"):
        load_config(tmp_path)
