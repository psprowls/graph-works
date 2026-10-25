"""Finish targets come from computed placement: one per code repository the item finishes into."""

import pytest
from gw_kernel.workspace.provenance import default_base
from gw_pipeline import finish as finish_module
from gw_pipeline import place
from gw_pipeline.finish import (
    VerifiedIntegration,
    finish_strategies,
    inspect_finish,
    receipt_entry_data,
    resolve_finish_targets,
)
from gw_pipeline.placement import placement_chain
from gw_pipeline.test_support.finish_repos import git as run_git
from okf_io import load_bundle, parse
from work_tracker_okf.items import load_items


def _page(env, path):
    return env.layout.bundle_dir / f"{path}.md"


def _edit(env, path, **fields):
    page = _page(env, path)
    document = parse(page.read_text(encoding="utf-8"), path=page)
    for name, value in fields.items():
        document.set(name, value)
    page.write_text(document.serialize(), encoding="utf-8", newline="\n")


def _items(env):
    return list(load_items(load_bundle(env.layout.bundle_dir)))


def _plan(env, path):
    return resolve_finish_targets(env.layout, _items(env), path)


def _placed_leaf(env, *, parent=None, affects=("src/a.py",)):
    path = env.file(parent=parent)
    _edit(env, path, phase="finish", affects=list(affects))
    assert place.ensure(env.ctx(), env.chain_for(path)).ok
    return path


def _placed_epic_with_leaf(env):
    epic = env.file("epic-e", kind="Epic")
    _edit(env, epic, phase="finish", affects=["src/a.py"])
    leaf = env.file("leaf", parent=epic)
    _edit(env, leaf, phase="finish", affects=["src/a.py"])
    assert place.ensure(env.ctx(), env.chain_for(leaf)).ok
    return epic, leaf


def test_top_level_leaf_single_target(place_env):
    env = place_env
    path = _placed_leaf(env)
    plan = _plan(env, path)
    assert plan.blockers == ()
    (target,) = plan.targets
    placement = env.chain_for(path)[-1]
    assert target.repo.name == "code"
    assert target.worktree == str(placement.worktree.resolve())
    assert target.source_branch == placement.branch
    assert target.target_branch == "main"
    assert target.target_worktree == str(env.repo.resolve())
    assert target.default_strategy == "squash"


def test_nested_leaf_targets_owner_worktree(place_env):
    env = place_env
    _epic, leaf = _placed_epic_with_leaf(env)
    (target,) = _plan(env, leaf).targets
    owner = env.chain_for(leaf)[0]
    assert target.target_branch == owner.branch
    assert target.target_worktree == str(owner.worktree.resolve())


def _declare(env, tmp_path, names):
    manifest = env.layout.manifest_path
    text = manifest.read_text(encoding="utf-8")
    extra = ""
    for name in names:
        repo = tmp_path / name
        repo.mkdir()
        for args in (("init", "-b", "main"), ("config", "user.email", "t@example.test"), ("config", "user.name", "T")):
            run_git(repo, *args)
        (repo / "f.txt").write_text("x\n", encoding="utf-8", newline="\n")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-m", "base")
        extra += f"  {name}: {{path: {repo}}}\n"
    manifest.write_text(text.rstrip("\n") + "\n" + extra, encoding="utf-8", newline="\n")


def _write_receipt(env, path, repo_name):
    entry = VerifiedIntegration(repo_name, "b", "a" * 40, "main", "c" * 40)
    lines = [f"  {'- ' if i == 0 else '  '}{k}: {v}" for i, (k, v) in enumerate(receipt_entry_data(entry).items())]
    receipt = env.layout.bundle_dir / path / "references/04-finish-receipt.md"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        f"---\ntype: Explanation\nreceipt_version: 2\nowner: {path}\nintegrations:\n" + "\n".join(lines) + "\n---\n",
        encoding="utf-8",
        newline="\n",
    )


