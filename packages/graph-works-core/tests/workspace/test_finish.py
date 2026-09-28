import subprocess
from dataclasses import replace
from datetime import date

import pytest
from graph_works_core.workspace import finish as finish_module
from graph_works_core.workspace.finish import resolve_finish_targets
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.repo_context import RepositoryContext
from okf_io import load_bundle
from work_tracker_okf.items import load_items


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _git_workspace_with_code(tmp_path):
    from graph_works_core import apply_init, plan_init

    layout = apply_init(plan_init(tmp_path / "workspace", today=date(2026, 9, 23), topic="Finish")).layout
    code = tmp_path / "code"
    code.mkdir()
    for repo in (layout.root, code):
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "config", "user.email", "test@example.test")
        _git(repo, "add", "-A")
        _git(repo, "commit", "--allow-empty", "-m", "base")
    layout.manifest_path.write_text(f"version: 1\nrepositories:\n  code: {{path: {code}}}\n", encoding="utf-8")
    _git(layout.root, "add", "-A")
    _git(layout.root, "commit", "-m", "configure code")
    return layout, code


def _write_workspace_finish_item(layout, *, ws_source, code_source=None, parent_anchor=None, code_anchor=None):
    owner = "work/epic-child"
    if parent_anchor is not None:
        parent = layout.bundle_dir / "work/epic-parent.md"
        parent.parent.mkdir(parents=True, exist_ok=True)
        stamps = "repo_stamps:\n"
        if parent_anchor:
            stamps += f"  _workspace: {{worktree: {parent_anchor}, branch: epic/ws}}\n"
        if code_anchor:
            stamps += f"  code: {{worktree: {code_anchor}, branch: epic/code}}\n"
        scalar = f"worktree: {code_anchor}\nbranch: epic/code\n" if code_anchor else ""
        parent.write_text(f"---\ntype: Epic\nrepo: code\n{scalar}{stamps}---\n", encoding="utf-8")
        owner = "work/epic-parent/children/epic-child"
    page = layout.bundle_dir / f"{owner}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    code_stamp = f"  code: {{worktree: {code_source}, branch: feature/code}}\n" if code_source else ""
    page.write_text(
        "---\ntype: Epic\nrepo: code\nphase: finish\nwork_status: in-progress\n"
        f"repo_stamps:\n{code_stamp}  _workspace: {{worktree: {ws_source}, branch: feature/ws}}\n---\n",
        encoding="utf-8",
    )
    return owner


@pytest.mark.parametrize("nested", [False, True])
def test_workspace_stamp_targets_the_enclosing_workspace_anchor_or_main(tmp_path, nested):
    layout, code = _git_workspace_with_code(tmp_path)
    ws_anchor, ws_source, code_source = (tmp_path / n for n in ("ws-anchor", "ws-source", "code-source"))
    _git(layout.root, "worktree", "add", "-b", "epic/ws", str(ws_anchor))
    _git(layout.root, "worktree", "add", "-b", "feature/ws", str(ws_source), "epic/ws" if nested else "main")
    code_anchor = tmp_path / "code-anchor" if nested else None
    if code_anchor:
        _git(code, "worktree", "add", "-b", "epic/code", str(code_anchor))
    _git(code, "worktree", "add", "-b", "feature/code", str(code_source))
    owner = _write_workspace_finish_item(
        layout,
        ws_source=ws_source,
        code_source=code_source,
        parent_anchor=ws_anchor if nested else None,
        code_anchor=code_anchor,
    )
    result = resolve_finish_targets(layout, load_items(load_bundle(layout.bundle_dir)), owner)
    assert not result.blockers
    assert [t.repo.name for t in result.targets] == ["code", "_workspace"]
    ws = result.targets[-1]
    assert (ws.source_branch, ws.target_branch) == ("feature/ws", "epic/ws" if nested else "main")


def test_foreign_only_code_item_still_finishes_in_its_code_worktree(tmp_path):
    layout, code = _git_workspace_with_code(tmp_path)
    ws_source, code_source = tmp_path / "ws-source", tmp_path / "code-source"
    _git(layout.root, "worktree", "add", "-b", "feature/ws", str(ws_source))
    _git(code, "worktree", "add", "-b", "feature/code", str(code_source))
    owner = _write_workspace_finish_item(layout, ws_source=ws_source, code_source=code_source)
    result = resolve_finish_targets(layout, load_items(load_bundle(layout.bundle_dir)), owner)
    assert not result.blockers
    assert [t.repo.name for t in result.targets] == ["code", "_workspace"]
    assert result.targets[0].worktree == str(code_source)


