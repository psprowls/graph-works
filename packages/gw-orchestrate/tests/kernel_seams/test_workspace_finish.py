import re
from dataclasses import replace

import pytest
from gw_kernel.workspace.layout import layout_for
from gw_pipeline import finish as finish_module
from gw_pipeline.finish import resolve_finish_targets
from gw_pipeline.placement import branch_name
from gw_pipeline.repo_context import RepositoryContext
from okf_io import load_bundle
from work_tracker_okf.items import LegacyPlacement, Stamp, load_items


def setup(tmp_path, monkeypatch, *, scalar=True, nested=False, base_checkout=True, ui_child=True):
    layout = layout_for(tmp_path)
    layout.manifest_path.write_text(
        "version: 1\nworkflow_config: workflow.yaml\nrepositories:\n  core: {path: /core}\n  ui: {path: /ui}\n",
        encoding="utf-8",
    )
    (layout.root / "workflow.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {stage: finish}\n"
        '      prompt_tail: "Auto-drive context: merge target {merge_target}"\n',
        encoding="utf-8",
    )
    root = layout.bundle_dir / "work"
    root.mkdir(parents=True)
    path = "work/epic-a"
    if nested:
        (root / "epic-parent.md").write_text(
            "---\ntype: Epic\nrepo: core\nworktree: /core/parent\nbranch: epic/parent\n"
            "repo_stamps:\n  ui: {worktree: /ui/parent, branch: epic/parent}\n---\n",
            encoding="utf-8",
        )
        path = "work/epic-parent/children/epic-a"
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Epic\nrepo: core\nphase: finish\nwork_status: in-progress\naffects: [packages]\n"
        + ("worktree: /core/epic\nbranch: epic/a\n" if scalar else "")
        + "repo_stamps:\n  ui: {worktree: /ui/epic, branch: epic/a}\n---\n",
        encoding="utf-8",
    )

    if ui_child:
        # A resolved child that finished into ui makes ui a finish target of the epic (design section 5).
        child = layout.bundle_dir / f"{path}/children/feature-ui.md"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.write_text(
            "---\ntype: Feature\nrepo: ui\nphase: done\nwork_status: resolved\naffects: [packages]\n---\n",
            encoding="utf-8",
        )
        receipt = child.parent / "feature-ui" / "references" / "04-finish-receipt.md"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        sha = "a" * 40
        receipt.write_text(
            f"---\ntype: Explanation\nreceipt_version: 2\nowner: {path}/children/feature-ui\nintegrations:\n"
            f"  - repo: ui\n    source_branch: epic/a\n    source_commit: {sha}\n    target_branch: main\n"
            f"    result_commit: {sha}\n    strategy: attested\n    evidence: accepted\n"
            "    accepted_by: Reviewer\n    reason: Reviewed\n---\n",
            encoding="utf-8",
            newline="\n",
        )

    def observe(repo, **kwargs):
        prefix = str(repo)
        inventory = {"epic/a": (prefix + "/epic",), "epic/parent": (prefix + "/parent",)}
        exists = {prefix + "/epic": True, prefix + "/parent": True}
        usable = {prefix: True, prefix + "/epic": True, prefix + "/parent": True}
        branches = {"main", "trunk", "epic/a", "epic/parent"}
        # The item's computed checkout is a live worktree on its computed branch.

        kind = re.search(r"^type: (\w+)", page.read_text(encoding="utf-8"), re.M).group(1)
        computed = branch_name(path, kind)
        checkout = str((layout.worktrees_dir / prefix.strip("/") / computed).resolve())
        inventory[computed] = (checkout,)
        exists[checkout] = usable[checkout] = True
        branches.add(computed)
        if base_checkout:
            inventory["main"] = (prefix,)
            exists[prefix] = True
        return RepositoryContext(
            prefix, prefix, "main", True, inventory, exists, True, usable, branches=frozenset(branches)
        )

    monkeypatch.setattr(finish_module, "observe_repository", observe)
    return layout, load_items(load_bundle(layout.bundle_dir)), path


