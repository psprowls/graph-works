"""Changing the validation lens needs no reread or reparse."""

import unicodedata
from dataclasses import replace

import pytest
from gw_kernel.workspace import bundle as loaders
from okf_io.bundle import walk
from work_tracker_okf.items import IGNORE


@pytest.mark.parametrize("partial", [False, True])
def test_reclassify_matches_loader_ignore_for_every_member_kind(tmp_path, partial, monkeypatch):
    unreadable_nfd = f"work/a/references/{unicodedata.normalize('NFD', 'café')}.md"
    members = {
        "ordinary.md": b"---\ntitle: P\n---\n",
        "unselected.md": b"unselected\n",
        "work/a/references/x.md": b"---\ntitle: X\n---\n",
        "work/a/references/index.md": b"# Index\n",
        "work/a/references/log.md": b"# Log\n",
        "work/a/references/invalid.md": b"\xff",
        "work/a/references/unselected.md": b"unselected\n",
        "work/a/references/asset.bin": b"\x00",
        "work/.DS_Store": b"asset",
        unreadable_nfd: b"\xff",
    }
    for member, data in members.items():
        target = tmp_path / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    inventory = walk(tmp_path, prune=loaders.with_clone_prune())
    only = set(members) - {"unselected.md", "work/a/references/unselected.md"}
    if partial:
        before = loaders.load_partial_bundle(tmp_path, only=only, inventory=inventory)
        oracle = loaders.load_partial_bundle(tmp_path, only=only, inventory=inventory, ignore=IGNORE)
    else:
        before = loaders.load_bundle_at(tmp_path)
        oracle = loaders.load_bundle_at(tmp_path, ignore=IGNORE)
    monkeypatch.setattr(loaders, "load_bundle", lambda *a, **k: pytest.fail("reclassification must not load"))
    actual = loaders.reclassify_ignored(before, IGNORE)
    assert actual.member_id("work/a/references/café.md") == oracle.member_id("work/a/references/café.md")
    assert replace(actual, concepts={}, indexes={}, logs={}) == replace(oracle, concepts={}, indexes={}, logs={})
    for field in ["concepts", "indexes", "logs"]:
        got = getattr(actual, field)
        expected = getattr(oracle, field)
        assert {key: doc.raw_text for key, doc in got.items()} == {key: doc.raw_text for key, doc in expected.items()}
        assert all(doc is getattr(before, field)[key] for key, doc in got.items())


@pytest.mark.parametrize("partial", [False, True])
def test_reclassify_keeps_unreadable_directory_diagnostics_even_for_markdown_names(tmp_path, partial, monkeypatch):
    from okf_io import bundle as bundle_mod

    directory = "work/a/references/locked.md"
    member = "work/a/references/bad.md"
    target = tmp_path / member
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff")
    original = bundle_mod.walk

    def denied(root, **kwargs):
        return replace(original(root, **kwargs), unreadable_dirs={directory: "denied"})

    monkeypatch.setattr(bundle_mod, "walk", denied)
    if partial:
        inventory = denied(tmp_path, prune=loaders.with_clone_prune())
        before = loaders.load_partial_bundle(tmp_path, only={member}, inventory=inventory)
        oracle = loaders.load_partial_bundle(tmp_path, only={member}, inventory=inventory, ignore=IGNORE)
    else:
        before = loaders.load_bundle_at(tmp_path)
        oracle = loaders.load_bundle_at(tmp_path, ignore=IGNORE)
    actual = loaders.reclassify_ignored(before, IGNORE)
    assert actual == oracle
    assert actual.unreadable == {directory: "denied"}
    assert member in actual.ignored and directory not in actual.ignored
