"""Live finish occupancy must survive shell filtering and failed readmission."""

from dataclasses import replace

import pytest
from gw_orchestrate import commands
from gw_pipeline import finish
from gw_pipeline.placement_resolve import item_placement
from gw_pipeline.repo_context import RepositoryContext
from okf_io import load_bundle
from test_orchestrate_shell import _workspace, _write
from work_tracker_okf.items import load_items


def _placed(layout, path, repo):
    """Where placement computes *path*'s checkout and branch in the declared repository *repo*."""
    items = load_items(load_bundle(layout.bundle_dir))
    placement = item_placement(layout, items.by_path[path], items.by_path, repo_name=repo)
    assert placement is not None
    return placement


def _setup(tmp_path, monkeypatch, *, nested=False, foreign=False):
    layout = _workspace(
        tmp_path,
        "version: 1\nrepositories:\n  core: {path: /core}\n  ui: {path: /ui}\n",
        workflow="workflow:\n  auto_drive: {max_parallel: 4}\n",
    )
    (layout.root / "workflow.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {stage: finish}\n"
        '      prompt_tail: "Auto-drive context: merge target {merge_target}"\n',
        encoding="utf-8",
        newline="",
    )
    root = "work/epic-x"
    a, b = (f"{root}/children/feature-{n}" if nested else f"work/feature-{n}" for n in ("a", "b"))
    if nested:
        _write(layout, root, type="Epic", phase="execute", work_status="in-progress", affects=())
    for path, name in ((a, "a"), (b, "b"), *([(root, "epic")] if nested else [])):
        if path != root:
            _write(layout, path, phase="finish", work_status="in-progress", affects=(name,))
        page = layout.bundle_dir / f"{path}.md"
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("---\n", "---\nrepo: core\n", 1), encoding="utf-8", newline="")
    contexts = {}
    for repo in ("/core", "/ui"):
        # Placement is computed: each item's branch is checked out at its computed worktree in both repositories.
        placements = [_placed(layout, path, repo.strip("/")) for path in (a, b, *([root] if nested else []))]
        inventory = {"main": (repo,), **{pl.branch: (str(pl.worktree),) for pl in placements}}
        paths = {p: True for values in inventory.values() for p in values}
        contexts[repo] = RepositoryContext(
            repo,
            repo,
            "main",
            True,
            inventory,
            paths,
            True,
            paths,
            branches=frozenset(inventory),
        )

    def observe(repo, **kwargs):
        return contexts[str(repo)]

    monkeypatch.setattr(commands, "observe_repository", observe)
    monkeypatch.setattr(finish, "observe_repository", observe)
    monkeypatch.setattr(commands, "repository_identity", lambda p: str(p))
    return layout, a, b, contexts


def _assert_waits(layout, a, b, target):
    result = commands.run_orchestrate(layout, b, live=(commands.session_name(a, "Feature", "finish"),))
    assert not result.dispatches
    assert [(x.path, x.kind) for x in result.blocked] == [(b, "worktree-pending")]
    assert f"{target} held by {a}" in result.blocked[0].reason
    assert [d.slug for d in commands.run_orchestrate(layout, b).dispatches] == [b]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("phase", ["finish", "done"])
def test_shell_retains_live_finish_outside_candidate_subtree(tmp_path, monkeypatch, nested, phase):
    layout, a, b, _ = _setup(tmp_path, monkeypatch, nested=nested)
    if phase == "done":
        page = layout.bundle_dir / f"{a}.md"
        page.write_text(
            page.read_text(encoding="utf-8")
            .replace("phase: finish", "phase: done")
            .replace("work_status: in-progress", "work_status: resolved"),
            encoding="utf-8",
            newline="",
        )
    _assert_waits(layout, a, b, str(_placed(layout, "work/epic-x", "core").worktree) if nested else "/core")


