"""A pinned directory, and the operations a transaction performs against it.

`transactions.py` anchors every live-tree operation to an open directory
descriptor so that a path swapped underneath the engine cannot redirect a
write.  On POSIX that anchor *is* an `int` and every operation is an
`os.*(..., dir_fd=...)` call.  Windows has no such primitive, so the `int`
cannot be the interface; this module makes the *handle* the interface and
leaves one implementation behind it.

Five things that are POSIX-only become methods here, which is the whole point
of the exercise: `flock`, the `renameat2`/`renameatx_np` NOREPLACE rename,
descriptor-to-path resolution, `st_dev`/`st_ino` identity, and the directory
`fsync` Windows documents as a no-op.

The platform is an *argument*, never read from `sys` except as a default --
the same shape `graph_works_core.util.platform.build_report(platform_name=...)`
has, which inherits it from okf-io's required `today=`.  That is what lets a
POSIX box assert the behaviour a Windows user would actually see, and it is
why neither selector arm is dead lines against a 95% coverage floor.

This module is the *only* place in `graph_works_core.workspace` that imports
`fcntl` or `ctypes`.  `transactions.py` imports neither, which is a hard
acceptance property of the item that created this module: on native Windows the
engine then imports successfully and fails at call time, which is strictly
better than dying at import.

It deliberately does not import `graph_works_core.util.platform`:
`workspace` is the bottom import-linter layer and `util` sits above it, so
citing `POSIX_ONLY_MODULES` here would invert the contract.  The structural
assertion that this module is the only POSIX-only importer lives in the tests,
where that import is legal.
"""

from __future__ import annotations

import fcntl
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from ctypes import CDLL, c_char_p, c_int, c_uint, get_errno
from pathlib import Path
from typing import Protocol, runtime_checkable

#: The tier name a POSIX anchor declares.  `graph_works_core.util.platform`'s
#: `DurabilityTierProvider` currently answers by proxy (is `fcntl` importable?);
#: once it asks the selector instead, this is the string it reports.  Retiring
#: that proxy is child 7's work, not this module's.
POSIX_STRONG_TIER = "posix-strong"


class UnsupportedAnchorPlatform(RuntimeError):
    """No anchor implementation is registered for the requested platform."""


def nofollow_flag() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def require_regular_file(descriptor: int, label: str) -> os.stat_result:
    """Reject a descriptor that is not a regular file.  From transactions.py:257."""
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} is not a regular file")
    return info


@runtime_checkable
class Anchor(Protocol):
    """A directory this process has pinned open.

    Every method is expressed in terms of *this* directory: a `name` argument
    is always a single path component resolved beneath the anchor, never a
    path.  Multi-component descent is `open_child` in a loop, which is what
    makes an unsafe ancestor detectable at the component that is unsafe.
    """

    # -- lifetime ---------------------------------------------------------
    def open_child(self, name: str) -> Anchor: ...
    def open_or_create_child(self, name: str) -> Anchor: ...
    def duplicate(self) -> Anchor: ...
    def close(self) -> None: ...

    # -- identity and metadata --------------------------------------------
    def self_stat(self) -> os.stat_result: ...
    def lstat(self, name: str) -> os.stat_result: ...
    def identity(self, name: str) -> tuple[int, int]: ...
    def assert_identity(self, path: Path, label: str) -> None: ...
    def assert_directory_identity(self, path: Path, label: str) -> None: ...
    def path(self) -> Path: ...
    def alias(self) -> Path: ...

    # -- entry operations --------------------------------------------------
    def mkdir(self, name: str, mode: int | None = None) -> None: ...
    def unlink(self, name: str) -> None: ...
    def rmdir(self, name: str) -> None: ...
    def listdir(self) -> list[str]: ...
    def readlink(self, name: str) -> str: ...
    def symlink(self, target: str, name: str) -> None: ...
    def link(self, source: str, name: str) -> None: ...
    def rename_noreplace(self, name: str, destination: Anchor, destination_name: str) -> None: ...
    def open_file(self, name: str, flags: int, mode: int = 0o777) -> int: ...
    def chmod_child_directory(self, name: str, mode: int) -> None: ...

    # -- durability ---------------------------------------------------------
    def fsync(self) -> None: ...

    # -- locking ------------------------------------------------------------
    def exclusive_lock(self) -> AbstractContextManager[None]: ...
    def lock_file(self, name: str, *, assert_identity: bool) -> AbstractContextManager[None]: ...


