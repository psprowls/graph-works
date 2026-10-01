from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from okf_ext.proposals import apply, list_proposals
from okf_io import build_link_graph, load_bundle
from repositories_okf.flagging import FlaggedLink, FlaggedPage, clone_prefix, flag_pages, plan_flag_proposal
from repositories_okf.git import FileChange
from repositories_okf.snapshots import Snapshot

CHANGES = (
    FileChange("M", "src/a.py"),
    FileChange("D", "docs/gone.md"),
    FileChange("R", "src/b.py", "src/c.py"),
    FileChange("A", "src/new.py"),
)


def _page(root: Path, rel: str, body: str, *, type_: str = "Concept") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {type_}\ntitle: {Path(rel).stem}\n---\n\n{body}\n", encoding="utf-8", newline="")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8", newline="")
    _page(root, "concepts/file-link.md", "See [a](/repositories/demo/references/git/src/a.py).")
    _page(root, "concepts/relative.md", "See [gone](../repositories/demo/references/git/docs/gone.md).")
    _page(root, "concepts/renamed.md", "See [b](/repositories/demo/references/git/src/b.py#L3).")
    _page(root, "concepts/directory.md", "See [src](/repositories/demo/references/git/src/).")
    _page(root, "concepts/root.md", "See [clone](/repositories/demo/references/git/).")
    _page(root, "concepts/unchanged.md", "See [readme](/repositories/demo/references/git/README.md).")
    _page(root, "concepts/added-only.md", "See [new](/repositories/demo/references/git/src/new.py).")
    _page(root, "concepts/other-repo.md", "See [x](/repositories/other/references/git/src/a.py).")
    _page(root, "concepts/external.md", "See [gh](https://github.com/org/demo/blob/main/src/a.py).")
    _page(root, "repositories/demo/research/notes.md", "See [a](/repositories/demo/references/git/src/a.py).")
    _page(
        root,
        "repositories/demo/snapshots/2026-09-01-aaaaaaa.md",
        "See [a](/repositories/demo/references/git/src/a.py).",
        type_="RepositorySnapshot",
    )
    _page(
        root,
        "repositories/demo/changelog.md",
        "See [a](/repositories/demo/references/git/src/a.py).",
        type_="RepositoryChangelog",
    )
    _page(root, "proposals/x.md", "See [a](/repositories/demo/references/git/src/a.py).", type_="Proposal")
    return root


def _flags(root: Path) -> dict[str, FlaggedPage]:
    bundle = load_bundle(root)
    return {page.page: page for page in flag_pages(bundle, build_link_graph(bundle), "demo", CHANGES)}


def test_clone_prefix() -> None:
    assert clone_prefix("demo") == "repositories/demo/references/git/"


def test_modified_deleted_renamed_directory_and_root_links_are_flagged(tmp_path: Path) -> None:
    flags = _flags(_bundle(tmp_path))
    assert set(flags) == {
        "concepts/file-link.md",
        "concepts/relative.md",
        "concepts/renamed.md",
        "concepts/directory.md",
        "concepts/root.md",
        "repositories/demo/research/notes.md",
    }
    assert flags["concepts/file-link.md"].links == (FlaggedLink("src/a.py", "src/a.py", "M", None),)
    assert flags["concepts/relative.md"].links == (FlaggedLink("docs/gone.md", "docs/gone.md", "D", None),)
    assert flags["concepts/renamed.md"].links == (FlaggedLink("src/b.py", "src/b.py", "R", "src/c.py"),)
    assert {link.changed for link in flags["concepts/directory.md"].links} == {"src/a.py", "src/b.py", "src/new.py"}
    assert {link.changed for link in flags["concepts/root.md"].links} == {
        "src/a.py",
        "docs/gone.md",
        "src/b.py",
        "src/new.py",
    }
    assert flags["concepts/file-link.md"].title == "file-link"


def test_reasons_read_as_prose() -> None:
    page = FlaggedPage(
        "p.md", "p", (FlaggedLink("src/", "src/b.py", "R", "src/c.py"), FlaggedLink("src/a.py", "src/a.py", "M", None))
    )
    assert page.reasons == ("`src/b.py` renamed to `src/c.py` (linked as `src/`)", "`src/a.py` modified")


def test_the_snapshot_page_is_the_proposal_source_and_a_second_advance_merges(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    at = datetime(2026, 9, 29, 20, 40, tzinfo=UTC)
    first = Snapshot(name="demo", commit="b" * 40, fetched_at="2026-09-29T20:40:00Z", describe="v1.1.0")
    bundle = load_bundle(root)
    page = _flags(root)["concepts/file-link.md"]
    plan = plan_flag_proposal(bundle, page, first, by="repositories-okf/test", at=at)
    assert plan.ok and not plan.is_empty
    apply(bundle, plan)
    second = Snapshot(name="demo", commit="c" * 40, fetched_at="2026-09-30T20:40:00Z", describe="v1.2.0")
    bundle = load_bundle(root)
    apply(bundle, plan_flag_proposal(bundle, page, second, by="repositories-okf/test", at=at))
    proposals = [
        proposal for proposal in list_proposals(load_bundle(root)) if proposal.target == "concepts/file-link.md"
    ]
    assert len(proposals) == 1
    resources = sorted(source["resource"] for source in proposals[0].sources)
    assert resources == [f"/{first.path}", f"/{second.path}"]


def test_directory_and_root_links_flag_additions_only(tmp_path: Path) -> None:
    bundle = load_bundle(_bundle(tmp_path))
    pages = flag_pages(bundle, build_link_graph(bundle), "demo", [FileChange("A", "src/new.py")])
    assert {page.page for page in pages} == {"concepts/directory.md", "concepts/root.md"}
    assert all(page.links[0].status == "A" for page in pages)


def test_directory_links_flag_both_rename_endpoints_but_exact_new_file_does_not(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    _page(root, "concepts/destination.md", "[dest](/repositories/demo/references/git/new/)")
    _page(root, "concepts/new-file.md", "[file](/repositories/demo/references/git/new/a.py)")
    bundle = load_bundle(root)
    change = FileChange("R", "src/a.py", "new/a.py")
    pages = flag_pages(bundle, build_link_graph(bundle), "demo", [change])
    flags = {page.page: page for page in pages}
    assert "concepts/directory.md" in flags
    assert "concepts/destination.md" in flags
    assert "concepts/root.md" in flags
    assert "concepts/new-file.md" not in flags
    assert flags["concepts/destination.md"].links == (FlaggedLink("new/", "src/a.py", "R", "new/a.py"),)
