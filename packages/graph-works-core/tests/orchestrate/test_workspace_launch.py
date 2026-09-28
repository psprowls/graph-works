"""Workspace-only planning and actual launch recording against real Git."""

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _transaction_helpers import _init_git
from fake_orca_port import FakeOrcaPort
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.orchestrate.dispatch import run_dispatch
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from okf_io import load
from test_orchestrate_dispatch import _reader_row
from test_workspace_e2e import CHILD, EPIC, LONE, TODAY, git, set_phase, workspace


def workspace_only(layout, path):
    doc = load(layout.bundle_dir / f"{path}.md")
    doc.set("affects", ["gw:workspace"])
    doc.save()
    git(layout.root, "add", "okf")
    git(layout.root, "commit", "-m", "workspace only")


def launch(layout, result, port):
    (dispatch,) = result.dispatches
    entry = asdict(dispatch)
    entry["path"] = entry.pop("slug")
    entry["repo"] = asdict(result.plan.dispatch_repos[dispatch.key])
    entry["repo"]["path"] = str(entry["repo"]["path"])
    return run_dispatch(
        layout,
        dispatch.key,
        plan={"path": result.plan.path, "dispatches": [entry]},
        run_id="run_1",
        port=port,
        today=TODAY,
        clock=lambda: datetime(2026, 9, 27, tzinfo=UTC),
        sleep=lambda _: None,
    )


@pytest.mark.parametrize("phase,scalar", [("execute", False), ("finish", False), ("execute", True)])
def test_workspace_only_launch_preserves_prepared_stamp(tmp_path: Path, phase: str, scalar: bool):
    layout, code = workspace(tmp_path, (LONE, "Feature", "accepted"))
    if scalar:
        doc = load(layout.bundle_dir / f"{LONE}.md")
        doc.set("worktree", str(code))
        doc.set("branch", "main")
        doc.save()
    workspace_only(layout, LONE)
    prepared = run_prepare_workspace(layout, LONE, today=TODAY, apply=True)
    assert prepared.refusal is None
    if phase == "finish":
        set_phase(layout, LONE, phase)
    before = (layout.bundle_dir / f"{LONE}.md").read_bytes()
    step = prepared.steps[-1]
    port = FakeOrcaPort(repos=[{"id": "repo1", "path": str(layout.root)}])
    row = {
        "id": "wt1",
        "repo_id": "repo1",
        "path": step.worktree,
        "branch": step.branch,
        "display_name": step.branch,
        "is_main": False,
        "parent_id": None,
    }
    port.worktrees = {"id:wt1": row, f"path:{step.worktree}": row}
    result = launch(layout, run_orchestrate(layout, LONE), port)
    assert result.ok, result.failure
    assert result.recorded == "unchanged"
    assert (layout.bundle_dir / f"{LONE}.md").read_bytes() == before
    assert "worktree_create" not in port.names()


