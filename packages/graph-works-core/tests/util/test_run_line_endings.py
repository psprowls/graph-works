"""`run_line_endings`: detect and optionally repair CRLF reaccumulation under
`layout.bundle_dir` (§B of the CRLF-reaccumulation design).

`.gitattributes` declares the bundle LF-in-worktree, but git only enforces that
at checkout -- nothing enforces it on write, and `git status` cannot see a
violation because it compares normalised content. This verb is the re-runnable
detector (and repairer) for that gap.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from graph_works_core.util.commands import run_line_endings
from graph_works_core.workspace.layout import layout_for


def _layout(tmp_path: Path):
    layout = layout_for(tmp_path / ".works")
    layout.bundle_dir.mkdir(parents=True)
    return layout


def test_a_clean_bundle_reports_no_findings(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.bundle_dir / "index.md").write_bytes(b"# Index\n")

    report = run_line_endings(layout)

    assert report.findings == ()


def test_a_crlf_member_is_reported_with_its_count(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.bundle_dir / "index.md").write_bytes(b"line one\r\nline two\r\n")

    report = run_line_endings(layout)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.member == "index.md"
    assert finding.crlf_count == 2


def test_findings_are_sorted_by_member(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.bundle_dir / "zed.md").write_bytes(b"a\r\n")
    (layout.bundle_dir / "alpha.md").write_bytes(b"b\r\n")

    report = run_line_endings(layout)

    assert [finding.member for finding in report.findings] == ["alpha.md", "zed.md"]


def test_a_nested_crlf_member_reports_its_relative_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    nested = layout.bundle_dir / "work" / "epic" / "children"
    nested.mkdir(parents=True)
    (nested / "bug.md").write_bytes(b"a\r\nb\r\n")

    report = run_line_endings(layout)

    assert report.findings[0].member == "work/epic/children/bug.md"


def test_binary_members_are_never_reported(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.bundle_dir / "asset.bin").write_bytes(b"\x00binary\r\ndata")

    report = run_line_endings(layout)

    assert report.findings == ()


def test_default_call_does_not_touch_disk(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "index.md"
    target.write_bytes(b"a\r\nb\r\n")

    run_line_endings(layout)

    assert target.read_bytes() == b"a\r\nb\r\n"


def test_fix_converts_crlf_members_to_lf(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "index.md"
    target.write_bytes(b"a\r\nb\r\n")

    report = run_line_endings(layout, fix=True)

    assert target.read_bytes() == b"a\nb\n"
    assert report.findings[0].member == "index.md"


def test_fix_preserves_every_other_byte(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "index.md"
    target.write_bytes(b"---\r\ntitle: Alpha\r\n---\r\n\r\n# Alpha\r\n\r\nBody \xc3\xa9.\r\n")

    run_line_endings(layout, fix=True)

    assert target.read_bytes() == b"---\ntitle: Alpha\n---\n\n# Alpha\n\nBody \xc3\xa9.\n"


def test_fix_leaves_binary_members_untouched(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "asset.bin"
    original = b"\x00binary\r\ndata"
    target.write_bytes(original)

    run_line_endings(layout, fix=True)

    assert target.read_bytes() == original


def test_fix_is_idempotent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    target = layout.bundle_dir / "index.md"
    target.write_bytes(b"a\r\nb\r\n")

    run_line_endings(layout, fix=True)
    second = run_line_endings(layout)

    assert second.findings == ()
    assert target.read_bytes() == b"a\nb\n"


@pytest.mark.parametrize("fix", [False, True])
def test_clone_members_are_neither_listed_nor_repaired(tmp_path, monkeypatch, fix):
    layout = _layout(tmp_path)
    root = layout.bundle_dir
    originals = {
        "index.md": b"index\r\n",
        "notes.txt": b"notes\r\n",
        "work/bug/references/probe.md": b"probe\r\n",
        "repositories/demo/references/git/page.md": b"clone\r\n",
        "repositories/demo/references/git/notes.txt": b"clone asset\r\n",
        ".git/config": b"git\r\n",
    }
    for member, data in originals.items():
        path = root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    clone = root / "repositories/demo/references/git"
    scandir = os.scandir

    def guarded_scandir(path):
        candidate = Path(path) if not isinstance(path, int) else None
        if candidate is not None and candidate.is_relative_to(root):
            assert not candidate.is_relative_to(clone), "enumerated clone subtree"
        return scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)
    report = run_line_endings(layout, fix=fix)
    assert [f.member for f in report.findings] == ["index.md", "notes.txt", "work/bug/references/probe.md"]
    assert report.fixed is fix
    for member, data in originals.items():
        wanted = (
            data.replace(b"\r\n", b"\n")
            if fix and member in {"index.md", "notes.txt", "work/bug/references/probe.md"}
            else data
        )
        assert (root / member).read_bytes() == wanted


def test_mixed_bundle_keeps_raw_bytes_and_all_member_kinds(tmp_path):
    layout = _layout(tmp_path)
    originals = {
        "index.md": b"root\r\n",
        "log.md": b"log\r\n",
        "nested/index.md": b"nested\r\n",
        "nested/log.md": b"log\r\n",
        "malformed.md": b"---\r\nbad: [\r\n---\r\n",
        "invalid.md": b"\xfftext\r\n",
        "asset.txt": b"text\r\n",
        "binary.bin": b"\x00binary\r\n",
    }
    for member, data in originals.items():
        path = layout.bundle_dir / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    expected = ["asset.txt", "index.md", "invalid.md", "log.md", "malformed.md", "nested/index.md", "nested/log.md"]
    assert [f.member for f in run_line_endings(layout).findings] == expected
    for member, data in originals.items():
        assert (layout.bundle_dir / member).read_bytes() == data
    assert [f.member for f in run_line_endings(layout, fix=True).findings] == expected
    for member, data in originals.items():
        assert (layout.bundle_dir / member).read_bytes() == (
            data if member == "binary.bin" else data.replace(b"\r\n", b"\n")
        )
    assert run_line_endings(layout, fix=True).findings == ()
