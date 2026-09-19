"""Trust decisions from each supported agent record shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_core.agent_config.trust import read_trust
from plugin_fork_io.adapters import TrustRecordSpec

CLAUDE = TrustRecordSpec("home", ".claude.json", "claude-projects", True)
CODEX = TrustRecordSpec("agent_home", "config.toml", "codex-projects", False)
PI = TrustRecordSpec("agent_home", "trust.json", "pi-map", True)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "repo"
    path.mkdir(parents=True)
    return path


def test_claude_exact_and_ancestor(tmp_path: Path, project: Path) -> None:
    record = _write(
        tmp_path / ".claude.json",
        json.dumps({"projects": {str(project): {"hasTrustDialogAccepted": True}}}),
    )
    status, findings = read_trust(CLAUDE, record, project)
    assert (status.state, status.match, status.matched_key, findings) == ("trusted", "exact", str(project), ())

    _write(record, json.dumps({"projects": {str(project.parent): {"hasTrustDialogAccepted": False}}}))
    status, _ = read_trust(CLAUDE, record, project)
    assert (status.state, status.match) == ("untrusted", "ancestor")


def test_claude_missing_projects_is_unrecorded_and_missing_field_inherits(tmp_path: Path, project: Path) -> None:
    record = _write(tmp_path / ".claude.json", "{}")
    assert read_trust(CLAUDE, record, project)[0].state == "unrecorded"

    _write(
        record,
        json.dumps(
            {"projects": {str(project): {"mcpServers": {}}, str(project.parent): {"hasTrustDialogAccepted": True}}}
        ),
    )
    status, _ = read_trust(CLAUDE, record, project)
    assert (status.state, status.match) == ("trusted", "ancestor")


def test_codex_is_exact_only_and_missing_projects_is_unrecorded(tmp_path: Path, project: Path) -> None:
    record = _write(tmp_path / "config.toml", f'[projects."{project.parent.as_posix()}"]\ntrust_level = "trusted"\n')
    status, findings = read_trust(CODEX, record, project)
    assert (status.state, findings) == ("unrecorded", ())

    _write(record, "other = true\n")
    assert read_trust(CODEX, record, project)[0].state == "unrecorded"

    _write(record, f'[projects."{project.as_posix()}"]\ntrust_level = "untrusted"\n')
    status, _ = read_trust(CODEX, record, project)
    assert (status.state, status.match) == ("untrusted", "exact")


def test_pi_nearest_ancestor_wins_and_null_is_no_decision(tmp_path: Path, project: Path) -> None:
    record = _write(
        tmp_path / "trust.json",
        json.dumps({str(project): None, str(project.parent): False, str(tmp_path): True}),
    )
    status, _ = read_trust(PI, record, project)
    assert (status.state, status.matched_key, status.match) == ("untrusted", str(project.parent), "ancestor")


def test_inheriting_agents_walk_resolved_project_ancestors(tmp_path: Path) -> None:
    real_project = tmp_path / "real" / "repo"
    real_project.mkdir(parents=True)
    linked_project = tmp_path / "linked-repo"
    try:
        linked_project.symlink_to(real_project, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    record = _write(
        tmp_path / ".claude.json",
        json.dumps({"projects": {str(real_project.parent): {"hasTrustDialogAccepted": True}}}),
    )

    status, findings = read_trust(CLAUDE, record, linked_project)

    assert (status.state, status.matched_key, status.match, findings) == (
        "trusted",
        str(real_project.parent),
        "ancestor",
        (),
    )


@pytest.mark.parametrize(
    ("text", "spec"),
    [
        ('[projects."/work/repo"]\ntrust_level = "maybe"\n', CODEX),
        ('[projects."/work/repo"]\ntrust_level = ["trusted"]\n', CODEX),
        ('[projects."/work/repo"]\ntrust_level = { value = "trusted" }\n', CODEX),
    ],
)
def test_malformed_matching_decisions_are_unknown_with_a_finding(
    tmp_path: Path, project: Path, text: str, spec: TrustRecordSpec
) -> None:
    record = _write(tmp_path / "config.toml", text.replace("/work/repo", project.as_posix()))
    status, findings = read_trust(spec, record, project)
    assert (status.state, status.matched_key, findings[0].code) == ("unknown", str(project), "agent-config.trust")


def test_missing_malformed_and_wrong_top_level_shape_are_unknown_with_a_finding(tmp_path: Path, project: Path) -> None:
    status, findings = read_trust(PI, tmp_path / "absent.json", project)
    assert status.state == "unknown" and findings[0].code == "agent-config.trust"

    bad = _write(tmp_path / "bad.json", "{not json")
    assert read_trust(PI, bad, project)[0].state == "unknown"

    listed = _write(tmp_path / "list.json", "[]")
    assert read_trust(PI, listed, project)[0].state == "unknown"

    wrong_projects = _write(tmp_path / "wrong-projects.json", '{"projects": []}')
    assert read_trust(CLAUDE, wrong_projects, project)[0].state == "unknown"


def test_invalid_stored_path_and_missing_spec_are_unknown_with_a_finding(tmp_path: Path, project: Path) -> None:
    record = _write(tmp_path / "trust.json", json.dumps({"\u0000": True}))
    status, findings = read_trust(PI, record, project)
    assert (status.state, status.record_path, findings[0].code) == ("unknown", record, "agent-config.trust")

    status, findings = read_trust(None, None, project)
    assert status.state == "unknown" and status.record_path is None and findings


def test_trailing_slash_and_case_normalisation(tmp_path: Path, project: Path) -> None:
    record = _write(tmp_path / "trust.json", json.dumps({str(project) + "/": True}))
    assert read_trust(PI, record, project)[0].state == "trusted"
