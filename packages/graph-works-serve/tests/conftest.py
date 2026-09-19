"""Shared serve fixtures: a real initialized workspace, a guarded client, and the CLI twin."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from graph_works_cli.cli import app as cli_app
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.app import build_app
from graph_works_serve.context import ServeContext
from starlette.testclient import TestClient
from typer.testing import CliRunner

TOKEN = "test-token"
PORT = 4321
TODAY = date(2026, 9, 18)

_ITEM = """---
type: Feature
title: {slug}
description: d
status: stable
work_status: open
phase: {phase}
effort: medium
opened: 2026-09-01
updated: 2026-09-02
affects:
- packages/a
{extra_fm}---

## Summary
d
{body}
## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def write_item(
    layout: WorkspaceLayout,
    slug: str,
    *,
    phase: str = "execute",
    body: str = "",
    extra_fm: str = "",
) -> str:
    path = f"work/{slug}"
    text = _ITEM.format(slug=slug, phase=phase, body=body, extra_fm=extra_fm)
    (layout.bundle_dir / f"{path}.md").write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def context(workspace: WorkspaceLayout) -> ServeContext:
    return ServeContext(root=workspace.root, cwd=workspace.root, port=PORT, pid=4242, gw_version="0.0.0-test")


@pytest.fixture
def client(context: ServeContext) -> Iterator[TestClient]:
    with TestClient(
        build_app(context, token=TOKEN),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as test_client:
        yield test_client


def cli_json(layout: WorkspaceLayout, *args: str) -> object:
    result = CliRunner().invoke(cli_app, [*args, "--workspace", str(layout.root), "--json"])
    return json.loads(result.stdout)
