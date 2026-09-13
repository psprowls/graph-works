"""The native directory primitive must never replace even an empty foreign directory."""

import pytest
from plugin_fork_io.machine import LocalFileSystem


def test_directory_promotion_preserves_identity_and_refuses_existing_directory(tmp_path):
    fs = LocalFileSystem()
    stage = tmp_path / "stage"
    stage.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    foreign_identity = fs.identity(foreign)
    identity = fs.identity(stage)
    with pytest.raises(FileExistsError):
        fs.rename_directory(stage, foreign)
    assert fs.identity(foreign) == foreign_identity
    assert fs.identity(stage) == identity
    destination = tmp_path / "destination"
    fs.rename_directory(stage, destination)
    assert not stage.exists()
    assert fs.identity(destination).inode == identity.inode


@pytest.mark.parametrize("platform", ["freebsd", "linux", "darwin"])
def test_unavailable_exclusive_rename_leaves_both_paths_untouched(tmp_path, monkeypatch, platform):
    import plugin_fork_io.machine as machine

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    before = LocalFileSystem().identity(destination)
    monkeypatch.setattr(machine.sys, "platform", platform)
    monkeypatch.setattr(machine.ctypes, "CDLL", lambda *args, **kwargs: object())
    with pytest.raises(OSError, match="unavailable"):
        LocalFileSystem().rename_directory(source, destination)
    assert source.is_dir() and LocalFileSystem().identity(destination) == before


def test_linux_native_call_uses_no_replace_and_preserves_kernel_refusal(tmp_path, monkeypatch):
    import errno

    import plugin_fork_io.machine as machine

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    calls = []

    class Rename:
        def __call__(self, *args):
            calls.append(args)
            return -1

    class Libc:
        renameat2 = Rename()

    monkeypatch.setattr(machine.sys, "platform", "linux")
    monkeypatch.setattr(machine.ctypes, "CDLL", lambda *args, **kwargs: Libc())
    monkeypatch.setattr(machine.ctypes, "get_errno", lambda: errno.EEXIST)
    with pytest.raises(FileExistsError):
        LocalFileSystem().rename_directory(source, destination)
    assert calls == [(-100, bytes(source), -100, bytes(destination), 1)]
    assert source.is_dir() and destination.is_dir()


def test_windows_rename_refusal_is_not_retried_with_replace(tmp_path, monkeypatch):
    import plugin_fork_io.machine as machine

    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    def refuse(*args):
        raise FileExistsError("Windows existing destination")

    monkeypatch.setattr(machine.sys, "platform", "win32")
    monkeypatch.setattr(machine.os, "rename", refuse)
    with pytest.raises(FileExistsError):
        LocalFileSystem().rename_directory(source, destination)
    assert source.is_dir() and destination.is_dir()