# Foreign repositories no longer come from repo_stamps (targets are placement-derived), so only /core is a target.
@pytest.mark.parametrize("failed_repos", [("/core",), ("/core", "/ui")])
@pytest.mark.parametrize("fault", ["dirty-source", "missing-source", "dirty-target"])
def test_shell_retains_root_targets_after_full_or_partial_revalidation_failure(
    tmp_path, monkeypatch, failed_repos, fault
):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch, foreign=True)
    for name in failed_repos:
        context = contexts[name]
        placed_a = _placed(layout, a, name.strip("/"))
        source = str(placed_a.worktree)
        contexts[name] = replace(
            context,
            checkout_usable_by_path={
                **context.checkout_usable_by_path,
                (name if fault == "dirty-target" else source): False,
            },
            inventory={k: v for k, v in context.inventory.items() if fault != "missing-source" or k != placed_a.branch},
        )
    _assert_waits(layout, a, b, failed_repos[0])


@pytest.mark.parametrize("fault", ["missing-target", "unknown-inventory", "unknown-repo"])
def test_unproven_live_target_never_authorizes_checkout_reuse(tmp_path, monkeypatch, fault):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch)
    # Give B an independent, valid repository. A's unknown target cannot be
    # assumed disjoint, even though the current pages have disjoint affects.
    page = layout.bundle_dir / f"{b}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("repo: core", "repo: ui"),
        encoding="utf-8",
        newline="",
    )
    context = contexts["/core"]
    if fault == "missing-target":
        contexts["/core"] = replace(context, inventory={k: v for k, v in context.inventory.items() if k != "main"})
    elif fault == "unknown-inventory":
        contexts["/core"] = replace(context, inventory_known=False)
    else:
        page = layout.bundle_dir / f"{a}.md"
        page.write_text(
            page.read_text(encoding="utf-8").replace("repo: core", "repo: undeclared"), encoding="utf-8", newline=""
        )
    result = commands.run_orchestrate(layout, b, live=(commands.session_name(a, "Feature", "finish"),))
    assert not result.dispatches
    assert [(x.path, x.kind) for x in result.blocked] == [(b, "worktree-unprovable")]
    assert a in result.blocked[0].reason


def test_real_git_shell_keeps_live_root_target_when_source_is_dirty(tmp_path):
    import subprocess

    def git(repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "base")
    layout = _workspace(tmp_path / "workspace", f"version: 1\nrepositories:\n  code: {{path: {repo}}}\n")
    (layout.root / "workflow.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {stage: finish}\n"
        '      prompt_tail: "Auto-drive context: merge target {merge_target}"\n',
        encoding="utf-8",
        newline="",
    )
    sources = {}
    for name in ("a", "b"):
        path = f"work/feature-{name}"
        _write(layout, path, phase="finish", work_status="in-progress", affects=(name,))
        # Each item's computed worktree on its computed branch.
        placed = _placed(layout, path, "code")
        placed.worktree.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-b", placed.branch, str(placed.worktree))
        sources[name] = placed.worktree
    (sources["a"] / "dirty.txt").write_text("merge in progress\n", encoding="utf-8", newline="")
    _assert_waits(layout, "work/feature-a", "work/feature-b", str(repo))


@pytest.mark.parametrize("phase", ["finish", "design"])
def test_live_finish_revalidation_failure_preserves_independent_work(tmp_path, monkeypatch, phase):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch)
    context = contexts["/core"]
    source = str(_placed(layout, a, "core").worktree)
    contexts["/core"] = replace(context, checkout_usable_by_path={**context.checkout_usable_by_path, source: False})
    page = layout.bundle_dir / f"{b}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("repo: core", "repo: ui").replace("phase: finish", f"phase: {phase}"),
        encoding="utf-8",
        newline="",
    )
    if phase == "design":
        # Even unknown live target evidence does not alter a design's placement.
        contexts["/core"] = replace(contexts["/core"], inventory_known=False)
    result = commands.run_orchestrate(layout, b, live=(commands.session_name(a, "Feature", "finish"),))
    assert [d.slug for d in result.dispatches] == [b], result.blocked
    if phase == "design":
        (dispatch,) = result.dispatches
        assert dispatch.worktree.path == str(result.placements[dispatch.key].worktree)
        assert result.placements[dispatch.key].repo_name == "ui"


def test_partial_unknown_evidence_keeps_the_other_proven_target_claim(tmp_path, monkeypatch):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch, foreign=True)
    contexts["/ui"] = replace(contexts["/ui"], inventory_known=False)
    page = layout.bundle_dir / f"{b}.md"
    page.write_text(
        page.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="",
    )
    _assert_waits(layout, a, b, "/core")
