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


def test_bundle_walk_is_iterative_beyond_the_python_recursion_limit(tmp_path):
    cursor = tmp_path
    parts: list[str] = []
    for _ in range(300):
        part = "d"
        parts.append(part)
        cursor /= part
        cursor.mkdir()
    (cursor / "leaf.md").write_text(CONCEPT, encoding="utf-8")
    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(250)
        loaded = bundle.load(tmp_path)
    finally:
        sys.setrecursionlimit(previous_limit)

    assert "/".join((*parts, "leaf")) in loaded.concepts


def test_descriptor_rooted_load_matches_path_load_and_leaves_caller_fd_open(tmp_path):
    write(tmp_path, "concept.md", CONCEPT)
    write(tmp_path, "nested/index.md", "# Nested\n")
    write(tmp_path, "nested/asset.bin", "asset")
    write(tmp_path, "nested/.kept.md", CONCEPT)
    write(tmp_path, "ignored/skip.md", CONCEPT)
    write(tmp_path, ".root-hidden.md", CONCEPT)
    (tmp_path / "invalid.md").write_bytes(b"\xff\xfe")
    (tmp_path / "linked.md").symlink_to("concept.md")
    (tmp_path / "directory-link").symlink_to("nested", target_is_directory=True)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        expected = bundle.load(tmp_path, ignore=("ignored/*",))
        actual = bundle._load_at(tmp_path, descriptor, ignore=("ignored/*",))
        os.fstat(descriptor)
    finally:
        os.close(descriptor)

    assert actual.root == expected.root
    assert actual.assets == expected.assets
    assert actual.ignored == expected.ignored
    assert actual.unreadable == expected.unreadable
    assert tuple(actual.concepts) == tuple(expected.concepts)
    assert tuple(actual.indexes) == tuple(expected.indexes)
    assert tuple(actual.logs) == tuple(expected.logs)
    assert {member: (document.raw_text, document.path) for member, document in actual.concepts.items()} == {
        member: (document.raw_text, document.path) for member, document in expected.concepts.items()
    }


def test_descriptor_rooted_load_rejects_a_non_directory_without_closing_it(tmp_path):
    file_path = tmp_path / "not-a-directory"
    file_path.write_bytes(b"file")
    descriptor = os.open(file_path, os.O_RDONLY)
    try:
        with pytest.raises(NotADirectoryError, match="not a directory"):
            bundle._load_at(tmp_path, descriptor)
        os.fstat(descriptor)
    finally:
        os.close(descriptor)


def test_descriptor_rooted_per_entry_lstat_failure_matches_path_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "good.md", CONCEPT)
    transient = write(tmp_path, "nested/transient.md", CONCEPT)
    real_stat = bundle.os.stat

    def fail_path_stat(path: os.PathLike[str] | str | bytes, *args: object, **kwargs: object):
        if Path(path) == transient and kwargs.get("follow_symlinks") is False:
            raise OSError("injected per-entry stat failure")
        return real_stat(path, *args, **kwargs)

    with monkeypatch.context() as path_patch:
        path_patch.setattr(bundle.os, "stat", fail_path_stat)
        with pytest.raises(OSError, match="injected per-entry stat failure"):
            bundle.load(tmp_path)

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def fail_one_lstat(
        path: int | str | bytes,
        *args: object,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
        **kwargs: object,
    ):
        if path == "transient.md" and dir_fd is not None and not follow_symlinks:
            raise OSError("injected per-entry stat failure")
        return real_stat(path, *args, dir_fd=dir_fd, follow_symlinks=follow_symlinks, **kwargs)

    monkeypatch.setattr(bundle.os, "stat", fail_one_lstat)
    try:
        with pytest.raises(OSError, match="injected per-entry stat failure"):
            bundle._load_at(tmp_path, descriptor)
    finally:
        os.close(descriptor)


def test_descriptor_rooted_follow_stat_failure_matches_path_symlink_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "real.md", CONCEPT)
    linked = tmp_path / "linked.md"
    linked.symlink_to("real.md")
    real_stat = bundle.os.stat

    def fail_path_follow_stat(path: os.PathLike[str] | str | bytes, *args: object, **kwargs: object):
        if Path(path) == linked and kwargs.get("follow_symlinks") is not False:
            raise OSError("injected follow-stat failure")
        return real_stat(path, *args, **kwargs)

    with monkeypatch.context() as path_patch:
        path_patch.setattr(bundle.os, "stat", fail_path_follow_stat)
        with pytest.raises(OSError, match="injected follow-stat failure"):
            bundle.load(tmp_path)

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def fail_one_follow_stat(
        path: int | str | bytes,
        *args: object,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
        **kwargs: object,
    ):
        if path == "linked.md" and dir_fd is not None and follow_symlinks:
            raise OSError("injected follow-stat failure")
        return real_stat(path, *args, dir_fd=dir_fd, follow_symlinks=follow_symlinks, **kwargs)

    monkeypatch.setattr(bundle.os, "stat", fail_one_follow_stat)
    try:
        with pytest.raises(OSError, match="injected follow-stat failure"):
            bundle._load_at(tmp_path, descriptor)
    finally:
        os.close(descriptor)


