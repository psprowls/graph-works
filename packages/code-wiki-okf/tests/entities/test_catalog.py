"""The two catalog renders. Pure -- a bundle in, a section body out."""

from __future__ import annotations

from pathlib import Path

from code_wiki_okf.entities.catalog import (
    CatalogEntry,
    contents_groups,
    render_contents,
    render_repositories,
    repository_entries,
)
from okf_io import load_bundle


def _bundle(tmp_path: Path, files: dict[str, str]):
    root = tmp_path / "kb"
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return load_bundle(root)


def _page(type_name: str, title: str, description: str = "") -> str:
    return (
        f'---\ntype: {type_name}\ntitle: "{title}"\nresource: "x:{title}"\n'
        f'description: "{description}"\n---\n\n## Overview\n\nprose\n'
    )


def test_a_bullet_is_a_root_absolute_markdown_link():
    entry = CatalogEntry(title="acme", concept_id="repositories/acme", description="the retail platform")
    assert render_repositories([entry]) == "- [acme](/repositories/acme.md) — the retail platform\n"


def test_a_blank_description_drops_the_dash():
    entry = CatalogEntry(title="acme", concept_id="repositories/acme", description="")
    assert render_repositories([entry]) == "- [acme](/repositories/acme.md)\n"


def test_bullets_are_sorted_case_insensitively_by_title():
    entries = [
        CatalogEntry(title="zulu", concept_id="repositories/zulu", description=""),
        CatalogEntry(title="Alpha", concept_id="repositories/alpha", description=""),
    ]
    assert render_repositories(entries).splitlines()[0] == "- [Alpha](/repositories/alpha.md)"


def test_angle_brackets_are_escaped():
    entry = CatalogEntry(title="a<b>", concept_id="repositories/a", description="uses <T>")
    assert render_repositories([entry]) == "- [a\\<b\\>](/repositories/a.md) — uses \\<T\\>\n"


def test_an_empty_catalog_renders_the_none_placeholder():
    assert render_repositories([]) == "_(none)_"
    assert render_contents({}) == "_(none)_"


def test_contents_renders_four_groups_in_a_fixed_order_omitting_empty_ones():
    groups = {
        "Packages": (CatalogEntry(title="widgets", concept_id="packages/widgets", description=""),),
        "Test suites": (CatalogEntry(title="unit", concept_id="test-suites/unit", description=""),),
    }
    assert render_contents(groups) == (
        "### Packages\n\n- [widgets](/packages/widgets.md)\n\n### Test suites\n\n- [unit](/test-suites/unit.md)\n"
    )


def test_repository_entries_reads_every_repository_page(tmp_path):
    bundle = _bundle(
        tmp_path,
        {
            "index.md": "---\nokf_version: 0.2\n---\n\n# kb\n",
            "repositories/acme.md": _page("Repository", "acme", "the retail platform"),
            "repositories/beta.md": _page("Repository", "beta"),
            "packages/widgets.md": _page("Package", "widgets"),
        },
    )
    assert [(e.title, e.concept_id, e.description) for e in repository_entries(bundle)] == [
        ("acme", "repositories/acme", "the retail platform"),
        ("beta", "repositories/beta", ""),
    ]


def test_contents_groups_buckets_by_type_and_drops_what_is_out_of_scope(tmp_path):
    bundle = _bundle(
        tmp_path,
        {
            "index.md": "---\nokf_version: 0.2\n---\n\n# kb\n",
            "repositories/acme.md": _page("Repository", "acme"),
            "packages/widgets.md": _page("Package", "widgets"),
            "apps/console.md": _page("App", "console"),
            "test-suites/unit.md": _page("TestSuite", "unit"),
            "agent-plugins/gw.md": _page("AgentPlugin", "gw"),
            "dependencies/ruamel.md": _page("Dependency", "ruamel"),
            "repositories/acme/src/a.py.md": _page("File", "src/a.py"),
            "packages/broken.md": "---\nnot: [closed\n",
        },
    )
    groups = contents_groups(
        bundle,
        [
            "repositories/acme",
            "packages/widgets",
            "apps/console",
            "test-suites/unit",
            "agent-plugins/gw",
            "dependencies/ruamel",
            "repositories/acme/src/a.py",
            "packages/broken",
            "packages/absent",
        ],
    )
    assert {group: tuple(e.concept_id for e in entries) for group, entries in groups.items()} == {
        "Apps": ("apps/console",),
        "Packages": ("packages/widgets",),
        "Agent plugins": ("agent-plugins/gw",),
        "Test suites": ("test-suites/unit",),
    }


def test_no_wikilink_is_ever_constructed():
    """`okf_io.LinkGraph` cannot see a wikilink, so a catalog written in them
    would be invisible to backlinks, broken-link validation and traversal."""
    source = (Path(__file__).resolve().parents[2] / "src" / "code_wiki_okf" / "entities" / "catalog.py").read_text(
        encoding="utf-8"
    )
    assert "[[" not in source
