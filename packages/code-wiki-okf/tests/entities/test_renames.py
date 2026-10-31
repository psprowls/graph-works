from __future__ import annotations

from pathlib import Path

from code_wiki_okf.entities.renames import pair_entity_renames
from code_wiki_okf.entities.sync import EntityWrite
from code_wiki_okf.mirror.model import MirrorPlan
from code_wiki_okf.placement import canonical_member, context_from_resource
from okf_ext.moves import MovePlan
from okf_io import load_bundle


def _page(root: Path, type_name: str, name: str, *, path: str | None, repo: str = "demo") -> str:
    context = context_from_resource(type_name, f"pkg:acme/{repo}/{name}")
    member = canonical_member(context)
    target = root / member
    target.parent.mkdir(parents=True, exist_ok=True)
    path_line = f"path: {path}\n" if path is not None else ""
    target.write_text(
        f"---\ntype: {type_name}\nresource: {context.resource}\ntitle: {name}\n{path_line}---\n\n## Purpose\n\nx\n",
        encoding="utf-8",
        newline="",
    )
    return member


def _write(type_name: str, name: str, path: str, *, repo: str = "demo") -> EntityWrite:
    prefix = "app" if type_name == "App" else "pkg"
    context = context_from_resource(type_name, f"{prefix}:acme/{repo}/{name}")
    return EntityWrite(
        context=context,
        member=canonical_member(context),
        frontmatter={"type": type_name, "title": name, "resource": context.resource, "path": path},
    )


def _mirror(root: Path, renames: dict[str, str], repo: str = "demo") -> MirrorPlan:
    return MirrorPlan(
        repo=repo,
        targets=(),
        moves=MovePlan(root=root, moves=(), edits=(), refusals=(), unrebased=(), digests={}),
        creates={},
        updates={},
        deletions=(),
        declined_deletions=(),
        renames=renames,
    )


def test_entity_without_path_key_is_not_paired(tmp_path: Path) -> None:
    _page(tmp_path, "Package", "old", path=None)
    pairs, warnings = pair_entity_renames(
        load_bundle(tmp_path),
        [_write("Package", "new", "packages/new")],
        [_mirror(tmp_path, {"packages/old/a.py": "packages/new/a.py"})],
        {"pkg:acme/demo/new"},
    )
    assert (dict(pairs), warnings) == ({}, ())


def test_pairs_one_renamed_package(tmp_path: Path) -> None:
    old = _page(tmp_path, "Package", "old", path="packages/old")
    write = _write("Package", "new", "packages/new")
    pairs, warnings = pair_entity_renames(
        load_bundle(tmp_path),
        [write],
        [_mirror(tmp_path, {"packages/old/a.py": "packages/new/a.py"})],
        {"pkg:acme/demo/new"},
    )
    assert dict(pairs) == {old: write.member}
    assert warnings == ()


def test_ambiguous_entity_rename_warns_and_falls_back(tmp_path: Path) -> None:
    _page(tmp_path, "Package", "a", path="packages/a")
    _page(tmp_path, "Package", "b", path="packages/b")
    pairs, warnings = pair_entity_renames(
        load_bundle(tmp_path),
        [_write("Package", "c", "packages/c")],
        [_mirror(tmp_path, {"packages/a/x.py": "packages/c/x.py", "packages/b/y.py": "packages/c/y.py"})],
        {"pkg:acme/demo/c"},
    )
    assert dict(pairs) == {}
    assert warnings
    assert all("entity rename ambiguous" in warning for warning in warnings)


def test_one_old_page_with_two_candidates_warns(tmp_path: Path) -> None:
    _page(tmp_path, "Package", "a", path="packages/a")
    pairs, warnings = pair_entity_renames(
        load_bundle(tmp_path),
        [_write("Package", "b", "packages/b"), _write("Package", "c", "packages/c")],
        [_mirror(tmp_path, {"packages/a/x.py": "packages/b/x.py", "packages/a/y.py": "packages/c/y.py"})],
        {"pkg:acme/demo/b", "pkg:acme/demo/c"},
    )
    assert dict(pairs) == {}
    assert len(warnings) == 1 and "entity rename ambiguous" in warnings[0]


def test_different_type_or_repo_is_not_paired(tmp_path: Path) -> None:
    _page(tmp_path, "Package", "old", path="packages/old")
    renames = {"packages/old/a.py": "packages/new/a.py"}
    by_type, _ = pair_entity_renames(
        load_bundle(tmp_path),
        [_write("App", "new", "packages/new")],
        [_mirror(tmp_path, renames)],
        {"app:acme/demo/new"},
    )
    by_repo, _ = pair_entity_renames(
        load_bundle(tmp_path),
        [_write("Package", "new", "packages/new", repo="other")],
        [_mirror(tmp_path, renames)],
        {"pkg:acme/other/new"},
    )
    assert dict(by_type) == {} and dict(by_repo) == {}


def test_no_renames_pairs_nothing(tmp_path: Path) -> None:
    _page(tmp_path, "Package", "old", path="packages/old")
    pairs, warnings = pair_entity_renames(load_bundle(tmp_path), [_write("Package", "new", "packages/new")], [], set())
    assert (dict(pairs), warnings) == ({}, ())