def test_descriptor_rooted_file_open_failure_matches_path_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "good.md", CONCEPT)
    bad = write(tmp_path, "bad.md", CONCEPT)
    real_read_bytes = Path.read_bytes

    def fail_path_read(path: Path) -> bytes:
        if path == bad:
            raise OSError("injected file open failure")
        return real_read_bytes(path)

    with monkeypatch.context() as path_patch:
        path_patch.setattr(Path, "read_bytes", fail_path_read)
        expected = bundle.load(tmp_path)

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_open = bundle.os.open

    def fail_descriptor_read(
        path: int | str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == "bad.md" and dir_fd is not None and not flags & os.O_DIRECTORY:
            raise OSError("injected file open failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(bundle.os, "open", fail_descriptor_read)
    try:
        actual = bundle._load_at(tmp_path, descriptor)
    finally:
        os.close(descriptor)

    assert tuple(actual.concepts) == tuple(expected.concepts)
    assert actual.assets == expected.assets
    assert actual.unreadable == expected.unreadable


def test_ignore_excludes_concepts_but_leaves_them_resolvable(tmp_path):
    write(tmp_path, "a.md", CONCEPT)
    write(tmp_path, "schema/kinds.md", CONCEPT)
    write(tmp_path, "build/out.tmp", "x")
    loaded = bundle.load(tmp_path, ignore=["schema/*", "**/*.tmp"])
    assert set(loaded.concepts) == {"a"}
    assert loaded.ignored == frozenset({"schema/kinds.md", "build/out.tmp"})
    assert loaded.assets == frozenset()
    assert loaded.has_member("schema/kinds.md")


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


def test_a_raw_concepts_lookup_on_the_query_path_misses_what_has_member_found(tmp_path):
    """The regression this whole work item exists to prevent: `has_member`
    can say yes for a non-ASCII path whose written form differs from the
    disk id, while a literal `concepts` lookup keyed on that same query
    string says no. A caller must route through `member_id` to get the id
    `concepts` actually uses -- this is the funnel's whole point, pinned
    through the public `Bundle` surface rather than any one consumer."""
    write(tmp_path, f"concepts/{_NFD}.md", CONCEPT)
    loaded = bundle.load(tmp_path)
    query = f"concepts/{_NFC}.md"

    assert loaded.has_member(query)
    assert loaded.concepts.get(query[:-3]) is None  # the query-keyed miss

    raw_id = loaded.member_id(query)
    assert raw_id is not None
    assert loaded.concepts.get(raw_id[:-3]) is not None  # the funnel finds it


def test_an_all_ascii_bundle_never_consults_the_canonical_map(acme):
    """`isascii()` is the whole fast-path story: an ASCII bundle carries no
    non-ASCII members, so `_canonical` is empty and behaviour is unchanged."""
    assert acme.has_member("metrics/revenue.md")
    assert not acme.has_member("metrics/does-not-exist.md")


def test_track_canonical_records_every_losing_raw_id_in_walk_order():
    canonical: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    bundle._track_canonical(canonical, collisions, f"concepts/{_NFD}.md")
    bundle._track_canonical(canonical, collisions, "concepts/plain.md")  # ASCII: no-op
    bundle._track_canonical(canonical, collisions, f"concepts/{_NFC}.md")

    cid = f"concepts/{_NFC}.md"
    assert canonical[cid] == f"concepts/{_NFC}.md"  # last write wins, unchanged
    assert collisions == {cid: [f"concepts/{_NFD}.md", f"concepts/{_NFC}.md"]}


def test_track_canonical_is_a_no_op_with_no_collision():
    canonical: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    bundle._track_canonical(canonical, collisions, f"concepts/{_NFD}.md")
    assert collisions == {}


def test_load_wires_a_real_collision_into_the_public_canonical_collisions_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_load`'s final `canonical_collisions=MappingProxyType(...)`
    construction is otherwise only ever exercised against an empty
    `collisions` dict: two real *simultaneous* sibling files that are
    NFC-equal but byte-different can't be committed and checked out here --
    APFS folds them into one directory entry. Monkeypatching `_files` -- the
    walk `_load` consumes when `root_fd is None`, exactly the path
    `bundle.load()` takes -- stands two differently-spelled `Path`s in for a
    real collision without needing two files: APFS resolves either spelling
    to the one file written on disk, so both opens succeed and `_load` runs
    its real collision-tracking code end to end, asserted on the public
    `Bundle` `load()` returns."""
    write(tmp_path, f"concepts/{_NFC}.md", CONCEPT)

    def fake_files(root: Path, *, unreadable: dict[str, str]):
        yield root / "concepts" / f"{_NFD}.md"
        yield root / "concepts" / f"{_NFC}.md"

    monkeypatch.setattr(bundle, "_files", fake_files)
    loaded = bundle.load(tmp_path)

    cid = f"concepts/{_NFC}.md"
    assert loaded.canonical_collisions == {cid: (f"concepts/{_NFD}.md", f"concepts/{_NFC}.md")}