class _PosixAnchor:
    """An `Anchor` backed by an open POSIX directory descriptor.

    Behaviour is bit-identical to the inline `os.*(..., dir_fd=...)` calls this
    was extracted from; the descriptor is exposed as `descriptor` for the two
    remaining F3 sites that legitimately need the raw `int` (a file opened
    beneath this directory is a file descriptor, not an anchor, and is not
    wrapped -- see the item's scope decision).
    """

    __slots__ = ("descriptor",)

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor

    # -- lifetime ---------------------------------------------------------

    @classmethod
    def open_root(cls, root: Path) -> _PosixAnchor:
        """From transactions.py:430."""
        return cls(os.open(root, directory_flags()))

    @classmethod
    def open_absolute(cls, path: Path) -> _PosixAnchor:
        """Walk every component of an absolute path without following links.

        From transactions.py:318.
        """
        if not path.is_absolute():
            raise ValueError(f"cache directory must be absolute: {path}")
        descriptor = os.open(path.anchor, directory_flags())
        try:
            for part in path.parts[1:]:
                child = os.open(part, directory_flags(), dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        except Exception:
            os.close(descriptor)
            raise
        return cls(descriptor)

    def open_child(self, name: str) -> _PosixAnchor:
        return _PosixAnchor(os.open(name, directory_flags(), dir_fd=self.descriptor))

    def open_or_create_child(self, name: str) -> _PosixAnchor:
        """From transactions.py:334."""
        try:
            return _PosixAnchor(os.open(name, directory_flags(), dir_fd=self.descriptor))
        except FileNotFoundError:
            os.mkdir(name, 0o700, dir_fd=self.descriptor)
            self.fsync()
            child = _PosixAnchor(os.open(name, directory_flags(), dir_fd=self.descriptor))
            child.fsync()
            return child

    def duplicate(self) -> _PosixAnchor:
        return _PosixAnchor(os.dup(self.descriptor))

    def close(self) -> None:
        os.close(self.descriptor)

    # -- identity and metadata --------------------------------------------

    def self_stat(self) -> os.stat_result:
        return os.fstat(self.descriptor)

    def lstat(self, name: str) -> os.stat_result:
        return os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)

    def identity(self, name: str) -> tuple[int, int]:
        """From transactions.py:1403."""
        info = self.lstat(name)
        return info.st_dev, info.st_ino

    def assert_identity(self, path: Path, label: str) -> None:
        """This anchor still names *path*.  From transactions.py:545."""
        expected = self.self_stat()
        current = os.stat(path, follow_symlinks=False)  # noqa: PTH116 -- no-follow identity check is required
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise ValueError(f"{label} changed during mutation")

    def assert_directory_identity(self, path: Path, label: str) -> None:
        """As `assert_identity`, with the wording and OSError wrap of transactions.py:345."""
        expected = self.self_stat()
        try:
            current = os.stat(path, follow_symlinks=False)  # noqa: PTH116 -- identity check must not follow links
        except OSError as exc:
            raise ValueError(f"{label} directory changed during mutation: {exc}") from exc
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise ValueError(f"{label} directory changed during mutation")

    def path(self) -> Path:
        """The current namespace path of this descriptor.  From transactions.py:355."""
        if sys.platform == "darwin":
            raw = fcntl.fcntl(self.descriptor, 50, bytes(1024))
            return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
        return Path(f"/proc/self/fd/{self.descriptor}").readlink()

    def alias(self) -> Path:
        """A descriptor-rooted path suitable for resolving descendants.  From transactions.py:363."""
        if sys.platform == "darwin":
            return self.path()
        return Path(f"/proc/self/fd/{self.descriptor}")

    # -- entry operations --------------------------------------------------

    def mkdir(self, name: str, mode: int | None = None) -> None:
        if mode is None:
            os.mkdir(name, dir_fd=self.descriptor)
        else:
            os.mkdir(name, mode, dir_fd=self.descriptor)

    def unlink(self, name: str) -> None:
        os.unlink(name, dir_fd=self.descriptor)

    def rmdir(self, name: str) -> None:
        os.rmdir(name, dir_fd=self.descriptor)

    def listdir(self) -> list[str]:
        return os.listdir(self.descriptor)  # noqa: PTH208 -- descriptor-anchored listing has no Path equivalent

    def readlink(self, name: str) -> str:
        return os.readlink(name, dir_fd=self.descriptor)

    def symlink(self, target: str, name: str) -> None:
        os.symlink(target, name, dir_fd=self.descriptor)

    def link(self, source: str, name: str) -> None:
        """A hard link from *source* to *name*, both beneath this anchor.

        From transactions.py:1430 -- `follow_symlinks=False` is load-bearing.
        """
        os.link(source, name, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor, follow_symlinks=False)

    def rename_noreplace(self, name: str, destination: Anchor, destination_name: str) -> None:
        """Rename that fails rather than clobbering.  From transactions.py:1281-1297.

        There is no `os` binding for this: `renameat2` on Linux and
        `renameatx_np` on darwin are reached through `ctypes`, and their
        NOREPLACE flags do not share a value.
        """
        if not isinstance(destination, _PosixAnchor):
            raise TypeError("rename_noreplace requires a POSIX anchor destination")
        flag = 0x00000004 if sys.platform == "darwin" else 0x00000001
        library = CDLL(None, use_errno=True)
        function = library.renameatx_np if sys.platform == "darwin" else library.renameat2
        function.argtypes = (c_int, c_char_p, c_int, c_char_p, c_uint)
        function.restype = c_int
        result = function(
            self.descriptor,
            os.fsencode(name),
            destination.descriptor,
            os.fsencode(destination_name),
            flag,
        )
        if result != 0:
            error = get_errno()
            raise OSError(error, os.strerror(error), destination_name)

    def open_file(self, name: str, flags: int, mode: int = 0o777) -> int:
        """Open a *file* beneath this anchor.  Returns a plain descriptor.

        A file descriptor is not an anchor and is deliberately not wrapped:
        Windows opens files by path without difficulty, so wrapping it would
        add surface with no portability payoff.
        """
        return os.open(name, flags, mode, dir_fd=self.descriptor)

    def chmod_child_directory(self, name: str, mode: int) -> None:
        """From transactions.py:1026 -- chmod a child *directory* via its own descriptor."""
        descriptor = os.open(name, directory_flags(), dir_fd=self.descriptor)
        try:
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)

    # -- durability ---------------------------------------------------------

    def fsync(self) -> None:
        """Windows documents the directory fsync as a no-op, which is why this
        is a method rather than an inline `os.fsync`."""
        os.fsync(self.descriptor)

    # -- locking ------------------------------------------------------------

    @contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        """Lock this directory *descriptor*.  From transactions.py:303.

        Locking the descriptor rather than the path is what makes the lock
        immune to the path being swapped underneath -- which is why this does
        not route through any path-based lock helper.
        """
        if not stat.S_ISDIR(self.self_stat().st_mode):
            raise NotADirectoryError("bundle root descriptor is not a directory")
        locked = False
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
            locked = True
            yield
        finally:
            if locked:
                with suppress(OSError):
                    fcntl.flock(self.descriptor, fcntl.LOCK_UN)

    @contextmanager
    def lock_file(self, name: str, *, assert_identity: bool) -> Iterator[None]:
        """Lock a regular file beneath this anchor.  From transactions.py:280-299."""
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | nofollow_flag(),
            0o600,
            dir_fd=self.descriptor,
        )
        locked = False
        try:
            require_regular_file(descriptor, "executor lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            if assert_identity:
                _assert_regular_entry_identity(self, name, descriptor, "executor lock")
            yield
        finally:
            if locked:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _assert_regular_entry_identity(anchor: Anchor, name: str, descriptor: int, label: str) -> None:
    """From transactions.py:264.  Duplicated as a module private rather than
    imported from `transactions`, which would be a circular import; the
    `transactions` copy is a delegator onto this one (see that module)."""
    expected = require_regular_file(descriptor, label)
    try:
        current = anchor.lstat(name)
    except OSError as exc:
        raise ValueError(f"{label} changed during mutation: {exc}") from exc
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        raise ValueError(f"{label} changed during mutation")


@contextmanager
def lock_path(path: Path) -> Iterator[None]:
    """Lock a regular file named by absolute path.

    The path-addressed half of `transactions._executor_lock`, kept
    bit-identical to what that branch did inline (transactions.py:279).  It is
    deliberately *not* the descriptor-anchored `lock_file` above and it is
    deliberately not routed through the workspace-wide path lock: this branch
    runs before any anchor exists, and strengthening it to a descriptor walk
    here would be a behaviour change smuggled into a mechanical extraction.
    """
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | nofollow_flag(), 0o600)
    locked = False
    try:
        require_regular_file(descriptor, "executor lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _anchor_platform(platform_name: str | None) -> str:
    return sys.platform if platform_name is None else platform_name


def anchor_tier(platform_name: str | None = None) -> str:
    """The durability tier this platform's anchor declares.

    Raises rather than returning a placeholder: a tier name for an
    implementation that does not exist would be a claim nothing can honour.
    """
    resolved = _anchor_platform(platform_name)
    if resolved.startswith("win"):
        raise UnsupportedAnchorPlatform(
            f"no anchor implementation for platform {resolved!r}: _WindowsAnchor is not implemented yet"
        )
    return POSIX_STRONG_TIER


def open_anchor(path: Path, *, platform_name: str | None = None) -> Anchor:
    """Pin an existing directory as an anchor, selecting by platform."""
    anchor_tier(platform_name)
    return _PosixAnchor.open_root(path)


def open_absolute_anchor(path: Path, *, platform_name: str | None = None) -> Anchor:
    """As `open_anchor`, walking an absolute path component by component."""
    anchor_tier(platform_name)
    return _PosixAnchor.open_absolute(path)


__all__ = [
    "POSIX_STRONG_TIER",
    "Anchor",
    "UnsupportedAnchorPlatform",
    "anchor_tier",
    "directory_flags",
    "lock_path",
    "nofollow_flag",
    "open_absolute_anchor",
    "open_anchor",
    "require_regular_file",
]
