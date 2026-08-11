import shutil
from pathlib import Path

from okf_io import load_bundle
from work_tracker_okf.children import ChildrenSync, apply_children_sync, plan_children_sync
from work_tracker_okf.items import IGNORE, load_items


def _vault(minimal_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    shutil.copytree(minimal_root, root)
    return root


def _load(root: Path):
    bundle = load_bundle(root, ignore=IGNORE)
    return bundle, load_items(bundle)


def test_the_fixture_vault_is_in_drift_on_exactly_one_parent(minimal_root, tmp_path):
    """`epic-alpha` has three children derived and none authored."""
    bundle, items = _load(_vault(minimal_root, tmp_path))
    syncs = plan_children_sync(bundle, items)
    assert [sync.slug for sync in syncs] == ["epic-alpha"]
    assert syncs[0] == ChildrenSync(
        slug="epic-alpha",
        path="work/epic-alpha.md",
        before=(),
        after=("bug-theta", "feature-beta"),
    )


def test_an_in_sync_parent_plans_nothing(minimal_root, tmp_path):
    root = _vault(minimal_root, tmp_path)
    bundle, items = _load(root)
    apply_children_sync(bundle, plan_children_sync(bundle, items), dry_run=False)
    bundle, items = _load(root)
    assert plan_children_sync(bundle, items) == ()


def test_an_archived_parent_is_never_planned(minimal_root, tmp_path):
    """An archived page is a record. Children are still derived across the
    archive on both ends -- only the rewrite stops at the boundary."""
    root = _vault(minimal_root, tmp_path)
    (root / "work" / "_archive").mkdir(parents=True, exist_ok=True)
    (root / "work" / "_archive" / "kid.md").write_text(
        "---\ntype: Bug\ntitle: Kid\nstatus: stable\nworkflow_status: open\nparent: bug-theta\n---\n\nbody\n",
        encoding="utf-8",
    )
    bundle, items = _load(root)
    assert "bug-theta" not in {sync.slug for sync in plan_children_sync(bundle, items)}


def test_dry_run_is_the_default_and_writes_nothing(minimal_root, tmp_path):
    root = _vault(minimal_root, tmp_path)
    bundle, items = _load(root)
    page = root / "work" / "epic-alpha.md"
    before = page.read_bytes()
    written = apply_children_sync(bundle, plan_children_sync(bundle, items))
    assert written == ("epic-alpha",)
    assert page.read_bytes() == before


def test_applying_writes_the_derived_order(minimal_root, tmp_path):
    root = _vault(minimal_root, tmp_path)
    bundle, items = _load(root)
    apply_children_sync(bundle, plan_children_sync(bundle, items), dry_run=False)
    text = (root / "work" / "epic-alpha.md").read_text(encoding="utf-8")
    assert "children:" in text
    assert text.index("bug-theta") < text.index("feature-beta")


def test_the_written_list_equals_the_projected_children(minimal_root, tmp_path):
    root = _vault(minimal_root, tmp_path)
    bundle, items = _load(root)
    apply_children_sync(bundle, plan_children_sync(bundle, items), dry_run=False)
    bundle, items = _load(root)
    epic = next(item for item in items if item.slug == "epic-alpha")
    document = bundle.concept("work/epic-alpha")
    assert document is not None
    assert tuple(document.fm_data()["children"]) == epic.children


def test_an_emptied_list_deletes_the_key(minimal_root, tmp_path):
    """`children:` is omitted-when-empty on these pages: a parent whose last
    child was detached loses the key rather than gaining `children: []`."""
    root = _vault(minimal_root, tmp_path)
    page = root / "work" / "spike-zeta.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("tags:", "children:\n  - ghost\ntags:"),
        encoding="utf-8",
    )
    bundle, items = _load(root)
    syncs = [sync for sync in plan_children_sync(bundle, items) if sync.slug == "spike-zeta"]
    assert syncs and syncs[0].after == ()
    apply_children_sync(bundle, syncs, dry_run=False)
    assert "children:" not in page.read_text(encoding="utf-8")


def test_the_rewrite_touches_only_the_children_key(minimal_root, tmp_path):
    root = _vault(minimal_root, tmp_path)
    bundle, items = _load(root)
    page = root / "work" / "epic-alpha.md"
    before = page.read_text(encoding="utf-8")
    apply_children_sync(bundle, plan_children_sync(bundle, items), dry_run=False)
    after = page.read_text(encoding="utf-8")
    removed = [line for line in before.splitlines() if line not in after.splitlines()]
    assert removed == []


def test_a_page_that_failed_to_parse_is_skipped_not_raised_on(minimal_root, tmp_path):
    root = _vault(minimal_root, tmp_path)
    (root / "work" / "unparseable.md").write_text("---\n: :\nbad\n---\n\nbody\n", encoding="utf-8")
    bundle, items = _load(root)
    assert plan_children_sync(bundle, items) == plan_children_sync(bundle, items)
    assert "unparseable" not in {sync.slug for sync in plan_children_sync(bundle, items)}
