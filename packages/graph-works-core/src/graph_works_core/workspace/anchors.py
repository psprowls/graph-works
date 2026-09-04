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
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn, Protocol, runtime_checkable

from okf_ext.locking import locked

#: The tier name a POSIX anchor declares.  `graph_works_core.util.platform`'s
#: `DurabilityTierProvider` asks `durability_tier()` for this string rather
#: than restating it.
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


#: Whether the kernel will refuse to follow a symlink on open.  Stated rather
#: than inferred at each call site: on Windows this is False and the flag
#: silently becomes 0, so the protection VANISHES with no diagnostic.  Naming
#: it is what lets ADR-0042 record the loss and `gw util platform` report it.
NOFOLLOW_AVAILABLE = hasattr(os, "O_NOFOLLOW")

#: Whether `Anchor.fsync()` actually flushes.  False on Windows (L2): rename
#: and link durability is not flushable there.
DIRECTORY_FSYNC_HONORED = sys.platform != "win32"


def _posix_only(symbol: str) -> NoReturn:
    """Refuse a POSIX-only primitive on a host that does not have it.

    Two jobs in one line. At runtime it turns an `AttributeError` naming a
    stdlib module into a refusal naming the *tier mistake* that produced it.
    At type-check time the `NoReturn` is what makes every statement below the
    call unreachable under `mypy --platform win32`, so the POSIX branch is
    skipped rather than reported -- the same effect a literal `raise` has, and
    the mechanism this whole module now leans on.
    """
    raise UnsupportedAnchorPlatform(f"{symbol} is POSIX-only and does not exist on win32")


def _flock_exclusive(descriptor: int) -> None:
    """`fcntl.flock(LOCK_EX)`.  The import stays inside the body: a module-scope
    `import fcntl` here kills `import transactions` on native Windows before
    argv is parsed, and four tests assert that structurally."""
    if sys.platform == "win32":
        _posix_only("fcntl.flock")
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _flock_release(descriptor: int) -> None:
    """`fcntl.flock(LOCK_UN)`.  See `_flock_exclusive`."""
    if sys.platform == "win32":
        _posix_only("fcntl.flock")
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def set_mode(descriptor: int, path: Path | Callable[[], Path], mode: int) -> None:
    """chmod through *descriptor* where the platform has `os.fchmod`, by *path* where it does not.

    A capability branch, not a platform branch, and the difference matters.
    `os.fchmod` gained Windows support in **CPython 3.13**; this workspace
    declares `requires-python = ">=3.12"`, so on the floor these calls raise
    `AttributeError` on Windows and work by accident of a newer local
    interpreter everywhere else.  Raising `mypy`'s `python_version` to 3.13
    would clear the error and leave the crash, which is why design decision
    D-3 rejects it.

    The fallback loses the descriptor's guarantee that the mode lands on the
    object the caller opened rather than on whatever holds that name now.
    That is recorded in ADR-0042 as L7.  On NTFS `chmod` honours only the
    read-only bit in any case, so the mode assertions this engine makes are
    meaningful on the POSIX pass only.

    *path* may be a `Path` or a zero-argument callable producing one. Some
    callers (`transactions._copy_backup_file`, `transactions._live_temporary`)
    can only name the path via `Anchor.resolve_descendant`, which re-`lstat`s
    (and, on darwin, makes an `fcntl` call) and can raise. Accepting a
    callable lets those callers defer that work into this function, so it
    runs only on the branch that actually needs the path -- never on POSIX,
    and never on win32 at 3.13+.
    """
    if sys.platform == "win32" and sys.version_info < (3, 13):
        resolved = path() if callable(path) else path
        resolved.chmod(mode)
        return
    os.fchmod(descriptor, mode)


def nofollow_flag() -> int:
    """`O_NOFOLLOW` where the platform has it, `0` where it does not.

    Returning 0 is not a graceful degradation -- the no-follow protection
    disappears and nothing raises.  On the windows-revalidated tier the
    replacement is `_WindowsAnchor.open_child`, which `lstat`s each component
    and refuses a link explicitly.  That is weaker: a check, not a kernel
    guarantee, so a swap between the check and the syscall is not caught.
    ADR-0042 records this as the tier's largest security difference.

    This one degrades rather than refusing, unlike `directory_flags` below,
    because a real replacement exists.  The `sys.platform` guard is what lets
    `mypy` skip the `os.O_NOFOLLOW` reference on a win32 pass;
    `NOFOLLOW_AVAILABLE` is a `bool` and narrows nothing.
    """
    if sys.platform == "win32":
        return 0
    return os.O_NOFOLLOW if NOFOLLOW_AVAILABLE else 0


