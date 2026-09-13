"""Every workspace-config reader goes through one seam, so they cannot disagree
about what the local overlay says."""

from __future__ import annotations

from graph_works_core.agent_substrate.roles import workspace_roles
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.manifest import read, resolve_checked_key

BASE = """\
version: 1
topic: Committed
repositories:
  gw:
    path: ../gw
"""

LOCAL = """\
topic: Laptop
repositories:
  gw:
    path: ../checkouts/gw
roles:
  planner:
    max_tokens: 4096
workflow:
  auto_drive:
    max_parallel: 2
  dispatch_rules: dispatch.yaml
"""


def _workspace(tmp_path):
    root = tmp_path / "works"
    (root / "okf").mkdir(parents=True)
    (root / "workspace.yaml").write_text(BASE, encoding="utf-8")
    (root / "workspace.local.yaml").write_text(LOCAL, encoding="utf-8")
    (root / "dispatch.yaml").write_text("pipeline:\n  rules: []\n", encoding="utf-8")
    (root / "dispatch.local.yaml").write_text(
        "pipeline:\n  rules:\n  - match: {stage: design}\n    skill: local:designer\n", encoding="utf-8"
    )
    return layout_for(root)


def test_every_reader_sees_the_local_overlay(tmp_path):
    layout = _workspace(tmp_path)

    assert read(layout.manifest_path).topic == "Laptop"
    assert resolve_checked_key(layout, "topic", environ={}).value == "Laptop"
    assert workspace_roles(layout)["planner"]["max_tokens"] == 4096
    assert load_dispatch_config(layout).rules[0].fields["skill"] == "local:designer"
    assert resolve_checked_key(layout, "workflow.auto_drive.max_parallel", environ={}).value == 2

    config = load_workspace_config(layout)
    declared = {repo.name: repo for repo in config.repos}
    assert declared["gw"].path == (layout.root / "../checkouts/gw").resolve()


def test_the_same_workspace_without_a_local_file_sees_only_the_base(tmp_path):
    layout = _workspace(tmp_path)
    layout.local_manifest_path.unlink()

    assert read(layout.manifest_path).topic == "Committed"
    assert resolve_checked_key(layout, "topic", environ={}).origin == "manifest"
    assert workspace_roles(layout) == {}
