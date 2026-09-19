"""End-to-end agent configuration reads over a temporary home and project."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_core.agent_config import read_project, show
from graph_works_core.agent_config.git_state import RepositoryContext
from graph_works_core.agent_config.read import _parse
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for


class FakeGit:
    def context(self, project: Path) -> RepositoryContext:
        return RepositoryContext()

    def __init__(self, states: dict[str, str] | None = None) -> None:
        self.states = states or {}

    def state(self, project: Path, relative: str) -> tuple[str, str | None]:
        found = self.states.get(relative)
        return (found, None) if found else ("unknown", "not-a-repository")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _trusted_home(home: Path, project: Path) -> None:
    _write(home / ".claude.json", json.dumps({"projects": {str(project): {"hasTrustDialogAccepted": True}}}))
    _write(
        home / ".codex" / "config.toml",
        f'model = "u"\n[projects."{project.as_posix()}"]\ntrust_level = "trusted"\n',
    )
    _write(home / ".pi" / "agent" / "trust.json", json.dumps({str(project.parent): True}))


def test_read_project_reports_layers_trust_and_effective(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "ws" / "repo"
    project.mkdir(parents=True)
    _trusted_home(home, project)
    _write(home / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["A"]}}))
    _write(project / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["B"]}}))
    _write(
        project / ".codex" / "config.toml",
        'model = "p"\nwhen = 2026-09-18T10:00:00Z\nday = 2026-09-18\ntime = 10:00:00\nitems = ["x"]\n',
    )
    git = FakeGit({".claude/settings.json": "committed", ".claude/settings.local.json": "ignored"})

    result = read_project(project, home=home, env={}, platform="linux", git=git)

    claude, codex, pi = result.agents
    assert result.exists and [agent.agent for agent in result.agents] == ["claude", "codex", "pi"]
    assert claude.trust.state == "trusted" and claude.trust.match == "exact"
    assert [(layer.scope, layer.parse, layer.git) for layer in claude.layers] == [
        ("user", "ok", "outside-repo"),
        ("project", "ok", "committed"),
        ("local", "absent", "ignored"),
        ("managed", "absent", "outside-repo"),
    ]
    assert claude.effective == {"permissions": {"allow": ["A", "B"]}}
    assert codex.effective["model"] == "p" and codex.effective["when"] == "2026-09-18T10:00:00+00:00"
    assert codex.effective["day"] == "2026-09-18"
    assert codex.effective["time"] == "10:00:00"
    assert codex.effective["items"] == ["x"]
    assert "projects" in codex.layers[0].data  # The user data remains verbatim, including the trust table.
    assert pi.trust.match == "ancestor"
    json.dumps([agent.effective for agent in result.agents])


def test_malformed_layers_never_raise(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "p"
    _write(project / ".pi" / "settings.json", "{broken")
    _write(project / ".codex" / "config.toml", "= nope")
    _write(project / ".claude" / "settings.json", "[1, 2]")
    (project / ".claude" / "settings.local.json").mkdir(parents=True)

    result = read_project(project, home=home, env={}, platform="linux", git=FakeGit())

    for agent in result.agents:
        codes = {finding.code for finding in agent.findings}
        assert "agent-config.parse" in codes or agent.agent == "claude"
    claude = result.agents[0]
    assert [layer.parse for layer in claude.layers[1:3]] == ["error", "error"]
    assert all(layer.data is None for layer in claude.layers[1:3])


@pytest.mark.parametrize(
    ("fmt", "text", "expected"),
    [
        ("json", '\ufeff{"nested": {"item": 1}}', "ok"),
        ("json", "[1, 2]", "error"),
        ("json", b"\xff".decode("latin-1"), "error"),
    ],
)
def test_parse_tolerates_bom_decoding_and_nonobject_content(tmp_path: Path, fmt: str, text: str, expected: str) -> None:
    path = tmp_path / f"settings.{fmt}"
    path.write_bytes(text.encode("latin-1") if text == b"\xff".decode("latin-1") else text.encode("utf-8"))

    _, parsed, _, data = _parse(path, fmt)  # type: ignore[arg-type]

    assert parsed == expected
    assert (data is not None) is (expected == "ok")


def test_parse_tolerates_directories_and_absent_files(tmp_path: Path) -> None:
    directory = tmp_path / "settings.json"
    directory.mkdir()

    _, parsed, error, data = _parse(directory, "json")
    assert parsed == "error" and error and data is None
    assert _parse(tmp_path / "missing.json", "json") == (False, "absent", None, None)


def test_untrusted_and_unknown_trust_gate_layers(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "p"
    _write(project / ".pi" / "settings.json", json.dumps({"k": 1}))
    _write(home / ".pi" / "agent" / "trust.json", json.dumps({str(project): False}))

    pi = read_project(project, home=home, env={}, agents=("pi",), platform="linux", git=FakeGit()).agents[0]
    assert (pi.layers[1].applied, pi.layers[1].excluded_reason) == (False, "untrusted")
    assert pi.effective == {}

    (home / ".pi" / "agent" / "trust.json").unlink()
    pi = read_project(project, home=home, env={}, agents=("pi",), platform="linux", git=FakeGit()).agents[0]
    assert pi.trust.state == "unknown" and pi.trust.assumed
    assert pi.effective == {"k": 1}
    assert "agent-config.trust-assumed" in {finding.code for finding in pi.findings}


def test_home_env_relocation_is_honoured(tmp_path: Path) -> None:
    home, project, moved = tmp_path / "home", tmp_path / "p", tmp_path / "codexhome"
    project.mkdir()
    _write(moved / "config.toml", 'model = "moved"\n')

    codex = read_project(
        project,
        home=home,
        env={"CODEX_HOME": str(moved)},
        agents=("codex",),
        platform="linux",
        git=FakeGit(),
    ).agents[0]

    assert codex.home == moved and codex.effective == {"model": "moved"}


def test_read_project_resolves_missing_paths_and_uses_default_git_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    probe = FakeGit({".pi/settings.json": "untracked"})
    monkeypatch.setattr("graph_works_core.agent_config.read.SubprocessGit", lambda: probe)

    assert read_project(tmp_path / "missing", home=tmp_path / "home", env={}).exists is False
    result = read_project(project, home=tmp_path / "home", env={}, agents=("pi",), platform="linux")

    assert result.path == project.resolve()
    assert result.agents[0].layers[1].git == "untracked"


def _layout(root: Path, repos: dict[str, Path]):
    root.mkdir(parents=True, exist_ok=True)
    body = "".join(f'  {name}:\n    path: "{path}"\n' for name, path in repos.items())
    _write(root / "workspace.yaml", "version: 1\n" + (f"repositories:\n{body}" if repos else ""))
    return layout_for(root)


def test_show_dedupes_root_and_declared_repo_and_reports_missing_repo(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    layout = _layout(root, {"self": root, "gone": tmp_path / "missing"})

    report = show(layout, home=tmp_path / "home", env={}, platform="linux", git=FakeGit())

    assert [(item.path, item.exists) for item in report.projects] == [
        (root.resolve(), True),
        ((tmp_path / "missing").resolve(), False),
    ]
    assert report.projects[1].agents == ()


def test_show_with_project_resolves_relative_and_symlink_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    _write(root / ".pi" / "settings.json", '{"project": true}')
    monkeypatch.chdir(tmp_path)

    report = show(
        None,
        project=Path("linked"),
        home=tmp_path / "home",
        env={},
        agents=("pi",),
        platform="linux",
        git=FakeGit({".pi/settings.json": "committed"}),
    )

    assert report.projects[0].path == root.resolve()
    assert report.projects[0].agents[0].layers[1].git == "committed"


def test_show_with_project_needs_no_layout(tmp_path: Path) -> None:
    report = show(None, project=tmp_path, home=tmp_path / "h", env={}, agents=("pi",), platform="linux", git=FakeGit())
    assert len(report.projects) == 1 and report.projects[0].agents[0].agent == "pi"
    with pytest.raises(ValueError):
        show(None, home=tmp_path, env={})


def test_show_without_manifest_reports_root_only_and_malformed_manifest_raises(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    layout = _layout(root, {})
    (root / "workspace.yaml").unlink()
    assert [item.path for item in show(layout, home=tmp_path, env={}, git=FakeGit()).projects] == [root.resolve()]
    _write(root / "workspace.yaml", "repositories: [not, a, mapping]\n")
    with pytest.raises(WorkspaceError):
        show(layout, home=tmp_path, env={}, git=FakeGit())


def test_unreadable_layer_with_unreadable_stat_preserves_other_agents(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "p"
    target = project / ".claude" / "settings.json"
    _write(target, "{}")
    _write(project / ".pi" / "settings.json", '{"survives": true}')
    read_bytes, exists = Path.read_bytes, Path.exists

    def denied_read(path):
        if path == target:
            raise PermissionError("read denied")
        return read_bytes(path)

    def denied_exists(path):
        if path == target:
            raise PermissionError("stat denied")
        return exists(path)

    monkeypatch.setattr(Path, "read_bytes", denied_read)
    monkeypatch.setattr(Path, "exists", denied_exists)
    result = read_project(project, home=home, env={}, git=FakeGit())
    claude, _, pi = result.agents
    assert claude.layers[1].parse == "error"
    assert claude.layers[1].parse_error == "read denied"
    assert any(f.code == "agent-config.parse" and f.message == "read denied" for f in claude.findings)
    assert pi.effective == {"survives": True}


@pytest.mark.parametrize("depth", [100, 1100, 5000])
@pytest.mark.parametrize("container", ["object", "array"])
def test_deep_layer_is_diagnosed_and_other_content_remains_encodable(tmp_path, depth, container):
    home, project = tmp_path / "home", tmp_path / "p"
    nested = '{"a":' * depth + "0" + "}" * depth if container == "object" else "[" * depth + "0" + "]" * depth
    _write(project / ".claude" / "settings.json", '{"deep":' + nested + "}")
    _write(home / ".claude" / "settings.json", '{"survives": true}')
    _write(project / ".pi" / "settings.json", '{"other": true}')
    result = show(None, project=project, home=home, env={}, git=FakeGit())
    claude, _, pi = result.projects[0].agents
    assert claude.layers[1].parse == "error"
    assert claude.layers[1].parse_error
    assert any(f.code == "agent-config.parse" for f in claude.findings)
    assert claude.effective == {"survives": True}
    assert pi.effective == {"other": True}
    json.dumps([agent.effective for agent in result.projects[0].agents])
