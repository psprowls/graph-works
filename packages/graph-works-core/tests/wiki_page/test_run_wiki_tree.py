"""`run_wiki_tree`: the root index's `##`/`###` sections and the pages under each."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.wiki_page.commands import TreePage, run_wiki_tree
from graph_works_core.workspace.errors import WorkspaceError

INDEX = """---
okf_version: 0.2
---

# Index — test

## Concepts

### Architecture

- [Layering](/concepts/layering.md) — how the bands stack
- [Untitled](concepts/untitled.md)

## Packages

- [pkg-a](/repositories/r/packages/pkg-a.md)
- [a directory](/work/index.md)
- [a folder](work/)
- [an asset](/scripts/tool.py)
- [external](https://example.com)
- [gone](/concepts/gone.md)

### Extra

- [pkg-b](/repositories/r/packages/pkg-b.md)

# Subdirectories

- [AGENTS.md](AGENTS.md)
"""


def _page(layout, page_id: str, fm: str) -> None:
    path = layout.bundle_dir / f"{page_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}---\n\n# x\n", encoding="utf-8")


@pytest.fixture
def layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=date(2026, 9, 19), topic="T")).layout
    (layout.bundle_dir / "index.md").write_text(INDEX, encoding="utf-8")
    _page(layout, "concepts/layering", "type: Explanation\ntitle: Layering bands\ndescription: d\n")
    _page(layout, "concepts/untitled", "description: d\n")
    _page(layout, "repositories/r/packages/pkg-a", "type: Package\ntitle: pkg-a\ndescription: d\n")
    _page(layout, "repositories/r/packages/pkg-b", "type: Package\ntitle: pkg-b\ndescription: d\n")
    (layout.bundle_dir / "work").mkdir(exist_ok=True)
    (layout.bundle_dir / "work" / "index.md").write_text("# Work\n", encoding="utf-8")
    (layout.bundle_dir / "scripts").mkdir()
    (layout.bundle_dir / "scripts" / "tool.py").write_text("x = 1\n", encoding="utf-8")
    return layout


def test_sections_nest_subsections_and_stop_at_the_next_title(layout) -> None:
    tree = run_wiki_tree(layout)

    assert [(node.heading, node.level) for node in tree.sections] == [("Concepts", 2), ("Packages", 2)]
    concepts, packages = tree.sections
    assert [(child.heading, child.level) for child in concepts.children] == [("Architecture", 3)]
    assert [(child.heading, child.level) for child in packages.children] == [("Extra", 3)]
    assert concepts.pages == ()


def test_pages_take_title_and_type_with_fallbacks(layout) -> None:
    architecture = run_wiki_tree(layout).sections[0].children[0]

    assert architecture.pages == (
        TreePage("concepts/layering", "Layering bands", "Explanation"),
        TreePage("concepts/untitled", "Untitled", None),
    )


def test_only_links_to_bundle_pages_survive(layout) -> None:
    packages = run_wiki_tree(layout).sections[1]

    assert [page.id for page in packages.pages] == ["repositories/r/packages/pkg-a"]


def test_generated_comes_from_root_index_declarations_and_is_inherited(layout) -> None:
    concepts, packages = run_wiki_tree(layout).sections

    assert concepts.generated is False
    assert concepts.children[0].generated is False
    assert packages.generated is True
    assert packages.children[0].generated is True


def test_no_root_index_is_an_empty_tree(layout) -> None:
    (layout.bundle_dir / "index.md").unlink()

    assert run_wiki_tree(layout).sections == ()


def test_a_broken_section_declaration_is_a_workspace_error(layout) -> None:
    (layout.config_dir / "sections" / "_broken.yaml").write_text("directories: [not, a, mapping]\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match=r"_broken\.yaml"):
        run_wiki_tree(layout)
