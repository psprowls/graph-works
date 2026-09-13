from __future__ import annotations

import ctypes
import errno
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .records import AncestorIdentity, Snapshot


class GitRunner(Protocol):
    def acquire(self, locator: str, revision: str) -> Snapshot: ...

    def merge_text(self, base: bytes, local: bytes, incoming: bytes) -> tuple[bytes, bool]: ...


class FileSystem(Protocol):
    def skill_files(self, root: Path) -> tuple[Path, ...]: ...

    def read_bytes(self, path: Path) -> bytes: ...

    def mode(self, path: Path) -> int: ...

    def readlink(self, path: Path) -> str: ...

    def children(self, path: Path) -> tuple[Path, ...]: ...

    def identity(self, path: Path) -> AncestorIdentity: ...

    def write_exclusive(self, path: Path, content: bytes) -> None: ...

    def chmod(self, path: Path, mode: int) -> None: ...

    def sync_file(self, path: Path) -> None: ...

    def sync_directory(self, path: Path) -> None: ...

    def replace(self, source: Path, destination: Path, *, absent: bool = False) -> None: ...

    def rename_directory(self, source: Path, destination: Path) -> None: ...

    def link(self, target: str, destination: Path) -> None: ...


@dataclass(frozen=True)
class LocalFileSystem:
    def skill_files(self, root: Path) -> tuple[Path, ...]:
        return tuple(sorted(root.rglob("SKILL.md")))

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def mode(self, path: Path) -> int:
        return os.lstat(path).st_mode

    def readlink(self, path: Path) -> str:
        return os.readlink(path)  # noqa: PTH115 - preserve raw link text through the machine boundary

    def children(self, path: Path) -> tuple[Path, ...]:
        return tuple(sorted(path.iterdir()))

    def identity(self, path: Path) -> AncestorIdentity:
        value = os.lstat(path)
        return AncestorIdentity(str(path), value.st_dev, value.st_ino, value.st_mode)

    def write_exclusive(self, path: Path, content: bytes) -> None:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def sync_file(self, path: Path) -> None:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    def sync_directory(self, path: Path) -> None:
        # Python exposes no directory fsync on native Windows.
        if sys.platform == "win32":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def replace(self, source: Path, destination: Path, *, absent: bool = False) -> None:
        if absent:
            os.link(source, destination, follow_symlinks=False)
            source.unlink()
        else:
            os.replace(source, destination)  # noqa: PTH105 - injected native promotion boundary
        self.sync_directory(destination.parent)

    def rename_directory(self, source: Path, destination: Path) -> None:
        """Move a staged directory exclusively, retaining its journaled inode.

        POSIX rename may replace an empty foreign directory, so it is never a
        fallback. Unsupported kernels/filesystems leave the staged evidence.
        """
        if sys.platform == "win32":
            os.rename(source, destination)  # noqa: PTH104 - Windows refuses existing destinations
        else:
            libc = ctypes.CDLL(None, use_errno=True)
            source_bytes, destination_bytes = os.fsencode(source), os.fsencode(destination)
            if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
                rename = libc.renamex_np
                rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
                rename.restype = ctypes.c_int
                result = rename(source_bytes, destination_bytes, 4)  # RENAME_EXCL
            elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
                rename = libc.renameat2
                rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
                rename.restype = ctypes.c_int
                result = rename(-100, source_bytes, -100, destination_bytes, 1)  # AT_FDCWD, RENAME_NOREPLACE
            else:
                raise OSError(errno.ENOTSUP, "Exclusive directory promotion is unavailable", str(destination))
            if result != 0:
                raise OSError(ctypes.get_errno(), "Exclusive directory promotion refused", str(destination))
        self.sync_directory(destination.parent)
        if source.parent != destination.parent:
            self.sync_directory(source.parent)

    def link(self, target: str, destination: Path) -> None:
        os.symlink(target, destination, target_is_directory=(destination.parent / target).is_dir())  # noqa: PTH211 - injected native link boundary
        self.sync_directory(destination.parent)

    def chmod(self, path: Path, mode: int) -> None:
        os.chmod(path, mode)  # noqa: PTH101 - injected native permission boundary


@dataclass(frozen=True)
class Services:
    filesystem: FileSystem
    clock: Callable[[], datetime]
    new_id: Callable[[], str]
    git: GitRunner | None = None
    platform: str = sys.platform

    @classmethod
    def local(cls) -> Services:
        from .git import LocalGitRunner

        return cls(LocalFileSystem(), lambda: datetime.now(UTC), lambda: str(uuid4()), LocalGitRunner())


def git_environment(home: Path) -> dict[str, str]:
    """Allow only executable/OS essentials; drop Git, shell and loader overrides."""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(home / "config"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file:https:ssh",
            "GIT_ATTR_NOSYSTEM": "1",
            "LC_ALL": "C",
        }
    )
    return env


def settings_environment() -> dict[str, str]:
    """Only settings-related environment is exposed at the CLI edge."""
    return {key: os.environ[key] for key in ("APPDATA", "XDG_CONFIG_HOME") if key in os.environ}
