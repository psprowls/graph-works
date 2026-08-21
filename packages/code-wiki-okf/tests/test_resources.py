from pathlib import Path

from code_wiki_okf.resources import resource_index
from okf_io import load_bundle


def _write_concept(root: Path, relative: str, resource: str, type_: str = "Package") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {type_}\ntitle: {relative}\nresource: {resource}\n---\n", encoding="utf-8")


def test_resource_index_maps_resource_to_document(tmp_path: Path) -> None:
    _write_concept(tmp_path, "packages/a.md", "package:a")
    _write_concept(tmp_path, "packages/b.md", "package:b")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    assert index.get("package:a").concept_id == "packages/a"
    assert index.get("package:b").concept_id == "packages/b"
    assert index.duplicates == ()


def test_resource_index_skips_documents_without_resource(tmp_path: Path) -> None:
    path = tmp_path / "packages" / "no_resource.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: Package\ntitle: no_resource\n---\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    assert index.get("anything") is None
    assert len(index.by_resource) == 0


def test_resource_index_first_in_bundle_order_wins_collision(tmp_path: Path) -> None:
    _write_concept(tmp_path, "packages/a.md", "package:dup")
    _write_concept(tmp_path, "packages/b.md", "package:dup")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    winner = index.get("package:dup")
    assert winner is not None
    assert winner.concept_id in {"packages/a", "packages/b"}
    assert index.duplicates == ("package:dup",)


def test_resource_index_triple_collision_still_one_duplicate_entry(tmp_path: Path) -> None:
    _write_concept(tmp_path, "packages/a.md", "package:triple")
    _write_concept(tmp_path, "packages/b.md", "package:triple")
    _write_concept(tmp_path, "packages/c.md", "package:triple")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    winner = index.get("package:triple")
    assert winner is not None
    assert index.duplicates == ("package:triple",)


def test_a_dot_nested_mirror_page_is_indexed(tmp_path: Path) -> None:
    """Regression for the okf-io walk that dropped dot-nested members.
    `resource_index` is a pure function of `bundle.concepts`, so a page the
    walk never yielded is invisible here through no fault of its own -- which
    is what made 23 correctly-written mirror pages unfindable in the live
    bundle.
    """
    _write_concept(
        tmp_path,
        "repositories/demo/.agents/skills/x/SKILL.md.md",
        "file:demo/.agents/skills/x/SKILL.md",
        type_="File",
    )
    index = resource_index(load_bundle(tmp_path))
    assert "file:demo/.agents/skills/x/SKILL.md" in index.by_resource
    assert index.by_resource["file:demo/.agents/skills/x/SKILL.md"].concept_id == (
        "repositories/demo/.agents/skills/x/SKILL.md"
    )
