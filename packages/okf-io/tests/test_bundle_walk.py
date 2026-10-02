from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from helpers import BUNDLES, EDGE, write
from okf_io import Bundle, load_bundle
from okf_io.bundle import Member, MemberStat, walk

FIXTURE_ROOTS = [BUNDLES / "acme_retail", BUNDLES / "ga4", EDGE, EDGE.parent / "nonconformant"]


def _loaded_ids(bundle: Bundle) -> set[str]:
    ids = {f"{cid}.md" for cid in bundle.concepts}
    ids |= {f"{d}/index.md" if d else "index.md" for d in bundle.indexes}
    ids |= {f"{d}/log.md" if d else "log.md" for d in bundle.logs}
    return (
        ids
        | set(bundle.assets)
        | set(bundle.ignored)
        | {k for k in bundle.unreadable if not (bundle.root / k).is_dir()}
    )


@pytest.mark.parametrize("root", FIXTURE_ROOTS, ids=lambda p: p.name)
def test_walk_members_equal_load_members(root: Path) -> None:
    bundle = load_bundle(root)
    result = walk(root)
    assert {m.id for m in result.members} == _loaded_ids(bundle)
    assert result.pruned == bundle.pruned


def test_walk_classifies_like_load(tmp_path: Path) -> None:
    for rel in ("a.md", "index.md", "sub/log.md", "sub/index.md", "pic.png", "schema/x.md"):
        write(tmp_path / rel, "---\ntitle: t\n---\n")
    kinds = {m.id: m.kind for m in walk(tmp_path, ignore=["schema/*"]).members}
    assert kinds == {
        "a.md": "concept",
        "index.md": "index",
        "sub/log.md": "log",
        "sub/index.md": "index",
        "pic.png": "asset",
        "schema/x.md": "ignored",
    }


def test_walk_order_is_depth_first_sorted(tmp_path: Path) -> None:
    for rel in ("b.md", "a/z.md", "a/b.md", "c.md"):
        write(tmp_path / rel, "x")
    assert [m.id for m in walk(tmp_path).members] == ["a/b.md", "a/z.md", "b.md", "c.md"]


def test_walk_carries_stat(tmp_path: Path) -> None:
    write(tmp_path / "a.md", "hello")
    os.utime(tmp_path / "a.md", ns=(1_000_000_000, 2_000_000_000))
    (member,) = walk(tmp_path).members
    assert member.stat is not None
    assert member.stat.size == 5 and member.stat.mtime_ns == 2_000_000_000


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_walk_excludes_git_root_dots_and_dir_symlinks(tmp_path: Path) -> None:
    write(tmp_path / ".obsidian/x.md", "x")
    write(tmp_path / "sub/.git/HEAD", "x")
    write(tmp_path / "sub/.agents/keep.md", "x")
    write(tmp_path / "real/a.md", "x")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    assert [m.id for m in walk(tmp_path).members] == ["real/a.md", "sub/.agents/keep.md"]


def test_walk_prunes_and_records(tmp_path: Path) -> None:
    write(tmp_path / "keep/a.md", "x")
    write(tmp_path / "vendor/deep/b.md", "x")
    result = walk(tmp_path, prune=["vendor"])
    assert [m.id for m in result.members] == ["keep/a.md"]
    assert result.pruned == frozenset({"vendor"})


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_walk_broken_symlink_has_no_stat(tmp_path: Path) -> None:
    (tmp_path / "dangling.md").symlink_to(tmp_path / "missing.md")
    (member,) = walk(tmp_path).members
    assert member == Member("dangling.md", "concept", None)
    assert "dangling.md" in load_bundle(tmp_path).unreadable


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0, reason="chmod is not enforced")
def test_walk_records_unreadable_directory(tmp_path: Path) -> None:
    write(tmp_path / "locked/a.md", "x")
    (tmp_path / "locked").chmod(0)
    try:
        result = walk(tmp_path)
        assert result.unreadable_dirs["locked"].startswith("could not be read: ")
    finally:
        (tmp_path / "locked").chmod(0o755)


def test_walk_root_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        walk(tmp_path / "nope")


def test_member_stat_same_treats_zero_ino_as_unknown() -> None:
    a = MemberStat(1, 2, 0, 3)
    assert a.same(MemberStat(1, 2, 99, 3))
    assert MemberStat(1, 2, 99, 3).same(a)
    assert MemberStat(1, 2, 99, 3).same(MemberStat(1, 2, 99, 3))
    assert not a.same(MemberStat(9, 2, 99, 3))
    assert not a.same(MemberStat(1, 2, 99, 9))
    assert not a.same(MemberStat(1, 9, 99, 3))
    assert not a.same(None)
    assert not MemberStat(1, 2, 5, 3).same(MemberStat(1, 2, 6, 3))
