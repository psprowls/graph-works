"""`run_code_graph_tree` and `run_code_graph_search`: scanned code-graph pages, nested and ranked."""

from __future__ import annotations

from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.code_read import run_code_graph_search, run_code_graph_tree

TODAY = date(2026, 9, 19)


def _page(layout, page_id, *, type_name="Package", title=None, resource=None, description="d"):
    path = layout.bundle_dir / f"{page_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = f"type: {type_name}\ntitle: {title or page_id.rsplit('/', 1)[-1]}\ndescription: {description}\n"
    if resource:
        fm += f"resource: {resource}\n"
    path.write_text(f"---\n{fm}---\n\nx\n", encoding="utf-8", newline="")


@pytest.fixture
def layout(tmp_path, declare_repos):
    built = apply_init(plan_init(tmp_path / "repo" / ".works", today=TODAY, topic="T")).layout
    declare_repos(built, {"demo": (tmp_path / "demo", []), "other": (tmp_path / "other", [])})
    _page(built, "code-graph/demo", type_name="Repository", title="demo")
    _page(built, "code-graph/demo/entities/packages/core", title="core", resource="pkg:acme/demo/core")
    _page(built, "code-graph/demo/file-system/src/core/app.py", type_name="File", title="app.py")
    (built.bundle_dir / "code-graph/demo/file-system/src/index.md").write_text(
        "---\ntitle: src\n---\n\n# src\n", encoding="utf-8"
    )
    _page(built, "code-graph/other/entities/packages/core", title="core-other")
    return built


def test_tree_nests_files_under_index_documents(layout) -> None:
    tree = run_code_graph_tree(layout, "demo")
    by_id = {node.id: node for node in tree.nodes}
    assert by_id["code-graph/demo/file-system/src/index"].is_index
    assert by_id["code-graph/demo/file-system/src/core/app.py"].parent == "code-graph/demo/file-system/src/index"
    assert by_id["code-graph/demo/entities/packages/core"].parent == "code-graph/demo"
    assert by_id["code-graph/demo"].parent is None
    assert not any(node.id.startswith("code-graph/other") for node in tree.nodes)
    assert [node.id for node in tree.nodes] == sorted(by_id)


def test_tree_unknown_repository(layout) -> None:
    assert run_code_graph_tree(layout, "nope").refusal == "unknown-repository"


def test_search_ranks_exact_then_prefix_then_substring(layout) -> None:
    _page(layout, "code-graph/demo/entities/packages/core-utils", title="core-utils")
    _page(layout, "code-graph/demo/entities/packages/hardcore", title="hardcore")
    hits = run_code_graph_search(layout, "CORE", repo="demo").hits
    assert [h.title for h in hits[:3]] == ["core", "core-utils", "hardcore"]
    assert [h.id for h in hits] == [
        "code-graph/demo/entities/packages/core",
        "code-graph/demo/entities/packages/core-utils",
        "code-graph/demo/entities/packages/hardcore",
        "code-graph/demo/file-system/src/core/app.py",
    ]
    assert {h.repo for h in hits} == {"demo"}


def test_search_without_repo_spans_repositories(layout) -> None:
    ids = [h.id for h in run_code_graph_search(layout, "core").hits]
    assert "code-graph/other/entities/packages/core" in ids and "code-graph/demo/entities/packages/core" in ids


def test_search_truncates(layout) -> None:
    result = run_code_graph_search(layout, "core", limit=1)
    assert len(result.hits) == 1 and result.truncated


def test_search_unknown_repository(layout) -> None:
    assert run_code_graph_search(layout, "core", repo="nope").refusal == "unknown-repository"