def directory_flags() -> int:
    """Open flags for a directory descriptor on the strong tier.

    Refuses rather than degrading: there is no Windows substitute for a
    directory descriptor, and a caller that reaches this has landed on
    `_PosixAnchor` by mistake -- almost always a test forcing
    `platform_name="linux"` on a Windows host.  Saying so is worth more than
    `AttributeError: module 'os' has no attribute 'O_DIRECTORY'`, which is
    what 96 failures in one Windows run looked like.

    The guard replaced a comment claiming the omission was safe because
    "a Windows run never reaches it".  True of production and silent about
    type-checking, which was exactly the gap.
    """
    if sys.platform == "win32":
        _posix_only("os.O_DIRECTORY")
    flags = os.O_RDONLY | os.O_DIRECTORY
    if NOFOLLOW_AVAILABLE:
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
    def resolve_descendant(self, relative: str) -> Path: ...

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

    def resolve_descendant(self, relative: str) -> Path:
        """A path to a possibly-multi-component descendant of this anchor.

        The `Anchor` vocabulary is single-component by design, but
        `transactions._projected_symlink_is_internal` genuinely needs to open a
        multi-component relative path in one call.  This is the one sanctioned
        way to get one, and it is per-tier: the POSIX arm resolves through the
        pinned descriptor's `/proc/self/fd` alias so the descendant cannot be
        redirected by a rename of an ancestor; the path arm resolves under the
        held root, which can be.
        """
        return self.alias().joinpath(relative)

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
        """From transactions.py:1026 -- chmod a child *directory* via its own descriptor.

        Refuses on `win32` rather than falling back: reaching this at all means
        a caller landed on the strong tier on a host that has no directory
        descriptors, and `_WindowsAnchor` supplies the path-chmod equivalent.
        """
        descriptor = os.open(name, directory_flags(), dir_fd=self.descriptor)
        try:
            if sys.platform == "win32":
                # Unreachable: `directory_flags()` above already refused under
                # this identical condition, so `os.open` never returns on
                # win32. This branch exists only so mypy can see `os.fchmod`
                # below is unreached on a platform where it does not exist --
                # it cannot see that `directory_flags()` already refused.
                _posix_only("os.fchmod")
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
        if not stat.S_ISDIR(self.self_stat().st_mode):
            raise NotADirectoryError("bundle root descriptor is not a directory")
        held = False
        try:
            _flock_exclusive(self.descriptor)
            held = True
            yield
        finally:
            if held:
                with suppress(OSError):
                    _flock_release(self.descriptor)

    @contextmanager
    def lock_file(self, name: str, *, assert_identity: bool) -> Iterator[None]:
        """Lock a regular file beneath this anchor.  From transactions.py:280-299.

        `assert_identity=False` is exercised only by unit tests; the engine
        always passes `True` (see `transactions._executor_lock:275`).
        """
        descriptor = os.open(
            name,
            os.O_RDWR | os.O_CREAT | nofollow_flag(),
            0o600,
            dir_fd=self.descriptor,
        )
        held = False
        try:
            require_regular_file(descriptor, "executor lock")
            _flock_exclusive(descriptor)
            held = True
            if assert_identity:
                _assert_regular_entry_identity(self, name, descriptor, "executor lock")
            yield
        finally:
            if held:
                with suppress(OSError):
                    _flock_release(descriptor)
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

#: The weak tier's on-disk serialization point, inside the bundle root.
#:
#: A Windows bundle carries a file a POSIX bundle does not.  It is
#: dot-prefixed and lives at the root, where ADR-0028's root-scoped dot
#: exclusion already keeps it out of `load_bundle()` -- but the two digest
#: tests assert that rather than assume it, because a divergence here would
#: make the two tiers compute different manifest digests for identical
#: content.
BUNDLE_LOCK_NAME = ".gw-bundle.lock"

#: The `GetVolumeInformationW` file-system flag bit reporting hard-link
#: support.  Named so the refusal message, the test suite, and ADR-0042 all
#: cite the same flag.
FILE_SUPPORTS_HARD_LINKS = 0x00400000

