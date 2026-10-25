"""The accessor prefers a legacy stamp and otherwise computes the placement."""

from pathlib import Path

from gw_kernel.workspace.repos import ItemRepo
from gw_pipeline.placement import EffectivePlacement, LegacyRow, effective_placement, legacy_rows, placement_for
from okf_io import load_bundle
from work_tracker_okf.items import load_items

SHA = "a" * 40
OTHER_SHA = "b" * 40


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


def test_legacy_scalar_pair(tmp_path):
    items = _load(tmp_path / "b", {"work/feature-a": f"worktree: /w\nbranch: b\nstart_sha: {SHA}\n"})
    got = _call(items["work/feature-a"], items, tmp_path, repo=_repo(tmp_path))
    assert got == EffectivePlacement("code", "/w", "b", SHA, "legacy")


def test_legacy_repo_stamp_entry_with_own_start_sha(tmp_path):
    items = _load(
        tmp_path / "b",
        {
            "work/feature-a": f"start_sha: {SHA}\nrepo_stamps:\n"
            f"  other: {{worktree: /o, branch: ob, start_sha: {OTHER_SHA}}}\n"
        },
    )
    got = _call(items["work/feature-a"], items, tmp_path, repo=_repo(tmp_path, "other"))
    assert got == EffectivePlacement("other", "/o", "ob", OTHER_SHA, "legacy")


def test_legacy_stamp_wins_over_computed(tmp_path):
    items = _load(tmp_path / "b", {"work/feature-a": "worktree: /w\nbranch: psprowls/x-orca\n"})
    item = items["work/feature-a"]
    repo = _repo(tmp_path)
    computed = placement_for(
        item,
        items,
        repo_name="code",
        repo_root=repo.path,
        working_checkout=repo.path,
        worktrees_dir=tmp_path / "wt",
        default_base="main",
    )
    assert computed is not None and computed.branch != "psprowls/x-orca"
    assert _call(item, items, tmp_path, repo=repo).branch == "psprowls/x-orca"


def test_computed_when_unstamped(tmp_path):
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


def test_legacy_rows_order(tmp_path):
    items = _load(
        tmp_path / "b",
        {
            "work/feature-a": "worktree: /w\nbranch: b\nrepo_stamps:\n"
            "  zed: {worktree: /z, branch: zb}\n  alpha: {worktree: /a, branch: ab}\n"
        },
    )
    rows = legacy_rows(items["work/feature-a"])
    assert [r.repo_key for r in rows] == [None, "alpha", "zed"]
    assert rows[0] == LegacyRow(None, "/w", "b", None)
