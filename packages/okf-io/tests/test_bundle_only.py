# packages/okf-io/tests/test_bundle_only.py
from datetime import date

import pytest
from okf_io import Document, build_link_graph, load_bundle, validate
from okf_io.bundle import walk


def _tree(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


FILES = {
    "a.md": "---\ntitle: A\n---\n[b](b.md) [c](sub/c.md)\n",
    "b.md": "---\ntitle: B\n---\nbody\n",
    "sub/c.md": "---\ntitle: C\n---\n[x](../a.md)\n",
    "sub/index.md": "# Sub\n",
    "img.png": "binary-ish",
    "log.md": "# Log\n",
}


@pytest.fixture
def parses(monkeypatch):
    calls = []
    real = Document.parse.__func__

    def counted(cls, text, *, path=None):
        calls.append(path)
        return real(cls, text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(counted))
    return calls


def test_only_parses_the_named_members_and_inventories_the_rest(tmp_path, parses):
    root = _tree(tmp_path, FILES)
    bundle = load_bundle(root, only={"a.md"})
    assert set(bundle.concepts) == {"a"}
    assert bundle.unloaded == {"b.md", "sub/c.md", "sub/index.md", "log.md"}
    assert bundle.partial == frozenset({"a.md"})
    assert len(parses) == 1
    assert bundle.has_member("sub/c.md") and bundle.member_id("b.md") == "b.md"
    assert bundle.assets == {"img.png"}


def test_no_only_is_todays_bundle(tmp_path):
    root = _tree(tmp_path, FILES)
    bundle = load_bundle(root)
    assert bundle.partial is None and bundle.unloaded == frozenset()


@pytest.mark.parametrize("mode", ["full", "partial", "empty-reuse", "unrelated-reuse"])
def test_unseeded_path_load_uses_the_incremental_reader_boundary(tmp_path, monkeypatch, mode):
    from okf_io import bundle as bundle_mod
    from okf_io.bundle import Member, Unreadable, Walk

    # Model names unavailable on the host filesystem through the public reader,
    # as the Unicode/Windows equivalence fixtures do. Derivation stays real.
    inventory = Walk(
        (Member("virtual.md", "concept", None), Member("bad.md", "concept", None)),
        {"locked": "denied"},
        frozenset(),
    )
    doc = Document.parse("---\ntitle: Virtual\n---\nbody\n", path=tmp_path / "virtual.md")
    monkeypatch.setattr(
        bundle_mod, "read_member", lambda root, member: doc if member == "virtual.md" else Unreadable("bad bytes")
    )
    only = None if mode == "full" else {"virtual.md", "bad.md"}
    reuse = {} if mode == "empty-reuse" else {"other.md": doc} if mode == "unrelated-reuse" else None
    bundle = load_bundle(tmp_path, only=only, reuse=reuse, inventory=inventory)

    assert bundle.concepts == {"virtual": doc}
    assert bundle.concepts["virtual"].fm.title == "Virtual"
    assert bundle.unreadable == {"bad.md": "bad bytes", "locked": "denied"}
    assert bundle.unreadable_dirs == {"locked"}
    assert bundle.member_id("virtual.md") == "virtual.md"
    assert bundle.partial == only


def test_links_into_unloaded_members_are_not_broken(tmp_path):
    root = _tree(tmp_path, FILES)
    graph = build_link_graph(load_bundle(root, only={"a.md"}))
    assert graph.broken == ()
    report = validate(load_bundle(root, only={"a.md"}), today=date(2026, 1, 1))
    assert report.by_code("links.broken") == ()


def test_only_matches_nfc_insensitively(tmp_path):
    import unicodedata

    nfd = unicodedata.normalize("NFD", "café.md")
    root = _tree(tmp_path, {nfd: "---\ntitle: X\n---\n", "z.md": "---\ntitle: Z\n---\n"})
    bundle = load_bundle(root, only={unicodedata.normalize("NFC", "café.md")})
    assert len(bundle.concepts) == 1 and "z.md" in bundle.unloaded


def test_reuse_returns_the_same_document_for_identical_bytes(tmp_path, parses):
    root = _tree(tmp_path, FILES)
    first = load_bundle(root, only={"a.md", "b.md"})
    parses.clear()
    second = load_bundle(root, only={"a.md", "b.md"}, reuse={"a.md": first.concepts["a"], "b.md": first.concepts["b"]})
    assert second.concepts["a"] is first.concepts["a"]
    assert parses == []


def test_reuse_reparses_changed_bytes(tmp_path, parses):
    root = _tree(tmp_path, FILES)
    first = load_bundle(root, only={"b.md"})
    (root / "b.md").write_text("---\ntitle: B2\n---\nbody\n", encoding="utf-8", newline="")
    parses.clear()
    second = load_bundle(root, only={"b.md"}, reuse={"b.md": first.concepts["b"]})
    assert second.concepts["b"] is not first.concepts["b"] and second.concepts["b"].fm.title == "B2"
    assert len(parses) == 1


def test_reuse_skips_a_dirty_document(tmp_path):
    root = _tree(tmp_path, FILES)
    first = load_bundle(root, only={"b.md"})
    doc = first.concepts["b"]
    doc.set("title", "edited")
    second = load_bundle(root, only={"b.md"}, reuse={"b.md": doc})
    assert second.concepts["b"] is not doc


def test_reuse_without_only_is_a_caller_error(tmp_path):
    with pytest.raises(ValueError, match="reuse= requires only="):
        load_bundle(_tree(tmp_path, FILES), reuse={})


def test_inventory_skips_the_second_walk(tmp_path, monkeypatch):
    from okf_io import bundle as bundle_mod

    root = _tree(tmp_path, FILES)
    inventory = walk(root)
    monkeypatch.setattr(bundle_mod, "_files", lambda *a, **k: pytest.fail("walked twice"))
    bundle = load_bundle(root, only={"a.md"}, inventory=inventory)
    assert set(bundle.concepts) == {"a"}


def test_inventory_is_reclassified_under_the_loads_own_ignore(tmp_path):
    root = _tree(tmp_path, FILES)
    bundle = load_bundle(root, ignore=("sub/*",), only={"a.md"}, inventory=walk(root))
    assert "sub/c.md" in bundle.ignored and "sub/c.md" not in bundle.unloaded


def test_unreadable_unloaded_member_is_not_reported(tmp_path):
    root = _tree(tmp_path, FILES)
    (root / "bad.md").write_bytes(b"\xff\xfe")
    bundle = load_bundle(root, only={"a.md"})
    assert "bad.md" in bundle.unloaded and "bad.md" not in bundle.unreadable


def test_descriptor_load_honours_only_and_reuse(tmp_path):
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("descriptor walk is POSIX-only")
    from okf_io.bundle import _load_at

    root = _tree(tmp_path, FILES)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        first = _load_at(root, fd, only={"a.md"})
        second = _load_at(root, fd, only={"a.md"}, reuse={"a.md": first.concepts["a"]})
    finally:
        os.close(fd)
    assert second.concepts["a"] is first.concepts["a"] and "b.md" in second.unloaded


def test_a_subclass_may_still_add_a_required_field():
    from dataclasses import dataclass

    from okf_io import Bundle

    @dataclass(frozen=True, slots=True)
    class Extended(Bundle):
        extra: int

    assert "extra" in Extended.__dataclass_fields__


def test_empty_only_reads_no_markdown_and_views_see_only_parsed_members(tmp_path, parses):
    root = _tree(tmp_path, FILES)
    bundle = load_bundle(root, only=set())
    assert bundle.partial == frozenset()
    assert bundle.unloaded == {"a.md", "b.md", "sub/c.md", "sub/index.md", "log.md"}
    assert bundle.concepts == bundle.indexes == bundle.logs == {}
    assert bundle.by_type("Metric") == bundle.by_tag("x") == bundle.by_status("stable") == ()
    assert parses == []


def test_unloaded_members_resolve_without_probing_even_after_removal(tmp_path, monkeypatch):
    from okf_io import bundle as bundle_mod

    root = _tree(tmp_path, FILES)
    bundle = load_bundle(root, only={"a.md"})
    (root / "b.md").unlink()
    monkeypatch.setattr(bundle_mod, "_pruned_member", lambda *a, **k: pytest.fail("probed unloaded member"))
    assert bundle.member_id("b.md") == "b.md"
    assert bundle.has_member("sub/index.md") and bundle.has_member("log.md")


def test_partial_lists_raw_admitted_members_including_read_failures(tmp_path):
    root = _tree(tmp_path, FILES)
    (root / "bad.md").write_bytes(b"\xff")
    bundle = load_bundle(root, only={"bad.md", "sub/index.md", "log.md", "img.png", "absent.md"})
    assert bundle.partial == {"bad.md", "sub/index.md", "log.md"}
    assert set(bundle.indexes) == {"sub"} and set(bundle.logs) == {""}
    assert "bad.md" in bundle.unreadable and "bad.md" not in bundle.unloaded


def test_unloaded_canonical_collisions_preserve_raw_walk_order(tmp_path):
    from okf_io.bundle import Member, Walk

    # A supplied inventory can represent the two entries even on APFS.
    nfd, nfc = "cafe\u0301.md", "caf\u00e9.md"
    inventory = Walk((Member(nfd, "concept", None), Member(nfc, "concept", None)), {}, frozenset())
    bundle = load_bundle(tmp_path, only=set(), inventory=inventory)
    assert bundle.unloaded == {nfd, nfc}
    assert bundle.canonical_collisions == {nfc: (nfd, nfc)}
    assert bundle.member_id(nfd) == nfd and bundle.member_id(nfc) == nfc


def test_inventory_retains_unreadable_directories_and_pruned_roots(tmp_path):
    from okf_io.bundle import Walk

    root = _tree(tmp_path, FILES)
    found = walk(root, prune=("sub",))
    inventory = Walk(found.members, {"denied": "could not be read: denied"}, found.pruned)
    bundle = load_bundle(root, only={"a.md"}, prune=("sub",), inventory=inventory)
    assert bundle.pruned == {"sub"}
    assert bundle.unreadable == {"denied": "could not be read: denied"}
    assert bundle.unreadable_dirs == {"denied"}
    assert "sub/c.md" not in bundle.unloaded


def test_reuse_reparses_a_moved_member_with_identical_bytes(tmp_path, parses):
    root = _tree(tmp_path, FILES)
    first = load_bundle(root, only={"b.md"})
    old = first.concepts["b"]
    (root / "b.md").rename(root / "moved.md")
    parses.clear()
    second = load_bundle(root, only={"moved.md"}, reuse={"moved.md": old})
    assert second.concepts["moved"] is not old
    assert second.concepts["moved"].path == root / "moved.md"
    assert len(parses) == 1


def test_reuse_does_not_skip_read_failures(tmp_path):
    root = _tree(tmp_path, FILES)
    first = load_bundle(root, only={"a.md"})
    inventory = walk(root)
    (root / "a.md").unlink()
    second = load_bundle(root, only={"a.md"}, reuse={"a.md": first.concepts["a"]}, inventory=inventory)
    assert second.concepts == {}
    assert second.unreadable["a.md"].startswith("could not be read:")


def test_inventory_reclassifies_previously_ignored_members(tmp_path):
    root = _tree(tmp_path, FILES)
    bundle = load_bundle(root, only={"sub/c.md"}, inventory=walk(root, ignore=("sub/*",)))
    assert set(bundle.concepts) == {"sub/c"}
    assert "sub/index.md" in bundle.unloaded and bundle.ignored == frozenset()