def test_owner_two_repos_resolved_and_wontfix_child(place_env, tmp_path):
    env = place_env
    _declare(env, tmp_path, ["other", "third"])
    epic = env.file("epic-e", kind="Epic")
    _edit(env, epic, phase="finish", affects=["src/a.py"], repo="code")
    done = env.file("done", parent=epic)
    skipped = env.file("skipped", parent=epic)
    _edit(env, done, work_status="resolved", phase="done")
    _edit(env, skipped, work_status="wontfix", phase="done")
    _write_receipt(env, done, "other")
    _write_receipt(env, skipped, "third")
    items = {item.path: item for item in _items(env)}
    for name in ("code", "other"):
        repo = (tmp_path / name).resolve()
        chain = placement_chain(
            items[epic],
            items,
            repo_name=name,
            repo_root=repo,
            working_checkout=repo,
            worktrees_dir=env.layout.worktrees_dir,
            default_base=default_base(repo),
        )
        if name == "code":
            assert place.ensure(env.ctx(), chain).ok
        else:
            run_git(repo, "worktree", "add", "-b", chain[-1].branch, str(chain[-1].worktree))
    plan = _plan(env, epic)
    assert plan.blockers == ()
    assert [t.repo.name for t in plan.targets] == ["code", "other"]


def test_workspace_only_leaf_has_no_targets(place_env, tmp_path):
    env = place_env
    _declare(env, tmp_path, ["other"])
    epic = env.file("epic-e", kind="Epic")
    _edit(env, epic, repo="code")
    leaf = env.file("leaf", parent=epic)
    _edit(env, leaf, phase="finish", affects=["gw:workspace"])
    plan = _plan(env, leaf)
    assert plan.targets == () and plan.blockers == ()
    verification = inspect_finish(env.layout, leaf)
    assert verification.complete is True and verification.resolved_in is None


def test_unrelated_malformed_strategy_does_not_block(place_env, tmp_path):
    env = place_env
    _declare(env, tmp_path, ["other"])
    manifest = env.layout.manifest_path
    text = manifest.read_text(encoding="utf-8").replace("  other: {path:", "  other: {finish: 7, path:")
    manifest.write_text(text, encoding="utf-8", newline="\n")
    epic = env.file("epic-e", kind="Epic")
    _edit(env, epic, repo="code")
    leaf = env.file("leaf", parent=epic)
    _edit(env, leaf, phase="finish", affects=["src/a.py"])
    assert place.ensure(env.ctx(), env.chain_for(leaf)).ok
    plan = _plan(env, leaf)
    assert plan.blockers == () and len(plan.targets) == 1


def test_missing_worktree_blocker_names_place(place_env):
    env = place_env
    path = env.file()
    _edit(env, path, phase="finish", affects=["src/a.py"])
    (blocker,) = _plan(env, path).blockers
    assert path in blocker and "gw work place" in blocker


def test_worktree_on_other_branch_blocker(place_env):
    env = place_env
    path = _placed_leaf(env)
    run_git(env.chain_for(path)[-1].worktree, "switch", "-c", "elsewhere")
    (blocker,) = _plan(env, path).blockers
    assert path in blocker and "gw work place" in blocker


def test_missing_target_branch_blocker(place_env):
    env = place_env
    _epic, leaf = _placed_epic_with_leaf(env)
    run_git(env.chain_for(leaf)[0].worktree, "switch", "--detach")
    run_git(env.repo, "branch", "-D", env.chain_for(leaf)[0].branch)
    (blocker,) = _plan(env, leaf).blockers
    assert leaf in blocker and "target branch" in blocker


def test_nested_target_not_held_blocker_names_owner_place(place_env):
    env = place_env
    epic, leaf = _placed_epic_with_leaf(env)
    run_git(env.chain_for(leaf)[0].worktree, "switch", "--detach")
    (blocker,) = _plan(env, leaf).blockers
    assert f"gw work place {epic}" in blocker


def test_target_held_twice_blocker(place_env, tmp_path):
    env = place_env
    path = _placed_leaf(env)
    run_git(env.repo, "worktree", "add", "--force", str(tmp_path / "second-main"), "main")
    (blocker,) = _plan(env, path).blockers
    assert path in blocker and "'main'" in blocker and "second-main" in blocker


def test_finish_strategies_reads_once(place_env, monkeypatch):
    env = place_env
    store = finish_module.workspace_store(env.layout)
    calls = []
    original = type(store).read_explicit

    def counted(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(type(store), "read_explicit", counted)
    assert finish_strategies(env.layout, ["code", "code", "other"]) == {"code": "squash", "other": "squash"}
    assert len(calls) == 1


@pytest.mark.parametrize("names", [()])
def test_no_names_no_strategies(place_env, names):
    assert finish_strategies(place_env.layout, names) == {}
