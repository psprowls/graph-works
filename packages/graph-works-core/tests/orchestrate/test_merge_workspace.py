"""Workspace integration and receipts against disposable real Git repositories."""

import subprocess
from datetime import date
from types import SimpleNamespace

import pytest
from _transaction_helpers import _git, _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.merge_workspace import run_merge_workspace
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from graph_works_core.workspace.finish import inspect_finish

TODAY = date(2026, 9, 27)


def git(root, *args):
    return _git(root, *args).strip()


def setup(tmp_path, nested=False):
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Merge")).layout
    path = "work/feature-a" if not nested else "work/epic-a/children/feature-a"
    for owner, kind in ([("work/epic-a", "Epic")] if nested else []) + [(path, "Feature")]:
        page = layout.bundle_dir / f"{owner}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: {kind}\ntitle: Merge\ndescription: d\nstatus: stable\n"
            f"work_status: in-progress\nphase: {'execute' if kind == 'Epic' else 'finish'}\n"
            "effort: medium\nowner: pat\n"
            "opened: 2026-09-27\nupdated: 2026-09-27\naffects: []\n---\n\n## Summary\nd\n",
            encoding="utf-8",
            newline="\n",
        )
    _init_git(layout.root)
    prepared = run_prepare_workspace(layout, path, today=TODAY, apply=True)
    assert prepared.refusal is None, prepared
    step = prepared.steps[-1]
    from pathlib import Path

    source = Path(step.worktree)
    (source / "docs").mkdir()
    (source / "docs/page.md").write_text("source side\n", encoding="utf-8", newline="\n")
    git(source, "add", "docs/page.md")
    git(source, "commit", "-m", "authored workspace change")
    return SimpleNamespace(layout=layout, path=path, branch=step.branch, source=source)


@pytest.fixture
def lone(tmp_path):
    return setup(tmp_path)


def merge(lone, apply=True):
    return run_merge_workspace(lone.layout, lone.path, today=TODAY, apply=apply)


def test_plan_by_default_changes_nothing(lone):
    head = git(lone.layout.root, "rev-parse", "HEAD")
    result = merge(lone, False)
    assert result.refusal is None and not result.applied
    assert (result.source_branch, result.target_branch) == (lone.branch, "main")
    assert not (lone.layout.root / "docs/page.md").exists()
    assert git(lone.layout.root, "rev-parse", "HEAD") == head


def test_apply_merges_and_records_the_receipt(lone):
    result = merge(lone)
    assert result.refusal is None and result.applied and result.merge_commit, result
    assert (lone.layout.root / "docs/page.md").exists()
    assert (
        git(lone.layout.root, "show", "-s", "--format=%B", result.merge_commit)
        == f"workspace: merge {lone.branch} for {lone.path}"
    )
    assert len(git(lone.layout.root, "show", "-s", "--format=%P", result.merge_commit).split()) == 2
    assert inspect_finish(lone.layout, lone.path).complete
    assert git(lone.layout.root, "status", "--porcelain") == ""


def conflict(lone):
    page = lone.layout.root / "docs/page.md"
    page.parent.mkdir(exist_ok=True)
    page.write_text("main side\n", encoding="utf-8", newline="\n")
    git(lone.layout.root, "add", "docs/page.md")
    git(lone.layout.root, "commit", "-m", "conflict")


def test_conflict_aborts_and_refuses(lone):
    conflict(lone)
    head = git(lone.layout.root, "rev-parse", "HEAD")
    result = merge(lone)
    assert result.refusal == "merge-failed"
    assert git(lone.layout.root, "rev-parse", "HEAD") == head
    assert git(lone.layout.root, "status", "--porcelain") == ""
    assert not inspect_finish(lone.layout, lone.path).complete


def test_existing_merge_is_not_aborted(lone):
    conflict(lone)
    subprocess.run(["git", "-C", str(lone.layout.root), "merge", lone.branch], capture_output=True, check=False)
    before = git(lone.layout.root, "status", "--porcelain")
    merge_head = git(lone.layout.root, "rev-parse", "MERGE_HEAD")
    assert merge(lone).refusal == "merge-failed"
    assert git(lone.layout.root, "rev-parse", "MERGE_HEAD") == merge_head
    assert git(lone.layout.root, "status", "--porcelain") == before


def test_nested_child_is_refused_toward_its_epic_anchor(tmp_path):
    result = merge(setup(tmp_path, nested=True))
    assert result.refusal == "not-main-target"
    assert "finish-receipt.py record --repo _workspace" in result.detail


def test_wrong_checkout_refuses(lone):
    git(lone.layout.root, "checkout", "-b", "elsewhere")
    assert merge(lone).refusal == "wrong-checkout"


