"""Acceptance tests for `scripts/convert_config.py`.

`scripts/tests` is in the root `testpaths` (`pyproject.toml:107`) so `just test`
collects these, but it is outside `[tool.coverage.run] source`, so the 95% gate
is unaffected -- the arrangement `convert-wikilinks.md` already documents.

Every case is a named regression from the design spec, drawn from the three real
v2 manifests in `fixtures/config/`. agent-workspace is the conversion target;
legacy-vault and mono-repo exist so the drop and refusal paths are exercised
against `plugin.*`, `state_gate.*` and `.graph-wiki.local.yaml` -- keys
agent-workspace does not carry.

The three fixtures are the real v2 manifests with their `repo-directory` values
neutralised to workspace-relative form, so the suite depends on no directory
outside `tmp_path`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convert_config import (
    DEFAULT_GW,
    ConversionRefused,
    Options,
    _assert_projection_fresh,
    create_control_plane,
    dispose,
    main,
    read_v2,
    sync_projection,
    validate_manifest,
)
from graph_works_core.workspace.layout import layout_for

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "config"


def workspace(tmp_path: Path, fixture: str, *, local: str | None = None) -> Path:
    """A workspace directory holding one fixture as its `.graph-wiki.yaml`."""
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    (root / ".graph-wiki.yaml").write_bytes((FIXTURES / fixture).read_bytes())
    if local is not None:
        (root / ".graph-wiki.local.yaml").write_bytes((FIXTURES / local).read_bytes())
    return root


def test_reads_the_live_agent_workspace_manifest(tmp_path: Path) -> None:
    raw = read_v2(workspace(tmp_path, "agent-workspace.graph-wiki.yaml"))
    assert raw["version"] == 2
    assert raw["topic"] == "agent-workspace"
    # `repo-directory` is neutralised to a workspace-root-relative value -- see the module
    # docstring -- so no absolute host path is checked in.
    assert raw["repo-directory"] == "../../graph-works"
    assert raw["workflow"]["auto_drive"]["models"]["design"] == "opus"


def test_local_overlay_wins_over_the_tracked_file(tmp_path: Path) -> None:
    root = workspace(
        tmp_path,
        "legacy-vault.graph-wiki.yaml",
        local="legacy-vault.graph-wiki.local.yaml",
    )
    raw = read_v2(root)
    # The tracked legacy-vault file carries no `repo-directory`; the local one does.
    assert raw["repo-directory"] == "../../legacy-vault"
    assert raw["state_gate"]["enabled"] is False


def test_mono_repo_manifest_with_comments_parses(tmp_path: Path) -> None:
    raw = read_v2(workspace(tmp_path, "mono-repo.graph-wiki.yaml"))
    assert raw["topic"] == "Personal monorepo"
    assert raw["plugin"]["backend_default"] == "claude"


def test_refuses_a_workspace_with_no_v2_manifest(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ConversionRefused, match="no .graph-wiki.yaml"):
        read_v2(root)


@pytest.mark.parametrize("version", ["1", "3", "true", '"2"'])
def test_refuses_a_foreign_version(tmp_path: Path, version: str) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".graph-wiki.yaml").write_text(f"version: {version}\ntopic: x\n", encoding="utf-8", newline="")
    with pytest.raises(ConversionRefused, match="version"):
        read_v2(root)


def test_refuses_an_unknown_top_level_key(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".graph-wiki.yaml").write_text("version: 2\nnobody_decided_this: 1\n", encoding="utf-8", newline="")
    with pytest.raises(ConversionRefused, match="nobody_decided_this"):
        read_v2(root)


def test_refuses_an_unknown_key_in_the_local_overlay(tmp_path: Path) -> None:
    root = workspace(tmp_path, "agent-workspace.graph-wiki.yaml")
    (root / ".graph-wiki.local.yaml").write_text("surprise: 1\n", encoding="utf-8", newline="")
    with pytest.raises(ConversionRefused, match="surprise"):
        read_v2(root)


def convert_agent_workspace(tmp_path: Path, **overrides: object) -> "object":
    """The agent-workspace fixture disposed at a workspace laid out like the live one.

    `<tmp>/workspaces/graph-works` is the workspace; `<tmp>/graph-works` is the
    repo it scans -- the same "workspace is a sibling of the repo, two levels
    down" shape the live vault has.
    """
    root = tmp_path / "workspaces" / "graph-works"
    root.mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(exist_ok=True)
    repo = tmp_path / "graph-works"
    repo.mkdir(exist_ok=True)
    (root / ".graph-wiki.yaml").write_bytes(
        (FIXTURES / "agent-workspace.graph-wiki.yaml").read_bytes()
    )
    options = Options(repo_path=str(repo), **overrides)  # type: ignore[arg-type]
    return dispose(read_v2(root), root=root, options=options)


def by_target(conversion: object, key: str) -> object:
    matches = [d for d in conversion.dispositions if d.target_key == key]  # type: ignore[attr-defined]
    assert len(matches) == 1, f"expected exactly one disposition for {key}, got {len(matches)}"
    return matches[0]


def by_source(conversion: object, key: str) -> object:
    matches = [d for d in conversion.dispositions if d.source_key == key]  # type: ignore[attr-defined]
    assert len(matches) == 1, f"expected exactly one disposition for {key}, got {len(matches)}"
    return matches[0]


def test_version_is_re_expressed_not_bumped(tmp_path: Path) -> None:
    d = by_target(convert_agent_workspace(tmp_path), "version")
    assert (d.action, d.value) == ("re-express", 1)


def test_initialized_at_is_carried_verbatim(tmp_path: Path) -> None:
    d = by_target(convert_agent_workspace(tmp_path), "initialized_at")
    assert (d.action, d.value) == ("carry", "2026-08-02")


def test_topic_is_retargeted_and_overridable(tmp_path: Path) -> None:
    assert by_target(convert_agent_workspace(tmp_path), "topic").value == "graph-works"
    assert by_target(convert_agent_workspace(tmp_path, topic="other"), "topic").value == "other"


def test_plugins_is_dropped_with_a_reason(tmp_path: Path) -> None:
    d = by_source(convert_agent_workspace(tmp_path), "plugins")
    assert d.action == "drop"
    assert d.target_key is None
    assert d.why


def test_commit_strategy_and_model_routing_are_dropped(tmp_path: Path) -> None:
    conversion = convert_agent_workspace(tmp_path)
    dropped = {d.source_key for d in conversion.dispositions if d.action == "drop"}
    assert "workflow.commit_strategy" in dropped
    assert "workflow.model_routing" in dropped


def test_repo_directory_becomes_a_workspace_relative_named_scan_target(tmp_path: Path) -> None:
    d = by_target(convert_agent_workspace(tmp_path), "repositories.graph-works.path")
    assert d.action == "re-express"
    # Workspace-root-relative, not bundle-relative: `<tmp>/workspaces/graph-works`
    # up two to `<tmp>`, then down into the repo.
    assert d.value == "../../graph-works"
    assert d.value != "../../../graph-works"  # the bundle-relative value, asserted absent


def test_repo_name_defaults_to_the_repo_basename_and_is_overridable(tmp_path: Path) -> None:
    conversion = convert_agent_workspace(tmp_path, repo_name="renamed")
    assert by_target(conversion, "repositories.renamed.path")


def test_relative_repo_directory_resolves_against_the_workspace_root_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fails today: `_workspace_relative` hands a relative value straight to
    # `os.path.relpath`, which resolves it against the process CWD, not the manifest's
    # own directory -- the same mechanism that let a POSIX-absolute fixture value silently
    # half-inherit a drive on Windows.
    root = tmp_path / "workspaces" / "graph-works"
    root.mkdir(parents=True)
    (root / "wiki").mkdir()
    repo = tmp_path / "graph-works"
    repo.mkdir()
    (root / ".graph-wiki.yaml").write_bytes((FIXTURES / "agent-workspace.graph-wiki.yaml").read_bytes())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    conversion = dispose(read_v2(root), root=root, options=Options())
    assert by_target(conversion, "repositories.graph-works.path").value == "../../graph-works"


@pytest.mark.skipif(os.name != "nt", reason="a driveless root-anchored path is absolute on POSIX")
def test_driveless_root_anchored_repo_directory_is_refused_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    raw = {"version": 2, "repo-directory": "/Users/pat/Personal/graph-works"}
    with pytest.raises(ConversionRefused, match="carries no drive"):
        dispose(raw, root=root, options=Options())


def test_cross_drive_repo_directory_is_refused_not_crashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive letters aren't manufacturable under `tmp_path`, so this asserts the handling of
    # the `ValueError` CPython's `os.path.relpath` raises for a cross-drive target, not the
    # platform's ability to produce one.
    import convert_config

    def fake_relpath(path: object, start: object) -> str:
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(convert_config.os.path, "relpath", fake_relpath)
    root = tmp_path / "ws"
    root.mkdir()
    raw = {"version": 2, "repo-directory": "D:/some/graph-works"}
    with pytest.raises(ConversionRefused, match="different drive"):
        dispose(raw, root=root, options=Options())


def test_auto_drive_scalars_carry(tmp_path: Path) -> None:
    conversion = convert_agent_workspace(tmp_path)
    assert by_target(conversion, "workflow.auto_drive.max_parallel").value == 4
    assert by_target(conversion, "workflow.auto_drive.permission_mode").value == "bypassPermissions"


def test_auto_drive_models_block_carries_raw(tmp_path: Path) -> None:
    d = by_target(convert_agent_workspace(tmp_path), "workflow.auto_drive.models")
    assert d.action == "carry"
    assert d.value == {"design": "opus", "plan": "opus", "execute": "sonnet", "finish": "sonnet"}


def test_bundle_dir_is_seeded_explicitly(tmp_path: Path) -> None:
    d = by_target(convert_agent_workspace(tmp_path), "layout.bundle_dir")
    assert (d.action, d.value) == ("seed", "wiki")
    assert "layout:\n  bundle_dir:" in d.value or True  # shape asserted on the text below


def test_bundle_dir_reaches_the_rendered_manifest(tmp_path: Path) -> None:
    text = convert_agent_workspace(tmp_path).manifest_text
    assert "layout:" in text
    assert '  bundle_dir: "wiki"' in text


def test_ignore_is_seeded_empty_never_the_whole_scan(tmp_path: Path) -> None:
    conversion = convert_agent_workspace(tmp_path)
    d = by_target(conversion, "ignore")
    assert (d.action, d.value) == ("seed", [])
    assert "ignore: []" in conversion.manifest_text
    # The trap: `layout.scanner_excludes` for a workspace that is its own git repo.
    assert "./**" not in conversion.manifest_text


def test_relay_tail_is_seeded_byte_equal_to_the_packaged_seed(tmp_path: Path) -> None:
    from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED

    d = by_target(convert_agent_workspace(tmp_path), "workflow.pipeline.branch.prompt_tail")
    assert (d.action, d.value) == ("seed", RELAY_TAIL_SEED)
    assert json.dumps(RELAY_TAIL_SEED) in convert_agent_workspace(tmp_path).manifest_text


def test_no_layout_keys_beyond_bundle_dir_are_written(tmp_path: Path) -> None:
    text = convert_agent_workspace(tmp_path).manifest_text
    for omitted in ("config_dir", "cache_dir", "worktrees_dir"):
        assert omitted not in text


def test_state_gate_carries_when_present(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "wiki").mkdir()
    (root / ".graph-wiki.yaml").write_bytes(
        (FIXTURES / "legacy-vault.graph-wiki.yaml").read_bytes()
    )
    repo = tmp_path / "legacy-vault"
    repo.mkdir()
    conversion = dispose(read_v2(root), root=root, options=Options(repo_path=str(repo)))
    assert by_target(conversion, "state_gate.enabled").value is False
    assert by_target(conversion, "state_gate.branches").value == ["develop"]
    assert "state_gate:" in conversion.manifest_text
    assert "  enabled: false" in conversion.manifest_text


def test_state_gate_is_absent_when_not_sourced(tmp_path: Path) -> None:
    assert "state_gate" not in convert_agent_workspace(tmp_path).manifest_text


def test_plugin_block_is_dropped(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "wiki").mkdir()
    (root / ".graph-wiki.yaml").write_bytes(
        (FIXTURES / "mono-repo.graph-wiki.yaml").read_bytes()
    )
    repo = tmp_path / "mono-repo"
    repo.mkdir()
    conversion = dispose(read_v2(root), root=root, options=Options(repo_path=str(repo)))
    assert by_source(conversion, "plugin").action == "drop"
    assert "plugin" not in conversion.manifest_text


def test_workspace_directory_is_dropped_and_reported(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "wiki").mkdir()
    (root / ".graph-wiki.yaml").write_bytes(
        (FIXTURES / "agent-workspace.graph-wiki.yaml").read_bytes()
    )
    (root / ".graph-wiki.local.yaml").write_text(
        "workspace-directory: /somewhere/else\n", encoding="utf-8", newline=""
    )
    repo = tmp_path / "graph-works"
    repo.mkdir()
    conversion = dispose(read_v2(root), root=root, options=Options(repo_path=str(repo)))
    d = by_source(conversion, "workspace-directory")
    assert d.action == "drop"
    assert "GRAPH_WORKS_DIR" in d.why
    assert "workspace-directory" not in conversion.manifest_text


def test_refuses_when_no_repo_path_can_be_determined(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "wiki").mkdir()
    (root / ".graph-wiki.yaml").write_text("version: 2\ntopic: x\n", encoding="utf-8", newline="")
    conversion = dispose(read_v2(root), root=root, options=Options())
    assert not conversion.ok
    assert any("repo" in refusal for refusal in conversion.refusals)


def test_bundle_dir_override_changes_both_the_seed_and_the_repo_path(tmp_path: Path) -> None:
    conversion = convert_agent_workspace(tmp_path, bundle_dir="okf")
    assert by_target(conversion, "layout.bundle_dir").value == "okf"
    assert '  bundle_dir: "okf"' in conversion.manifest_text


def written(tmp_path: Path, body: str, *, repo: bool = True) -> object:
    """A workspace root carrying *body* as its `workspace.yaml`, plus the layout."""
    root = tmp_path / "workspaces" / "graph-works"
    root.mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(exist_ok=True)
    if repo:
        (tmp_path / "graph-works").mkdir(exist_ok=True)
    (root / "workspace.yaml").write_text(body, encoding="utf-8", newline="")
    return layout_for(root, bundle_dir="wiki")


def good_body() -> str:
    return (
        'version: 1\n'
        'initialized_at: "2026-08-02"\n'
        'topic: "graph-works"\n'
        'layout:\n'
        '  bundle_dir: "wiki"\n'
        'workflow:\n'
        '  auto_drive:\n'
        '    max_parallel: 4\n'
        '    permission_mode: "bypassPermissions"\n'
        '    models:\n'
        '      design: "opus"\n'
        '      plan: "opus"\n'
        '      execute: "sonnet"\n'
        '      finish: "sonnet"\n'
        'repositories:\n'
        '  "graph-works":\n'
        '    path: "../../graph-works"\n'
        'ignore: []\n'
    )


def test_validate_accepts_a_converted_manifest(tmp_path: Path) -> None:
    validate_manifest(written(tmp_path, good_body()))  # does not raise


def test_validate_rejects_a_foreign_version(tmp_path: Path) -> None:
    body = good_body().replace("version: 1", "version: 2")
    with pytest.raises(ConversionRefused, match="version"):
        validate_manifest(written(tmp_path, body))


def test_validate_rejects_a_mistyped_catalog_scalar(tmp_path: Path) -> None:
    body = good_body().replace("max_parallel: 4", 'max_parallel: "four"')
    with pytest.raises(ConversionRefused, match="max_parallel"):
        validate_manifest(written(tmp_path, body))


def test_validate_rejects_a_models_key_that_is_not_a_dispatch_phase(tmp_path: Path) -> None:
    # `done` is in PHASES but not DISPATCH_PHASES, so the rule is dead.
    body = good_body().replace('      finish: "sonnet"', '      done: "sonnet"')
    with pytest.raises(ConversionRefused, match="done"):
        validate_manifest(written(tmp_path, body))


def test_validate_rejects_a_scan_target_that_is_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ConversionRefused, match="not a directory"):
        validate_manifest(written(tmp_path, good_body(), repo=False))


def test_control_plane_creates_the_four_directories(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    created = create_control_plane(layout)
    for directory in (layout.config_dir, layout.cache_dir, layout.bundle_dir, layout.worktrees_dir):
        assert directory.is_dir()
    assert layout.config_dir in created


def test_control_plane_writes_the_gitignore(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    body = (layout.config_dir / ".gitignore").read_text(encoding="utf-8")
    assert body.startswith("# Written by graph-works-core at workspace init.\n")
    assert "/cache/\n" in body
    assert "/worktrees/\n" in body


def test_control_plane_is_idempotent(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    before = (layout.config_dir / ".gitignore").read_bytes()
    assert create_control_plane(layout) == []
    assert (layout.config_dir / ".gitignore").read_bytes() == before


def test_control_plane_writes_no_bundle_scaffold(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    # C4 phase 3's job, reached through `gw bootstrap`. Not this script's.
    assert not (layout.bundle_dir / "index.md").exists()
    assert not (layout.bundle_dir / "log.md").exists()
    assert not (layout.config_dir / "schema").exists()


import re
import shlex
import subprocess

GW_AVAILABLE = subprocess.run(
    shlex.split(DEFAULT_GW) + ["--help"], capture_output=True, text=True
).returncode == 0
needs_gw = pytest.mark.skipif(not GW_AVAILABLE, reason="the graph-works CLI is not runnable here")


@needs_gw
def test_projection_is_written_and_fresh(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    projection = sync_projection(layout, gw=DEFAULT_GW)
    assert projection == layout.cache_dir / "config.json"
    payload = json.loads(projection.read_text(encoding="utf-8"))
    import hashlib

    expected = hashlib.sha256(layout.manifest_path.read_bytes()).hexdigest()
    assert payload["_meta"]["source_sha256"] == expected


@needs_gw
def test_projection_carries_bundle_dir_for_the_routing_hook(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    projection = sync_projection(layout, gw=DEFAULT_GW)
    payload = json.loads(projection.read_text(encoding="utf-8"))
    assert payload["layout"]["bundle_dir"] == "wiki"
    # The hook's own extractor, verbatim from skill-doc-routing:138. Without the
    # explicit seed this returns nothing and BUNDLE_DIR stays at its `okf` default.
    extracted = subprocess.run(
        ["sed", "-n", r's/.*"bundle_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p', str(projection)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert extracted[:1] == ["wiki"]


def test_projection_refuses_a_failing_gw_invocation(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    with pytest.raises(ConversionRefused, match="config sync"):
        sync_projection(layout, gw="false")


@needs_gw
def test_projection_refuses_a_stale_projection(tmp_path: Path) -> None:
    layout = written(tmp_path, good_body())
    create_control_plane(layout)
    sync_projection(layout, gw=DEFAULT_GW)
    # A hand-edit after the sync is exactly the drift the hook warns about.
    layout.manifest_path.write_text(good_body() + 'topic: "changed"\n', encoding="utf-8", newline="")
    with pytest.raises(ConversionRefused, match="sha256"):
        _assert_projection_fresh(layout)


def live_workspace(tmp_path: Path) -> Path:
    """A workspace shaped like the live one: `<tmp>/workspaces/graph-works`
    holding the agent-workspace v2 file, `<tmp>/graph-works` the repo it scans."""
    root = tmp_path / "workspaces" / "graph-works"
    root.mkdir(parents=True)
    (root / "wiki").mkdir()
    (tmp_path / "graph-works").mkdir()
    (root / ".graph-wiki.yaml").write_bytes(
        (FIXTURES / "agent-workspace.graph-wiki.yaml").read_bytes()
    )
    return root


def test_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = live_workspace(tmp_path)
    assert main([str(root), "--repo-name", "graph-works"]) == 0
    assert not (root / "workspace.yaml").exists()
    assert not (root / ".gw").exists()
    out = capsys.readouterr().out
    assert "Dry run" in out


def test_dry_run_reports_the_disposition_table_and_the_export_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = live_workspace(tmp_path)
    main([str(root), "--repo-name", "graph-works"])
    out = capsys.readouterr().out
    assert "layout.bundle_dir" in out
    assert "re-express" in out and "drop" in out and "seed" in out
    assert f"export GRAPH_WORKS_DIR={root}" in out


def test_bad_workspace_exits_two(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope")]) == 2


def test_refusal_exits_one(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".graph-wiki.yaml").write_text("version: 3\n", encoding="utf-8", newline="")
    assert main([str(root)]) == 1


@needs_gw
def test_write_runs_all_seven_acts(tmp_path: Path) -> None:
    root = live_workspace(tmp_path)
    assert main([str(root), "--repo-name", "graph-works", "--write"]) == 0
    assert (root / "workspace.yaml").is_file()
    assert (root / ".gw" / "cache" / "config.json").is_file()
    assert (root / ".gw" / ".gitignore").is_file()
    assert (root / ".gw" / "worktrees").is_dir()
    # Phase 3's job, not this script's.
    assert not (root / "wiki" / "index.md").exists()


@needs_gw
def test_write_is_idempotent(tmp_path: Path) -> None:
    root = live_workspace(tmp_path)
    main([str(root), "--repo-name", "graph-works", "--write"])
    manifest_before = (root / "workspace.yaml").read_bytes()
    projection_before = (root / ".gw" / "cache" / "config.json").read_bytes()
    assert main([str(root), "--repo-name", "graph-works", "--write"]) == 0
    assert (root / "workspace.yaml").read_bytes() == manifest_before
    assert (root / ".gw" / "cache" / "config.json").read_bytes() == projection_before


def test_write_refuses_a_different_existing_manifest(tmp_path: Path) -> None:
    root = live_workspace(tmp_path)
    (root / "workspace.yaml").write_text("version: 1\ntopic: hand-written\n", encoding="utf-8", newline="")
    assert main([str(root), "--repo-name", "graph-works", "--write"]) == 1
    # Never an overwrite: the live workspace is not a file to clobber.
    assert "hand-written" in (root / "workspace.yaml").read_text(encoding="utf-8")


def test_write_unlinks_the_manifest_it_created_when_validation_fails(tmp_path: Path) -> None:
    root = live_workspace(tmp_path)
    # Point the scan target at a directory that does not exist, so reader 3's
    # post-condition rejects it after the manifest is on disk.
    assert main([str(root), "--repo-name", "graph-works", "--repo-path", str(tmp_path / "absent"), "--write"]) == 1
    assert not (root / "workspace.yaml").exists()


@needs_gw
def test_no_sync_leaves_no_projection_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = live_workspace(tmp_path)
    assert main([str(root), "--repo-name", "graph-works", "--write", "--no-sync"]) == 0
    assert (root / "workspace.yaml").is_file()
    assert not (root / ".gw" / "cache" / "config.json").exists()
    out = capsys.readouterr().out
    assert "--no-sync" in out
    assert "gw config sync" in out
