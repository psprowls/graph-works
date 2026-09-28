"""Finish cleanup plans inspect real Git worktrees without changing them."""

import subprocess
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.finish import CleanupRow, plan_finish_cleanup

OWNER = "work/epic-example"


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def init_repo(repo: Path) -> None:
    repo.mkdir(exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    git(repo, "commit", "--allow-empty", "-m", "base")


def setup(tmp_path: Path, *, resolved: bool = True, receipt: bool = True):
    code = tmp_path / "code"
    init_repo(code)
    root = tmp_path / "wiki"
    root.mkdir()
    layout = apply_init(plan_init(root, today=date(2026, 9, 26), topic="Cleanup")).layout
    init_repo(root)
    layout.manifest_path.write_text(f"version: 1\nrepositories:\n  code: {{path: {code}}}\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "workspace base")
    repos = {"code": code, "_workspace": root}
    worktrees = {}
    for name, repo in repos.items():
        worktree = tmp_path / (name + "-source")
        git(repo, "worktree", "add", "-b", "feature", str(worktree))
        git(worktree, "commit", "--allow-empty", "-m", "source")
        git(repo, "merge", "feature")
        worktrees[name] = worktree
    page = layout.bundle_dir / (OWNER + ".md")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Epic\ntitle: Example\n"
        f"work_status: {'resolved' if resolved else 'in-progress'}\n"
        f"phase: {'done' if resolved else 'finish'}\nrepo: code\n"
        f"worktree: {worktrees['code']}\nbranch: feature\nrepo_stamps:\n"
        f"  _workspace: {{worktree: {worktrees['_workspace']}, branch: feature}}\n"
        "---\n\nAuthored content.\n",
        encoding="utf-8",
    )
    if receipt:
        entries = []
        for name, repo in repos.items():
            source = git(repo, "rev-parse", "refs/heads/feature")
            result = git(repo, "rev-parse", "refs/heads/main")
            entries.extend(
                [
                    f"  - repo: {name}",
                    "    source_branch: feature",
                    f"    source_commit: {source}",
                    "    target_branch: main",
                    f"    result_commit: {result}",
                ]
            )
        receipt_path = layout.bundle_dir / OWNER / "references/04-finish-receipt.md"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            "---\ntype: Explanation\nreceipt_version: 1\n"
            f"owner: {OWNER}\nintegrations:\n" + "\n".join(entries) + "\n---\n",
            encoding="utf-8",
        )
    return layout, repos, worktrees, page


def plan(layout, runner_cwd=None):
    return plan_finish_cleanup(layout, OWNER, runner_cwd=runner_cwd)


def by_repo(result):
    return {row.repo: row for row in result.rows}


def test_resolved_item_includes_reserved_workspace_stamp(tmp_path):
    layout, repos, worktrees, _ = setup(tmp_path)
    result = plan(layout)
    assert result.refusal is None
    assert by_repo(result) == {
        name: CleanupRow(name, str(worktrees[name].resolve()), "feature", "main", "remove", "") for name in repos
    }


def test_unresolved_item_and_missing_or_malformed_receipt_refuse(tmp_path):
    layout, _, _, page = setup(tmp_path, resolved=False, receipt=False)
    result = plan(layout)
    assert result.rows == () and "not resolved" in result.refusal
    page.write_text(
        page.read_text(encoding="utf-8").replace("in-progress", "resolved").replace("finish\n", "done\n"),
        encoding="utf-8",
    )
    result = plan(layout)
    assert result.rows == () and "no finish receipt" in result.refusal
    receipt_path = layout.bundle_dir / OWNER / "references/04-finish-receipt.md"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("---\ntype: Explanation\nreceipt_version: 9\n---\n", encoding="utf-8")
    result = plan(layout)
    assert result.rows == () and "malformed" in result.refusal


def test_receipt_must_name_workspace_stamp(tmp_path):
    layout, _, _, _ = setup(tmp_path)
    receipt_path = layout.bundle_dir / OWNER / "references/04-finish-receipt.md"
    text = receipt_path.read_text(encoding="utf-8")
    receipt_path.write_text(text[: text.index("  - repo: _workspace")] + "---\n", encoding="utf-8")
    result = plan(layout)
    assert result.rows == () and "_workspace" in result.refusal


def test_unmerged_branch_is_skipped(tmp_path):
    layout, _, worktrees, _ = setup(tmp_path)
    git(worktrees["code"], "commit", "--allow-empty", "-m", "after merge")
    row = by_repo(plan(layout))["code"]
    assert (row.action, row.reason) == ("skip", "unmerged")


def test_deleted_branch_leaves_worktree_only_row(tmp_path):
    layout, repos, worktrees, _ = setup(tmp_path)
    git(worktrees["code"], "switch", "--detach")
    git(repos["code"], "branch", "-d", "feature")
    row = by_repo(plan(layout))["code"]
    assert (row.worktree, row.branch, row.action) == (str(worktrees["code"].resolve()), "", "remove")


