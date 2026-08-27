"""Unit tests for `_WindowsAnchor`, the path-revalidating anchor tier.

These are the tier's unit tests; the engine-level coverage comes from
Task 11.  Every test constructs `_WindowsAnchor` directly rather than going
through `open_anchor`/`anchor_tier`, since Task 5 has not wired the selectors
to return this tier yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.workspace import anchors


def _anchor(root: Path) -> anchors._WindowsAnchor:
    return anchors._WindowsAnchor(root, long_paths_enabled=True)


def test_windows_anchor_satisfies_the_anchor_protocol(tmp_path: Path) -> None:
    """`Anchor` is `runtime_checkable`, so this is a real structural check."""
    anchor = _anchor(tmp_path)
    try:
        assert isinstance(anchor, anchors.Anchor)
    finally:
        anchor.close()


def test_windows_anchor_holds_a_resolved_root(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    anchor = _anchor(tmp_path / "link")
    try:
        assert anchor.root == (tmp_path / "real").resolve()
        assert anchor.path() == anchor.root
        assert anchor.alias() == anchor.root
    finally:
        anchor.close()


def test_windows_anchor_refuses_a_root_that_is_not_a_directory(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    target.write_text("x\n", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        anchors._WindowsAnchor(target, long_paths_enabled=True)


def test_open_child_refuses_a_symlinked_component(tmp_path: Path) -> None:
    """The replacement for `O_NOFOLLOW`, which vanishes on Windows (L3)."""
    (tmp_path / "escape").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "escape", target_is_directory=True)
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(OSError):
            anchor.open_child("link")
    finally:
        anchor.close()


def test_open_child_raises_file_not_found_for_a_missing_component(tmp_path: Path) -> None:
    """`transactions._open_parent:436` catches FileNotFoundError specifically
    and treats every other OSError as an unsafe ancestor.  The Windows arm
    must preserve that distinction or the create-on-demand path breaks."""
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(FileNotFoundError):
            anchor.open_child("absent")
    finally:
        anchor.close()


def test_open_or_create_child_creates_then_reopens(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    try:
        child = anchor.open_or_create_child("lane")
        try:
            assert child.root == (tmp_path / "lane").resolve()
            assert (tmp_path / "lane").is_dir()
        finally:
            child.close()
        again = anchor.open_or_create_child("lane")
        again.close()
    finally:
        anchor.close()


def test_re_validation_catches_a_directory_swapped_under_the_anchor(tmp_path: Path) -> None:
    """L1: re-validation narrows the TOCTOU window; it must at least catch a
    swap between the anchor being taken and the next operation."""
    (tmp_path / "lane").mkdir()
    anchor = _anchor(tmp_path)
    try:
        child = anchor.open_child("lane")
        try:
            (tmp_path / "lane").rmdir()
            (tmp_path / "lane").mkdir()  # same path, new inode
            with pytest.raises(ValueError, match="changed during mutation"):
                child.listdir()
        finally:
            child.close()
    finally:
        anchor.close()


def test_rename_noreplace_refuses_to_clobber(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a\n", encoding="utf-8")
    (tmp_path / "b").write_text("b\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(FileExistsError):
            anchor.rename_noreplace("a", anchor, "b")
        assert (tmp_path / "a").read_text(encoding="utf-8") == "a\n"
        assert (tmp_path / "b").read_text(encoding="utf-8") == "b\n"
    finally:
        anchor.close()


def test_rename_noreplace_across_two_anchors(tmp_path: Path) -> None:
    (tmp_path / "from").mkdir()
    (tmp_path / "to").mkdir()
    (tmp_path / "from" / "page.md").write_text("x\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        source = anchor.open_child("from")
        destination = anchor.open_child("to")
        try:
            source.rename_noreplace("page.md", destination, "page.md")
            assert (tmp_path / "to" / "page.md").is_file()
        finally:
            source.close()
            destination.close()
    finally:
        anchor.close()


def test_fsync_is_a_documented_no_op(tmp_path: Path) -> None:
    """L2: directory fsync is impossible on Windows and this is where that
    fact lives.  `None` is the contract, not an accident."""
    anchor = _anchor(tmp_path)
    try:
        assert anchor.fsync() is None
    finally:
        anchor.close()


def test_symlink_raises_with_the_remedy_named(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    try:
        with pytest.raises(ValueError) as caught:
            anchor.symlink("target", "link")
        message = str(caught.value)
        assert "Developer Mode" in message
        assert "SeCreateSymbolicLinkPrivilege" in message
    finally:
        anchor.close()


def test_identity_and_lstat_agree(tmp_path: Path) -> None:
    (tmp_path / "page.md").write_text("x\n", encoding="utf-8")
    anchor = _anchor(tmp_path)
    try:
        info = anchor.lstat("page.md")
        assert anchor.identity("page.md") == (info.st_dev, info.st_ino)
    finally:
        anchor.close()


def test_open_absolute_walks_every_component(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    anchor = anchors._WindowsAnchor.open_absolute(nested)
    try:
        assert anchor.root == nested.resolve()
    finally:
        anchor.close()


def test_open_absolute_refuses_a_relative_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        anchors._WindowsAnchor.open_absolute(Path("relative/path"))
