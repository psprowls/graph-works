"""resolve_skill_anchor / gather_skill_sources / link walking.

Nineteen ported from wiki-io's test_ingest_source.py, five new. Nine ported
assertions changed a `list` literal to a `tuple` literal: `SkillBundle` is
frozen and its two file lists are tuples (spec §2.2). Nothing else was edited.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from doc_wiki_okf.reading import SkillBundle, gather_skill_sources, resolve_skill_anchor

# ---------------------------------------------------------------------------
# resolve_skill_anchor — ported
# ---------------------------------------------------------------------------


def test_resolve_skill_anchor_directory_with_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")
    assert resolve_skill_anchor(skill_dir) == skill_dir / "SKILL.md"


def test_resolve_skill_anchor_skill_md_file(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# My Skill\n", encoding="utf-8")
    assert resolve_skill_anchor(skill_md) == skill_md


def test_resolve_skill_anchor_unrelated_file_returns_none(tmp_path: Path) -> None:
    other = tmp_path / "notes.md"
    other.write_text("# Notes\n", encoding="utf-8")
    assert resolve_skill_anchor(other) is None


def test_resolve_skill_anchor_directory_without_skill_md_returns_none(tmp_path: Path) -> None:
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    (plain_dir / "README.md").write_text("# Readme\n", encoding="utf-8")
    assert resolve_skill_anchor(plain_dir) is None


def test_skill_bundle_fields() -> None:
    field_names = {f.name for f in dataclasses.fields(SkillBundle)}
    assert field_names == {
        "combined_text",
        "skill_dir",
        "anchor",
        "title",
        "included_files",
        "excluded_files",
        "scripts_dominant",
    }


# ---------------------------------------------------------------------------
# gather_skill_sources — single file, ported
# ---------------------------------------------------------------------------


def test_gather_single_file_combined_text_and_marker(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    anchor = skill_dir / "SKILL.md"
    anchor.write_text("# Skill\n\nBody line.\n", encoding="utf-8")

    bundle = gather_skill_sources(anchor)

    assert bundle.included_files == ("SKILL.md",)  # tuple (spec §2.2)
    assert bundle.combined_text.startswith("<!-- skill-file: SKILL.md -->\n")
    assert "Body line." in bundle.combined_text
    # The marker appears exactly once for the single included file.
    assert bundle.combined_text.count("<!-- skill-file:") == 1
    assert bundle.skill_dir == skill_dir.resolve()
    assert bundle.anchor == anchor.resolve()


def test_gather_title_frontmatter_name_wins(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    anchor = skill_dir / "SKILL.md"
    anchor.write_text(
        "---\nname: Frontmatter Name\n---\n\n# Heading Title\n\nBody.\n",
        encoding="utf-8",
    )
    assert gather_skill_sources(anchor).title == "Frontmatter Name"


def test_gather_title_falls_back_to_heading(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    anchor = skill_dir / "SKILL.md"
    anchor.write_text("# Heading Title\n\nBody.\n", encoding="utf-8")
    assert gather_skill_sources(anchor).title == "Heading Title"


def test_gather_title_none_when_absent(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    anchor = skill_dir / "SKILL.md"
    anchor.write_text("Just body text, no heading and no frontmatter.\n", encoding="utf-8")
    assert gather_skill_sources(anchor).title is None


def test_gather_excluded_files_captures_non_markdown(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n", encoding="utf-8")
    (skill_dir / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    (skill_dir / "logo.png").write_bytes(b"\x89PNG\r\n")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")

    assert bundle.excluded_files == ("helper.py", "logo.png")  # tuple (spec §2.2)
    assert "helper.py" not in bundle.combined_text  # not read into combined text


def test_gather_scripts_dominant_on_top_level_scripts_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# S\n", encoding="utf-8")
    (skill_dir / "scripts" / "run.sh").write_text("echo hi\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")

    assert bundle.scripts_dominant is True
    assert bundle.excluded_files == ("scripts/run.sh",)  # tuple (spec §2.2)


def test_gather_scripts_dominant_on_excluded_majority(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n", encoding="utf-8")  # 1 included
    (skill_dir / "a.py").write_text("a\n", encoding="utf-8")  # 2 excluded > 1 included
    (skill_dir / "b.py").write_text("b\n", encoding="utf-8")

    assert gather_skill_sources(skill_dir / "SKILL.md").scripts_dominant is True


def test_gather_not_scripts_dominant_when_markdown_majority(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n", encoding="utf-8")
    (skill_dir / "data.json").write_text("{}\n", encoding="utf-8")  # 1 excluded, 1 included

    assert gather_skill_sources(skill_dir / "SKILL.md").scripts_dominant is False


# ---------------------------------------------------------------------------
# gather_skill_sources — transitive link following, ported
# ---------------------------------------------------------------------------


def test_gather_transitive_dfs_order(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# S\n\nSee [a](references/a.md).\n", encoding="utf-8")
    (skill_dir / "references" / "a.md").write_text("# A\n\nSee [b](b.md).\n", encoding="utf-8")
    (skill_dir / "references" / "b.md").write_text("# B\n\nLeaf.\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")

    # tuple (spec §2.2)
    assert bundle.included_files == ("SKILL.md", "references/a.md", "references/b.md")
    # Each file gets exactly one marker, in DFS order.
    assert bundle.combined_text.count("<!-- skill-file:") == 3
    assert bundle.combined_text.index("SKILL.md -->") < bundle.combined_text.index("references/a.md -->")
    assert bundle.combined_text.index("references/a.md -->") < bundle.combined_text.index("references/b.md -->")


def test_gather_reference_style_links_followed(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n\nSee [the guide][g].\n\n[g]: guide.md\n", encoding="utf-8")
    (skill_dir / "guide.md").write_text("# Guide\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")
    assert bundle.included_files == ("SKILL.md", "guide.md")  # tuple (spec §2.2)


def test_gather_cycle_terminates_each_once(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n\nSee [a](a.md).\n", encoding="utf-8")
    (skill_dir / "a.md").write_text("# A\n\nBack to [b](b.md).\n", encoding="utf-8")
    (skill_dir / "b.md").write_text("# B\n\nBack to [a](a.md).\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")
    assert bundle.included_files == ("SKILL.md", "a.md", "b.md")  # tuple (spec §2.2)
    assert bundle.combined_text.count("<!-- skill-file: a.md -->") == 1
    assert bundle.combined_text.count("<!-- skill-file: b.md -->") == 1


def test_gather_directory_boundary_guard(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n\nEscape [o](../outside.md).\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")
    assert bundle.included_files == ("SKILL.md",)  # tuple (spec §2.2)
    assert "Outside" not in bundle.combined_text


def test_gather_skips_non_md_http_and_anchor_targets(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# S\n\n"
        "Script [s](helper.py).\n"
        "Web [w](https://example.com/page.md).\n"
        "Anchor [a](#section).\n"
        "Mail [m](mailto:x@y.md).\n"
        "Real [r](real.md).\n",
        encoding="utf-8",
    )
    (skill_dir / "helper.py").write_text("x\n", encoding="utf-8")
    (skill_dir / "real.md").write_text("# Real\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")
    assert bundle.included_files == ("SKILL.md", "real.md")  # tuple (spec §2.2)


def test_gather_strips_fragment_before_resolving(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n\nSee [a](a.md#heading).\n", encoding="utf-8")
    (skill_dir / "a.md").write_text("# A\n", encoding="utf-8")

    bundle = gather_skill_sources(skill_dir / "SKILL.md")
    assert bundle.included_files == ("SKILL.md", "a.md")  # tuple (spec §2.2)


# --- new coverage, not ported --------------------------------------------


def test_skill_bundle_is_frozen() -> None:
    """Spec §2.2: the rest of the rebuild returns frozen values."""
    import pytest

    bundle = SkillBundle(
        combined_text="",
        skill_dir=Path("/x"),
        anchor=Path("/x/SKILL.md"),
        title=None,
        included_files=(),
        excluded_files=(),
        scripts_dominant=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.title = "nope"  # type: ignore[misc]


def test_gather_title_ignores_unterminated_frontmatter(tmp_path: Path) -> None:
    """`_skill_title`'s `end == -1` branch: a `---` open fence with no close."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: Never Closed\n\n# Heading\n", encoding="utf-8")
    assert gather_skill_sources(skill_dir / "SKILL.md").title == "Heading"


def test_gather_title_ignores_empty_frontmatter_name(tmp_path: Path) -> None:
    """`_skill_title`'s `if value:` false branch."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname:\n---\n\n# Heading\n", encoding="utf-8")
    assert gather_skill_sources(skill_dir / "SKILL.md").title == "Heading"


def test_gather_title_skips_non_name_frontmatter_lines(tmp_path: Path) -> None:
    """`_skill_title`'s frontmatter loop: a line that isn't `name:` keeps looping."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: x\nname: Real Name\n---\n\n# Heading\n", encoding="utf-8")
    assert gather_skill_sources(skill_dir / "SKILL.md").title == "Real Name"


def test_gather_skips_a_bare_fragment_target(tmp_path: Path) -> None:
    """`resolve_companion`'s empty-after-fragment-strip guard: `[a](#)`."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n\nBare [a](#).\nMissing [m](gone.md).\n", encoding="utf-8")
    assert gather_skill_sources(skill_dir / "SKILL.md").included_files == ("SKILL.md",)
