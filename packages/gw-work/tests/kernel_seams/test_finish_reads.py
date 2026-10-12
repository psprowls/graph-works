from gw_kernel.workspace.layout import layout_for
from gw_pipeline import finish as finish_module
from gw_pipeline.finish import resolve_finish_targets
from gw_pipeline.repo_context import RepositoryContext
from okf_io import load_bundle
from work_tracker_okf.items import load_items


def setup(tmp_path, monkeypatch, *, scalar=True, nested=False, base_checkout=False):
    layout = layout_for(tmp_path)
    layout.manifest_path.write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\n"
        "repositories:\n  core: {path: /core}\n  ui: {path: /ui}\n",
        encoding="utf-8",
    )
    (layout.root / "dispatch.yaml").write_text(
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


def test_next_projects_shared_targets_and_nonfinish_is_empty(tmp_path, monkeypatch):
    from graph_works_wire.work import next_payload
    from gw_work.commands import run_next

    layout, items, path = setup(tmp_path, monkeypatch)
    expected = resolve_finish_targets(layout, items, path).targets
    result = run_next(layout, path)
    assert result.finish_targets == expected
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert [t["repo"]["name"] for t in payload["finish_targets"]] == ["core", "ui"]
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text().replace("phase: finish", "phase: design"), encoding="utf-8")
    assert run_next(layout, path).finish_targets == ()


def test_next_blocks_entire_finish_when_one_target_is_unverified(tmp_path, monkeypatch):
    from graph_works_wire.work import next_payload
    from gw_work.commands import run_next

    layout, _items, path = setup(tmp_path, monkeypatch)
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text().replace("branch: epic/a}", "branch: missing}"), encoding="utf-8")
    result = run_next(layout, path)
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert payload["action"] is None
    assert payload["blockers"]
    assert [t.repo.name for t in result.finish_targets] == ["core"]
