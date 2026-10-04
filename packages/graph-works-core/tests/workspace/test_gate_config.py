"""The per-repository gate block: catalog-declared, validated, layered."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.workspace import gate_config, manifest
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.gate_config import RepoGate, ScopedGate, repo_gate
from graph_works_core.workspace.layout import layout_for


def _layout(tmp_path: Path, gate: str):
    (tmp_path / "code").mkdir()
    (tmp_path / "workspace.yaml").write_text(
        f"version: 1\nrepositories:\n  code:\n    path: code\n{gate}", encoding="utf-8", newline="\n"
    )
    return layout_for(tmp_path)


def test_absent_gate_is_empty(tmp_path: Path) -> None:
    assert repo_gate(_layout(tmp_path, ""), "code") == RepoGate(None, None)


def test_full_and_scoped_resolve(tmp_path: Path) -> None:
    layout = _layout(
        tmp_path,
        "    gate:\n      full: just check\n      scoped:\n        roots: packages/*\n"
        "        command: just check-pkg {name}\n",
    )
    assert repo_gate(layout, "code") == RepoGate("just check", ScopedGate("packages/*", "just check-pkg {name}"))


def test_local_overlay_overrides_full(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "    gate:\n      full: just check\n")
    (tmp_path / "workspace.local.yaml").write_text(
        "repositories:\n  code:\n    gate:\n      full: just check-fast\n", encoding="utf-8", newline="\n"
    )
    assert repo_gate(layout, "code").full == "just check-fast"


@pytest.mark.parametrize(
    ("gate", "key"),
    [
        ("    gate:\n      full: '   '\n", "repositories.code.gate.full"),
        ("    gate:\n      full: 3\n", "repositories.code.gate.full"),
        ("    gate:\n      scoped:\n        roots: packages/*\n", "repositories.code.gate.scoped.command"),
        ("    gate:\n      scoped:\n        command: x {name}\n", "repositories.code.gate.scoped.roots"),
        ("    gate:\n      scoped:\n        roots: p/*\n        command: x\n", "repositories.code.gate.scoped.command"),
        (
            "    gate:\n      scoped:\n        roots: p/*\n        command: x {name} {other}\n",
            "repositories.code.gate.scoped.command",
        ),
        (
            "    gate:\n      scoped:\n        roots: /abs/*\n        command: x {name}\n",
            "repositories.code.gate.scoped.roots",
        ),
        (
            "    gate:\n      scoped:\n        roots: ../p/*\n        command: x {name}\n",
            "repositories.code.gate.scoped.roots",
        ),
    ],
)
def test_malformed_gate_names_the_key(tmp_path: Path, gate: str, key: str) -> None:
    with pytest.raises(WorkspaceError, match=key.replace(".", r"\.")):
        repo_gate(_layout(tmp_path, gate), "code")


def test_gate_keys_are_settable_through_the_catalog(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "")
    manifest.set_value(layout.manifest_path, "repositories.code.gate.full", "make check")
    manifest.set_value(layout.manifest_path, "repositories.code.gate.scoped.roots", "packages/*")
    manifest.set_value(layout.manifest_path, "repositories.code.gate.scoped.command", "make pkg {name}")
    assert repo_gate(layout, "code") == RepoGate("make check", ScopedGate("packages/*", "make pkg {name}"))
    listed = {r.key for r in manifest.resolve_checked_all(layout, environ={})}
    assert "repositories.code.gate.full" in listed


def test_units_and_extra_inputs_are_read(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "    gate:\n      full: just check\n      units: emit\n      extra_inputs: [docs/**]\n")
    gate = repo_gate(layout, "code")
    assert gate.units == "emit" and gate.extra_inputs == ("docs/**",)


@pytest.mark.parametrize("value", ["../x", "/abs/**", "C:/abs/**", "C:relative/**", r"a\b", ""])
def test_extra_inputs_must_be_repository_relative(tmp_path: Path, value: str) -> None:
    layout = _layout(tmp_path, f"    gate:\n      full: just check\n      extra_inputs: ['{value}']\n")
    with pytest.raises(WorkspaceError, match="extra_inputs"):
        repo_gate(layout, "code")


def test_unit_jobs_defaults_to_none(tmp_path: Path) -> None:
    assert gate_config.unit_jobs(_layout(tmp_path, "")) is None


@pytest.mark.parametrize("value", ["0", "-1", "true", "'two'"])
def test_unit_jobs_refuses_nonpositive_or_noninteger(tmp_path: Path, value: str) -> None:
    layout = _layout(tmp_path, f"workflow:\n  gate:\n    unit_jobs: {value}\n")
    with pytest.raises(WorkspaceError, match="unit_jobs"):
        gate_config.unit_jobs(layout)


def test_new_gate_settings_overlay_and_settable_catalog(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "    gate:\n      units: emit-shared\n      extra_inputs: [docs/**]\n")
    manifest.set_value(layout.manifest_path, "workflow.gate.unit_jobs", "3")
    assert gate_config.unit_jobs(layout) == 3
    (tmp_path / "workspace.local.yaml").write_text(
        "repositories:\n  code:\n    gate:\n      units: emit-local\n      extra_inputs: [local/**]\n"
        "workflow:\n  gate:\n    unit_jobs: 1\n",
        encoding="utf-8",
        newline="\n",
    )
    gate = repo_gate(layout, "code")
    assert gate.units == "emit-local" and gate.extra_inputs == ("local/**",)
    assert gate_config.unit_jobs(layout) == 1


@pytest.mark.parametrize("value", ["'not-a-list'", "[1]", "true"])
def test_extra_inputs_refuses_wrong_types(tmp_path: Path, value: str) -> None:
    layout = _layout(tmp_path, f"    gate:\n      extra_inputs: {value}\n")
    with pytest.raises(WorkspaceError, match="extra_inputs"):
        repo_gate(layout, "code")


@pytest.mark.parametrize("value", ["3", "' '"])
def test_units_refuses_wrong_type_or_empty_command(tmp_path: Path, value: str) -> None:
    layout = _layout(tmp_path, f"    gate:\n      units: {value}\n")
    with pytest.raises(WorkspaceError, match="units"):
        repo_gate(layout, "code")