def test_deleted_branch_with_unrelated_current_branch_is_skipped(tmp_path):
    """A deleted stamped branch alone is not proof the surviving checkout is safe."""
    layout, repos, worktrees, _ = setup(tmp_path)
    git(worktrees["code"], "switch", "-c", "other")
    git(repos["code"], "branch", "-d", "feature")
    row = by_repo(plan(layout))["code"]
    assert (row.action, row.reason) == ("skip", "unmerged")


def test_deleted_branch_with_unmerged_detached_head_is_skipped(tmp_path):
    layout, repos, worktrees, _ = setup(tmp_path)
    git(worktrees["code"], "switch", "--detach")
    git(worktrees["code"], "commit", "--allow-empty", "-m", "post-merge work")
    git(repos["code"], "branch", "-d", "feature")
    row = by_repo(plan(layout))["code"]
    assert (row.action, row.reason) == ("skip", "unmerged")


def test_main_checkout_and_target_branch_are_protected(tmp_path):
    layout, repos, worktrees, page = setup(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(f"worktree: {worktrees['code']}", f"worktree: {repos['code']}"),
        encoding="utf-8",
    )
    row = by_repo(plan(layout))["code"]
    assert (row.action, row.reason) == ("skip", "target")


def test_deleted_branch_checkout_now_at_target_is_protected(tmp_path):
    layout, repos, worktrees, page = setup(tmp_path)
    page.write_text(
        page.read_text(encoding="utf-8").replace(f"worktree: {worktrees['code']}", f"worktree: {repos['code']}"),
        encoding="utf-8",
    )
    git(worktrees["code"], "switch", "--detach")
    git(repos["code"], "branch", "-d", "feature")
    row = by_repo(plan(layout))["code"]
    assert (row.action, row.reason) == ("skip", "target")


def test_live_sibling_sharing_worktree_or_branch_is_protected(tmp_path):
    layout, _, worktrees, _ = setup(tmp_path)
    sibling = layout.bundle_dir / "work/feature-sibling.md"
    sibling.write_text(
        "---\ntype: Feature\ntitle: Sibling\nwork_status: in-progress\nphase: execute\nrepo: code\n"
        f"worktree: {worktrees['code']}\nbranch: feature\n---\n",
        encoding="utf-8",
    )
    rows = by_repo(plan(layout))
    assert (rows["code"].action, rows["code"].reason) == ("skip", "shared")
    assert rows["_workspace"].action == "remove"
    sibling.write_text(
        sibling.read_text(encoding="utf-8").replace("in-progress", "resolved").replace("execute", "done"),
        encoding="utf-8",
    )
    assert by_repo(plan(layout))["code"].action == "remove"


def test_live_sibling_sharing_branch_only_is_protected(tmp_path):
    """A live sibling naming the same branch at an unrelated path still shares it."""
    layout, _, worktrees, _ = setup(tmp_path)
    sibling = layout.bundle_dir / "work/feature-sibling.md"
    unrelated = worktrees["code"].parent / "unrelated-checkout"
    sibling.write_text(
        "---\ntype: Feature\ntitle: Sibling\nwork_status: in-progress\nphase: execute\nrepo: code\n"
        f"worktree: {unrelated}\nbranch: feature\n---\n",
        encoding="utf-8",
    )
    rows = by_repo(plan(layout))
    assert (rows["code"].action, rows["code"].reason) == ("skip", "shared")
    assert rows["_workspace"].action == "remove"


def test_runner_worktree_is_deferred(tmp_path):
    layout, _, worktrees, _ = setup(tmp_path)
    inside = worktrees["code"] / "sub"
    inside.mkdir()
    rows = by_repo(plan(layout, runner_cwd=inside))
    assert (rows["code"].action, rows["code"].reason) == ("deferred", "runner")
    assert rows["_workspace"].action == "remove"


def test_partial_removal_is_idempotent(tmp_path):
    layout, repos, worktrees, _ = setup(tmp_path)
    git(repos["code"], "worktree", "remove", str(worktrees["code"]))
    git(repos["code"], "branch", "-d", "feature")
    first = plan(layout)
    assert first.refusal is None and set(by_repo(first)) == {"_workspace"}
    assert plan(layout) == first
    git(repos["_workspace"], "worktree", "remove", str(worktrees["_workspace"]))
    git(repos["_workspace"], "branch", "-d", "feature")
    assert plan(layout).rows == () and plan(layout).refusal is None


def test_plan_performs_no_git_writes(tmp_path):
    layout, repos, _, _ = setup(tmp_path)
    before = [
        (git(repo, "for-each-ref"), git(repo, "worktree", "list", "--porcelain"), git(repo, "reflog", "--all"))
        for repo in repos.values()
    ]
    assert plan(layout).refusal is None
    after = [
        (git(repo, "for-each-ref"), git(repo, "worktree", "list", "--porcelain"), git(repo, "reflog", "--all"))
        for repo in repos.values()
    ]
    assert after == before
