from gw_kernel.workspace.layout import layout_for
from gw_pipeline import finish as finish_module
from gw_pipeline.finish import resolve_finish_targets
from gw_pipeline.placement import branch_name
from gw_pipeline.repo_context import RepositoryContext
from okf_io import load_bundle
from work_tracker_okf.items import load_items


def setup(tmp_path, monkeypatch, *, nested=False, base_checkout=False):
    layout = layout_for(tmp_path)
    layout.manifest_path.write_text(
        "version: 1\nworkflow_config: dispatch.yaml\nrepositories:\n  core: {path: /core}\n  ui: {path: /ui}\n",
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
            "---\ntype: Epic\nrepo: core\n---\n",
            encoding="utf-8",
        )
        path = "work/epic-parent/children/epic-a"
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Epic\nrepo: core\nphase: finish\nwork_status: in-progress\naffects: [packages]\n" + "---\n",
        encoding="utf-8",
    )

    def observe(repo, **kwargs):
        prefix = str(repo)
        computed = branch_name(path, "Epic")
        checkout = str((layout.worktrees_dir / prefix.strip("/") / computed).resolve())
        inventory = {computed: (checkout,)}
        exists = {checkout: True}
        usable = {prefix: True, checkout: True}
        branches = {"main", "trunk", computed}
        if nested:
            parent_branch = branch_name("work/epic-parent", "Epic")
            parent_checkout = str((layout.worktrees_dir / prefix.strip("/") / parent_branch).resolve())
            inventory[parent_branch] = (parent_checkout,)
            exists[parent_checkout] = usable[parent_checkout] = True
            branches.add(parent_branch)
        if base_checkout:
            inventory["main" if prefix == "/core" else "trunk"] = (prefix,)
            exists[prefix] = True
        return RepositoryContext(
            prefix,
            prefix,
            "main" if prefix == "/core" else "trunk",
            True,
            inventory,
            exists,
            True,
            usable,
            branches=frozenset(branches),
        )

    monkeypatch.setattr(finish_module, "observe_repository", observe)
    return layout, load_items(load_bundle(layout.bundle_dir)), path


def test_next_projects_shared_targets_and_nonfinish_is_empty(tmp_path, monkeypatch):
    from gw_wire.work import next_payload
    from gw_work.commands import run_next

    layout, items, path = setup(tmp_path, monkeypatch, base_checkout=True)
    expected = resolve_finish_targets(layout, items, path).targets
    result = run_next(layout, path)
    assert result.finish_targets == expected
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert [t["repo"]["name"] for t in payload["finish_targets"]] == ["core"]
    page = layout.bundle_dir / f"{path}.md"
    page.write_text(page.read_text().replace("phase: finish", "phase: design"), encoding="utf-8")
    assert run_next(layout, path).finish_targets == ()


def test_next_blocks_finish_when_the_declared_checkout_does_not_hold_the_target(tmp_path, monkeypatch):
    from gw_wire.work import next_payload
    from gw_work.commands import run_next

    layout, _items, path = setup(tmp_path, monkeypatch, base_checkout=False)
    result = run_next(layout, path)
    payload = next_payload(result, bundle_root=layout.bundle_dir)
    assert payload["action"] is None
    assert payload["blockers"]
    assert result.finish_targets == ()