#: Win32 error codes `CreateHardLinkW` returns when the filesystem driver does
#: not implement hard links at all.  `ERROR_INVALID_FUNCTION` is what exFAT
#: returns (measured); `ERROR_NOT_SUPPORTED` is the code Microsoft's own docs
#: list for the same class of filesystem refusal on some network shares.
ERROR_INVALID_FUNCTION = 1
ERROR_NOT_SUPPORTED = 50


def hard_links_supported(path: Path) -> bool:
    """Whether the filesystem hosting *path* supports hard links.

    Off Windows there is no such restriction, so the answer is unconditionally
    yes -- the same reasoning as `long_paths_enabled()`, and for the same
    purpose: it is what lets the POSIX coverage pass construct a
    `_WindowsAnchor` at all.

    On Windows this is `GetVolumePathNameW` (walk up to the nearest existing
    mount point -- this works for a path that does not exist yet, and for a
    UNC share, resolving it to its share root rather than a drive letter)
    followed by `GetVolumeInformationW`, testing the `FILE_SUPPORTS_HARD_LINKS`
    bit. The flag is the volume driver's *claim*, not a measurement: if a
    filesystem advertises the flag and still fails `os.link`, this probe
    passes and `_WindowsAnchor.link`'s backstop (see Fix 3) is what actually
    catches it. Any `OSError` from either Win32 call returns `False`, matching
    `long_paths_enabled()`'s own `except OSError: return False` -- refusing on
    a false negative is the safe direction, because the alternative is a
    commit that dies partway through.
    """
    if sys.platform != "win32":
        return True
    import ctypes  # Windows-only, imported at the point of use

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_unicode_buffer(260)
    try:
        if not kernel32.GetVolumePathNameW(str(path), buffer, len(buffer)):
            return False
        flags = ctypes.c_uint32(0)
        if not kernel32.GetVolumeInformationW(buffer.value, None, 0, None, None, ctypes.byref(flags), None, 0):
            return False
    except OSError:
        return False
    return bool(flags.value & FILE_SUPPORTS_HARD_LINKS)


