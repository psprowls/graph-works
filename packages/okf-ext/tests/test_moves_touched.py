"""Candidate scans must over-include rather than miss a move's referrers."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest
from ext_helpers import write_tree
from okf_ext.moves import scan_touched
from okf_io import Document, load_bundle

SRC = "work/e/children/bug-x"
REFERRERS = [
    ("percent-encoded", "[x](/work/e/children/bug%2Dx.md)\n"),
    ("reference-definition", "[x][r]\n\n[r]: /work/e/children/bug-x.md\n"),
    (
        "frontmatter-source",
        "---\ntitle: R\nsources:\n  - id: d\n    resource: /work/e/children/bug-x/references/01-design.md\n---\n",
    ),
    ("depends-on", "---\ntitle: R\ndepends_on:\n  - path: work/e/children/bug-x\n---\n"),
    ("wikilink", "see [[work/e/children/bug-x]]\n"),
    ("parse-error", "---\ntitle: [unterminated\n---\n[x](/work/e/children/bug-x.md)\n"),
    ("dot-segments", "[x](./../e/children/./bug-x.md)\n"),
]
ESCAPED_REFERRERS = [
    ("entity", f"[x](/{SRC.replace('bug-x', 'bug&#45;x')}.md)\n", "unlocatable-reference"),
    ("hex-entity", f"[x](/{SRC.replace('bug-x', 'bug&#x2d;x')}.md)\n", "unlocatable-reference"),
    ("backslash", f"[x](/{SRC.replace('bug-x', r'bug\-x')}.md)\n", "unlocatable-reference"),
    ("entity-definition", f"[x][r]\n\n[r]: /{SRC.replace('bug-x', 'bug&#45;x')}.md\n", "unlocatable-reference"),
    ("backslash-definition", f"[x][r]\n\n[r]: /{SRC.replace('bug-x', r'bug\-x')}.md\n", "unlocatable-reference"),
    *[
        (
            f"yaml-{name}",
            f'---\ntitle: R\nsources:\n- id: r\n  resource: "/{SRC.replace("bug-x", spelling)}.md"\n---\n',
            None,
        )
        for name, spelling in [
            ("unicode", r"bug\u002dx"),
            ("long-unicode", r"bug\U0000002dx"),
            ("hex", r"bug\x2dx"),
            ("continuation", "bug\\\n    -x"),
        ]
    ],
    (
        "yaml-dependency",
        f'---\ntitle: R\ndepends_on:\n- path: "{SRC.replace("bug-x", r"bug\u002dx")}"\n---\n',
        None,
    ),
]


def _root(tmp_path, extra=None):
    files = {
        f"{SRC}.md": "---\ntitle: X\n---\n",
        f"{SRC}/references/01-design.md": "# D\n",
        f"{SRC}/references/img.png": "png",
        f"{SRC}/index.md": "# Index\n",
        f"{SRC}/log.md": "# Log\n",
        f"{SRC}/ignored.md": "opaque",
        "work/e.md": "---\ntitle: E\n---\n",
        "work/e/children/index.md": "- [X](bug-x.md)\n",
        "work/unrelated.md": "---\ntitle: U\n---\nnothing here\n",
    }
    files.update(extra or {})
    return write_tree(tmp_path / "b", files)


def test_subtree_and_textual_referrers_are_touched(tmp_path):
    scan = scan_touched(_root(tmp_path), [SRC], ignore=("*/ignored.md",))
    assert {
        f"{SRC}.md",
        f"{SRC}/references/01-design.md",
        f"{SRC}/references/img.png",
        f"{SRC}/index.md",
        f"{SRC}/log.md",
        f"{SRC}/ignored.md",
        "work/e/children/index.md",
    } <= scan.members
    assert "work/unrelated.md" not in scan.members
    assert scan.complete and not scan.warnings


@pytest.mark.parametrize("name,text", REFERRERS)
def test_every_referrer_form_is_touched(tmp_path, name, text):
    scan = scan_touched(_root(tmp_path, {f"work/r-{name}.md": text}), [SRC])
    assert f"work/r-{name}.md" in scan.members


@pytest.mark.parametrize("name,text,_refusal", ESCAPED_REFERRERS, ids=[case[0] for case in ESCAPED_REFERRERS])
def test_grammar_escaped_referrers_are_conservatively_touched(tmp_path, monkeypatch, name, text, _refusal):
    root = _root(tmp_path, {"work/r.md": text})

    def parse(*args, **kwargs):
        raise AssertionError("the escaped candidate scan parsed a document")

    monkeypatch.setattr(Document, "parse", parse)
    assert "work/r.md" in scan_touched(root, [SRC]).members


def test_an_nfd_named_referrer_is_touched(tmp_path):
    nfd = unicodedata.normalize("NFD", "work/réf.md")
    assert nfd in scan_touched(_root(tmp_path, {nfd: f"[x](/{SRC}.md)\n"}), [SRC]).members


def test_an_nfd_spelled_needle_in_text_is_touched(tmp_path):
    src = "work/e/children/bug-café"
    root = write_tree(
        tmp_path / "b", {f"{src}.md": "# C\n", "work/r.md": unicodedata.normalize("NFD", f"[x](/{src}.md)\n")}
    )
    assert "work/r.md" in scan_touched(root, [src]).members


def test_a_percent_encoded_source_needle_is_decoded(tmp_path):
    root = _root(tmp_path, {"work/r.md": f"[x](/{SRC}.md)\n"})
    assert "work/r.md" in scan_touched(root, [SRC.replace("bug-x", "bug%2Dx")]).members


def test_extra_members_are_always_touched(tmp_path):
    assert "work/index.md" in scan_touched(_root(tmp_path), [SRC], extra={"work/index.md"}).members


@pytest.mark.parametrize("contents", [b"\xff\xfe", "a real replacement: �\n".encode()])
def test_undecodable_or_replacement_markdown_is_touched(tmp_path, contents):
    root = _root(tmp_path)
    (root / "work/bad.md").write_bytes(contents)
    scan = scan_touched(root, [SRC])
    assert "work/bad.md" in scan.members
    if contents == b"\xff\xfe":
        assert "work/bad.md" in load_bundle(root, only=scan.members, inventory=scan.inventory).unreadable


def test_an_unreadable_markdown_file_is_touched(tmp_path, monkeypatch):
    root = _root(tmp_path, {"work/bad.md": "nothing here\n"})
    real = Path.read_bytes

    def read(path):
        if path == root / "work/bad.md":
            raise PermissionError("read denied")
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    scan = scan_touched(root, [SRC])
    assert "work/bad.md" in scan.members
    assert "work/bad.md" in load_bundle(root, only=scan.members, inventory=scan.inventory).unreadable


def test_unreadable_directories_make_the_scan_incomplete(tmp_path, monkeypatch):
    root = _root(tmp_path, {"locked/x.md": "x\n", "also-locked/x.md": "x\n"})
    real = os.scandir

    def scandir(path):
        if Path(path).name in {"locked", "also-locked"}:
            raise PermissionError("directory denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    scan = scan_touched(root, [SRC])
    assert not scan.complete
    assert set(scan.inventory.unreadable_dirs) == {"locked", "also-locked"}
    assert scan.warnings == (
        "touched scan could not read also-locked: falling back to a full load",
        "touched scan could not read locked: falling back to a full load",
    )


def test_assets_ignored_members_and_subtree_files_are_never_read(tmp_path, monkeypatch):
    root = _root(tmp_path, {"other.png": SRC, "ignored.md": SRC})
    real = Path.read_bytes

    def read(path):
        relative = path.relative_to(root).as_posix()
        if path.suffix == ".png" or path.name == "ignored.md" or relative.startswith(SRC):
            raise AssertionError(f"candidate scan read {relative}")
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    scan = scan_touched(root, [SRC], ignore=("ignored.md", "*/ignored.md"))
    assert f"{SRC}/ignored.md" in scan.members
    assert "ignored.md" not in scan.members and "other.png" not in scan.members


def test_the_scan_never_parses_and_preserves_pruned_inventory(tmp_path, monkeypatch):
    root = _root(tmp_path, {"clones/repo/x.md": SRC})

    def parse(*args, **kwargs):
        raise AssertionError("the candidate scan parsed a document")

    monkeypatch.setattr(Document, "parse", parse)
    scan = scan_touched(root, [SRC], prune=("clones/*",))
    assert scan.inventory.pruned == {"clones/repo"}
    assert "clones/repo/x.md" not in scan.members
    assert "work/e/children/index.md" in scan.members


def test_a_missing_root_is_a_caller_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_touched(tmp_path / "missing", [SRC])
