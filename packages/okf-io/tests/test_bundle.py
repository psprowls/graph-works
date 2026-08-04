from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from helpers import BUNDLES
from okf_io import bundle

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.fixture(scope="module")
def acme():
    return bundle.load(BUNDLES / "acme_retail")


def write(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n# Definition\n"


def test_the_walk_sorts_members_into_their_kinds(acme):
    assert len(acme.concepts) == 9
    assert "metrics/revenue" in acme.concepts
    assert "index" not in acme.concepts
    assert set(acme.indexes) == {
        "",
        "attesters",
        "computations",
        "metrics",
        "policies",
        "skills",
        "tables",
    }
    assert set(acme.logs) == {""}
    assert acme.assets == frozenset({"viz.html", "attesters/sql_equality.py"})
    assert acme.unreadable == {}


def test_reserved_names_are_recognised_at_any_depth(acme):
    assert acme.indexes["metrics"].body.startswith("# Metric")
    assert not any(cid.endswith("/index") or cid.endswith("/log") for cid in acme.concepts)


def test_lookup_and_views_are_sorted(acme):
    assert acme.concept("metrics/revenue") is not None
    assert acme.concept("nope") is None
    assert acme.by_type("Attested Computation") == (
        "computations/gross-margin-period",
        "computations/revenue-ytd",
    )
    assert acme.by_tag("finance") == tuple(sorted(acme.by_tag("finance")))
    assert "metrics/gross-margin-legacy" in acme.by_status("deprecated")


def test_by_status_uses_the_effective_status(tmp_path):
    """§5.4's default is computed once, here, not re-derived by each caller."""
    write(tmp_path, "a.md", "---\ntype: Metric\n---\n\n# D\n")
    loaded = bundle.load(tmp_path)
    assert loaded.by_status("stable") == ("a",)


def test_has_member_covers_every_kind(acme):
    assert acme.has_member("metrics/revenue.md")
    assert acme.has_member("index.md")
    assert acme.has_member("metrics/index.md")
    assert acme.has_member("log.md")
    assert acme.has_member("viz.html")
    assert not acme.has_member("metrics/nope.md")


def test_a_non_utf8_member_becomes_unreadable_not_an_exception(tmp_path):
    write(tmp_path, "good.md", CONCEPT)
    (tmp_path / "bad.md").write_bytes(b"---\ntype: Metric\n---\n\n\xff\xfe\n")
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"good"}
    assert "bad.md" in loaded.unreadable
    assert "utf-8" in loaded.unreadable["bad.md"].lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_an_unreadable_file_does_not_stop_the_walk(tmp_path):
    write(tmp_path, "good.md", CONCEPT)
    denied = write(tmp_path, "denied.md", CONCEPT)
    denied.chmod(0o000)
    try:
        loaded = bundle.load(tmp_path)
    finally:
        denied.chmod(0o644)
    assert "good" in loaded.concepts
    assert "denied.md" in loaded.unreadable


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_an_unreadable_subdirectory_becomes_unreadable_not_a_silent_drop(tmp_path):
    """A subdirectory the walk cannot enter is bundle content, not a caller error.

    Losing the entire subtree with no record anywhere would contradict
    ``load()``'s own promise that an ``OSError`` on read is recorded and the
    walk continues -- that promise has to hold for a directory, not only a
    file.
    """
    write(tmp_path, "outside.md", CONCEPT)
    write(tmp_path, "denied/inside.md", CONCEPT)
    denied_dir = tmp_path / "denied"
    denied_dir.chmod(0o000)
    try:
        loaded = bundle.load(tmp_path)
    finally:
        denied_dir.chmod(0o755)
    assert set(loaded.concepts) == {"outside"}
    assert "denied/inside" not in loaded.concepts
    assert "denied" in loaded.unreadable
    assert "could not be read" in loaded.unreadable["denied"]


def test_a_nonexistent_root_raises(tmp_path):
    with pytest.raises(OSError):
        bundle.load(tmp_path / "does-not-exist")


def test_a_root_that_is_a_file_raises(tmp_path):
    not_a_dir = write(tmp_path, "a-file.md", CONCEPT)
    with pytest.raises(OSError):
        bundle.load(not_a_dir)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink permissions")
def test_a_file_symlink_is_followed_and_loaded(tmp_path):
    write(tmp_path, "real.md", CONCEPT)
    (tmp_path / "link.md").symlink_to(tmp_path / "real.md")
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"real", "link"}


def test_dot_entries_are_not_members_at_any_depth(tmp_path):
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path, "sub/.hidden/b.md", CONCEPT)
    write(tmp_path, ".dotfile.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"a"}
    assert loaded.assets == frozenset()


@pytest.mark.skipif(sys.platform == "win32", reason="symlink permissions")
def test_directory_symlinks_are_not_followed(tmp_path):
    write(tmp_path, "real/a.md", CONCEPT)
    (tmp_path / "loop").symlink_to(tmp_path / "real", target_is_directory=True)
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"real/a"}


def test_ignore_excludes_concepts_but_leaves_them_resolvable(tmp_path):
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, "_schema/kinds.md", CONCEPT)
    write(tmp_path, "build/out.tmp", "x")
    loaded = bundle.load(tmp_path, ignore=["_schema/*", "**/*.tmp"])
    assert set(loaded.concepts) == {"a"}
    assert loaded.ignored == frozenset({"_schema/kinds.md", "build/out.tmp"})
    assert loaded.assets == frozenset()
    assert loaded.has_member("_schema/kinds.md")


def test_mappings_are_read_only(acme):
    with pytest.raises(TypeError):
        acme.concepts["injected"] = None  # type: ignore[index]
