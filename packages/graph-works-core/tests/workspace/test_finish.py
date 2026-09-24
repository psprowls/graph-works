from dataclasses import replace

import pytest
from graph_works_core.workspace.finish import resolve_finish_targets
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.repo_context import RepositoryContext
from okf_io import load_bundle
from work_tracker_okf.items import load_items


def setup(tmp_path, monkeypatch, *, scalar=True, nested=False):
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
        return RepositoryContext(
            prefix,
            prefix,
            "main" if prefix == "/core" else "trunk",
            True,
            {"epic/a": (prefix + "/epic",), "epic/parent": (prefix + "/parent",)},
            {prefix + "/epic": True, prefix + "/parent": True},
            True,
            {prefix: True, prefix + "/epic": True, prefix + "/parent": True},
            branches=frozenset({"main", "trunk", "epic/a", "epic/parent"}),
        )

    monkeypatch.setattr("graph_works_core.workspace.finish.observe_repository", observe)
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
        replace(root, path=p, type="Feature", parent_path=root_path, ancestor_paths=(root_path,), active_child_paths=())
        for p in paths
    )
    root = replace(root, phase="execute", active_child_paths=paths)
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