def _next_and_orchestrate(tmp_path, monkeypatch, scalar):
    from gw_orchestrate import commands
    from gw_pipeline import finish
    from gw_work.commands import run_next

    layout, _items, path = setup(tmp_path, monkeypatch, scalar=scalar)
    monkeypatch.setattr(commands, "observe_repository", finish.observe_repository)
    monkeypatch.setattr(commands, "repository_identity", lambda p: str(p))
    # Relay dispatch requires an explicit tail, as in initialized workspaces.
    (layout.root / "workflow.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {stage: finish}\n"
        '      prompt_tail: "Auto-drive context: merge target {merge_target}"\n',
        encoding="utf-8",
    )
    return layout, run_next(layout, path), commands.run_orchestrate(layout, path)


@pytest.mark.parametrize("scalar", [True, False])
def test_next_and_orchestrate_have_identical_finish_targets(tmp_path, monkeypatch, scalar):
    _, attended, automated = _next_and_orchestrate(tmp_path, monkeypatch, scalar)
    assert len(automated.dispatches) == 1, automated.blocked
    dispatch = automated.dispatches[0]
    assert automated.finish_targets[dispatch.key] == attended.finish_targets


@pytest.mark.parametrize("scalar", [True, False])
def test_next_and_orchestrate_payloads_have_identical_finish_targets(tmp_path, monkeypatch, scalar):
    from gw_wire.work import next_payload, orchestrate_payload

    layout, attended, automated = _next_and_orchestrate(tmp_path, monkeypatch, scalar)
    assert (
        orchestrate_payload(automated)["dispatches"][0]["finish_targets"]
        == next_payload(attended, bundle_root=layout.bundle_dir)["finish_targets"]
    )


@pytest.mark.parametrize("refuse_first", [True, False])
def test_finish_reservations_follow_acceptance(tmp_path, monkeypatch, refuse_first):
    from gw_kernel.workspace.dispatch_config import load_dispatch_config
    from gw_orchestrate.commands import plan
    from gw_pipeline.finish import FinishPlan

    layout, items, root_path = setup(tmp_path, monkeypatch)
    root = items[0]
    paths = tuple(f"{root_path}/children/feature-{name}" for name in ("a", "b"))
    children = tuple(
        replace(root, path=p, type="Feature", parent_path=root_path, ancestor_paths=(root_path,), child_paths=())
        for p in paths
    )
    root = replace(root, phase="execute", child_paths=paths)
    targets = resolve_finish_targets(layout, items, root_path).targets
    # The first candidate refuses; the second must receive the single global slot.
    rules = load_dispatch_config(layout).rules
    result = plan(
        (root, *children),
        root_path,
        dispatch_rules=rules,
        max_parallel=1 if refuse_first else 2,
        workspace=str(layout.root),
        default_base="main",
        worktrees_dir=str(layout.worktrees_dir),
        finish_plans={
            paths[0]: FinishPlan((), ("unverified target",)) if refuse_first else FinishPlan(targets, ()),
            paths[1]: FinishPlan(targets, ()),
        },
    )
    assert [d.slug for d in result.dispatches] == [paths[1] if refuse_first else paths[0]]
    assert result.blocked[0].path == (paths[0] if refuse_first else paths[1])
    if not refuse_first:
        assert result.blocked[0].kind == "worktree-pending"


@pytest.mark.parametrize("leaf_type", ["Feature", "Bug"])
def test_unstamped_leaf_finishes_verified_declared_checkout(tmp_path, monkeypatch, leaf_type):
    from gw_orchestrate import commands
    from gw_pipeline import finish
    from gw_work.commands import run_next

    layout, _items, path = setup(tmp_path, monkeypatch, scalar=False, ui_child=False)
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(
        f"---\ntype: {leaf_type}\nrepo: core\nphase: finish\nwork_status: in-progress\naffects: [packages]\n---\n",
        encoding="utf-8",
    )
    previous = finish.observe_repository

    def observe(repo, **kwargs):
        context = previous(repo, **kwargs)
        return replace(
            context,
            inventory={**context.inventory, "main": (str(repo),)},
            path_exists={**context.path_exists, str(repo): True},
        )

    monkeypatch.setattr(finish, "observe_repository", observe)
    monkeypatch.setattr(commands, "observe_repository", observe)
    monkeypatch.setattr(commands, "repository_identity", lambda p: str(p))
    attended = run_next(layout, path)
    assert len(attended.finish_targets) == 1
    target = attended.finish_targets[0]
    assert target.worktree == str(layout.worktrees_dir / "core" / target.source_branch)
    assert (target.source_branch, target.target_branch, target.target_worktree) == (
        branch_name(path, leaf_type),
        "main",
        "/core",
    )
    automated = commands.run_orchestrate(layout, path)
    assert len(automated.dispatches) == 1, automated.blocked
    assert automated.finish_targets[automated.dispatches[0].key] == attended.finish_targets
    legacy = load_items(load_bundle(layout.bundle_dir))[0].legacy_placement
    assert legacy is None or not legacy.branch


