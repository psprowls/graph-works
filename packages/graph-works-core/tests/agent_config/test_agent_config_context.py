"""Repository identity, actual local files and permission trust through public reads."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from graph_works_core.agent_config import read_project
from graph_works_core.agent_config.git_state import SubprocessGit


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")


def git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    ignore = tmp_path / "empty-ignore"
    ignore.write_text("", encoding="utf-8", newline="\n")
    git(root, "config", "core.excludesFile", str(ignore))
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "init")
    return root


def test_claude_configuration_home_local_permissions_skip_trust(tmp_path):
    home = tmp_path / "home"
    write(home / ".claude.json", {"projects": {}})
    write(home / ".claude/settings.local.json", {"permissions": {"allow": ["A"], "additionalDirectories": ["x"]}})
    agent = read_project(home, home=home, env={}, agents=("claude",)).agents[0]
    assert agent.trust.state == "unrecorded"
    assert agent.effective == {"permissions": {"allow": ["A"], "additionalDirectories": ["x"]}}
    assert all(key.effective_from == ("local",) for key in agent.keys)


def test_claude_nested_clone_cannot_inherit_outer_trust(tmp_path, repo):
    child = repo / "nested"
    child.mkdir()
    git(child, "init", "-q")
    home = tmp_path / "home"
    write(home / ".claude.json", {"projects": {str(repo): {"hasTrustDialogAccepted": True}}})
    write(child / ".claude/settings.json", {"permissions": {"allow": ["A"]}})
    agent = read_project(child, home=home, env={}, agents=("claude",)).agents[0]
    assert agent.trust.state == "unrecorded"
    assert agent.effective == {}


def test_codex_subdirectory_uses_repository_trust_but_direct_decision_wins(tmp_path, repo):
    project, home = repo / "sub", tmp_path / "home"
    write(project / ".codex/dummy.json", {})
    target = home / ".codex/config.toml"
    target.parent.mkdir(parents=True)
    target.write_text(f'[projects."{repo.as_posix()}"]\ntrust_level = "trusted"\n', encoding="utf-8", newline="\n")
    agent = read_project(project, home=home, env={}, agents=("codex",)).agents[0]
    assert agent.trust.state == "trusted" and agent.trust.matched_key == str(repo)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f'[projects."{project.as_posix()}"]\ntrust_level = "untrusted"\n')
    assert read_project(project, home=home, env={}, agents=("codex",)).agents[0].trust.state == "untrusted"


def test_failed_git_identity_is_uncertain_not_unrecorded(tmp_path):
    write(tmp_path / "home/.claude.json", {"projects": {}})
    agent = read_project(
        tmp_path, home=tmp_path / "home", env={}, agents=("claude",), git=SubprocessGit("git-definitely-not-installed")
    ).agents[0]
    assert agent.trust.state == "unknown" and agent.trust.assumed
    assert any("git-missing" in finding.message for finding in agent.findings)


def test_linked_worktree_uses_main_trust_and_local_file_plus_legacy(tmp_path, repo):
    linked, home = tmp_path / "linked", tmp_path / "home"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    project = linked / "sub"
    write(project / ".claude/settings.local.json", {"value": "legacy", "permissions": {"allow": ["legacy"]}})
    write(repo / ".claude/settings.local.json", {"value": "root", "permissions": {"allow": ["root"]}})
    write(home / ".claude.json", {"projects": {str(repo): {"hasTrustDialogAccepted": True}}})
    agent = read_project(
        project, home=home, env={}, agents=("claude",), platform="linux", user_id=repo.stat().st_uid
    ).agents[0]
    assert agent.trust.state == "trusted" and agent.trust.matched_key == str(repo)
    local = [layer for layer in agent.layers if layer.scope == "local"]
    assert [layer.path for layer in local] == [
        project / ".claude/settings.local.json",
        repo / ".claude/settings.local.json",
    ]
    assert local[1].git == "untracked"
    assert agent.effective == {"value": "root", "permissions": {"allow": ["legacy", "root"]}}


@pytest.mark.parametrize("platform,ownership", [("win32", "ours"), ("linux", "foreign"), ("linux", "missing")])
def test_local_path_platform_and_ownership_exceptions(tmp_path, repo, platform, ownership):
    home, project = tmp_path / "home", repo / "sub"
    write(project / ".claude/settings.local.json", {"location": "starting"})
    write(repo / ".claude/settings.local.json", {"location": "root"})
    user_id = None if ownership == "missing" else repo.stat().st_uid + (ownership == "foreign")
    agent = read_project(project, home=home, env={}, agents=("claude",), platform=platform, user_id=user_id).agents[0]
    local = next(layer for layer in agent.layers if layer.scope == "local")
    assert local.path == project / ".claude/settings.local.json"
    if ownership == "missing":
        assert not local.applied and "location" not in agent.effective
        assert any(f.code == "agent-config.local-location" for f in agent.findings)
    else:
        assert agent.effective == {"location": "starting"}


def test_repository_root_is_home_keeps_local_in_starting_directory(tmp_path, repo):
    project = repo / "sub"
    write(project / ".claude/settings.local.json", {"location": "starting"})
    write(repo / ".claude/settings.local.json", {"location": "root"})
    agent = read_project(project, home=repo, env={}, agents=("claude",), user_id=repo.stat().st_uid).agents[0]
    assert agent.effective == {"location": "starting"}


def test_relocated_configuration_home_exception_stops_when_local_moves_to_repo_root(tmp_path, repo):
    project, home = repo / "sub", tmp_path / "home"
    write(home / ".claude.json", {"projects": {}})
    write(repo / ".claude/settings.local.json", {"permissions": {"allow": ["root"]}})
    project.mkdir()
    agent = read_project(
        project,
        home=home,
        env={"CLAUDE_CONFIG_DIR": str(project / ".claude")},
        agents=("claude",),
        user_id=repo.stat().st_uid,
    ).agents[0]
    assert agent.effective == {}
    assert agent.layers[2].path == repo / ".claude/settings.local.json"


@pytest.mark.parametrize("symlink,tracked", [(False, False), (True, False), (False, True)])
def test_parent_acceptance_approves_only_personal_local_rules(tmp_path, symlink, tracked):
    # A context seam models the already-resolved outside-repository case. A
    # tracked observation exercises the repository-supplied local-file gate.
    from graph_works_core.agent_config.git_state import RepositoryContext

    home, project = tmp_path / "home", tmp_path / "parent/sub"
    write(home / ".claude.json", {"projects": {str(project.parent): {"hasTrustDialogAccepted": True}}})
    write(project / ".claude/settings.local.json", {"permissions": {"allow": ["local"]}})
    write(project / ".claude/settings.json", {"permissions": {"allow": ["shared"]}})
    if symlink:
        real = project / "real"
        (project / ".claude").rename(real)
        (project / ".claude").symlink_to(real, target_is_directory=True)

    class Probe:
        def state(self, project, relative):
            return ("committed" if tracked else "untracked", None)

    agent = read_project(
        project, home=home, env={}, agents=("claude",), git=Probe(), context=RepositoryContext()
    ).agents[0]
    expected = {} if symlink or tracked else {"permissions": {"allow": ["local"]}}
    assert agent.effective == expected


def test_pretrust_untracked_local_rules_remain_gated(tmp_path, repo):
    home = tmp_path / "home"
    write(home / ".claude.json", {"projects": {}})
    write(repo / ".claude/settings.local.json", {"permissions": {"allow": ["A"]}})
    agent = read_project(repo, home=home, env={}, agents=("claude",)).agents[0]
    assert agent.layers[2].git == "untracked" and agent.effective == {}


def test_codex_custom_marker_and_main_worktree_trust_lookup(tmp_path, repo):
    linked, home = tmp_path / "linked", tmp_path / "home"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    project = linked / "marked/sub"
    project.mkdir(parents=True)
    (project.parent / "marker").touch()
    target = home / ".codex/config.toml"
    target.parent.mkdir(parents=True)
    target.write_text(
        f'project_root_markers = ["marker"]\n[projects."{repo.as_posix()}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
        newline="\n",
    )
    assert read_project(project, home=home, env={}, agents=("codex",)).agents[0].trust.matched_key == str(repo)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f'[projects."{project.parent.as_posix()}"]\ntrust_level = "untrusted"\n')
    assert read_project(project, home=home, env={}, agents=("codex",)).agents[0].trust.state == "untrusted"


def test_old_state_only_probe_is_usable_but_repository_identity_is_explicitly_unknown(tmp_path):
    class Probe:
        def state(self, project, relative):
            return "untracked", None

    write(tmp_path / "home/.claude.json", {"projects": {}})
    agent = read_project(tmp_path, home=tmp_path / "home", env={}, agents=("claude",), git=Probe()).agents[0]
    assert agent.trust.state == "unknown" and agent.trust.assumed
    assert any("does not provide repository identity" in f.message for f in agent.findings)


def test_ownership_stat_failure_is_an_explicit_local_location_finding(tmp_path, repo, monkeypatch):
    project = repo / "sub"
    write(project / ".claude/settings.local.json", {"x": 1})
    real = Path.lstat

    def denied(path):
        if path == repo / ".git":
            raise PermissionError("owner denied")
        return real(path)

    monkeypatch.setattr(Path, "lstat", denied)
    agent = read_project(
        project, home=tmp_path / "home", env={}, agents=("claude",), user_id=repo.stat().st_uid
    ).agents[0]
    assert not agent.layers[2].applied
    assert any(f.code == "agent-config.local-location" and "owner denied" in f.message for f in agent.findings)


def test_configuration_home_override_outside_git_applies_local_rules(tmp_path):
    home, project = tmp_path / "home", tmp_path / "config-home"
    write(home / ".claude.json", {"projects": {}})
    write(project / ".claude/settings.local.json", {"permissions": {"allow": ["A"]}})
    agent = read_project(
        project, home=home, env={"CLAUDE_CONFIG_DIR": str(project / ".claude")}, agents=("claude",)
    ).agents[0]
    assert agent.effective == {"permissions": {"allow": ["A"]}}


def test_nonrepository_parent_trust_applies_no_shared_allow_rules(tmp_path):
    home, project = tmp_path / "home", tmp_path / "parent/sub"
    write(home / ".claude.json", {"projects": {str(project.parent): {"hasTrustDialogAccepted": True}}})
    write(project / ".claude/settings.json", {"permissions": {"allow": ["shared"], "deny": ["deny"]}})
    write(project / ".claude/settings.local.json", {"permissions": {"allow": ["personal"]}})
    agent = read_project(project, home=home, env={}, agents=("claude",)).agents[0]
    assert agent.trust.state == "trusted" and agent.trust.match == "ancestor"
    assert agent.effective == {"permissions": {"deny": ["deny"], "allow": ["personal"]}}