def test_missing_enclosing_workspace_anchor_blocks(tmp_path):
    layout, _code = _git_workspace_with_code(tmp_path)
    ws_source = tmp_path / "ws-source"
    _git(layout.root, "worktree", "add", "-b", "feature/ws", str(ws_source))
    owner = _write_workspace_finish_item(layout, ws_source=ws_source, parent_anchor="")
    result = resolve_finish_targets(layout, load_items(load_bundle(layout.bundle_dir)), owner)
    assert any("prepare enclosing integration anchor" in b and "'_workspace'" in b for b in result.blockers)


def test_workspace_stamp_with_placement_disabled_blocks(tmp_path, monkeypatch):
    layout, _code = _git_workspace_with_code(tmp_path)
    ws_source = tmp_path / "ws-source"
    _git(layout.root, "worktree", "add", "-b", "feature/ws", str(ws_source))
    owner = _write_workspace_finish_item(layout, ws_source=ws_source)
    monkeypatch.setattr(finish_module, "workspace_repo", lambda _layout: (None, "off"))
    result = resolve_finish_targets(layout, load_items(load_bundle(layout.bundle_dir)), owner)
    assert any("workspace placement is disabled" in b for b in result.blockers)


def test_inspect_is_incomplete_until_the_workspace_target_merges(tmp_path):
    from graph_works_core.orchestrate.finish_receipt import run_record_finish

    layout, code = _git_workspace_with_code(tmp_path)
    ws_source, code_source = tmp_path / "ws-source", tmp_path / "code-source"
    _git(layout.root, "worktree", "add", "-b", "feature/ws", str(ws_source))
    _git(code, "worktree", "add", "-b", "feature/code", str(code_source))
    _git(ws_source, "commit", "--allow-empty", "-m", "workspace work")
    _git(code_source, "commit", "--allow-empty", "-m", "code work")
    owner = _write_workspace_finish_item(layout, ws_source=ws_source, code_source=code_source)
    _git(code, "merge", "--ff-only", "feature/code")
    code_tip = _git(code, "rev-parse", "HEAD")
    recorded = run_record_finish(layout, owner, repo_name="code", today=date(2026, 9, 23))
    assert recorded.refusal is None
    pending = finish_module.inspect_finish(layout, owner)
    assert not pending.complete
    assert any("_workspace" in b for b in pending.blockers)
    _git(layout.root, "merge", "--no-ff", "--no-edit", "feature/ws")
    recorded = run_record_finish(layout, owner, repo_name="_workspace", today=date(2026, 9, 23))
    assert recorded.refusal is None
    complete = finish_module.inspect_finish(layout, owner)
    assert complete.complete, complete.blockers
    assert complete.resolved_in == code_tip


