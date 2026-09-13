"""The item's acceptance property: every reader of `workspace.yaml` agrees.

One manifest, read through every path that exists — the two store factories,
the three `manifest` resolvers, `roles`, `pipeline`, `orchestrate`'s routing
gate, the config adapter, and the projection writer. A disagreement between
any two of them is what this whole item exists to make impossible, and it was
a live defect before it: the store parsed with PyYAML (YAML 1.1) while
`load_config` parsed with ruamel (YAML 1.2), so `enabled: yes` was `True`
through one reader and the string `'yes'` through the other.
"""

from __future__ import annotations

import json
from pathlib import Path

from config_io import PROJECTION_FILENAME, write_projection
from graph_works_core.agent_substrate import roles
from graph_works_core.workspace import manifest
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.layout import layout_for

MANIFEST = """\
version: 1
initialized_at: '2026-09-11'
topic: Seam
layout:
  bundle_dir: okf
  config_dir: .gw/config
repositories:
  gw:
    path: ../code
    ignore:
      - build/**
ignore:
  - tmp/**
state_gate:
  enabled: true
  branches:
    - main
roles:
  librarian:
    model_id: some-model
workflow:
  dispatch_rules: dispatch.yaml
  auto_drive:
    max_parallel: 3
quirk: yes
"""


def _workspace(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    layout = layout_for(root)
    layout.manifest_path.write_text(MANIFEST, encoding="utf-8", newline="")
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (root / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n  - match: {variant: diagnosis}\n    skill: gw:custom-diagnosis\n", encoding="utf-8"
    )
    return layout


def test_every_reader_sees_the_same_topic(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)

    from_path_store = manifest.manifest_store(layout.manifest_path).read_explicit()["topic"]
    from_layout_store = manifest.workspace_store(layout).read_explicit()["topic"]
    from_read = manifest.read(layout.manifest_path).topic
    from_key = manifest.resolve_checked_key(layout, "topic", environ={}).value
    from_all = next(item.value for item in manifest.resolve_checked_all(layout, environ={}) if item.key == "topic")

    assert from_path_store == from_layout_store == from_read == from_key == from_all == "Seam"


def test_every_reader_sees_the_same_declared_repository(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)

    config = load_workspace_config(layout)
    assert [repo.name for repo in config.repos] == ["gw"]
    # ADR-0041: relative repo paths anchor on the manifest's own directory.
    assert config.repos[0].path == (layout.manifest_path.parent / "../code").resolve()
    # `ignore` is global-then-per-repo, in that order.
    assert config.repos[0].ignore == ("tmp/**", "build/**")
    assert config.state_gate.enabled is True
    assert config.state_gate.branches == ("main",)

    assert manifest.resolve_checked_key(layout, "repositories.gw.path", environ={}).value == "../code"


def test_the_role_pipeline_and_routing_readers_all_see_their_block(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)

    assert roles.workspace_roles(layout)["librarian"]["model_id"] == "some-model"
    assert load_dispatch_config(layout).rules[0].fields["skill"] == "gw:custom-diagnosis"
    assert manifest.resolve_checked_key(layout, "workflow.auto_drive.max_parallel", environ={}).value == 3


def test_the_projection_agrees_with_the_store(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    target = layout.cache_dir / PROJECTION_FILENAME

    write_projection(manifest.workspace_store(layout), target)

    projected = json.loads(target.read_text(encoding="utf-8"))
    assert projected["topic"] == "Seam"
    assert projected["state_gate"]["enabled"] is True


def test_a_yaml_1_1_flavoured_scalar_reads_as_a_string_through_every_reader(tmp_path: Path) -> None:
    # The defect this item removes, as a property. `quirk: yes` was `True`
    # through PlainYamlStore (PyYAML, YAML 1.1) and `'yes'` through
    # `load_config` (ruamel, YAML 1.2). After D-001 there is one answer.
    layout = _workspace(tmp_path)

    assert manifest.manifest_store(layout.manifest_path).read_explicit()["quirk"] == "yes"
    assert manifest.workspace_store(layout).read_explicit()["quirk"] == "yes"
    # `state_gate.enabled` is the reachable boolean key, and it is a real
    # boolean here, so the catalog accepts it through the checked resolver.
    assert manifest.resolve_checked_key(layout, "state_gate.enabled", environ={}).value is True
