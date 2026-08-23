import json
from datetime import date
from pathlib import Path

from code_wiki_okf.init import install_bundle
from code_wiki_okf.resources import resource_index
from okf_ext.shape import load_sections
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
    assert index.member_for("package:a") == "packages/a.md"
    assert index.members_by_resource == {
        "package:a": ("packages/a.md",),
        "package:b": ("packages/b.md",),
    }


def test_resource_index_skips_documents_without_resource(tmp_path: Path) -> None:
    path = tmp_path / "packages" / "no_resource.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: Package\ntitle: no_resource\n---\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    assert index.get("anything") is None
    assert len(index.by_resource) == 0


def test_resource_index_exposes_every_member_in_a_collision(tmp_path: Path) -> None:
    _write_concept(tmp_path, "packages/a.md", "package:dup")
    _write_concept(tmp_path, "packages/b.md", "package:dup")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    assert index.get("package:dup") is None
    assert index.member_for("package:dup") is None
    assert index.members_by_resource["package:dup"] == ("packages/a.md", "packages/b.md")


def test_resource_index_triple_collision_keeps_all_three_members(tmp_path: Path) -> None:
    _write_concept(tmp_path, "packages/a.md", "package:triple")
    _write_concept(tmp_path, "packages/b.md", "package:triple")
    _write_concept(tmp_path, "packages/c.md", "package:triple")
    bundle = load_bundle(tmp_path)
    index = resource_index(bundle)
    assert index.get("package:triple") is None
    assert index.members_by_resource["package:triple"] == (
        "packages/a.md",
        "packages/b.md",
        "packages/c.md",
    )


def test_resource_index_exposes_filesystem_equivalent_live_members(tmp_path: Path) -> None:
    _write_concept(tmp_path, "packages/Caf\N{LATIN SMALL LETTER E WITH ACUTE}.md", "package:one")

    index = resource_index(load_bundle(tmp_path))

    assert index.filesystem_members_for("PACKAGES/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md") == (
        "packages/Caf\N{LATIN SMALL LETTER E WITH ACUTE}.md",
    )


def test_resource_index_detects_a_directory_at_a_target_file_member(tmp_path: Path) -> None:
    occupied = tmp_path / "repositories" / "demo" / "files" / "a.py.md"
    occupied.mkdir(parents=True)

    index = resource_index(load_bundle(tmp_path))

    assert index.filesystem_path_conflicts_for("repositories/demo/files/a.py.md") == (
        "repositories/demo/files/a.py.md",
    )


def test_resource_index_detects_normalization_equivalent_ancestor_file(tmp_path: Path) -> None:
    relative = "repositories/demo/f\N{LATIN SMALL LETTER I}\N{COMBINING ACUTE ACCENT}les"
    occupied = tmp_path / relative
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"pre-existing ancestor file\n")

    index = resource_index(load_bundle(tmp_path))

    assert index.filesystem_path_conflicts_for("repositories/demo/f\N{LATIN SMALL LETTER I WITH ACUTE}les/a.py.md") == (
        relative,
    )


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


def test_dependency_declarations_own_and_validate_implemented_by(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=date(2026, 1, 1), dry_run=False)

    schema = json.loads((tmp_path / "schema/Dependency.schema.json").read_text(encoding="utf-8"))
    sections = load_sections(tmp_path / "sections")

    assert schema["properties"]["implemented_by"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert "implemented_by" in sections.types["Dependency"].frontmatter.owned