def setup(tmp_path, monkeypatch, *, scalar=True, nested=False, base_checkout=False):
    layout = layout_for(tmp_path)
    layout.manifest_path.write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\n"
        "repositories:\n  core: {path: /core}\n  ui: {path: /ui}\n",
        encoding="utf-8",
    )
    (layout.root / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {variant: branch}\n"
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

    def observe(repo, **kwargs):
        prefix = str(repo)
        inventory = {"epic/a": (prefix + "/epic",), "epic/parent": (prefix + "/parent",)}
        if base_checkout:
            inventory["main" if prefix == "/core" else "trunk"] = (prefix,)
        return RepositoryContext(
            prefix,
            prefix,
            "main" if prefix == "/core" else "trunk",
            True,
            inventory,
            {prefix + "/epic": True, prefix + "/parent": True, **({prefix: True} if base_checkout else {})},
            True,
            {prefix: True, prefix + "/epic": True, prefix + "/parent": True},
            branches=frozenset({"main", "trunk", "epic/a", "epic/parent"}),
        )

    monkeypatch.setattr(finish_module, "observe_repository", observe)
    return layout, load_items(load_bundle(layout.bundle_dir)), path


@pytest.mark.parametrize(
    "scalar,nested,expected",
    [
        (True, False, [("core", "main"), ("ui", "trunk")]),
        (False, False, [("ui", "trunk")]),
        (True, True, [("core", "epic/parent"), ("ui", "epic/parent")]),
    ],
)
def test_finish_targets(tmp_path, monkeypatch, scalar, nested, expected):
    layout, items, path = setup(tmp_path, monkeypatch, scalar=scalar, nested=nested)
    result = resolve_finish_targets(layout, items, path)
    assert result.blockers == ()
    assert [(t.repo.name, t.target_branch) for t in result.targets] == expected
    assert all(t.source_branch == "epic/a" for t in result.targets)


def test_trunk_target_worktree_is_the_checkout_holding_default_base(tmp_path, monkeypatch):
    layout, items, path = setup(tmp_path, monkeypatch, base_checkout=True)
    result = resolve_finish_targets(layout, items, path)
    assert result.blockers == ()
    assert {target.repo.name: target.target_worktree for target in result.targets} == {
        "core": "/core",
        "ui": "/ui",
    }


def test_nested_owner_target_worktree_is_the_anchor(tmp_path, monkeypatch):
    layout, items, path = setup(tmp_path, monkeypatch, nested=True)
    result = resolve_finish_targets(layout, items, path)
    assert result.blockers == ()
    assert {target.repo.name: target.target_worktree for target in result.targets} == {
        "core": "/core/parent",
        "ui": "/ui/parent",
    }


@pytest.mark.parametrize("fault", ["unknown", "wrong-branch", "missing-anchor"])
def test_finish_refuses_unproven_target(tmp_path, monkeypatch, fault):
    layout, items, path = setup(tmp_path, monkeypatch, nested=True)
    if fault == "unknown":
        items = tuple(
            replace(i, repo_stamps={"missing": next(iter(i.repo_stamps.values()))}) if i.path == path else i
            for i in items
        )
    elif fault == "wrong-branch":
        items = tuple(replace(i, branch="wrong") if i.path == path else i for i in items)
    else:
        items = tuple(replace(i, repo_stamps={}) if i.path != path else i for i in items)
    result = resolve_finish_targets(layout, items, path)
    assert result.blockers
    if fault == "missing-anchor":
        assert any("prepare" in b for b in result.blockers)


def test_next_projects_shared_targets_and_nonfinish_is_empty(tmp_path, monkeypatch):
    from graph_works_core.work.commands import run_next
    from graph_works_wire.work import next_payload

    layout, items, path = setup(tmp_path, monkeypatch)
    expected = resolve_finish_targets(layout, items, path).targets
    result = run_next(layout, path)
    assert result.finish_targets == expected
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert [t["repo"]["name"] for t in payload["finish_targets"]] == ["core", "ui"]
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text().replace("phase: finish", "phase: design"), encoding="utf-8")
    assert run_next(layout, path).finish_targets == ()


def test_explicit_override_cannot_invent_foreign_assignments(tmp_path, monkeypatch):
    from pathlib import Path

    from graph_works_core.workspace.repos import ItemRepo

    layout, items, path = setup(tmp_path, monkeypatch)
    result = resolve_finish_targets(layout, items, path, single_repo=ItemRepo(None, Path("/override"), "flag"))
    assert result.targets == ()
    assert "foreign" in result.blockers[0]


@pytest.mark.parametrize("scalar", [True, False])
def test_next_and_orchestrate_have_identical_finish_targets(tmp_path, monkeypatch, scalar):
    from graph_works_core.orchestrate import commands
    from graph_works_core.work.commands import run_next
    from graph_works_core.workspace import finish
    from graph_works_wire.work import next_payload, orchestrate_payload

    layout, _items, path = setup(tmp_path, monkeypatch, scalar=scalar)
    monkeypatch.setattr(commands, "observe_repository", finish.observe_repository)
    monkeypatch.setattr(commands, "repository_identity", lambda p: str(p))
    # Relay dispatch requires an explicit tail, as in initialized workspaces.
    (layout.root / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {variant: branch}\n"
        '      prompt_tail: "Auto-drive context: merge target {merge_target}"\n',
        encoding="utf-8",
    )
    attended = run_next(layout, path)
    automated = commands.run_orchestrate(layout, path)
    assert len(automated.dispatches) == 1, automated.blocked
    dispatch = automated.dispatches[0]
    assert automated.finish_targets[dispatch.key] == attended.finish_targets
    assert (
        orchestrate_payload(automated)["dispatches"][0]["finish_targets"]
        == next_payload(attended, bundle_root=layout.bundle_dir)["finish_targets"]
    )


@pytest.mark.parametrize("refuse_first", [True, False])
def test_finish_reservations_follow_acceptance(tmp_path, monkeypatch, refuse_first):
    from graph_works_core.orchestrate.commands import plan
    from graph_works_core.workspace.dispatch_config import load_dispatch_config
    from graph_works_core.workspace.finish import FinishPlan

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
        finish_plans={
            paths[0]: FinishPlan((), ("unverified target",)) if refuse_first else FinishPlan(targets, ()),
            paths[1]: FinishPlan(targets, ()),
        },
    )
    assert [d.slug for d in result.dispatches] == [paths[1] if refuse_first else paths[0]]
    assert result.blocked[0].path == (paths[0] if refuse_first else paths[1])
    if not refuse_first:
        assert result.blocked[0].kind == "worktree-pending"


