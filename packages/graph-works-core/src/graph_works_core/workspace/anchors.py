"""A pinned directory, and the operations a transaction performs against it.

`transactions.py` anchors every live-tree operation to an open directory
descriptor so that a path swapped underneath the engine cannot redirect a
write.  On POSIX that anchor *is* an `int` and every operation is an
`os.*(..., dir_fd=...)` call.  Windows has no such primitive, so the `int`
cannot be the interface; this module makes the *handle* the interface, with
two implementations behind it -- `_PosixAnchor` and `_WindowsAnchor` -- chosen
by the `open_anchor`/`open_absolute_anchor` selectors at `anchor_tier`'s
platform argument.

Five things that are POSIX-only become methods here, which is the whole point
of the exercise: `flock`, the `renameat2`/`renameatx_np` NOREPLACE rename,
descriptor-to-path resolution, `st_dev`/`st_ino` identity, and the directory
`fsync` Windows documents as a no-op.

The platform is an *argument*, never read from `sys` except as a default --
the same shape `graph_works_core.util.platform.build_report(platform_name=...)`
has, which inherits it from okf-io's required `today=`.  That is what lets a
POSIX box assert the behaviour a Windows user would actually see, and it is
why neither selector arm is dead lines against a 95% coverage floor.

This module is the *only* place in `graph_works_core.workspace` that touches
`fcntl` or `ctypes`, and it touches them only inside the function bodies that
need them -- never at module scope.  `transactions.py` imports this module at
module scope, so a module-scope `import fcntl` here would kill
`import transactions` on native Windows transitively, which is precisely the
failure the anchor seam exists to prevent.  `okf_ext.locking` documents the
same pattern for the same reason.

It deliberately does not import `graph_works_core.util.platform`:
`workspace` is the bottom import-linter layer and `util` sits above it, so
citing `POSIX_ONLY_MODULES` here would invert the contract.  The structural
assertion that this module is the only POSIX-only importer lives in the tests,
where that import is legal.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

#: The tier name a POSIX anchor declares.  `graph_works_core.util.platform`'s
#: `DurabilityTierProvider` currently answers by proxy (is `fcntl` importable?);
#: once it asks the selector instead, this is the string it reports.
POSIX_STRONG_TIER = "posix-strong"


class UnsupportedAnchorPlatform(RuntimeError):
    """No anchor implementation is registered for the requested platform."""


#: Win32 refuses these as filenames regardless of extension, at every path
#: component.  Read by the tier record and by ADR-0042.
RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{digit}" for digit in "123456789"} | {f"LPT{digit}" for digit in "123456789"}
)


@dataclass(frozen=True, slots=True)
class RefusedShape:
    """One member a tier declines, and what the user can do about it.

    A refusal is a declared contract statement of the tier, not an incident:
    `gw util platform` reports the refusable shapes as normal output, and
    ADR-0042 records them as the tier boundary.
    """

    member: str
    reason: str
    remedy: str


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

    # -- tier contract --------------------------------------------------------
    def refused_members(self, members: Sequence[str]) -> tuple[RefusedShape, ...]: ...


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
            import fcntl  # POSIX-only, imported at the point of use

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
        from ctypes import CDLL, c_char_p, c_int, c_uint, get_errno  # POSIX-only, imported at the point of use

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
            os.fsync(descriptor)
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
        import fcntl  # POSIX-only, imported at the point of use

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
        import fcntl  # POSIX-only, imported at the point of use

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

    # -- tier contract --------------------------------------------------------

    def refused_members(self, members: Sequence[str]) -> tuple[RefusedShape, ...]:
        """The strong tier refuses no plan shapes."""
        return ()


#: The tier name a path-revalidating anchor declares.  Paired with
#: `POSIX_STRONG_TIER`; ADR-0042 records what each one guarantees.
WINDOWS_REVALIDATED_TIER = "windows-revalidated"

#: The Win32 path length ceiling that the registry opt-in lifts.  Named so the
#: refusal message and ADR-0042 cite the same number.
MAX_PATH = 260


def long_paths_enabled() -> bool:
    """Whether this system lifts the 260-character `MAX_PATH` limit.

    Off Windows there is no limit to lift, so the answer is unconditionally
    yes -- and that is what makes the POSIX coverage pass able to construct a
    `_WindowsAnchor` at all.

    On Windows the registry value is the machine-wide opt-in.  The
    per-application manifest half is not readable from here, so a `False`
    can be a false negative for a manifested interpreter.  Refusing on a
    false negative is the safe direction: the alternative is a commit that
    fails partway through a deep member path, which is the failure this
    check exists to prevent.
    """
    if sys.platform != "win32":
        return True
    import winreg  # Windows-only, imported at the point of use

    try:  # pragma: no cover -- native Windows only
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False
    return bool(value)


class _WindowsAnchor:
    """An `Anchor` backed by a held, resolved directory path.

    `_PosixAnchor` pins a directory descriptor, which makes the anchored
    directory un-swappable for the descriptor's lifetime: a rename underneath
    the engine redirects the *path*, never the descriptor.  Windows cannot open
    a directory descriptor at all -- `os.O_DIRECTORY` does not exist there --
    so this tier holds the resolved path plus the `(st_dev, st_ino)` pair it
    had when the anchor was taken, and re-`lstat`s before every operation.

    That narrows the swap window.  It does not close it: between the
    re-validation and the syscall that follows, the path can still be
    replaced.  ADR-0042 records this as the tier's defining weakness, and
    records that the bundle-root lock is held for the whole mutation, so the
    exposure is to actors outside the transaction protocol -- a user, an
    editor, a sync client -- not to a second `gw`.
    """

    __slots__ = ("_identity", "_long_paths", "root")

    def __init__(self, root: Path, *, long_paths: bool | None = None) -> None:
        self.root = root.resolve(strict=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(f"anchor root is not a directory: {self.root}")
        self._identity = (info.st_dev, info.st_ino)
        self._long_paths = long_paths_enabled() if long_paths is None else long_paths
        if not self._long_paths:
            raise ValueError(
                f"the {WINDOWS_REVALIDATED_TIER} tier requires long path support: bundle "
                f"member paths routinely exceed the {MAX_PATH}-character MAX_PATH limit. "
                r"Set HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled "
                f"to 1 and restart, or run under WSL for the {POSIX_STRONG_TIER} tier."
            )

    # -- lifetime ---------------------------------------------------------

    @classmethod
    def open_root(cls, root: Path) -> _WindowsAnchor:
        return cls(root)

    @classmethod
    def open_absolute(cls, path: Path) -> _WindowsAnchor:
        """Walk every component of an absolute path without following links.

        The POSIX arm (`_PosixAnchor.open_absolute`) opens each component with
        `O_NOFOLLOW`; here each component is `lstat`ed and refused if it is a
        link, which is the same refusal without the kernel enforcing it.
        """
        if not path.is_absolute():
            raise ValueError(f"cache directory must be absolute: {path}")
        anchor = cls(Path(path.anchor))
        try:
            for part in path.parts[1:]:
                child = anchor.open_child(part)
                anchor.close()
                anchor = child
        except Exception:
            anchor.close()
            raise
        return anchor

    def open_child(self, name: str) -> _WindowsAnchor:
        """Descend one component, refusing a link or a non-directory.

        `FileNotFoundError` must propagate unchanged: `transactions._open_parent`
        catches it specifically to drive create-on-demand, and treats every
        other `OSError` as an unsafe ancestor.
        """
        candidate = self._revalidate() / name
        info = candidate.lstat()  # FileNotFoundError propagates
        if stat.S_ISLNK(info.st_mode):
            raise NotADirectoryError(f"refusing to follow a symlinked component: {name!r}")
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(f"not a directory: {name!r}")
        return _WindowsAnchor(candidate, long_paths=self._long_paths)

    def open_or_create_child(self, name: str) -> _WindowsAnchor:
        try:
            return self.open_child(name)
        except FileNotFoundError:
            (self._revalidate() / name).mkdir(mode=0o700)
            self.fsync()
            child = self.open_child(name)
            child.fsync()
            return child

    def duplicate(self) -> _WindowsAnchor:
        """A second handle on the same directory.

        The POSIX arm `dup`s a descriptor so the copy survives the original's
        close.  A held path has no such lifetime, so this re-anchors on the
        *same resolved path* and re-reads its identity -- which means a
        duplicate taken after a swap refuses, where the POSIX arm would carry
        the pre-swap directory forward.  That difference is a tier property,
        not a bug, and ADR-0042 names it.
        """
        return _WindowsAnchor(self.root, long_paths=self._long_paths)

    def close(self) -> None:
        """Nothing to release.  A path is not a descriptor.

        Deliberately not a no-op-by-omission: `transactions` closes anchors in
        `finally` blocks everywhere, and a missing `close` would be an
        `AttributeError` at exactly the moment an error is already being
        handled.
        """
        return None

    # -- identity and metadata --------------------------------------------

    def _revalidate(self) -> Path:
        """The held path, having confirmed it is still the same directory."""
        try:
            current = self.root.lstat()
        except OSError as exc:
            raise ValueError(f"anchored directory changed during mutation: {exc}") from exc
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != self._identity:
            raise ValueError("anchored directory changed during mutation")
        return self.root

    def self_stat(self) -> os.stat_result:
        return self._revalidate().lstat()

    def lstat(self, name: str) -> os.stat_result:
        return (self._revalidate() / name).lstat()

    def identity(self, name: str) -> tuple[int, int]:
        info = self.lstat(name)
        return info.st_dev, info.st_ino

    def assert_identity(self, path: Path, label: str) -> None:
        self._assert_named(path, f"{label} changed during mutation", wrap_oserror=False)

    def assert_directory_identity(self, path: Path, label: str) -> None:
        self._assert_named(path, f"{label} directory changed during mutation", wrap_oserror=True)

    def _assert_named(self, path: Path, message: str, *, wrap_oserror: bool) -> None:
        """The two `assert_*_identity` variants differ only in wording and in
        whether `OSError` is wrapped -- see `_PosixAnchor:195,205`, which is
        faithful to two distinct pre-refactor originals.  Collapsing them is a
        behavioural-change ticket of its own; this keeps the same two shapes."""
        expected = self._revalidate().lstat()
        try:
            current = path.lstat()
        except OSError as exc:
            if wrap_oserror:
                raise ValueError(f"{message}: {exc}") from exc
            raise
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise ValueError(message)

    def path(self) -> Path:
        """The anchored directory's path.

        On POSIX this is a descriptor-to-path resolution; here the path IS the
        anchor, so it is returned directly -- which is why this tier's
        `path()` cannot go stale in the way a resolved descriptor path can.
        """
        return self._revalidate()

    def alias(self) -> Path:
        """A path suitable for resolving descendants.

        `_PosixAnchor.alias` returns `/proc/self/fd/N` on Linux so a descendant
        resolves through the pinned descriptor.  There is no Windows analogue
        and none is needed: the held path already is the descendant-resolving
        root.  `transactions._projected_symlink_is_internal` is this method's
        only consumer -- see Task 10.
        """
        return self._revalidate()

    # -- entry operations --------------------------------------------------

    def mkdir(self, name: str, mode: int | None = None) -> None:
        target = self._revalidate() / name
        if mode is None:
            target.mkdir()
        else:
            target.mkdir(mode=mode)

    def unlink(self, name: str) -> None:
        (self._revalidate() / name).unlink()

    def rmdir(self, name: str) -> None:
        (self._revalidate() / name).rmdir()

    def listdir(self) -> list[str]:
        return os.listdir(self._revalidate())  # noqa: PTH208 -- parity with the POSIX arm

    def readlink(self, name: str) -> str:
        return str((self._revalidate() / name).readlink())

    def symlink(self, target: str, name: str) -> None:
        """Always refuses.  D-002's backstop.

        Creating a symlink on Windows needs Developer Mode or
        SeCreateSymbolicLinkPrivilege.  `transactions._copy_backup_to_live`
        (`:1029`, `:1065`) creates symlinks on the ROLLBACK path -- the one
        path that must not fail -- so an unprivileged failure there would
        leave a half-restored bundle.  Task 7's preflight makes this
        unreachable for planned members; this stays as the backstop because
        "unreachable" is a property of the preflight, not of this class.
        """
        raise ValueError(
            f"the {WINDOWS_REVALIDATED_TIER} tier cannot create symlinks "
            f"({name!r} -> {target!r}): enable Developer Mode or grant "
            "SeCreateSymbolicLinkPrivilege, or run under WSL for the "
            f"{POSIX_STRONG_TIER} tier"
        )

    def link(self, source: str, name: str) -> None:
        """A hard link, both names beneath this anchor.

        `CreateHardLinkW` on Windows, and it fails outright on FAT32, exFAT, a
        network share, or across volumes.  That is a filesystem requirement of
        the whole tier, not a degradation: `transactions._commit_write`
        (`:1380`, `:1417`) installs EVERY file this way.
        """
        directory = self._revalidate()
        os.link(directory / source, directory / name, follow_symlinks=False)

    def rename_noreplace(self, name: str, destination: Anchor, destination_name: str) -> None:
        """Fail-if-exists rename.

        `os.rename` IS no-replace on Windows -- it is `MoveFileExW` without
        `MOVEFILE_REPLACE_EXISTING`, which is exactly the semantic
        `transactions._take_custody` (`:1291`) and `_restore_custody_or_raise`
        (`:1325`) depend on.  It is NOT no-replace on POSIX, where it clobbers
        silently.  So when this tier runs on POSIX for coverage, the
        destination is probed first.

        The probe is itself a TOCTOU window and exists ONLY to make the POSIX
        pass faithful to the Windows semantic; on Windows the kernel enforces
        it and the probe is skipped.  Do not "simplify" this into an
        unconditional probe -- that would replace a kernel guarantee with a
        check on the platform where the guarantee is real.
        """
        if not isinstance(destination, _WindowsAnchor):
            raise TypeError("rename_noreplace requires a path-revalidated anchor destination")
        source_directory = self._revalidate()
        destination_directory = destination._revalidate()
        target = destination_directory / destination_name
        if sys.platform != "win32" and target.exists():
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(target))
        (source_directory / name).rename(target)

    def open_file(self, name: str, flags: int, mode: int = 0o777) -> int:
        """Open a *file* beneath this anchor.  Returns a plain descriptor.

        Windows opens files by path without difficulty, which is why the
        landed `Anchor` never wrapped file descriptors.  `O_NOFOLLOW` is
        already absent from `flags` here via `nofollow_flag()` -- see Task 9.
        """
        return os.open(self._revalidate() / name, flags, mode)

    def chmod_child_directory(self, name: str, mode: int) -> None:
        """chmod a child directory.

        `_PosixAnchor` opens the child, `os.fchmod`es it and `os.fsync`es the
        descriptor.  `os.fchmod` does not exist on Windows and a directory
        cannot be fsynced there, so this is a path chmod with no flush -- a
        sixth loss the design's table did not enumerate, recorded in ADR-0042.
        On NTFS `chmod` honours only the read-only bit; the mode bits the
        engine sets are preserved on the POSIX coverage pass, which is where
        the mode assertions actually run.
        """
        (self._revalidate() / name).chmod(mode)

    # -- durability ---------------------------------------------------------

    def fsync(self) -> None:
        """L2: a directory fsync is impossible on Windows.

        Returning `None` is the tier's declared contract, not an oversight.
        Rename and link durability on this tier is whatever the filesystem
        gives unprompted; ADR-0042 says so, and the manual verification run
        is what measures it.
        """
        return None

    # -- locking ------------------------------------------------------------

    def exclusive_lock(self) -> AbstractContextManager[None]:
        """Task 8 implements Windows directory locking for real.

        `Anchor` is `runtime_checkable`, which checks attribute presence, not
        behaviour, so a stub -- not an omission -- is what keeps
        `isinstance(anchor, Anchor)` structurally true in the meantime.  It
        raises rather than no-ops so a caller that reaches it before Task 8
        lands fails loudly instead of running unlocked.
        """
        raise NotImplementedError("Task 8 implements Windows directory locking")

    def lock_file(self, name: str, *, assert_identity: bool) -> AbstractContextManager[None]:
        """As `exclusive_lock`: a presence-only stub, replaced for real in Task 8."""
        raise NotImplementedError("Task 8 implements Windows file locking")

    # -- tier contract --------------------------------------------------------

    def refused_members(self, members: Sequence[str]) -> tuple[RefusedShape, ...]:
        """Every member of *members* this tier cannot honour.

        Called from `transactions._preflight` before the first live effect,
        because the symlink case is on the ROLLBACK path
        (`_copy_backup_to_live:1029,1065`) -- the one path that must not fail.
        Discovering it mid-rollback would leave a half-restored bundle.

        The scan is over the *planned* members only, not the whole bundle.  A
        symlink deeper inside a targeted directory subtree is caught instead
        by `symlink()` raising during snapshot creation
        (`_copy_live_entry:849`), which still runs before any live mutation --
        so the guarantee holds either way.  Preflight makes it explicit rather
        than incidental.
        """
        refusals: list[RefusedShape] = []
        root = self._revalidate()
        for member in members:
            parts = PurePosixPath(member).parts
            named = _refused_name(member, parts)
            if named is not None:
                refusals.append(named)
                continue
            if root.joinpath(*parts).is_symlink():
                refusals.append(
                    RefusedShape(
                        member,
                        "is a symlink",
                        "enable Developer Mode or grant SeCreateSymbolicLinkPrivilege, "
                        f"or run under WSL for the {POSIX_STRONG_TIER} tier",
                    )
                )
        return tuple(sorted(refusals, key=lambda item: item.member))


def _refused_name(member: str, parts: Sequence[str]) -> RefusedShape | None:
    """The name-shape half of the scan: reserved devices, trailing dots/spaces.

    Both are checked at EVERY component, not just the last: `CON/page.md` is
    as unopenable as `work/CON.md`, and `work/trailing./child.md` round-trips
    to `work/trailing/child.md`, silently violating member identity.
    """
    for part in parts:
        if part.split(".", 1)[0].upper() in RESERVED_DEVICE_NAMES:
            return RefusedShape(member, f"{part!r} is a reserved device name", "rename the member")
        if part != part.rstrip(". "):
            return RefusedShape(
                member,
                f"{part!r} ends in a trailing dot or space, which Win32 strips silently",
                "rename the member",
            )
    return None


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
    import fcntl  # POSIX-only, imported at the point of use

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

    Two tiers, both implemented, both declared in ADR-0042: `posix-strong`
    pins directory descriptors, `windows-revalidated` re-`lstat`s a held path.
    A platform name that does not match a Windows shape defaults to the
    POSIX-strong tier, since every non-Windows platform this engine runs on
    is POSIX-shaped.
    """
    resolved = _anchor_platform(platform_name)
    if _is_windows(resolved):
        return WINDOWS_REVALIDATED_TIER
    return POSIX_STRONG_TIER