def test_already_merged_is_a_clean_replay(lone):
    assert merge(lone).refusal is None
    head = git(lone.layout.root, "rev-parse", "HEAD")
    again = merge(lone)
    assert again.refusal is None and again.merge_commit
    assert git(lone.layout.root, "rev-parse", "HEAD") == head
    assert git(lone.layout.root, "status", "--porcelain") == ""


def test_receipt_uses_fresh_post_merge_owner_content(lone):
    page = lone.source / "okf" / f"{lone.path}.md"
    page.write_bytes(page.read_bytes() + b"\nFresh authored content from source.\n")
    git(lone.source, "add", ".")
    git(lone.source, "commit", "-m", "update owner prose")
    result = merge(lone)
    assert result.refusal is None, result
    assert b"Fresh authored content from source." in (lone.layout.bundle_dir / f"{lone.path}.md").read_bytes()
    assert inspect_finish(lone.layout, lone.path).complete


def test_other_target_blocker_prevents_merge(lone):
    page = lone.layout.bundle_dir / f"{lone.path}.md"
    page.write_bytes(
        page.read_bytes().replace(b"repo_stamps:\n", b"repo_stamps:\n  undeclared: {branch: bad, worktree: /missing}\n")
    )
    git(lone.layout.root, "add", ".")
    git(lone.layout.root, "commit", "-m", "invalid foreign stamp")
    head = git(lone.layout.root, "rev-parse", "HEAD")
    result = merge(lone)
    assert result.refusal == "no-workspace-target" and "undeclared" in result.detail
    assert git(lone.layout.root, "rev-parse", "HEAD") == head


@pytest.mark.parametrize(
    "case, refusal",
    [
        ("unknown", "unknown-path"),
        ("phase", "not-at-finish"),
        ("off", "disabled"),
        ("unstamped", "no-workspace-target"),
    ],
)
def test_basic_refusals(lone, case, refusal):
    page = lone.layout.bundle_dir / f"{lone.path}.md"
    if case == "unknown":
        lone.path = "work/feature-missing"
    elif case == "phase":
        page.write_bytes(page.read_bytes().replace(b"phase: finish", b"phase: execute"))
    elif case == "off":
        lone.layout.local_manifest_path.write_text(
            "workflow:\n  workspace_commits: off\n", encoding="utf-8", newline="\n"
        )
    else:
        from okf_io import parse

        doc = parse(page.read_text(encoding="utf-8"))
        doc.set("repo_stamps", {})
        page.write_text(doc.serialize(), encoding="utf-8", newline="\n")
    assert merge(lone).refusal == refusal


def test_merged_phase_change_refuses_receipt_with_merge_evidence(lone):
    page = lone.source / "okf" / f"{lone.path}.md"
    page.write_bytes(page.read_bytes().replace(b"phase: finish", b"phase: execute"))
    git(lone.source, "add", ".")
    git(lone.source, "commit", "-m", "change phase")
    result = merge(lone)
    assert result.refusal == "receipt-refused" and result.applied and result.merge_commit
    assert b"phase: execute" in (lone.layout.bundle_dir / f"{lone.path}.md").read_bytes()
    assert not inspect_finish(lone.layout, lone.path).complete


def test_merged_malformed_receipt_is_preserved(lone):
    receipt = lone.source / "okf" / lone.path / "references/04-finish-receipt.md"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("broken receipt\n", encoding="utf-8", newline="\n")
    git(lone.source, "add", ".")
    git(lone.source, "commit", "-m", "malformed receipt")
    result = merge(lone)
    assert result.refusal == "receipt-refused" and result.applied and result.merge_commit
    assert (lone.layout.bundle_dir / lone.path / "references/04-finish-receipt.md").read_bytes() == b"broken receipt\n"


def test_receipt_commit_failure_is_not_hidden(lone):
    hook = lone.layout.root / ".no-hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    # Keep the hook itself outside the clean workspace gate.
    with (lone.layout.root / ".git/info/exclude").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("/.no-hooks/\n")
    result = merge(lone)
    assert result.refusal == "receipt-refused" and result.applied and result.merge_commit
    assert "workspace commit failed" in result.detail
    assert result.receipt_path and (lone.layout.bundle_dir / result.receipt_path).exists()
    assert git(lone.layout.root, "status", "--porcelain")


def test_dirty_checkout_is_preserved(lone):
    page = lone.layout.root / "unrelated.txt"
    page.write_text("mine\n", encoding="utf-8", newline="\n")
    git(lone.layout.root, "add", "unrelated.txt")
    before = git(lone.layout.root, "status", "--porcelain")
    head = git(lone.layout.root, "rev-parse", "HEAD")
    assert merge(lone).refusal == "merge-failed"
    assert git(lone.layout.root, "status", "--porcelain") == before
    assert git(lone.layout.root, "rev-parse", "HEAD") == head