def test_next_blocks_entire_finish_when_one_target_is_unverified(tmp_path, monkeypatch):
    from graph_works_core.work.commands import run_next
    from graph_works_wire.work import next_payload

    layout, _items, path = setup(tmp_path, monkeypatch)
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text().replace("branch: epic/a}", "branch: missing}"), encoding="utf-8")
    result = run_next(layout, path)
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert payload["action"] is None
    assert payload["blockers"]
    assert [t.repo.name for t in result.finish_targets] == ["core"]


@pytest.mark.parametrize("leaf_type", ["Feature", "Bug"])
def test_unstamped_leaf_finishes_verified_declared_checkout(tmp_path, monkeypatch, leaf_type):
    from graph_works_core.orchestrate import commands
    from graph_works_core.work.commands import run_next
    from graph_works_core.workspace import finish

    layout, _items, path = setup(tmp_path, monkeypatch, scalar=False)
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
    assert (target.worktree, target.source_branch, target.target_branch) == ("/core", "main", "main")
    automated = commands.run_orchestrate(layout, path)
    assert len(automated.dispatches) == 1, automated.blocked
    assert automated.finish_targets[automated.dispatches[0].key] == attended.finish_targets
    assert not load_items(load_bundle(layout.bundle_dir))[0].branch


def test_unstamped_leaf_without_checkout_evidence_blocks(tmp_path, monkeypatch):
    layout, items, path = setup(tmp_path, monkeypatch)
    item = replace(items[0], type="Bug", worktree=None, branch=None, repo_stamps={})
    result = resolve_finish_targets(layout, (item,), path)
    assert not result.targets
    assert result.blockers


@pytest.mark.parametrize("leaf_type", ["Feature", "Bug"])
def test_unstamped_descendant_finishes_from_enclosing_anchor(tmp_path, monkeypatch, leaf_type):
    from graph_works_core.orchestrate import commands
    from graph_works_core.work.commands import run_next

    layout, _items, path = setup(tmp_path, monkeypatch, nested=True)
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
    assert target.worktree == "/core/parent"
    assert target.source_branch == "epic/parent"
    assert target.target_branch == "epic/parent"
    assert target.target_worktree == "/core/parent"
    automated = commands.run_orchestrate(layout, path)
    assert len(automated.dispatches) == 1, automated.blocked
    assert automated.finish_targets[automated.dispatches[0].key] == attended.finish_targets


def test_unstamped_descendant_with_unprepared_anchor_blocks(tmp_path, monkeypatch):
    layout, _items, path = setup(tmp_path, monkeypatch, nested=True)
    parent_page = layout.bundle_dir / "work/epic-parent.md"
    parent_page.write_text("---\ntype: Epic\nrepo: core\n---\n", encoding="utf-8")
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(
        "---\ntype: Bug\nrepo: core\nphase: finish\nwork_status: in-progress\naffects: [packages]\n---\n",
        encoding="utf-8",
    )
    items = load_items(load_bundle(layout.bundle_dir))
    result = resolve_finish_targets(layout, items, path)
    assert result.targets == ()
    assert any("prepare enclosing integration anchor" in b for b in result.blockers)
    assert not any("/core'" in b for b in result.blockers if "cannot verify" in b)


def test_unstamped_descendant_receipt_verifies_against_anchor_tip(tmp_path):
    import subprocess
    from datetime import date

    from graph_works_core import apply_init, plan_init
    from graph_works_core.workspace.finish import observe_integration

    def git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    layout = apply_init(plan_init(tmp_path / "workspace", today=date(2026, 9, 23), topic="Finish")).layout
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    git(repo, "commit", "--allow-empty", "-m", "base")
    layout.manifest_path.write_text(f"version: 1\nrepositories:\n  code: {{path: {repo}}}\n", encoding="utf-8")
    anchor = tmp_path / "anchor"
    git(repo, "worktree", "add", "-b", "epic/x", str(anchor))
    git(anchor, "commit", "--allow-empty", "-m", "child work")
    tip = git(anchor, "rev-parse", "HEAD")

    parent_page = layout.bundle_dir / "work/epic-parent.md"
    parent_page.parent.mkdir(parents=True, exist_ok=True)
    parent_page.write_text(f"---\ntype: Epic\nrepo: code\nworktree: {anchor}\nbranch: epic/x\n---\n", encoding="utf-8")
    child_page = layout.bundle_dir / "work/epic-parent/children/bug-a.md"
    child_page.parent.mkdir(parents=True, exist_ok=True)
    child_page.write_text(
        "---\ntype: Bug\nrepo: code\nphase: finish\nwork_status: in-progress\naffects: [packages]\n---\n",
        encoding="utf-8",
    )
    items = load_items(load_bundle(layout.bundle_dir))
    result = resolve_finish_targets(layout, items, "work/epic-parent/children/bug-a")
    assert result.blockers == ()
    [target] = result.targets
    entry = observe_integration(target)
    assert entry is not None
    assert entry.source_commit == entry.result_commit == tip


