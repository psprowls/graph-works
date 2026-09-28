"""The per-repository gate block: catalog-declared, validated, layered."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.workspace import manifest
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
