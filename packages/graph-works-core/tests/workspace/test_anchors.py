"""Unit tests for the directory-anchor seam.

Every selector test injects `platform_name` explicitly rather than
monkeypatching `sys.platform`, which is the whole point of the argument: a
POSIX box asserts the Windows arm without a Windows box, and neither arm is
dead lines against the 95% floor.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from graph_works_core.workspace import anchors


def test_open_anchor_selects_the_posix_implementation_and_reports_its_tier(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path, platform_name="linux")
    try:
        assert isinstance(anchor, anchors._PosixAnchor)
        assert anchors.anchor_tier("linux") == anchors.POSIX_STRONG_TIER
        assert anchors.anchor_tier("darwin") == anchors.POSIX_STRONG_TIER
    finally:
        anchor.close()


def test_open_anchor_on_windows_names_the_implementation_that_does_not_exist_yet(tmp_path: Path) -> None:
    with pytest.raises(anchors.UnsupportedAnchorPlatform, match="_WindowsAnchor"):
        anchors.open_anchor(tmp_path, platform_name="win32")
    with pytest.raises(anchors.UnsupportedAnchorPlatform, match="_WindowsAnchor"):
        anchors.open_absolute_anchor(tmp_path, platform_name="win32")
    with pytest.raises(anchors.UnsupportedAnchorPlatform, match="_WindowsAnchor"):
        anchors.anchor_tier("win32")


def test_posix_anchor_opens_creates_and_lists_children(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        child = anchor.open_or_create_child("created")
        try:
            assert stat.S_ISDIR(child.self_stat().st_mode)
        finally:
            child.close()
        reopened = anchor.open_or_create_child("created")
        reopened.close()
        anchor.mkdir("plain")
        anchor.mkdir("moded", 0o700)
        assert sorted(anchor.listdir()) == ["created", "moded", "plain"]
        anchor.rmdir("plain")
        assert "plain" not in anchor.listdir()
    finally:
        anchor.close()


def test_posix_anchor_entry_operations_cover_links_unlink_and_identity(tmp_path: Path) -> None:
    (tmp_path / "payload").write_text("bytes", encoding="utf-8")
    anchor = anchors.open_anchor(tmp_path)
    try:
        anchor.symlink("payload", "link")
        assert anchor.readlink("link") == "payload"
        anchor.link("payload", "hard")
        assert anchor.identity("hard") == anchor.identity("payload")
        assert not stat.S_ISLNK(anchor.lstat("payload").st_mode)
        assert stat.S_ISLNK(anchor.lstat("link").st_mode)
        anchor.unlink("hard")
        with pytest.raises(FileNotFoundError):
            anchor.lstat("hard")
        anchor.fsync()
    finally:
        anchor.close()


def test_posix_anchor_open_file_returns_a_plain_descriptor(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        descriptor = anchor.open_file("fresh", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            assert isinstance(descriptor, int)
            assert stat.S_ISREG(anchors.require_regular_file(descriptor, "fresh").st_mode)
        finally:
            os.close(descriptor)
        with pytest.raises(FileExistsError):
            anchor.open_file("fresh", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    finally:
        anchor.close()


def test_posix_anchor_rename_noreplace_refuses_to_clobber(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("one", encoding="utf-8")
    (tmp_path / "occupied").write_text("two", encoding="utf-8")
    anchor = anchors.open_anchor(tmp_path)
    try:
        with pytest.raises(OSError):
            anchor.rename_noreplace("source", anchor, "occupied")
        assert (tmp_path / "occupied").read_text(encoding="utf-8") == "two"
        anchor.rename_noreplace("source", anchor, "vacant")
        assert (tmp_path / "vacant").read_text(encoding="utf-8") == "one"
        assert not (tmp_path / "source").exists()
    finally:
        anchor.close()


def test_posix_anchor_duplicate_is_independently_closable(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    duplicate = anchor.duplicate()
    duplicate.close()
    try:
        assert stat.S_ISDIR(anchor.self_stat().st_mode)
    finally:
        anchor.close()


def test_posix_anchor_path_and_alias_resolve_to_the_pinned_directory(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        assert anchor.path() == tmp_path.resolve()
        assert (anchor.alias() / "..").exists()
    finally:
        anchor.close()


def test_posix_anchor_identity_assertions_detect_a_swapped_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    anchor = anchors.open_anchor(target)
    try:
        anchor.assert_identity(target, "target")
        anchor.assert_directory_identity(target, "target")
        target.rename(tmp_path / "moved")
        with pytest.raises(ValueError, match="directory changed during mutation"):
            anchor.assert_directory_identity(target, "target")
        (tmp_path / "target").mkdir()
        with pytest.raises(ValueError, match="changed during mutation"):
            anchor.assert_identity(target, "target")
    finally:
        anchor.close()


def test_open_absolute_anchor_rejects_a_relative_path_and_walks_an_absolute_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        anchors.open_absolute_anchor(Path("relative"))
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    anchor = anchors.open_absolute_anchor(nested)
    try:
        assert anchor.path() == nested.resolve()
    finally:
        anchor.close()
    with pytest.raises(FileNotFoundError):
        anchors.open_absolute_anchor(tmp_path / "absent")


def test_exclusive_lock_refuses_a_descriptor_that_is_not_a_directory(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")
    anchor = anchors._PosixAnchor(os.open(file, os.O_RDONLY))
    try:
        with pytest.raises(NotADirectoryError), anchor.exclusive_lock():
            pass
    finally:
        anchor.close()


def test_exclusive_lock_and_lock_file_round_trip(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    try:
        with anchor.exclusive_lock():
            pass
        with anchor.lock_file("executor.lock", assert_identity=True):
            assert (tmp_path / "executor.lock").is_file()
        with anchor.lock_file("executor.lock", assert_identity=False):
            pass
    finally:
        anchor.close()


def test_lock_file_detects_a_lock_swapped_between_open_and_lock(tmp_path: Path) -> None:
    anchor = anchors.open_anchor(tmp_path)
    original = anchors.fcntl.flock

    def swap_then_lock(descriptor: int, operation: int) -> None:
        if operation == anchors.fcntl.LOCK_EX and (tmp_path / "executor.lock").exists():
            (tmp_path / "executor.lock").unlink()
            (tmp_path / "executor.lock").write_text("", encoding="utf-8")
        original(descriptor, operation)

    try:
        anchors.fcntl.flock = swap_then_lock  # type: ignore[assignment]
        with (
            pytest.raises(ValueError, match="changed during mutation"),
            anchor.lock_file("executor.lock", assert_identity=True),
        ):
            pass
    finally:
        anchors.fcntl.flock = original  # type: ignore[assignment]
        anchor.close()


def test_lock_path_locks_an_absolute_regular_file(tmp_path: Path) -> None:
    with anchors.lock_path(tmp_path / "executor.lock"):
        assert (tmp_path / "executor.lock").is_file()
    (tmp_path / "directory").mkdir()
    with pytest.raises((ValueError, IsADirectoryError, OSError)), anchors.lock_path(tmp_path / "directory"):
        pass


def test_require_regular_file_rejects_a_directory(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            anchors.require_regular_file(descriptor, "directory")
    finally:
        os.close(descriptor)
