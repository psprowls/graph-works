"""`gw work migrate-placement` plans from raw frontmatter and applies git cleanup, strip and one commit."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from gitrepo import git
from gw_kernel.workspace.provenance import GitFailure
from gw_pipeline.placement import branch_name
from gw_work import migrate_placement as mp
from gw_work.migrate_placement import apply_migrate_placement, plan_migrate_placement
from workspace_fixture import bundle_bytes, make_workspace

TODAY = date(2026, 10, 23)


def _workspace(tmp_path: Path):
    layout = make_workspace(tmp_path)
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "commit", "-q", "--allow-empty", "-m", "Initial code")
    head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    layout.manifest_path.write_text(
        head + f"repositories:\n  code:\n    path: {repo.as_posix()}\n", encoding="utf-8", newline="\n"
    )
    git(layout.root, "add", "-A")
    git(layout.root, "commit", "-q", "-m", "Declare repositories")
    return layout, repo


def _page(layout, name, extra="", *, status="resolved", eol="\n"):
    path = f"work/{name}"
    text = (
        f"---\ntype: Feature\ntitle: {name}\ndescription: Migration test\nstatus: stable\n"
        f"work_status: {status}\nphase: {'done' if status == 'resolved' else 'execute'}\neffort: medium\n"
        f"opened: 2026-10-22\nupdated: 2026-10-22\nrepo: code\naffects: [src]\n{extra}---\n\n## Summary\nTest\n"
    )
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return path


def _stamp(worktree, branch):
    return f"worktree: {worktree}\nbranch: {branch}\n"


def _worktree(tmp_path, repo, branch, *, commit=False):
    path = tmp_path / "wts" / branch.replace("/", "-")
    git(repo, "worktree", "add", "-q", "-b", branch, str(path))
    if commit:
        (path / f"{path.name}.txt").write_text("x", encoding="utf-8")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "work")
    return path


def _branch(repo, branch, *, commit=False):
    git(repo, "branch", branch)
    if commit:
        tmp = repo.parent / f"tmp-{branch.replace('/', '-')}"
        git(repo, "worktree", "add", "-q", str(tmp), branch)
        (tmp / "f.txt").write_text(branch, encoding="utf-8")
        git(tmp, "add", "-A")
        git(tmp, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "work")
        git(repo, "worktree", "remove", "--force", str(tmp))


def _receipt(layout, path, repo, branch):
    tip = git(repo, "rev-parse", f"refs/heads/{branch}")
    target = git(repo, "rev-parse", "main")
    page = layout.bundle_dir / path / "references" / "04-finish-receipt.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Explanation\nreceipt_version: 2\nowner: {path}\nintegrations:\n"
        f"  - repo: code\n    source_branch: {branch}\n    source_commit: {tip}\n    target_branch: main\n"
        f"    result_commit: {target}\n    strategy: attested\n    evidence: accepted\n"
        "    accepted_by: Reviewer\n    reason: Reviewed integration\n---\n",
        encoding="utf-8",
        newline="\n",
    )


def _branches(repo):
    return set(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split())


def _rows(plan, action=None, reason=None):
    return [r for r in plan.rows if (action is None or r.action == action) and (reason is None or r.reason == reason)]


def _apply(layout):
    plan = plan_migrate_placement(layout)
    return plan, apply_migrate_placement(layout, plan, today=TODAY)


def _stripped(layout, path):
    text = (layout.bundle_dir / f"{path}.md").read_text(encoding="utf-8")
    return "worktree:" not in text and "\nbranch:" not in text


def test_declared_checkout_strip_only(tmp_path):
    layout, repo = _workspace(tmp_path)
    path = _page(layout, "feature-a", _stamp(repo, "main"))
    plan, result = _apply(layout)
    assert [(r.action, r.reason) for r in plan.rows] == [("strip", "declared-checkout")]
    assert result.refusal is None and result.stripped == (path,)
    assert "main" in _branches(repo) and repo.is_dir()
    assert _stripped(layout, path)


def test_absent_worktree_branch_merged_by_receipt_deleted(tmp_path):
    layout, repo = _workspace(tmp_path)
    _branch(repo, "feature/a-1", commit=True)
    path = _page(layout, "feature-a", _stamp(tmp_path / "wts" / "gone", "feature/a-1"))
    _receipt(layout, path, repo, "feature/a-1")
    plan, result = _apply(layout)
    assert _rows(plan, "delete-branch")
    assert result.deleted_branches == ("feature/a-1",)
    assert "feature/a-1" not in _branches(repo)


def test_absent_worktree_branch_ancestor_of_target_deleted(tmp_path):
    layout, repo = _workspace(tmp_path)
    _branch(repo, "feature/a-1")
    _page(layout, "feature-a", _stamp(tmp_path / "wts" / "gone", "feature/a-1"))
    plan, _ = _apply(layout)
    assert _rows(plan, "delete-branch", "ancestor-of-main")
    assert "feature/a-1" not in _branches(repo)


def test_clean_terminal_worktree_removed(tmp_path):
    layout, repo = _workspace(tmp_path)
    wt = _worktree(tmp_path, repo, "feature/a-1")
    path = _page(layout, "feature-a", _stamp(wt, "feature/a-1"))
    _, result = _apply(layout)
    assert result.removed_worktrees == (str(wt),)
    assert not wt.exists() and "feature/a-1" not in _branches(repo)
    assert _stripped(layout, path)


def test_dirty_worktree_kept(tmp_path):
    layout, repo = _workspace(tmp_path)
    wt = _worktree(tmp_path, repo, "feature/a-1")
    (wt / "scratch.txt").write_text("wip", encoding="utf-8")
    path = _page(layout, "feature-a", _stamp(wt, "feature/a-1"))
    plan, _ = _apply(layout)
    assert _rows(plan, "keep", "dirty")
    assert wt.is_dir() and "feature/a-1" in _branches(repo)
    assert _stripped(layout, path)


def test_open_adoptable_kept(tmp_path):
    layout, repo = _workspace(tmp_path)
    computed = branch_name("work/feature-a", "Feature")
    wt = _worktree(tmp_path, repo, computed)
    path = _page(layout, "feature-a", _stamp(wt, computed), status="open")
    plan, _ = _apply(layout)
    assert plan.blockers == ()
    assert _rows(plan, "keep", "adoptable")
    assert wt.is_dir() and computed in _branches(repo)
    assert _stripped(layout, path)


def test_unmerged_branch_kept(tmp_path):
    layout, repo = _workspace(tmp_path)
    _branch(repo, "feature/a-1", commit=True)
    _page(layout, "feature-a", _stamp(tmp_path / "wts" / "gone", "feature/a-1"))
    plan, _ = _apply(layout)
    assert _rows(plan, "keep", "unmerged")
    assert "feature/a-1" in _branches(repo)


def test_open_item_branch_kept(tmp_path):
    layout, repo = _workspace(tmp_path)
    _branch(repo, "feature/a-1")
    _page(layout, "feature-a", _stamp(tmp_path / "wts" / "gone", "feature/a-1"), status="open")
    plan, _ = _apply(layout)
    assert _rows(plan, "keep", "open")
    assert "feature/a-1" in _branches(repo)


def test_in_flight_orca_branch_blocks_apply(tmp_path):
    layout, repo = _workspace(tmp_path)
    wt = _worktree(tmp_path, repo, "orca/reader-1")
    path = _page(layout, "feature-a", _stamp(wt, "orca/reader-1"), status="open")
    before = bundle_bytes(layout)
    plan = plan_migrate_placement(layout)
    computed = branch_name(path, "Feature")
    assert len(plan.blockers) == 1
    blocker = plan.blockers[0]
    assert (blocker.item, blocker.legacy_branch, blocker.computed_branch) == (path, "orca/reader-1", computed)
    assert blocker.commands == (f"git -C {wt} branch -m orca/reader-1 {computed}", f"gw work place {path}")
    result = apply_migrate_placement(layout, plan, today=TODAY)
    assert result.refusal == "legacy-in-flight"
    assert wt.is_dir() and "orca/reader-1" in _branches(repo)
    assert bundle_bytes(layout) == before


def test_shared_worktree_kept_while_any_open_item_names_it(tmp_path):
    layout, repo = _workspace(tmp_path)
    computed = branch_name("work/feature-open", "Feature")
    wt = _worktree(tmp_path, repo, computed)
    done = _page(layout, "feature-done", _stamp(wt, computed))
    live = _page(layout, "feature-open", _stamp(wt, computed), status="open")
    plan, _ = _apply(layout)
    assert plan.blockers == ()
    assert wt.is_dir() and computed in _branches(repo)
    assert not _rows(plan, "remove-worktree")
    assert _stripped(layout, done) and _stripped(layout, live)


def test_shared_worktree_other_branch_group_in_use(tmp_path):
    layout, repo = _workspace(tmp_path)
    computed = branch_name("work/feature-open", "Feature")
    wt = _worktree(tmp_path, repo, computed)
    _page(layout, "feature-done", _stamp(wt, "legacy/old"))
    _page(layout, "feature-open", _stamp(wt, computed), status="open")
    plan = plan_migrate_placement(layout)
    assert _rows(plan, "keep", "in-use")
    assert not _rows(plan, "remove-worktree")


def test_spec_baseline_workspace_only_strip(tmp_path):
    layout, _ = _workspace(tmp_path)
    only = _page(layout, "feature-a", "spec_baseline:\n  workspace: " + "a" * 40 + "\n")
    both = _page(layout, "feature-b", "spec_baseline:\n  code: " + "b" * 40 + "\n  workspace: " + "c" * 40 + "\n")
    plan, result = _apply(layout)
    assert plan.rows == () and set(result.stripped) == {only, both}
    text_only = (layout.bundle_dir / f"{only}.md").read_text(encoding="utf-8")
    text_both = (layout.bundle_dir / f"{both}.md").read_text(encoding="utf-8")
    assert "spec_baseline" not in text_only
    assert f"spec_baseline:\n  code: {'b' * 40}\n" in text_both and "workspace" not in text_both


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_strip_is_byte_minimal_crlf_and_comments(tmp_path, eol):
    layout, repo = _workspace(tmp_path)
    extra = (
        f"worktree: {repo}  # drop\nowner: kept\n# keep me\nbranch: main\n"
        f"repo_stamps:\n  code:\n    worktree: {repo}\n    branch: main\n"
        "spec_baseline:\n  workspace: " + "a" * 40 + "\n"
        "after: kept\n# keep too\ntags: [x]  # tail\n"
    )
    path = _page(layout, "feature-a", extra, eol=eol)
    page = layout.bundle_dir / f"{path}.md"
    original = page.read_bytes().decode("utf-8")
    drop = (
        f"worktree: {repo}  # drop{eol}",
        f"branch: main{eol}",
        f"repo_stamps:{eol}  code:{eol}    worktree: {repo}{eol}    branch: main{eol}",
        f"spec_baseline:{eol}  workspace: {'a' * 40}{eol}",
    )
    expected = original
    for chunk in drop:
        assert chunk in expected
        expected = expected.replace(chunk, "", 1)
    _apply(layout)
    assert page.read_bytes().decode("utf-8") == expected


def test_one_commit_and_log_entry(tmp_path):
    layout, repo = _workspace(tmp_path)
    _branch(repo, "feature/a-1", commit=True)
    _page(layout, "feature-a", _stamp(tmp_path / "wts" / "gone", "feature/a-1"))
    _page(layout, "feature-b", _stamp(repo, "main"))
    before = int(git(layout.root, "rev-list", "--count", "HEAD"))
    _, result = _apply(layout)
    assert int(git(layout.root, "rev-list", "--count", "HEAD")) == before + 1
    assert git(layout.root, "log", "-1", "--format=%s") == "workspace: migrate-placement strip retired placement fields"
    assert result.commit is not None and result.commit.status == "committed"
    log = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert log.count("migrate-placement") == 1 and "(unmerged)" in log
    assert "updated: 2026-10-22" in (layout.bundle_dir / "work/feature-a.md").read_text(encoding="utf-8")


def test_rerun_reports_nothing_to_migrate(tmp_path):
    layout, repo = _workspace(tmp_path)
    _page(layout, "feature-a", _stamp(repo, "main"))
    _apply(layout)
    plan, result = _apply(layout)
    assert plan.nothing_to_migrate and result.stripped == () and result.commit is None


def test_failed_git_step_strips_nothing(tmp_path, monkeypatch):
    layout, repo = _workspace(tmp_path)
    one = _worktree(tmp_path, repo, "feature/a-1")
    two = _worktree(tmp_path, repo, "feature/b-1")
    _page(layout, "feature-a", _stamp(one, "feature/a-1"))
    _page(layout, "feature-b", _stamp(two, "feature/b-1"))
    before = bundle_bytes(layout)
    real = mp.strict_git
    calls = []

    def flaky(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            calls.append(args)
            if len(calls) == 2:
                return GitFailure("error", "boom")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(mp, "strict_git", flaky)
    plan = plan_migrate_placement(layout)
    result = apply_migrate_placement(layout, plan, today=TODAY)
    assert result.refusal == "git-failed" and result.stripped == ()
    assert len(result.removed_worktrees) == 1 and "boom" in (result.failed_step or "")
    assert bundle_bytes(layout) == before


def test_orphans_reported_never_deleted(tmp_path):
    layout, repo = _workspace(tmp_path)
    _branch(repo, "psprowls/gw-reader-abc123")
    _page(layout, "feature-a", _stamp(repo, "main"))
    plan, _ = _apply(layout)
    assert [(o.repo, o.branch) for o in plan.orphans] == [("code", "psprowls/gw-reader-abc123")]
    assert "psprowls/gw-reader-abc123" in _branches(repo)


def test_workspace_stamp_is_stripped_and_reported_without_touching_the_worktree(tmp_path):
    layout, _ = _workspace(tmp_path)
    ws_tree = tmp_path / "ws-wt"
    git(layout.root, "worktree", "add", "-q", "-b", "ws/feature-a", str(ws_tree))
    (ws_tree / "note.txt").write_text("x", encoding="utf-8")
    git(ws_tree, "add", "-A")
    git(ws_tree, "commit", "-q", "-m", "ws work")
    stamp = f"repo_stamps:\n  _workspace:\n    worktree: {ws_tree}\n    branch: ws/feature-a\n"
    path = _page(layout, "feature-a", stamp)
    plan, result = _apply(layout)
    assert not plan.nothing_to_migrate and result.refusal is None and result.stripped == (path,)
    assert "repo_stamps" not in (layout.bundle_dir / f"{path}.md").read_text(encoding="utf-8")
    assert ws_tree.is_dir() and "ws/feature-a" in _branches(layout.root)
    for reported in (plan.workspace_stamps, result.workspace_stamps):
        assert [(s.item, s.worktree, s.branch, s.ahead) for s in reported] == [(path, str(ws_tree), "ws/feature-a", 1)]
    assert plan.rows == ()