@pytest.mark.parametrize("leaf_type", ["Feature", "Bug"])
def test_unstamped_descendant_finishes_from_enclosing_anchor(tmp_path, monkeypatch, leaf_type):
    from gw_orchestrate import commands
    from gw_work.commands import run_next

    layout, _items, path = setup(tmp_path, monkeypatch, nested=True, ui_child=False)
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(
        f"---\ntype: {leaf_type}\nrepo: core\nphase: finish\nwork_status: in-progress\naffects: [packages]\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "observe_repository", finish_module.observe_repository)
    monkeypatch.setattr(commands, "repository_identity", lambda p: str(p))
    attended = run_next(layout, path)
    assert attended.route.blockers == ()
    [target] = attended.finish_targets
    assert target.repo.name == "core"
    assert target.worktree == str(layout.worktrees_dir / "core" / target.source_branch)
    assert target.source_branch == branch_name(path, leaf_type)
    assert target.target_branch == "epic/parent"
    assert target.target_worktree == "/core/parent"
    automated = commands.run_orchestrate(layout, path)
    assert len(automated.dispatches) == 1, automated.blocked
    assert automated.finish_targets[automated.dispatches[0].key] == attended.finish_targets


@pytest.mark.parametrize("same_worktree", [True, False])
def test_live_foreign_source_stays_reserved_after_anchor_revalidation_fails(tmp_path, monkeypatch, same_worktree):
    from gw_kernel.workspace.dispatch_config import load_dispatch_config
    from gw_orchestrate.commands import plan, session_name
    from gw_pipeline.finish import FinishPlan

    layout, items, root_path = setup(tmp_path, monkeypatch)
    root = items[0]
    live_path = f"{root_path}/children/feature-live"
    ready_path = f"{root_path}/children/feature-ready"
    live_item = replace(
        root,
        legacy_placement=LegacyPlacement(None, None, {"ui": Stamp("/ui/epic", "epic/a")}),
        path=live_path,
        type="Feature",
        parent_path=root_path,
        ancestor_paths=(root_path,),
        affects=("live-only",),
    )
    ready_item = replace(live_item, path=ready_path, affects=("ready-only",) if same_worktree else live_item.affects)
    root = replace(root, phase="execute", child_paths=(live_path, ready_path))
    targets = resolve_finish_targets(layout, items, root_path).targets[1:]
    from pathlib import Path

    from gw_pipeline import finish

    context = finish.observe_repository(Path("/ui"))
    if not same_worktree:
        targets = (replace(targets[0], worktree="/ui/other"),)
    result = plan(
        (root, live_item, ready_item),
        root_path,
        dispatch_rules=load_dispatch_config(layout).rules,
        max_parallel=2,
        workspace=str(layout.root),
        default_base="main",
        worktrees_dir=str(layout.worktrees_dir),
        live=(session_name(live_path, "Feature", "finish"),),
        repo_contexts={"ui": context},
        finish_plans={live_path: FinishPlan((), ("enclosing anchor dirty",)), ready_path: FinishPlan(targets, ())},
    )
    assert not result.dispatches
    expected_kind = "worktree-pending" if same_worktree else "affects-overlap"
    assert any(b.path == ready_path and b.kind == expected_kind for b in result.blocked)
