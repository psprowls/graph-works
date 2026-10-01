"""`prune=`: directories the walk never lists (feature-okf-io-prune-ignored-directories)."""

from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from helpers import BUNDLES, write_tree
from okf_io import bundle, links
from okf_io.validate import validate

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# Definition\n"


def _big_tree(root: Path) -> None:
    write_tree(
        root,
        {
            "top.md": CONCEPT,
            "big/index.md": "# Big\n",
            "big/log.md": "# Log\n",
            "big/concept.md": CONCEPT,
            "big/asset.bin": "x",
            "big/café.md": CONCEPT,
            "big/nested/deep.md": CONCEPT,
            "big/nested/deeper/leaf.txt": "x",
        },
    )


def _beneath(listed: list[str], directory: str) -> list[str]:
    return [path for path in listed if path == directory or path.startswith(f"{directory}/")]


def test_load_never_lists_inside_a_pruned_directory(tmp_path, monkeypatch):
    _big_tree(tmp_path)
    listed: list[str] = []
    real_iterdir = Path.iterdir

    def recording_iterdir(self: Path):
        listed.append(self.relative_to(tmp_path).as_posix())
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", recording_iterdir)
    loaded = bundle.load(tmp_path, prune=["big"])

    assert _beneath(listed, "big") == []
    assert listed == ["."]  # the root was listed, through the real iterdir
    assert loaded.pruned == frozenset({"big"})
    assert set(loaded.concepts) == {"top"}
    assert dict(loaded.indexes) == {}
    assert dict(loaded.logs) == {}
    assert loaded.assets == frozenset()
    assert loaded.ignored == frozenset()
    assert dict(loaded.unreadable) == {}
    assert dict(loaded._canonical) == {}
    assert dict(loaded.canonical_collisions) == {}


def test_a_glob_prunes_every_matching_directory_and_is_anchored_at_the_root(tmp_path):
    write_tree(
        tmp_path,
        {
            "repos/a/references/git/README.md": CONCEPT,
            "repos/a/references/notes.md": CONCEPT,
            "repos/b/references/git/src/x.py": "x",
            "repos/c/references/other/y.md": CONCEPT,
            "deep/repos/d/references/git/z.md": CONCEPT,
        },
    )

    anchored = bundle.load(tmp_path, prune=["repos/*/references/git"])
    assert anchored.pruned == frozenset({"repos/a/references/git", "repos/b/references/git"})
    assert set(anchored.concepts) == {
        "repos/a/references/notes",
        "repos/c/references/other/y",
        "deep/repos/d/references/git/z",
    }

    anywhere = bundle.load(tmp_path, prune=["*/references/git"])
    assert anywhere.pruned == frozenset(
        {"repos/a/references/git", "repos/b/references/git", "deep/repos/d/references/git"}
    )
    assert set(anywhere.concepts) == {"repos/a/references/notes", "repos/c/references/other/y"}


