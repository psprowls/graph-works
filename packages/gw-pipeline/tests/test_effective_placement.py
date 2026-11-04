"""The accessor computes the placement."""

from pathlib import Path

from gw_kernel.workspace.repos import ItemRepo
from gw_pipeline.placement import effective_placement
from okf_io import load_bundle
from work_tracker_okf.items import load_items

SHA = "a" * 40


def _load(root, pages):
    for path, body in pages.items():
        page = root / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"---\ntype: Feature\ntitle: T\ndescription: d\n{body}---\n", encoding="utf-8", newline="\n")
    return load_items(load_bundle(root)).by_path


def _call(item, items, tmp_path, *, repo, own="code"):
    return effective_placement(
        item, items, repo=repo, own_repo_name=own, worktrees_dir=tmp_path / "wt", default_base="main"
    )


def _repo(tmp_path, name="code"):
    return ItemRepo(name, tmp_path / name, "frontmatter")


def test_computed_placement(tmp_path):
    items = _load(tmp_path / "b", {"work/feature-a": f"start_sha: {SHA}\n"})
    item = items["work/feature-a"]
    got = _call(item, items, tmp_path, repo=_repo(tmp_path))
    assert got is not None and got.source == "computed"
    assert got.worktree == str(Path(tmp_path / "wt" / "code" / got.branch))
    assert got.start_sha == SHA


def test_owner_in_foreign_repo_computed(tmp_path):
    items = _load(tmp_path / "b", {"work/epic-a": "repo: code\n", "work/epic-a/children/feature-b": ""})
    got = _call(items["work/epic-a"], items, tmp_path, repo=_repo(tmp_path, "other"))
    assert got is not None and got.source == "computed"
    assert Path(got.worktree).parent == tmp_path / "wt" / "other" / Path(got.branch).parent
    assert got.start_sha is None
