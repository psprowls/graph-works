"""Folder cleanup preserves everything whose generated ownership is uncertain."""

from pathlib import Path

import pytest
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.entities.indexes import apply_index_prune, plan_index_prune
from okf_io import load_bundle

ROOT = "code-graph/demo/file-system"
STUB = b"# File\n\n## Files\n\n_(none)_\n\n## Directories\n\n_(none)_\n"


def seed(root: Path, member: str, content: bytes = STUB) -> Path:
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_nested_generated_indexes_delete_children_before_parents(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "bundle"
    seed(root, f"{ROOT}/index.md")
    parent = seed(
        root,
        f"{ROOT}/gone/index.md",
        b"## Directories\n\n- [child](/code-graph/demo/file-system/gone/child/index.md)\n",
    )
    child = seed(root, f"{ROOT}/gone/child/index.md")
    seed(root, "code-graph/demo/entities/packages/index.md")
    seed(root, "code-graph/other/file-system/gone/index.md")
    plan = plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),))
    assert plan.result.deleted == (f"{ROOT}/gone/child/index.md", f"{ROOT}/gone/index.md")
    assert parent.exists() and child.exists()
    assert apply_index_prune(root, plan) == plan.result
    assert not parent.exists() and not child.exists()
    assert (root / f"{ROOT}/index.md").exists()
    assert (root / "code-graph/demo/entities/packages/index.md").exists()
    assert (root / "code-graph/other/file-system/gone/index.md").exists()


@pytest.mark.parametrize(
    "content",
    [
        b"---\n---\n" + STUB,
        STUB + b"\nHuman notes.\n",
        STUB + b"\n<!-- keep -->\n",
        STUB + b"\n## Custom\n",
        b"## Files\n\n- [old](old.py.md) - important authored note\n",
        b"```\n## Files\n```\n",
        b"## Files\n\n- [Runbook](https://example.com/operations.md)\n",
        b"## Files\n\n- [Runbook](/docs/operations.md)\n",
        b"## Files\n\n- [Runbook](../../../operations.md)\n",
        b"## Files\n\n- [<!-- note -->](old.py.md)\n",
        b"- [old](old.py.md)\n\n## Files\n",
        b"## Files\n\n- [child](child/index.md)\n",
        b"## Directories\n\n- [old](old.py.md)\n",
        b"File\n====\n",
    ],
)
def test_unknown_index_content_protects_ancestors(tmp_path: Path, content: bytes) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "bundle"
    parent = seed(root, f"{ROOT}/gone/index.md")
    child = seed(root, f"{ROOT}/gone/child/index.md", content)
    plan = plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),))
    assert plan.result.deleted == ()
    assert (f"{ROOT}/gone/child/index.md", "unrecognized-content") in plan.result.declined
    apply_index_prune(root, plan)
    assert child.read_bytes() == content and parent.exists()


@pytest.mark.parametrize("member", ["note.md", "image.png", "log.md"])
def test_retained_non_index_members_protect_ancestors(tmp_path: Path, member: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "bundle"
    seed(root, f"{ROOT}/gone/index.md")
    seed(root, f"{ROOT}/gone/child/{member}", b"authored content")
    plan = plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),))
    assert plan.result.deleted == ()


def test_existing_source_directory_and_unavailable_repository_are_preserved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "gone").mkdir(parents=True)
    root = tmp_path / "bundle"
    path = seed(root, f"{ROOT}/gone/index.md")
    assert not plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),)).result.deleted
    (repo / "gone").rmdir()
    repo.rmdir()
    assert not plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),)).result.deleted
    assert path.exists()


def test_intervening_edit_is_declined_and_protects_parent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "bundle"
    parent = seed(root, f"{ROOT}/gone/index.md")
    child = seed(root, f"{ROOT}/gone/child/index.md")
    plan = plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),))
    child.write_bytes(STUB + b"\nNew prose\n")
    result = apply_index_prune(root, plan)
    assert result.deleted == ()
    assert (f"{ROOT}/gone/child/index.md", "changed-since-plan") in result.declined
    assert child.exists() and parent.exists()


def test_candidate_disappearing_after_plan_does_not_remove_ancestor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "bundle"
    parent = seed(root, f"{ROOT}/gone/index.md")
    child = seed(root, f"{ROOT}/gone/child/index.md")
    plan = plan_index_prune(load_bundle(root), repos=(RepoConfig("demo", repo, ()),))
    child.unlink()
    result = apply_index_prune(root, plan)
    assert result.deleted == ()
    assert (f"{ROOT}/gone/child/index.md", "unavailable-since-plan") in result.declined
    assert parent.exists()
