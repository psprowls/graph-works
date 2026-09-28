"""Workspace content reaches main only through the owning finish integration."""

from datetime import date
from pathlib import Path

from _transaction_helpers import _git, _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.commands import run_orchestrate
from graph_works_core.orchestrate.finish_receipt import run_record_finish
from graph_works_core.orchestrate.merge_workspace import run_merge_workspace
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from graph_works_core.workspace.finish import inspect_finish
from okf_io import load

TODAY = date(2026, 9, 27)
LONE = "work/feature-lone"
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"


def git(root: Path, *args: str) -> str:
    return _git(root, *args).strip()


def workspace(tmp_path: Path, *items: tuple[str, str, str]):
    code = tmp_path / "code"
    code.mkdir()
    _init_git(code)
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Workspace e2e")).layout
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {code.as_posix()}"
        ),
        encoding="utf-8",
        newline="\n",
    )
    for path, kind, status in items:
        page = layout.bundle_dir / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: {kind}\ntitle: Workspace e2e\ndescription: d\nstatus: stable\n"
            f"work_status: {status}\nphase: execute\neffort: medium\nowner: pat\n"
            "opened: 2026-09-27\nupdated: 2026-09-27\n"
            "repo: code\naffects:\n- code:src/example.py\n---\n\n## Summary\nd\n",
            encoding="utf-8",
            newline="\n",
        )
    _init_git(layout.root)
    return layout, code


def set_phase(layout, path: str, phase: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    doc = load(page)
    doc.set("phase", phase)
    doc.save()
    git(layout.root, "add", "okf")
    git(layout.root, "commit", "-m", f"move {path} to {phase}")


def commit_content(worktree: Path, relative: str) -> None:
    page = worktree / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"{relative}\n", encoding="utf-8", newline="\n")
    git(worktree, "add", relative)
    git(worktree, "commit", "-m", f"write {relative}")


def test_lone_code_item_content_reaches_workspace_main_after_both_finishes(tmp_path: Path) -> None:
    layout, code = workspace(tmp_path, (LONE, "Feature", "accepted"))
    initial = run_orchestrate(layout, LONE)
    assert [(p.owner_path, p.base_branch) for p in initial.workspace_preparations] == [(LONE, "main")]
    assert [(b.path, b.kind) for b in initial.blocked] == [(LONE, "workspace-pending")]

    prepared = run_prepare_workspace(layout, LONE, today=TODAY, apply=True)
    assert prepared.refusal is None and prepared.applied, prepared
    source = Path(prepared.steps[-1].worktree)
    code_source = tmp_path / "code-source"
    git(code, "worktree", "add", "-b", "feature/lone-code", str(code_source), "main")
    git(code_source, "commit", "--allow-empty", "-m", "code change")
    page = layout.bundle_dir / f"{LONE}.md"
    doc = load(page)
    doc.set("worktree", str(code_source))
    doc.set("branch", "feature/lone-code")
    doc.save()
    git(layout.root, "add", "okf")
    git(layout.root, "commit", "-m", "record code placement")
    (dispatch,) = run_orchestrate(layout, LONE).dispatches
    assert f"Workspace content root: {source} (branch {prepared.steps[-1].branch})" in dispatch.prompt

    commit_content(source, "docs/lone.md")
    assert not (layout.root / "docs/lone.md").exists()
    assert git(layout.root, "status", "--porcelain") == ""

    set_phase(layout, LONE, "finish")
    git(code, "merge", "--no-ff", "-m", "integrate code", "feature/lone-code")
    code_merge = git(code, "rev-parse", "HEAD")
    recorded = run_record_finish(layout, LONE, repo_name="code", today=TODAY)
    assert recorded.refusal is None and recorded.changed, recorded
    assert not (layout.root / "docs/lone.md").exists()
    assert not inspect_finish(layout, LONE).complete

    merged = run_merge_workspace(layout, LONE, today=TODAY, apply=True)
    assert merged.refusal is None and merged.applied and merged.merge_commit, merged
    assert (layout.root / "docs/lone.md").read_text(encoding="utf-8") == "docs/lone.md\n"
    finished = inspect_finish(layout, LONE)
    assert finished.complete and {entry.repo for entry in finished.entries} == {"code", "_workspace"}
    assert finished.resolved_in == code_merge
    assert git(layout.root, "status", "--porcelain") == ""


def test_child_content_reaches_epic_anchor_before_workspace_main(tmp_path: Path) -> None:
    layout, _code = workspace(tmp_path, (EPIC, "Epic", "in-progress"), (CHILD, "Feature", "accepted"))
    initial = run_orchestrate(layout, EPIC)
    assert [p.owner_path for p in initial.workspace_preparations] == [EPIC]
    assert (CHILD, "workspace-pending") in [(b.path, b.kind) for b in initial.blocked]

    prepared = run_prepare_workspace(layout, CHILD, today=TODAY, apply=True)
    assert prepared.refusal is None and prepared.applied, prepared
    assert [s.owner_path for s in prepared.steps] == [EPIC, CHILD]
    anchor, child = (Path(step.worktree) for step in prepared.steps)
    commit_content(child, "docs/child.md")
    assert not (anchor / "docs/child.md").exists()
    assert not (layout.root / "docs/child.md").exists()
    assert git(layout.root, "status", "--porcelain") == ""

    set_phase(layout, CHILD, "finish")
    git(anchor, "merge", "--no-ff", "-m", "integrate child workspace", prepared.steps[-1].branch)
    child_merge = git(anchor, "rev-parse", "HEAD")
    recorded = run_record_finish(layout, CHILD, repo_name="_workspace", today=TODAY)
    assert recorded.refusal is None and recorded.changed, recorded
    assert (anchor / "docs/child.md").read_text(encoding="utf-8") == "docs/child.md\n"
    assert not (layout.root / "docs/child.md").exists()
    child_finish = inspect_finish(layout, CHILD)
    assert child_finish.complete and {entry.repo for entry in child_finish.entries} == {"_workspace"}
    assert child_finish.resolved_in == child_merge

    set_phase(layout, EPIC, "finish")
    merged = run_merge_workspace(layout, EPIC, today=TODAY, apply=True)
    assert merged.refusal is None and merged.applied and merged.merge_commit, merged
    assert (layout.root / "docs/child.md").read_text(encoding="utf-8") == "docs/child.md\n"
    assert inspect_finish(layout, EPIC).complete
    assert git(layout.root, "status", "--porcelain") == ""