def _hard_link_refusal_message(path: Path) -> str:
    """The one place the hard-link refusal text is authored.

    Fix 1 (anchor construction), Fix 2 (`gw bootstrap` preflight) and Fix 3
    (the `link` backstop) all call this, so the three refusals cannot drift
    apart.
    """
    return (
        f"the {WINDOWS_REVALIDATED_TIER} tier requires hard link support: every file is "
        f"installed by `CreateHardLinkW` (`transactions._commit_write`), and {path} is on a "
        "filesystem that does not implement it -- exFAT, FAT32 and some network shares do not. "
        f"Move the workspace to an NTFS or ReFS volume, or run under WSL for the "
        f"{POSIX_STRONG_TIER} tier."
    )


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

    __slots__ = ("_hard_links", "_identity", "_long_paths", "root")

    def __init__(
        self,
        root: Path,
        *,
        long_paths: bool | None = None,
        hard_links: bool | None = None,
    ) -> None:
        # `resolve()` would silently follow a symlinked final component, so the
        # refusal has to happen on the UNresolved path -- same shape and
        # reasoning as `open_child`'s refusal of a symlinked component, just at
        # construction time and for the root itself.  A symlinked ANCESTOR of
        # the root is unaffected: `resolve()` below still collapses it, which
        # matches `_PosixAnchor.open_root`'s behaviour.
        unresolved = root.lstat()
        if stat.S_ISLNK(unresolved.st_mode):
            raise NotADirectoryError(f"refusing to anchor a symlinked root: {root!r}")
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
        self._hard_links = hard_links_supported(self.root) if hard_links is None else hard_links
        if not self._hard_links:
            raise ValueError(_hard_link_refusal_message(self.root))

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
        return _WindowsAnchor(candidate, long_paths=self._long_paths, hard_links=self._hard_links)

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
        return _WindowsAnchor(self.root, long_paths=self._long_paths, hard_links=self._hard_links)

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

    def resolve_descendant(self, relative: str) -> Path:
        """A path to a possibly-multi-component descendant of this anchor.

        See `Anchor.resolve_descendant`.  This tier has no descriptor to pin,
        so the descendant resolves under the re-validated held root instead.
        """
        return self._revalidate().joinpath(relative)

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
        (`:1697`, `:1741`) installs EVERY file this way.

        The construction-time probe (`hard_links_supported`) is a claim, not a
        measurement -- if a filesystem advertises `FILE_SUPPORTS_HARD_LINKS`
        and still fails here, this backstop translates the two known error
        codes into the same named refusal the constructor raises, so the
        defect is fixed whether or not the probe was right.  `FileExistsError`
        must propagate untouched: both call sites in `_commit_write` catch it
        specifically to detect a name that changed since planning, and
        swallowing it here would break that concurrency detection.
        """
        directory = self._revalidate()
        try:
            os.link(directory / source, directory / name, follow_symlinks=False)
        except FileExistsError:
            raise
        except OSError as exc:
            if getattr(exc, "winerror", None) in (ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED):
                raise ValueError(_hard_link_refusal_message(self.root)) from exc
            raise

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

        ORs in `os.O_BINARY` **on Windows**: every caller in this engine reads
        and writes raw bytes it already encoded itself, but the Windows CRT
        defaults a descriptor opened without `O_BINARY` to *text* mode, which
        silently rewrites every `\\n` byte in an `os.write` call to `\\r\\n` --
        corrupting content no caller here asked to have translated, with no
        exception raised to say so.

        The `sys.platform` guard is not cosmetic.  `os.O_BINARY` exists only
        on Windows, so an unconditional reference broke this method on every
        POSIX host -- where the weak tier is *simulated* for the whole of
        D-001's cross-tier coverage.  It is the exact mirror of the
        `O_DIRECTORY` defect, in the opposite direction, and it stayed
        invisible for the same reason: a one-platform type gate.
        """
        target = self._revalidate() / name
        if sys.platform == "win32":
            return os.open(target, flags | os.O_BINARY, mode)
        return os.open(target, flags, mode)

    def chmod_child_directory(self, name: str, mode: int) -> None:
        """chmod a child directory.

        `_PosixAnchor` opens the child, `os.fchmod`es it and `os.fsync`es the
        descriptor.  This tier has no directory descriptor to do either
        through -- `os.O_DIRECTORY` is what it lacks, not `os.fchmod`, which
        Windows gained in CPython 3.13 -- so this is a path chmod with no
        flush, a sixth loss the design's table did not enumerate, recorded in
        ADR-0042 as L6.  On NTFS `chmod` honours only the read-only bit; the
        mode bits the engine sets are preserved on the POSIX coverage pass,
        which is where the mode assertions actually run.
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

    @contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        """Serialize every executor over this anchored directory, via a lock file.

        The strong tier flocks the directory DESCRIPTOR, which is immune to the
        directory being swapped underneath it.  This tier cannot: Windows has
        no directory lock, so serialization moves to a file inside the
        directory, and the immunity is replaced by `_revalidate()`'s check at
        acquisition time.  ADR-0042 records the difference.
        """
        directory = self._revalidate()
        with locked(directory / BUNDLE_LOCK_NAME):
            yield

    @contextmanager
    def lock_file(self, name: str, *, assert_identity: bool) -> Iterator[None]:
        """Lock a regular file beneath this anchor.

        `assert_identity=False` is exercised only by unit tests; the engine
        always passes `True` (see `transactions._executor_lock:275`).  The
        kwarg exists to mirror `_PosixAnchor.lock_file`'s signature, which in
        turn mirrors the two branches the pre-refactor `_executor_lock` had.
        """
        directory = self._revalidate()
        target = directory / name
        descriptor = os.open(target, os.O_RDONLY | os.O_CREAT, 0o600)
        try:
            with locked(target):
                if assert_identity:
                    _assert_regular_entry_identity(self, name, descriptor, "executor lock")
                yield
        finally:
            os.close(descriptor)

    # -- tier contract --------------------------------------------------------

    def refused_members(self, members: Sequence[str]) -> tuple[RefusedShape, ...]:
        """Every member of *members* this tier cannot honour.

        Called from `transactions._preflight` before the first live effect,
        because the symlink case is on the ROLLBACK path
        (`_copy_backup_to_live:1029,1065`) -- the one path that must not fail.
        Discovering it mid-rollback would leave a half-restored bundle.

        The scan is over whatever *members* the caller passes in -- it does
        not itself walk anything.  `transactions._preflight` no longer passes
        only the plan's literal members: it expands every directory a
        mutation touches or validates (via `_expand_directory_members`) into
        its existing descendants first, and scans that whole expanded set.
        So a symlink (or reserved device name, or trailing-dot/space name)
        living anywhere inside a directory the plan mkdirs, deletes,
        validates, or otherwise names is caught here, not just one staged at
        a literal planned path. Before that expansion existed, a symlink
        deeper inside a targeted subtree could still be caught by `symlink()`
        raising during snapshot creation (`_copy_live_entry:849`), which
        still runs before any live mutation -- so the rollback-safety
        guarantee this scan protects held either way. The scan here now
        exists to make that guarantee explicit and early (at preflight)
        rather than incidental and late (mid-snapshot).
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

    Windows has no descriptor-based flock to be faithful to in the first
    place, so its arm is a straight call onto `okf_ext.locking.locked` --
    the portable primitive, not the workspace-wide path lock this docstring
    otherwise disclaims.  The POSIX path's own bookkeeping flag is `held`,
    not `locked`: the two names collided, and because Python binds locals per
    function rather than per branch, the collision made the Windows arm raise
    `UnboundLocalError` on every workspace mutation.
    """
    if sys.platform == "win32":
        with locked(path):
            yield
        return
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | nofollow_flag(), 0o600)
    held = False
    try:
        require_regular_file(descriptor, "executor lock")
        _flock_exclusive(descriptor)
        held = True
        yield
    finally:
        if held:
            with suppress(OSError):
                _flock_release(descriptor)
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


@dataclass(frozen=True, slots=True)
class DurabilityTier:
    """What a platform's transaction engine guarantees, and what it does not.

    The two tiers are declared in ADR-0042.  `gw util platform` renders this
    verbatim; the refusals are contract statements, not error conditions.

    Every field is derived from a constant in this module rather than restated,
    so a change to the tier's behaviour cannot silently desynchronize from what
    the verb prints -- that derivation is the whole reason
    `util.platform.DurabilityTierProvider` is a provider seam and not a table.
    """

    name: str
    anchoring: str
    directory_fsync: bool
    nofollow_protection: bool
    refused_plan_shapes: tuple[str, ...]
    filesystem_requirement: str
    verification_status: str


_POSIX_TIER = DurabilityTier(
    name=POSIX_STRONG_TIER,
    anchoring="pinned directory descriptors; every operation is os.*(..., dir_fd=...)",
    directory_fsync=True,
    nofollow_protection=True,
    refused_plan_shapes=(),
    filesystem_requirement="any POSIX filesystem",
    verification_status="continuously tested",
)

_WINDOWS_TIER = DurabilityTier(
    name=WINDOWS_REVALIDATED_TIER,
    anchoring=(
        f"a held resolved path, re-lstat'ed before every operation, under a bundle-root lock file ({BUNDLE_LOCK_NAME})"
    ),
    # Literals, not DIRECTORY_FSYNC_HONORED / NOFOLLOW_AVAILABLE: those constants reflect the
    # REAL host's sys.platform, but this record describes Windows's properties even when
    # durability_tier() is asked about a simulated "win32" from a POSIX dev box.
    directory_fsync=False,
    nofollow_protection=False,
    refused_plan_shapes=(
        "symlink members without SeCreateSymbolicLinkPrivilege or Developer Mode",
        f"members whose name at any component is a reserved device name ({', '.join(sorted(RESERVED_DEVICE_NAMES))})",
        "members whose name at any component ends in a trailing dot or space",
    ),
    filesystem_requirement="NTFS, same volume (every file install is a hard link)",
    verification_status=(
        "logic-tested continuously on POSIX; platform-unverified pending the manual Windows verification run"
    ),
)


def durability_tier(platform_name: str | None = None) -> DurabilityTier:
    """The full tier declaration for *platform_name*.

    `anchor_tier()` answers the name alone and stays the cheap query; this
    answers everything `gw util platform` and ADR-0042 need.
    """
    return _WINDOWS_TIER if _is_windows(_anchor_platform(platform_name)) else _POSIX_TIER


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
    "BUNDLE_LOCK_NAME",
    "DIRECTORY_FSYNC_HONORED",
    "MAX_PATH",
    "NOFOLLOW_AVAILABLE",
    "POSIX_STRONG_TIER",
    "RESERVED_DEVICE_NAMES",
    "WINDOWS_REVALIDATED_TIER",
    "Anchor",
    "DurabilityTier",
    "RefusedShape",
    "UnsupportedAnchorPlatform",
    "anchor_tier",
    "durability_tier",
    "lock_path",
    "long_paths_enabled",
    "nofollow_flag",
    "open_absolute_anchor",
    "open_anchor",
    "require_regular_file",
    "set_mode",
]