@pytest.mark.parametrize("phase", ["design", "plan"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_workspace_only_reader_uses_committed_workspace_lineage(
    tmp_path: Path, phase: str, nested: bool, override: bool
):
    items = (
        ((EPIC, "Epic", "in-progress"), (CHILD, "Feature", "accepted")) if nested else ((LONE, "Feature", "accepted"),)
    )
    layout, code = workspace(tmp_path, *items)
    path, root = (CHILD, EPIC) if nested else (LONE, LONE)
    if nested:
        unused = tmp_path / "unused-code"
        unused.mkdir()
        _init_git(unused)
        from ruamel.yaml import YAML

        yaml = YAML()
        manifest = yaml.load(layout.manifest_path.read_text(encoding="utf-8"))
        manifest["repositories"]["unused"] = {"path": str(unused)}
        with layout.manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
            yaml.dump(manifest, stream)
        doc = load(layout.bundle_dir / f"{path}.md")
        doc.set("repo", "unused")
        doc.save()
        git(layout.root, "add", "workspace.yaml")
    workspace_only(layout, path)
    set_phase(layout, path, phase)
    initial = run_orchestrate(layout, root, repo=code if override else None)
    assert not initial.preparations
    assert [p.owner_path for p in initial.workspace_preparations] == ([EPIC] if nested else [])
    for request in initial.workspace_preparations:
        prepared = run_prepare_workspace(layout, request.owner_path, today=TODAY, apply=True)
        assert prepared.refusal is None
    result = run_orchestrate(layout, root, repo=code if override else None)
    assert not result.preparations and not result.workspace_preparations
    (reader,) = result.dispatches
    assert reader.worktree.action == "pin-detached"
    assert result.plan.dispatch_repos[reader.key].name == "_workspace"
    assert reader.worktree.start_sha == git(layout.root, "rev-parse", reader.worktree.base_branch)
    assert reader.worktree.path is None and reader.worktree.branch is None
    assert git(code, "branch", "--format=%(refname:short)") == "main"

    before = (layout.bundle_dir / f"{path}.md").read_bytes()
    checkout = tmp_path / "reader"
    port = FakeOrcaPort(repos=[{"id": "repo1", "path": str(layout.root)}])
    if nested:
        parent = reader.worktree.parent_path
        port.worktrees[f"path:{parent}"] = {
            "id": "anchor",
            "repo_id": "repo1",
            "path": parent,
            "branch": reader.worktree.base_branch,
            "is_main": False,
            "parent_id": None,
        }

    def create(*, name, repo_id, base_branch, comment):
        git(layout.root, "worktree", "add", "-b", name, str(checkout), base_branch)
        return _reader_row(port, checkout, comment=comment)

    port.create = create
    launched = launch(layout, result, port)
    assert launched.ok, launched.failure
    assert launched.recorded == "reader-receipt"
    assert git(checkout, "rev-parse", "HEAD") == reader.worktree.start_sha
    assert git(checkout, "branch", "--show-current") == ""
    assert (layout.bundle_dir / f"{path}.md").read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "wrong-branch", "malformed", "dirty"])
def test_workspace_reader_requires_verified_owner_but_reads_committed_dirty_tip(tmp_path: Path, damage: str):
    layout, _ = workspace(tmp_path, (EPIC, "Epic", "in-progress"), (CHILD, "Feature", "accepted"))
    workspace_only(layout, CHILD)
    set_phase(layout, CHILD, "design")
    prepared = run_prepare_workspace(layout, EPIC, today=TODAY, apply=True)
    assert prepared.refusal is None
    step = prepared.steps[-1]
    sha = git(Path(step.worktree), "rev-parse", "HEAD")
    doc = load(layout.bundle_dir / f"{EPIC}.md")
    if damage == "missing":
        doc.set("repo_stamps", {})
    elif damage == "malformed":
        doc.set("repo_stamps", {"_workspace": {"branch": step.branch}})
    elif damage == "wrong-branch":
        doc.set("repo_stamps", {"_workspace": {"worktree": step.worktree, "branch": "nonexistent"}})
    else:
        (Path(step.worktree) / "uncommitted.txt").write_text("dirty", encoding="utf-8", newline="\n")
    doc.save()
    result = run_orchestrate(layout, EPIC)
    if damage == "dirty":
        (reader,) = result.dispatches
        assert reader.worktree.action == "pin-detached" and reader.worktree.start_sha == sha
    else:
        assert not result.dispatches
        assert any(b.path == CHILD and b.kind == "worktree-unprovable" for b in result.blocked)


@pytest.mark.parametrize("phase", ["design", "plan"])
@pytest.mark.parametrize("nested", [False, True])
def test_code_reader_keeps_explicit_override_with_workspace_enabled(tmp_path: Path, phase: str, nested: bool):
    items = (
        ((EPIC, "Epic", "in-progress"), (CHILD, "Feature", "accepted")) if nested else ((LONE, "Feature", "accepted"),)
    )
    layout, code = workspace(tmp_path, *items)
    path, root = (CHILD, EPIC) if nested else (LONE, LONE)
    base = "main"
    if nested:
        anchor = tmp_path / "code-anchor"
        base = "epic/code"
        git(code, "worktree", "add", "-b", base, str(anchor), "main")
        doc = load(layout.bundle_dir / f"{EPIC}.md")
        doc.set("worktree", str(anchor))
        doc.set("branch", base)
        doc.save()
    set_phase(layout, path, phase)
    result = run_orchestrate(layout, root, repo=code)
    (reader,) = result.dispatches
    selected = result.plan.dispatch_repos[reader.key]
    assert selected.path == code and selected.name is None and selected.source == "flag"
    assert reader.worktree.action == "pin-detached"
    assert reader.worktree.base_branch == base
    assert reader.worktree.start_sha == git(code, "rev-parse", base)
