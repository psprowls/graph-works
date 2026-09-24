"""The two catalog renders. Pure -- a bundle in, a section body out."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from code_wiki_okf.entities.catalog import (
    CatalogEntry,
    catalog_pages,
    reconcile_catalogs,
    render_contents,
)
from code_wiki_okf.entities.delete import prune_entities
from code_wiki_okf.init import install_bundle
from okf_io import load_bundle


def test_an_empty_catalog_renders_the_none_placeholder():
    assert render_contents({}) == "_(none)_"


def test_contents_renders_five_groups_in_a_fixed_order_omitting_empty_ones():
    groups = {
        "Packages": (CatalogEntry(title="widgets", concept_id="packages/widgets", description=""),),
        "Test Suites": (CatalogEntry(title="unit", concept_id="test-suites/unit", description=""),),
        "Dependencies": (CatalogEntry(title="httpx", concept_id="dependencies/httpx", description=""),),
    }
    assert render_contents(groups) == (
        "### Packages\n\n- [widgets](/packages/widgets.md)\n\n### Test Suites\n\n- [unit](/test-suites/unit.md)\n"
        "\n### Dependencies\n\n- [httpx](/dependencies/httpx.md)\n"
    )


def test_bullets_within_a_group_sort_case_insensitively_by_title():
    """`_bullets`' sort key is `(title.casefold(), concept_id)`, not raw title.

    Plain ASCII string comparison sorts every uppercase letter before every
    lowercase one, so `"Zeta"` sorts before `"apple"` by raw title even
    though `"apple"` should come first once case is ignored. Titles whose
    casefold order happens to agree with their ASCII order (e.g. `"Alpha"`,
    `"beta"`) would pass this assertion even with `.casefold()` removed from
    the sort key, proving nothing. `"Zeta"` vs. `"apple"` is chosen because
    the two orderings genuinely disagree. This is `_bullets`' own
    responsibility, not `render_contents`', but `_bullets` is private and
    `render_contents` is the surviving public entry point that reaches it.
    """
    groups = {
        "Packages": (
            CatalogEntry(title="Zeta", concept_id="packages/zeta", description=""),
            CatalogEntry(title="apple", concept_id="packages/apple", description=""),
        ),
    }
    rendered = render_contents(groups)
    titles_in_order = [line.split("[", 1)[1].split("]", 1)[0] for line in rendered.splitlines() if line.startswith("-")]
    assert titles_in_order == ["apple", "Zeta"]


def test_bullet_escapes_angle_brackets_in_both_title_and_description():
    """`_bullet` runs `escape_angle_brackets` over the title and the description.

    `escape_angle_brackets` itself is unit-tested in `okf-ext`, but nothing
    else in this package asserts that `_bullet` actually calls it on both
    fields -- a caller that forgot to escape one of them would otherwise
    slip an unescaped `<`/`>` into rendered markdown undetected.
    """
    groups = {
        "Packages": (CatalogEntry(title="a<b>", concept_id="packages/a", description="uses <T>"),),
    }
    assert render_contents(groups) == "### Packages\n\n- [a\\<b\\>](/packages/a.md) — uses \\<T\\>\n"


def test_no_wikilink_is_ever_constructed():
    """`okf_io.LinkGraph` cannot see a wikilink, so a catalog written in them
    would be invisible to backlinks, broken-link validation and traversal."""
    source = (Path(__file__).resolve().parents[2] / "src" / "code_wiki_okf" / "entities" / "catalog.py").read_text(
        encoding="utf-8"
    )
    assert "[[" not in source


def _canonical_page(type_name: str, title: str, resource: str, *, generated: bool = False, prose: str = "") -> str:
    generated_block = "generated:\n  by: code-wiki-okf/0.4.0\n  at: 2026-01-01T00:00:00Z\n" if generated else ""
    body = f"\n## Purpose\n\n{prose}\n" if prose else ""
    return (
        f'---\ntype: {type_name}\ntitle: "{title}"\nresource: "{resource}"\n'
        f'description: "{title} description"\n{generated_block}---\n{body}'
    )


def _write(root: Path, member: str, text: str) -> None:
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reconcile_catalogs_creates_every_invariant_catalog_from_actual_disk(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=date(2026, 1, 1), dry_run=False)
    _write(
        root,
        "code-graph/one.md",
        # A Contents heading is the anchor the catalog splices into.
        _canonical_page("Repository", "one", "repo:acme/one") + "\n## Contents\n\n_(none)_\n",
    )
    _write(
        root,
        "code-graph/two.md",
        _canonical_page("Repository", "two", "repo:acme/two"),
    )
    _write(
        root,
        "code-graph/one/entities/packages/widgets.md",
        _canonical_page("Package", "widgets", "pkg:acme/one/widgets"),
    )
    _write(
        root,
        "code-graph/two/entities/packages/retained.md",
        _canonical_page("Package", "retained", "pkg:acme/two/retained"),
    )
    _write(
        root,
        "code-graph/one/file-system/src/main.py.md",
        _canonical_page("File", "main.py", "file:acme/one/src/main.py"),
    )
    _write(
        root,
        "code-graph/one/entities/dependencies/pypi/httpx.md",
        _canonical_page("Dependency", "httpx", "dependency:acme/one/pypi/httpx"),
    )
    _write(
        root,
        "code-graph/one/entities/dependencies/npm/react.md",
        _canonical_page("Dependency", "react", "dependency:acme/one/npm/react"),
    )
    root_index = root / "index.md"
    root_index.write_text(root_index.read_text(encoding="utf-8") + "\nA human introduction.\n", encoding="utf-8")

    result = reconcile_catalogs(load_bundle(root), today=date(2026, 1, 1))

    assert result.ok
    required = {
        "code-graph/index.md",
        "code-graph/one/index.md",
        "code-graph/one/entities/index.md",
        "code-graph/one/entities/packages/index.md",
        "code-graph/one/entities/apps/index.md",
        "code-graph/one/entities/agent-plugins/index.md",
        "code-graph/one/entities/test-suites/index.md",
        "code-graph/one/entities/dependencies/index.md",
        "code-graph/one/entities/dependencies/npm/index.md",
        "code-graph/one/entities/dependencies/pypi/index.md",
        "code-graph/one/file-system/index.md",
        "code-graph/one/file-system/src/index.md",
        "code-graph/two/index.md",
        "code-graph/two/entities/index.md",
        "code-graph/two/entities/packages/index.md",
        "code-graph/two/entities/apps/index.md",
        "code-graph/two/entities/agent-plugins/index.md",
        "code-graph/two/entities/test-suites/index.md",
        "code-graph/two/file-system/index.md",
    }
    assert required == {path.relative_to(root).as_posix() for path in (root / "code-graph").rglob("index.md")}
    for stale in ("repositories", "dependencies", "files", "packages", "apps", "agent-plugins", "test-suites"):
        assert not (root / stale).exists()

    root_text = root_index.read_text(encoding="utf-8")
    assert "A human introduction." in root_text
    assert "## Repositories" in root_text
    for heading in ("Packages", "Apps", "Agent Plugins", "Test Suites", "Dependencies"):
        assert f"## {heading}" not in root_text
    assert "/code-graph/one.md" in root_text
    assert "/code-graph/one/entities/packages/widgets.md" not in root_text

    def read(member: str) -> str:
        return (root / member).read_text(encoding="utf-8")

    assert "/code-graph/one.md" in read("code-graph/index.md")
    repo_stub = read("code-graph/one/index.md")
    assert "## Repository" in repo_stub
    assert "/code-graph/one.md" in repo_stub
    assert "/code-graph/one/entities/packages/widgets.md" not in repo_stub
    entities = read("code-graph/one/entities/index.md")
    for heading in ("Packages", "Apps", "Agent Plugins", "Test Suites", "Dependencies"):
        assert f"## {heading}" in entities
    assert "/code-graph/one/entities/packages/widgets.md" in entities
    assert "/code-graph/one/entities/dependencies/npm/index.md" in entities
    assert "/code-graph/one/entities/dependencies/npm/index.md" in read("code-graph/one/entities/dependencies/index.md")
    assert "/code-graph/one/entities/dependencies/npm/react.md" in read(
        "code-graph/one/entities/dependencies/npm/index.md"
    )
    assert "/code-graph/one/file-system/src/index.md" in read("code-graph/one/file-system/index.md")
    assert "/code-graph/one/file-system/src/main.py.md" in read("code-graph/one/file-system/src/index.md")
    contents = read("code-graph/one.md")
    assert "### Dependencies" in contents
    assert "/code-graph/one/entities/dependencies/npm/react.md" in contents

    actual = load_bundle(root)
    assert actual.by_type("Package") == (
        "code-graph/one/entities/packages/widgets",
        "code-graph/two/entities/packages/retained",
    )


def test_old_shape_dependency_page_is_skipped_not_crashed(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=date(2026, 1, 1), dry_run=False)
    _write(root, "dependencies/pypi/httpx.md", _canonical_page("Dependency", "httpx", "dependency:pypi/httpx"))

    _write(
        root,
        "repositories/one/packages/widgets.md",
        _canonical_page("Package", "widgets", "pkg:acme/one/widgets"),
    )

    assert catalog_pages(load_bundle(root)) == ()


def test_catalog_pages_excludes_a_page_that_is_not_at_its_canonical_placement(tmp_path: Path) -> None:
    """Global catalogs link only to canonical pages: `catalog_pages` recomputes
    each page's expected concept id from its own `resource` and drops any page
    whose actual concept id disagrees. A page filed at the wrong path for its
    resource must never surface as a catalog member, even though the page
    itself parses cleanly.
    """
    root = tmp_path / "bundle"
    install_bundle(root, today=date(2026, 1, 1), dry_run=False)
    _write(
        root,
        "code-graph/one.md",
        _canonical_page("Repository", "one", "repo:acme/one"),
    )
    _write(
        root,
        "code-graph/one/entities/packages/widgets.md",
        _canonical_page("Package", "widgets", "pkg:acme/one/widgets"),
    )
    _write(
        root,
        "code-graph/one/entities/packages/misplaced.md",
        _canonical_page("Package", "widgets", "pkg:acme/one/widgets"),
    )

    pages = catalog_pages(load_bundle(root))

    concept_ids = {page.entry.concept_id for page in pages}
    assert "code-graph/one/entities/packages/widgets" in concept_ids
    assert "code-graph/one/entities/packages/misplaced" not in concept_ids


def test_catalogs_follow_guarded_post_prune_disk_and_are_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=date(2026, 1, 1), dry_run=False)
    _write(
        root,
        "code-graph/one.md",
        _canonical_page("Repository", "one", "repo:acme/one"),
    )
    _write(
        root,
        "code-graph/one/entities/packages/retained.md",
        _canonical_page(
            "Package",
            "retained",
            "pkg:acme/one/retained",
            generated=True,
            prose="A human explanation.",
        ),
    )
    _write(
        root,
        "code-graph/one/entities/packages/removed.md",
        _canonical_page("Package", "removed", "pkg:acme/one/removed", generated=True),
    )
    reconcile_catalogs(load_bundle(root), today=date(2026, 1, 1))

    pruned = prune_entities(load_bundle(root), frozenset({"repo:acme/one"}))
    assert pruned.deleted == ("code-graph/one/entities/packages/removed",)
    assert pruned.declined == (("code-graph/one/entities/packages/retained", "prose-edited"),)
    first = reconcile_catalogs(load_bundle(root), today=date(2026, 1, 1))
    after_first = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    second = reconcile_catalogs(load_bundle(root), today=date(2026, 1, 1))

    packages = (root / "code-graph/one/entities/packages/index.md").read_text(encoding="utf-8")
    assert "/code-graph/one/entities/packages/retained.md" in packages
    assert "/code-graph/one/entities/packages/removed.md" not in packages
    assert first.ok and second.ok
    assert second.written == ()
    assert {
        path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == after_first
