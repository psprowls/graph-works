"""The placement adapter: computed placement and the engine behind one seam."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from gw_kernel.workspace.provenance import probe_git
from gw_kernel.workspace.repos import ItemRepo
from gw_orchestrate import placing
from gw_pipeline.placement import NullNotifier, branch_name
from okf_ext.shape import load_sections
from okf_io import load_bundle, parse
from work_tracker_okf.filing import FilingSeed, apply, plan_filing
from work_tracker_okf.items import load_items

TODAY = date(2026, 10, 22)


class Env:
    def __init__(self, layout, repo):
        self.layout = layout
        self.repo = repo
        self.repo_item = ItemRepo("code", repo, "sole")

    def git(self, *args, cwd=None):
        result = probe_git(self.repo if cwd is None else cwd, *args)
        assert result.returncode == 0, result
        return result.stdout.strip()

    def file(self, name, kind, parent=None):
        items = load_items(load_bundle(self.layout.bundle_dir))
        seed = FilingSeed(kind, name, "Placing test", TODAY, name=name, parent_path=parent)
        plan = plan_filing(self.layout.bundle_dir, items, seed, load_sections(self.layout.config_dir / "sections"))
        assert plan.refusal is None, plan
        apply(plan)
        return plan.path

    def edit(self, path, **fields):
        page = self.layout.bundle_dir / f"{path}.md"
        document = parse(page.read_text(encoding="utf-8"), path=page)
        for key, value in fields.items():
            document.set(key, value)
        page.write_text(document.serialize(), encoding="utf-8", newline="\n")

    def items(self):
        return load_items(load_bundle(self.layout.bundle_dir)).by_path

    def realize(self, path, *, phase="design", verify_only=False):
        return placing.realize(
            self.layout,
            path,
            phase=phase,
            verify_only=verify_only,
            notifier=NullNotifier(),
            occupied=lambda p: None,
            today=TODAY,
        )


@pytest.fixture
def env(tmp_path, git_repo, code_layout):
    e = Env(code_layout, tmp_path / "code")
    e.git("config", "user.email", "place@example.test")
    e.git("config", "user.name", "Placing Tests")
    e.git("checkout", "-B", "main")
    e.git("commit", "-m", "Initial code")
    return e


@pytest.fixture
def pair(env):
    epic = env.file("epic-a", "Epic")
    child = env.file("child-a", "Feature", parent=epic)
    return env, epic, child


def _kw(env):
    return {"worktrees_dir": env.layout.worktrees_dir, "default_base": "main"}


def test_compute_matches_placement_for(pair):
    env, epic, child = pair
    items = env.items()
    placed = placing.compute(items[child], items, env.repo_item, **_kw(env))
    assert placed is not None
    assert placed.worktree == env.layout.worktrees_dir / "code" / branch_name(child, "Feature")
    assert placed.parent_branch == branch_name(epic, "Epic")


def test_compute_is_none_for_an_unresolved_repo(pair):
    env, _, child = pair
    items = env.items()
    repo = ItemRepo(None, None, "sole")
    assert placing.compute(items[child], items, repo, **_kw(env)) is None
    assert placing.compute_chain(items[child], items, repo, **_kw(env)) == ()


def test_explicit_override_names_repo_by_basename(pair):
    env, _, child = pair
    items = env.items()
    placed = placing.compute(items[child], items, ItemRepo(None, Path("/x/code"), "flag"), **_kw(env))
    assert placed is not None and placed.repo_name == "code"


def test_compute_chain_is_outermost_first(pair):
    env, epic, child = pair
    items = env.items()
    chain = placing.compute_chain(items[child], items, env.repo_item, **_kw(env))
    assert [p.path for p in chain] == [epic, child]


def test_realize_execute_starts_and_records_baseline(pair):
    env, _, child = pair
    env.edit(child, phase="execute", updated=date(2026, 10, 1))
    result = env.realize(child, phase="execute")
    assert isinstance(result, placing.Realized), result
    head = env.git("rev-parse", "HEAD", cwd=result.placement.worktree)
    assert result.start_sha == head
    assert [s.action for s in result.chain] == ["created", "created"]
    assert env.items()[child].start_sha == head


def test_realize_design_ensures_without_baseline(pair):
    env, _, child = pair
    result = env.realize(child, phase="design")
    assert isinstance(result, placing.Realized), result
    assert result.start_sha is None
    assert env.items()[child].start_sha is None


def test_realize_verify_only_skips_refresh_and_dirty_check(pair):
    env, _, child = pair
    first = env.realize(child)
    assert isinstance(first, placing.Realized)
    worktree = first.placement.worktree
    epic_tree = first.placement.parent_worktree
    (epic_tree / "new.txt").write_text("n\n", encoding="utf-8", newline="\n")
    env.git("add", "new.txt", cwd=epic_tree)
    env.git("commit", "-m", "epic moves", cwd=epic_tree)
    (worktree / "dirty.txt").write_text("d\n", encoding="utf-8", newline="\n")
    before = env.git("rev-parse", "HEAD", cwd=worktree)
    result = env.realize(child, verify_only=True)
    assert isinstance(result, placing.Realized), result
    assert env.git("rev-parse", "HEAD", cwd=worktree) == before
    assert isinstance(env.realize(child), placing.Refused)


def test_realize_verify_only_refuses_a_missing_worktree(pair):
    env, _, child = pair
    result = env.realize(child, verify_only=True)
    assert isinstance(result, placing.Refused)
    assert result.kind == "foreign-checkout"


def test_realize_refusal_is_returned_not_raised(pair):
    env, _, child = pair
    items = env.items()
    placed = placing.compute(items[child], items, env.repo_item, **_kw(env))
    assert placed is not None
    placed.worktree.mkdir(parents=True)
    result = env.realize(child)
    assert isinstance(result, placing.Refused)
    assert result.kind == "foreign-checkout"


def test_realize_unknown_item_and_no_repo(env):
    assert env.realize("work/missing") == placing.Refused("unknown-item", "no work item at work/missing")


def test_refusal_kinds_reexport():
    assert {
        "refresh-conflict",
        "occupied",
        "foreign-checkout",
        "dirty-worktree",
        "parent-unresolved",
        "timeout",
        "already-started",
        "git-unavailable",
        "unknown-item",
    } <= placing.REFUSAL_KINDS


def test_realize_workspace_only_item_has_no_repository(env):
    path = env.file("ws-only", "Feature")
    env.edit(path, affects=["gw:workspace"])
    result = env.realize(path)
    assert isinstance(result, placing.Refused) and result.kind == "unknown-item"


def test_realize_git_unavailable(pair):
    env, _, child = pair
    result = placing.realize(
        env.layout,
        child,
        phase="design",
        verify_only=False,
        notifier=NullNotifier(),
        occupied=lambda p: None,
        environ={"PATH": ""},
    )
    assert isinstance(result, placing.Refused) and result.kind == "git-unavailable"
