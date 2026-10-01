"""Live finish occupancy must survive shell filtering and failed readmission."""

from dataclasses import replace

import pytest
from graph_works_core.orchestrate import commands
from graph_works_core.workspace import finish
from graph_works_core.workspace.repo_context import RepositoryContext
from test_orchestrate_shell import _workspace, _write


def _setup(tmp_path, monkeypatch, *, nested=False, foreign=False):
    layout = _workspace(
        tmp_path,
        "version: 1\nrepositories:\n  core: {path: /core}\n  ui: {path: /ui}\n"
        "workflow:\n  auto_drive: {max_parallel: 4}\n",
    )
    (layout.root / "dispatch.yaml").write_text(
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
        stamp = f"repo: core\nworktree: /core/{name}\nbranch: branch/{name}\n"
        if foreign:
            stamp += f"repo_stamps:\n  ui: {{worktree: /ui/{name}, branch: branch/{name}}}\n"
        page.write_text(text.replace("---\n", "---\n" + stamp, 1), encoding="utf-8", newline="")
    contexts = {}
    for repo in ("/core", "/ui"):
        inventory = {"main": (repo,), **{f"branch/{n}": (f"{repo}/{n}",) for n in ("a", "b", "epic")}}
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
    _assert_waits(layout, a, b, "/core/epic" if nested else "/core")


@pytest.mark.parametrize("failed_repos", [("/core",), ("/ui",), ("/core", "/ui")])
@pytest.mark.parametrize("fault", ["dirty-source", "missing-source", "dirty-target"])
def test_shell_retains_root_targets_after_full_or_partial_revalidation_failure(
    tmp_path, monkeypatch, failed_repos, fault
):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch, foreign=True)
    for name in failed_repos:
        context = contexts[name]
        source = name + "/a"
        contexts[name] = replace(
            context,
            checkout_usable_by_path={
                **context.checkout_usable_by_path,
                (name if fault == "dirty-target" else source): False,
            },
            inventory={k: v for k, v in context.inventory.items() if fault != "missing-source" or k != "branch/a"},
        )
    _assert_waits(layout, a, b, failed_repos[0])


@pytest.mark.parametrize("fault", ["missing-target", "unknown-inventory", "unknown-repo"])
def test_unproven_live_target_never_authorizes_checkout_reuse(tmp_path, monkeypatch, fault):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch)
    # Give B an independent, valid repository. A's unknown target cannot be
    # assumed disjoint, even though the current pages have disjoint affects.
    page = layout.bundle_dir / f"{b}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("/core/", "/ui/").replace("repo: core", "repo: ui"),
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
    sources = {name: tmp_path / name for name in ("a", "b")}
    for name, source in sources.items():
        git(repo, "worktree", "add", "-b", f"feature/{name}", str(source))
    layout = _workspace(tmp_path / "workspace", f"version: 1\nrepositories:\n  code: {{path: {repo}}}\n")
    (layout.root / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {stage: finish}\n"
        '      prompt_tail: "Auto-drive context: merge target {merge_target}"\n',
        encoding="utf-8",
        newline="",
    )
    for name, source in sources.items():
        path = f"work/feature-{name}"
        _write(layout, path, phase="finish", work_status="in-progress", affects=(name,))
        page = layout.bundle_dir / f"{path}.md"
        page.write_text(
            page.read_text(encoding="utf-8").replace("---\n", f"---\nworktree: {source}\nbranch: feature/{name}\n", 1),
            encoding="utf-8",
            newline="",
        )
    (sources["a"] / "dirty.txt").write_text("merge in progress\n", encoding="utf-8", newline="")
    _assert_waits(layout, "work/feature-a", "work/feature-b", str(repo))


@pytest.mark.parametrize("phase", ["finish", "design"])
def test_live_finish_revalidation_failure_preserves_independent_work(tmp_path, monkeypatch, phase):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch)
    context = contexts["/core"]
    contexts["/core"] = replace(context, checkout_usable_by_path={**context.checkout_usable_by_path, "/core/a": False})
    page = layout.bundle_dir / f"{b}.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        .replace("/core/", "/ui/")
        .replace("repo: core", "repo: ui")
        .replace("phase: finish", f"phase: {phase}"),
        encoding="utf-8",
        newline="",
    )
    contexts["/ui"] = replace(contexts["/ui"], branch_tips={"branch/b": "a" * 40})
    if phase == "design":
        # Even unknown live target evidence does not alter reader placement.
        contexts["/core"] = replace(contexts["/core"], inventory_known=False)
    result = commands.run_orchestrate(layout, b, live=(commands.session_name(a, "Feature", "finish"),))
    assert [d.slug for d in result.dispatches] == [b], result.blocked
    if phase == "design":
        assert result.dispatches[0].worktree.action == "pin-detached"
        assert result.dispatches[0].worktree.start_sha == "a" * 40


def test_partial_unknown_evidence_keeps_the_other_proven_target_claim(tmp_path, monkeypatch):
    layout, a, b, contexts = _setup(tmp_path, monkeypatch, foreign=True)
    contexts["/ui"] = replace(contexts["/ui"], inventory_known=False)
    page = layout.bundle_dir / f"{b}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("repo_stamps:\n  ui: {worktree: /ui/b, branch: branch/b}\n", ""),
        encoding="utf-8",
        newline="",
    )
    _assert_waits(layout, a, b, "/core")
