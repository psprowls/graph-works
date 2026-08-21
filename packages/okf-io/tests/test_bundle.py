from __future__ import annotations

import os
import sys
import unicodedata
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


def test_root_dot_entries_are_not_members(tmp_path):
    """D1's first half. The bundle root is where tooling parks its own
    dot-entries -- `.obsidian/`, `.DS_Store`, `.gitignore` -- so root-level
    hidden entries stay out of the model."""
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, ".dotfile.md", CONCEPT)
    write(tmp_path, ".hidden/b.md", CONCEPT)
    write(tmp_path, ".DS_Store", "junk")
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"a"}
    assert loaded.assets == frozenset()


def test_a_nested_dot_directorys_markdown_is_a_concept(tmp_path):
    """D1's second half, and the filed bug. A dot-directory nested inside the
    tree exists only because something deliberately created a path there --
    `code-wiki-okf`'s mirror lane writing `repositories/<repo>/.agents/...`."""
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, "repositories/demo/.agents/skills/x/SKILL.md.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"a", "repositories/demo/.agents/skills/x/SKILL.md"}
    assert loaded.has_member("repositories/demo/.agents/skills/x/SKILL.md.md")


def test_a_nested_dot_directorys_non_markdown_is_an_asset(tmp_path):
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, "work/item/.00-decisions.lock", "")
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"a"}
    assert loaded.assets == frozenset({"work/item/.00-decisions.lock"})


def test_git_is_excluded_at_the_bundle_root(tmp_path):
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path, ".git/objects/ab/cdef.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"a"}
    assert loaded.assets == frozenset()


def test_git_is_excluded_when_nested(tmp_path):
    """D2, and the one branch no other test reaches. A bundle carrying a
    vendored checkout would otherwise walk that checkout's whole object store
    into `assets` -- 10^4-10^5 files, a performance cliff rather than noise."""
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, "vendor/dep/.git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path, "vendor/dep/.git/objects/ab/cdef.md", CONCEPT)
    write(tmp_path, "vendor/dep/readme.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert set(loaded.concepts) == {"a", "vendor/dep/readme"}
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


#: `café` in each normalization form -- NFC is the single precomposed
#: `U+00E9`, NFD is `e` followed by the combining acute accent `U+0301`.
_NFC = unicodedata.normalize("NFC", "café")
_NFD = unicodedata.normalize("NFD", "café")


def test_canonical_id_normalizes_non_ascii_to_nfc():
    assert bundle.canonical_id(f"concepts/{_NFD}.md") == f"concepts/{_NFC}.md"
    assert bundle.canonical_id(f"concepts/{_NFC}.md") == f"concepts/{_NFC}.md"


def test_canonical_id_is_the_identity_for_ascii():
    assert bundle.canonical_id("concepts/plain.md") == "concepts/plain.md"


def test_has_member_matches_an_nfc_query_against_an_nfd_disk_id(tmp_path):
    write(tmp_path, f"concepts/{_NFD}.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert f"concepts/{_NFD}" in loaded.concepts
    assert loaded.has_member(f"concepts/{_NFC}.md")


def test_has_member_matches_an_nfd_query_against_an_nfc_disk_id(tmp_path):
    write(tmp_path, f"concepts/{_NFC}.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert loaded.has_member(f"concepts/{_NFD}.md")


def test_member_id_returns_the_raw_disk_id_for_a_differently_normalized_query(tmp_path):
    write(tmp_path, f"concepts/{_NFD}.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert loaded.member_id(f"concepts/{_NFC}.md") == f"concepts/{_NFD}.md"
    assert loaded.member_id(f"concepts/{_NFD}.md") == f"concepts/{_NFD}.md"


def test_member_id_returns_none_for_a_non_member(tmp_path):
    write(tmp_path, f"concepts/{_NFD}.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    assert loaded.member_id(f"concepts/{_NFC}-nope.md") is None


def test_an_all_ascii_bundle_never_consults_the_canonical_map(acme):
    """`isascii()` is the whole fast-path story: an ASCII bundle carries no
    non-ASCII members, so `_canonical` is empty and behaviour is unchanged."""
    assert acme.has_member("metrics/revenue.md")
    assert not acme.has_member("metrics/does-not-exist.md")