def _anchor_class(platform_name: str | None) -> type[_PosixAnchor] | type[_WindowsAnchor]:
    return _WindowsAnchor if _is_windows(_anchor_platform(platform_name)) else _PosixAnchor


def _is_windows(platform_name: str) -> bool:
    """`sys.platform` is `win32` on every 32- and 64-bit CPython for Windows;
    `cygwin` is matched too because it is the other name a Windows Python
    reports, and misrouting it to the descriptor arm would fail obscurely."""
    return platform_name.startswith("win") or platform_name == "cygwin"


def open_anchor(path: Path, *, platform_name: str | None = None) -> Anchor:
    """Pin an existing directory as an anchor, selecting by platform."""
    return _anchor_class(platform_name).open_root(path)


def open_absolute_anchor(path: Path, *, platform_name: str | None = None) -> Anchor:
    """As `open_anchor`, walking an absolute path component by component."""
    return _anchor_class(platform_name).open_absolute(path)


__all__ = [
    "MAX_PATH",
    "POSIX_STRONG_TIER",
    "RESERVED_DEVICE_NAMES",
    "WINDOWS_REVALIDATED_TIER",
    "Anchor",
    "RefusedShape",
    "UnsupportedAnchorPlatform",
    "anchor_tier",
    "directory_flags",
    "lock_path",
    "long_paths_enabled",
    "nofollow_flag",
    "open_absolute_anchor",
    "open_anchor",
    "require_regular_file",
]
