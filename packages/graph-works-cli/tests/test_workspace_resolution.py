"""`resolve_workspace()` (D-002) — `graph_works_core.workspace.discovery.resolve` mocked, per the
design spec's own test strategy: `graph-works-core` is a real dependency of this package (Task 1),
so `monkeypatch.setattr` on the real imported name is enough — no `sys.modules` injection needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import typer
from graph_works_core.workspace.errors import WorkspaceNotFound
from graph_works_core.workspace.layout import WorkspaceLayout


@pytest.fixture
def fake_resolve(monkeypatch):
    """Patch `graph_works_core.workspace.discovery.resolve` (the name `workspace_resolution.py`
    imports) and return the call log."""
    calls: list[dict[str, object]] = []

    def _fake(*, workspace=None, cwd=None, environ=None):
        calls.append({"workspace": workspace, "cwd": cwd, "environ": environ})
        if workspace == "__missing__":
            raise WorkspaceNotFound("no workspace.yaml here")
        return WorkspaceLayout(
            root=Path(workspace or "/default"),
            config_dir=Path("/default/_config"),
            cache_dir=Path("/default/_cache"),
            bundle_dir=Path("/default/okf"),
            worktrees_dir=Path("/default/worktrees"),
        )

    monkeypatch.setattr("graph_works_cli.workspace_resolution.discovery.resolve", _fake)
    return calls


def test_forwards_workspace_cwd_and_environ(fake_resolve, monkeypatch, tmp_path) -> None:
    from graph_works_cli.workspace_resolution import resolve_workspace

    monkeypatch.chdir(tmp_path)
    expected_cwd = Path.cwd()

    result = resolve_workspace("/explicit/path")

    assert fake_resolve == [{"workspace": "/explicit/path", "cwd": expected_cwd, "environ": os.environ}]
    assert result.root == Path("/explicit/path")


def test_empty_workspace_string_becomes_none(fake_resolve) -> None:
    from graph_works_cli.workspace_resolution import resolve_workspace

    resolve_workspace("")

    assert fake_resolve[0]["workspace"] is None


def test_workspace_not_found_exits_not_initialized(fake_resolve, capsys) -> None:
    from graph_works_cli import exit_codes
    from graph_works_cli.workspace_resolution import resolve_workspace

    with pytest.raises(typer.Exit) as exc_info:
        resolve_workspace("__missing__")

    assert exc_info.value.exit_code == exit_codes.NOT_INITIALIZED
    assert "no workspace.yaml here" in capsys.readouterr().err
