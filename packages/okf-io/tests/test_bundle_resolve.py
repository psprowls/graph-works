from __future__ import annotations

from pathlib import Path

from helpers import write
from okf_io import Document, load_bundle
from okf_io.bundle import Unreadable, read_member, resolve_member


def test_read_member_parses(tmp_path: Path) -> None:
    write(tmp_path / "a.md", "---\ntitle: A\n---\nbody\n")
    doc = read_member(tmp_path, "a.md")
    assert isinstance(doc, Document) and doc.fm.title == "A"


def test_read_member_bad_utf8_matches_load(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe---\n")
    result = read_member(tmp_path, "bad.md")
    assert isinstance(result, Unreadable)
    assert result.reason == load_bundle(tmp_path).unreadable["bad.md"]
    assert result.reason.startswith("not valid UTF-8: ")


def test_read_member_missing_is_unreadable_not_raised(tmp_path: Path) -> None:
    result = read_member(tmp_path, "gone.md")
    assert isinstance(result, Unreadable) and result.reason.startswith("could not be read: ")


def _oracle(ids: set[str], canon: dict[str, str]):
    return {"has_raw": ids.__contains__, "canonical": canon.get}


def test_resolve_exact(tmp_path: Path) -> None:
    assert resolve_member(" a.md ", root=tmp_path, pruned=(), **_oracle({"a.md"}, {})) == "a.md"


def test_resolve_empty_is_none(tmp_path: Path) -> None:
    assert resolve_member("  ", root=tmp_path, pruned=(), **_oracle({""}, {})) is None


def test_resolve_nfc_falls_back_to_canonical(tmp_path: Path) -> None:
    nfd, nfc = "café.md", "café.md"
    assert resolve_member(nfc, root=tmp_path, pruned=(), **_oracle({nfd}, {nfc: nfd})) == nfd


def test_resolve_pruned_probe(tmp_path: Path) -> None:
    write(tmp_path / "vendor/x/y.md", "x")
    hit = resolve_member("vendor/x/y.md", root=tmp_path, pruned=("vendor",), **_oracle(set(), {}))
    assert hit == "vendor/x/y.md"
    assert resolve_member("vendor/../y.md", root=tmp_path, pruned=("vendor",), **_oracle(set(), {})) is None


def test_bundle_member_id_still_answers_the_same(tmp_path: Path) -> None:
    write(tmp_path / "café.md", "x")
    write(tmp_path / "vendor/z.md", "x")
    bundle = load_bundle(tmp_path, prune=["vendor"])
    assert bundle.member_id("café.md") is not None
    assert bundle.member_id("vendor/z.md") == "vendor/z.md"
    assert bundle.member_id("nope.md") is None