def test_the_bundle_root_is_never_pruned(tmp_path):
    write_tree(tmp_path, {"top.md": CONCEPT})
    for patterns in ([""], ["*"], ["."]):
        loaded = bundle.load(tmp_path, prune=patterns)
        assert loaded.pruned == frozenset()
        assert set(loaded.concepts) == {"top"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlink permissions")
def test_excluded_directories_never_appear_in_pruned(tmp_path):
    write_tree(
        tmp_path,
        {".hidden/a.md": CONCEPT, "x/.git/HEAD": "ref\n", "x/kept.md": CONCEPT, "real/a.md": CONCEPT},
    )
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    loaded = bundle.load(tmp_path, prune=[".hidden", "x/.git", "link"])

    assert loaded.pruned == frozenset()
    assert set(loaded.concepts) == {"x/kept", "real/a"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_an_unreadable_directory_beneath_a_pruned_root_is_never_visited(tmp_path):
    write_tree(tmp_path, {"top.md": CONCEPT, "big/denied/inside.md": CONCEPT})
    denied = tmp_path / "big" / "denied"
    denied.chmod(0o000)
    try:
        loaded = bundle.load(tmp_path, prune=["big"])
    finally:
        denied.chmod(0o755)

    assert dict(loaded.unreadable) == {}
    assert loaded.pruned == frozenset({"big"})
    assert set(loaded.concepts) == {"top"}


def test_prune_and_ignore_are_independent(tmp_path):
    write_tree(tmp_path, {"schema/kinds.md": CONCEPT, "big/a.md": CONCEPT})

    both = bundle.load(tmp_path, ignore=["schema/*", "big/*"], prune=["big"])
    assert both.ignored == frozenset({"schema/kinds.md"})
    assert both.pruned == frozenset({"big"})

    ignore_only = bundle.load(tmp_path, ignore=["big/*"])
    assert ignore_only.pruned == frozenset()
    assert ignore_only.ignored == frozenset({"big/a.md"})


def test_no_prune_is_todays_bundle():
    root = BUNDLES / "acme_retail"
    default = bundle.load(root)
    unmatched = bundle.load(root, prune=["no-such-directory"])

    assert default.pruned == frozenset()
    assert unmatched.pruned == frozenset()
    assert unmatched.assets == default.assets
    assert unmatched.ignored == default.ignored
    assert dict(unmatched.unreadable) == dict(default.unreadable)
    assert tuple(unmatched.concepts) == tuple(default.concepts)
    assert tuple(unmatched.indexes) == tuple(default.indexes)
    assert tuple(unmatched.logs) == tuple(default.logs)
    assert dict(unmatched._canonical) == dict(default._canonical)


def test_a_subclass_may_still_add_a_required_field():
    """`pruned` is keyword-only so `work_tracker_okf.mutation._AliasedBundle`
    (a `Bundle` subclass with a required `aliases` field) still defines."""

    @dataclass(frozen=True, slots=True)
    class Subclass(bundle.Bundle):
        extra: str

    assert "extra" in Subclass.__dataclass_fields__


_NEEDS_O_DIRECTORY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="opens a directory descriptor with os.O_DIRECTORY, which does not exist on Windows",
)


@_NEEDS_O_DIRECTORY
def test_descriptor_rooted_load_never_opens_inside_a_pruned_directory(tmp_path, monkeypatch):
    """Wraps `_open_relative_directory` -- the one place `_files_at` turns a
    relative directory into a descriptor it then `os.listdir`s -- so the
    recorded argument is the bundle-relative path being listed."""
    _big_tree(tmp_path)
    opened: list[str] = []
    real_open = bundle._open_relative_directory

    def recording_open(root_fd: int, relative: str) -> int:
        opened.append(relative)
        return real_open(root_fd, relative)

    monkeypatch.setattr(bundle, "_open_relative_directory", recording_open)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        loaded = bundle._load_at(tmp_path, descriptor, prune=["big"])
    finally:
        os.close(descriptor)

    assert _beneath(opened, "big") == []
    assert loaded.pruned == frozenset({"big"})
    assert set(loaded.concepts) == {"top"}


GIT_ROOT = "repos/a/references/git"
TODAY = date(2026, 9, 29)


def _pruned_bundle(root: Path) -> bundle.Bundle:
    write_tree(
        root,
        {
            "top.md": CONCEPT,
            f"{GIT_ROOT}/README.md": "# Readme\n",
            f"{GIT_ROOT}/src/main.py": "x",
            f"{GIT_ROOT}/.git/HEAD": "ref\n",
        },
    )
    return bundle.load(root, prune=["repos/*/references/git"])


def test_member_id_resolves_a_real_file_beneath_a_pruned_root(tmp_path):
    loaded = _pruned_bundle(tmp_path)
    assert loaded.member_id(f"{GIT_ROOT}/README.md") == f"{GIT_ROOT}/README.md"
    assert loaded.member_id(f"  {GIT_ROOT}/src/main.py ") == f"{GIT_ROOT}/src/main.py"
    assert loaded.has_member(f"{GIT_ROOT}/src/main.py")
    assert loaded.member_id("top.md") == "top.md"  # the exact-match fast path is untouched


@pytest.mark.parametrize(
    "query",
    [
        f"{GIT_ROOT}/missing.md",  # absent file
        f"{GIT_ROOT}/src",  # a directory is not a member
        GIT_ROOT,  # the pruned root itself
        f"{GIT_ROOT}/",  # empty trailing component
        f"{GIT_ROOT}/src//main.py",  # empty middle component
        f"{GIT_ROOT}/./README.md",
        f"{GIT_ROOT}/src/../README.md",
        f"{GIT_ROOT}/../git/README.md",
        f"{GIT_ROOT}/.git/HEAD",  # mirrors the walk's .git exclusion
        f"{GIT_ROOT}/README.md/child",  # an intermediate that is a file
        "repos/b/references/git/README.md",  # beneath no pruned root
        "repos/a/references/gitx/README.md",  # a sibling sharing the prefix string
    ],
)
def test_member_id_refuses_everything_else_beneath_or_beside_a_pruned_root(tmp_path, query):
    loaded = _pruned_bundle(tmp_path)
    assert loaded.member_id(query) is None
    assert not loaded.has_member(query)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink permissions")
def test_the_probe_follows_file_symlinks_but_not_directory_symlinks(tmp_path):
    loaded = _pruned_bundle(tmp_path)
    git = tmp_path / GIT_ROOT
    (git / "linked-dir").symlink_to(git / "src", target_is_directory=True)
    (git / "linked-file.md").symlink_to(git / "README.md")
    (git / "dir-link.md").symlink_to(git / "src", target_is_directory=True)
    (git / "dangling.md").symlink_to(git / "nowhere.md")

    assert loaded.member_id(f"{GIT_ROOT}/linked-dir/main.py") is None
    assert loaded.member_id(f"{GIT_ROOT}/linked-file.md") == f"{GIT_ROOT}/linked-file.md"
    assert loaded.member_id(f"{GIT_ROOT}/dir-link.md") is None
    assert loaded.member_id(f"{GIT_ROOT}/dangling.md") is None


def test_the_probe_matches_a_non_ascii_pruned_root_in_either_normalization_form(tmp_path):
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    write_tree(tmp_path, {f"repos/{nfd}/references/git/README.md": "# Readme\n"})
    loaded = bundle.load(tmp_path, prune=["repos/*/references/git"])
    (raw_root,) = loaded.pruned  # whatever spelling this host's readdir returned

    for spelling in (nfc, nfd):
        assert loaded.member_id(f"repos/{spelling}/references/git/README.md") == f"{raw_root}/README.md"


def test_the_probe_is_never_consulted_without_pruned_roots(tmp_path, monkeypatch):
    write_tree(tmp_path, {"top.md": CONCEPT})
    loaded = bundle.load(tmp_path)

    def exploding(self, member):
        raise AssertionError("probe consulted with no pruned roots")

    monkeypatch.setattr(bundle.Bundle, "_pruned_member", exploding)
    assert loaded.member_id("missing.md") is None
    assert loaded.member_id("café-missing.md") is None


def test_links_beneath_a_pruned_root_resolve_or_warn_broken(tmp_path):
    write_tree(
        tmp_path,
        {
            "a.md": CONCEPT + f"[r](/{GIT_ROOT}/README.md)\n\n[m](/{GIT_ROOT}/missing.md)\n",
            f"{GIT_ROOT}/README.md": "# Readme\n",
        },
    )
    loaded = bundle.load(tmp_path, prune=["repos/*/references/git"])

    graph = links.build(loaded)
    assert [link.raw for link in graph.broken] == [f"/{GIT_ROOT}/missing.md"]

    report = validate(loaded, today=TODAY)
    findings = report.by_code("links.broken")
    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "missing.md" in findings[0].message
    assert report.ok is True