@pytest.mark.parametrize("same_worktree", [True, False])
def test_live_foreign_source_stays_reserved_after_anchor_revalidation_fails(tmp_path, monkeypatch, same_worktree):
    from graph_works_core.orchestrate.commands import plan, session_name
    from graph_works_core.workspace.dispatch_config import load_dispatch_config
    from graph_works_core.workspace.finish import FinishPlan
    from work_tracker_okf.items import Stamp

    layout, items, root_path = setup(tmp_path, monkeypatch)
    root = items[0]
    live_path = f"{root_path}/children/feature-live"
    ready_path = f"{root_path}/children/feature-ready"
    live_item = replace(
        root,
        path=live_path,
        type="Feature",
        parent_path=root_path,
        ancestor_paths=(root_path,),
        affects=("live-only",),
        worktree=None,
        branch=None,
        repo_stamps={"ui": Stamp("/ui/epic", "epic/a")},
    )
    ready_item = replace(live_item, path=ready_path, affects=("ready-only",) if same_worktree else live_item.affects)
    root = replace(root, phase="execute", child_paths=(live_path, ready_path))
    targets = resolve_finish_targets(layout, items, root_path).targets[1:]
    from pathlib import Path

    from graph_works_core.workspace import finish

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
        live=(session_name(live_path, "Feature", "finish"),),
        repo_contexts={"ui": context},
        finish_plans={live_path: FinishPlan((), ("enclosing anchor dirty",)), ready_path: FinishPlan(targets, ())},
    )
    assert not result.dispatches
    expected_kind = "worktree-pending" if same_worktree else "affects-overlap"
    assert any(b.path == ready_path and b.kind == expected_kind for b in result.blocked)


@pytest.mark.parametrize("nested", [False, True])
def test_real_git_foreign_only_owner_targets_nearest_anchor(tmp_path, nested):
    import subprocess
    from datetime import date

    from graph_works_core import apply_init, plan_init

    def git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    layout = apply_init(plan_init(tmp_path / "workspace", today=date(2026, 9, 23), topic="Finish")).layout
    repos = {}
    for name in ("code", "ui"):
        repo = tmp_path / name
        repo.mkdir()
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.test")
        git(repo, "commit", "--allow-empty", "-m", "base")
        repos[name] = repo
    layout.manifest_path.write_text(
        "version: 1\nrepositories:\n" + "".join(f"  {n}: {{path: {p}}}\n" for n, p in repos.items()), encoding="utf-8"
    )
    parent = tmp_path / "parent"
    source = tmp_path / "source"
    git(repos["ui"], "worktree", "add", "-b", "epic/parent", str(parent))
    git(repos["ui"], "worktree", "add", "-b", "epic/child", str(source), "epic/parent")
    owner = "work/epic-child"
    if nested:
        parent_page = layout.bundle_dir / "work/epic-parent.md"
        parent_page.parent.mkdir(parents=True, exist_ok=True)
        parent_page.write_text(
            f"---\ntype: Epic\nrepo: code\nrepo_stamps:\n  ui: {{worktree: {parent}, branch: epic/parent}}\n---\n",
            encoding="utf-8",
        )
        owner = "work/epic-parent/children/epic-child"
    page = layout.bundle_dir / (owner + ".md")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Epic\nrepo: code\nphase: finish\nwork_status: in-progress\nrepo_stamps:\n"
        f"  ui: {{worktree: {source}, branch: epic/child}}\n---\n",
        encoding="utf-8",
    )
    result = resolve_finish_targets(layout, load_items(load_bundle(layout.bundle_dir)), owner)
    assert not result.blockers
    [target] = result.targets
    assert target.repo.name == "ui"
    assert target.worktree == str(source)
    assert target.target_branch == ("epic/parent" if nested else "main")
