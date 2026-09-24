"""Durable, rollback-journaled application of work mutation plans.

The domain package plans immutable bundle-relative effects.  This module is
the workspace-aware commit boundary: it revalidates the complete plan before
the first live effect, keeps recovery evidence outside the bundle, and either
lands every effect plus its targeted postconditions or restores the snapshot.

Postcondition validation is *differential*: a baseline is captured under the
same lock before the first effect, and only failures whose count rises are the
mutation's fault.  That doubles validation cost per mutation (~9s each on a
large live bundle).  Two optimizations are known and deliberately deferred:
reusing the planner's already-loaded bundle for the baseline, and restricting
validation to the targeted members.  Both are separate work -- a gate that is
only conditionally correct is worth nothing.

Every platform-specific primitive this module needs -- advisory locking, the
NOREPLACE rename, descriptor-to-path resolution, st_dev/st_ino identity, the
directory fsync -- is reached through `workspace.anchors`.  This module
therefore imports no POSIX-only module at module scope, which is a *tested*
property, not a convention: on native Windows the engine imports successfully
and raises at call time, which is strictly better than dying at import.  Adding
`import fcntl` back here would pass every behavioural test and silently undo
that.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import IO, Literal, Protocol

from okf_ext.moves import Move
from okf_ext.writing import body_digest
from okf_io import Bundle, Rule, parse, validate
from okf_io import load_bundle as _load_bundle
from okf_io.bundle import _load_at as _load_bundle_at
from work_tracker_okf.compose import rule_set
from work_tracker_okf.indexes import reconcile_entries, render_entry
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import WorkMutationPlan, directory_manifest_digest
from work_tracker_okf.paths import parse_item_path

from graph_works_core.workspace import anchors
from graph_works_core.workspace.anchors import Anchor, open_absolute_anchor, open_anchor
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class MutationApplication:
    """The durable outcome of applying one immutable domain plan."""

    transaction_id: str
    journal: Path
    moved: tuple[tuple[str, str], ...]
    written: tuple[str, ...]
    created_directories: tuple[str, ...]
    warnings: tuple[str, ...]
    failures: tuple[str, ...]
    rolled_back: bool

    @property
    def ok(self) -> bool:
        return not self.failures and not self.rolled_back


@dataclass(frozen=True, slots=True)
class _SnapshotEntry:
    member: str
    existed: bool
    backup: Path | None
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class _Effect:
    kind: Literal["mkdir", "move", "write", "delete"]
    member: str
    destination: str | None = None
    staged: Path | None = None


@dataclass(frozen=True, slots=True)
class _DirectoryMode:
    source: str
    destination: str
    mode: int


@dataclass(frozen=True, slots=True)
class _EntryIdentity:
    device: int
    inode: int
    kind: int
    mode: int


class _CustodyConflict(ValueError):
    """A captured entry cannot be restored without overwriting external bytes."""


class _HashSink(Protocol):
    def update(self, content: bytes) -> object: ...


def _write_bytes(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        offset += os.write(descriptor, content[offset:])


def _append_journal(
    journal: Path,
    state: str,
    *,
    _parent: Anchor | None = None,
    _journal_fd: int | None = None,
    **details: object,
) -> None:
    record = _journal_record(state, details)
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if _journal_fd is not None:
        if _parent is None:
            raise ValueError("retained journal requires its anchored parent descriptor")
        _assert_regular_entry_identity(_parent, journal.name, _journal_fd, "journal")
        _write_bytes(_journal_fd, encoded)
        os.fsync(_journal_fd)
        _fsync_live_directory(_parent)
        return
    stream: IO[bytes]
    if _parent is None:
        stream = journal.open("ab")
    else:
        descriptor = _parent.open_file(
            journal.name,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        stream = os.fdopen(descriptor, "ab")
    with stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if _parent is None:
        _fsync_directory(journal.parent)
    else:
        _fsync_live_directory(_parent)


def _journal_has_terminal_complete(
    journal: Path,
    expected_history: Sequence[Mapping[str, object]],
    *,
    journal_fd: int | None = None,
) -> bool:
    try:
        if journal_fd is None:
            content = journal.read_text(encoding="utf-8")
        else:
            _require_regular_file(journal_fd, "journal")
            os.lseek(journal_fd, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while chunk := os.read(journal_fd, 1024 * 1024):
                chunks.append(chunk)
            os.lseek(journal_fd, 0, os.SEEK_END)
            content = b"".join(chunks).decode("utf-8")
        if not content.endswith("\n"):
            return False
        lines = content.splitlines()
        records: list[object] = [
            json.loads(
                line,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
                parse_float=_finite_json_float,
            )
            for line in lines
        ]
        if not all(isinstance(record, dict) for record in records):
            return False
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return False
    return _json_values_equal(records, list(expected_history))


def _json_values_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        if not isinstance(expected, dict) or actual.keys() != expected.keys():
            return False
        return all(_json_values_equal(value, expected[key]) for key, value in actual.items())
    if isinstance(actual, list):
        if not isinstance(expected, list) or len(actual) != len(expected):
            return False
        return all(
            _json_values_equal(actual_value, expected_value)
            for actual_value, expected_value in zip(actual, expected, strict=True)
        )
    return actual == expected


def _journal_record(state: str, details: Mapping[str, object]) -> dict[str, object]:
    return {"state": state, **details}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate JSON object key: {key}")
        record[key] = value
    return record


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _complete_record(
    transaction_id: str,
    moved: Sequence[tuple[str, str]],
    written: Sequence[str],
    created: Sequence[str],
) -> dict[str, object]:
    return _journal_record(
        "complete",
        {
            "transaction_id": transaction_id,
            "moved": [[source, destination] for source, destination in moved],
            "written": list(written),
            "created_directories": list(created),
        },
    )


def _fsync_directory(path: Path) -> None:
    """Flush *path*'s directory entry, honoring `anchors.DIRECTORY_FSYNC_HONORED`.

    Opening a directory with `O_RDONLY` and calling `os.fsync` on it raises
    `OSError: [Errno 9] Bad file descriptor` on Windows -- `_commit()` (what
    `os.fsync` maps to there) cannot flush a directory handle. `_WindowsAnchor.fsync`
    already documents this as a no-op contract for the windows-revalidated tier;
    this free function -- called with a bare `Path`, not an `Anchor` -- had not
    been gated to match.
    """
    if not anchors.DIRECTORY_FSYNC_HONORED:
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _nofollow_flag() -> int:
    # A delegator, not a re-export.  See the note above `_open_parent`.
    return anchors.nofollow_flag()


def _require_regular_file(descriptor: int, label: str) -> os.stat_result:
    # A delegator, not a re-export.  See the note above `_open_parent`.
    return anchors.require_regular_file(descriptor, label)


def _assert_regular_entry_identity(parent: Anchor, name: str, descriptor: int, label: str) -> None:
    anchors._assert_regular_entry_identity(parent, name, descriptor, label)


@contextmanager
def _executor_lock(transaction_root: Path, *, root: Anchor | None = None) -> Iterator[None]:
    """Serialize mutation executors that honor the workspace transaction boundary."""
    if root is None:
        transaction_root.mkdir(parents=True, exist_ok=True)
        with anchors.lock_path(transaction_root / "executor.lock"):
            yield
    else:
        with root.lock_file("executor.lock", assert_identity=True):
            yield


@contextmanager
def _bundle_root_lock(root: Anchor) -> Iterator[None]:
    """Serialize every executor holding this anchored bundle directory."""
    with root.exclusive_lock():
        yield


# ---------------------------------------------------------------------------
# The anchored-operation vocabulary below is a layer of thin delegators onto
# `graph_works_core.workspace.anchors`.  It looks like dead indirection and it
# is not: `monkeypatch.setattr(transactions, ...)` only bites if this module's
# own call sites resolve the name through *this* module's globals.  The test
# suite injects 16 of these names -- `_open_parent`, `_open_or_create_directory`,
# `_rename_noreplace_at`, `_digest_in_directory`, `_executor_lock`,
# `_fsync_live_file`, `_fsync_live_directory`, `_entry_fingerprint_at`,
# `_append_journal`, `_commit_effect`, `_create_snapshot`, `_restore_snapshot`,
# `_validate_postconditions`, `_load_bundle_through`, `directory_manifest_digest`,
# `plan_indexes` -- and replacing any of these `def`s with a
# `from anchors import ...` would leave the import succeeding, the attribute
# present, and every one of those injections silently inert, with no runtime
# signal, in the most safety-critical module in this repository.
#
# Do not "tidy" these into re-exports.
# ---------------------------------------------------------------------------


def _open_absolute_directory(path: Path) -> Anchor:
    """Open an absolute directory by walking every component without following links."""
    return open_absolute_anchor(path)


def _refuse_unsupported_shapes(root: Anchor, members: Sequence[str]) -> None:
    refusals = root.refused_members(members)
    if not refusals:
        return
    detail = "; ".join(f"{item.member}: {item.reason} ({item.remedy})" for item in refusals)
    raise ValueError(f"this durability tier refuses the planned members -- {detail}")


def _open_or_create_directory(parent: Anchor, name: str) -> Anchor:
    return parent.open_or_create_child(name)


def _assert_directory_identity(path: Path, anchor: Anchor, label: str) -> None:
    anchor.assert_directory_identity(path, label)


def _descriptor_path(anchor: Anchor) -> Path:
    """Return the current namespace path of an open directory anchor."""
    return anchor.path()


def _directory_descriptor_alias(anchor: Anchor) -> Path:
    """A descriptor-rooted namespace path suitable for resolving descendants."""
    return anchor.alias()


def _raw_descriptor(anchor: Anchor) -> int:
    """The POSIX descriptor backing *anchor*.

    A narrow escape hatch with exactly one remaining consumer:
    `_load_bundle_through`'s POSIX arm, which still calls `okf_io.bundle._load_at`
    with a raw `int`. `_projected_symlink_is_internal` no longer needs this --
    Task 10 moved it onto `Anchor.resolve_descendant` instead.

    Refuses anything that is not a `_PosixAnchor` because a path-revalidated
    anchor has no descriptor to lend, by construction: `_WindowsAnchor` never
    opens one, so there is nothing here to hand back.
    """
    if not isinstance(anchor, anchors._PosixAnchor):
        raise anchors.UnsupportedAnchorPlatform("a path-revalidated anchor has no descriptor to lend")
    return anchor.descriptor


def _load_bundle_through(root: Anchor, path: Path, *, ignore: Sequence[str]) -> Bundle:
    """Load the bundle at *path*, anchored as strongly as this tier allows.

    `okf_io.bundle._load_at` takes a raw descriptor and is the strong tier's
    load: every read happens beneath the pinned root, so a rename mid-load
    cannot redirect it.  The path-revalidated tier has no descriptor to give,
    so it uses okf-io's ordinary path walk -- which is the SAME loader
    (`okf_io.bundle._load` with `root_fd=None`), not a different one.  The
    caller's `_assert_root_identity` before and after still bounds the window.

    This is the last consumer of `_raw_descriptor`.
    """
    if isinstance(root, anchors._PosixAnchor):
        return _load_bundle_at(path, _raw_descriptor(root), ignore=ignore)
    return _load_bundle(path, ignore=ignore)


@contextmanager
def _locked_transaction_root(cache_dir: Path, transaction_root: Path) -> Iterator[tuple[Anchor, Anchor]]:
    """Anchor the cache namespace before acquiring the cross-process executor lock."""
    try:
        cache = _open_absolute_directory(cache_dir)
    except FileNotFoundError:
        parent = _open_absolute_directory(cache_dir.parent)
        try:
            cache = _open_or_create_directory(parent, cache_dir.name)
        finally:
            parent.close()
    try:
        transaction_root_anchor = _open_or_create_directory(cache, transaction_root.name)
        try:
            with _executor_lock(transaction_root, root=transaction_root_anchor):
                _assert_directory_identity(cache_dir, cache, "cache")
                _assert_directory_identity(transaction_root, transaction_root_anchor, "transaction cache")
                yield cache, transaction_root_anchor
        finally:
            transaction_root_anchor.close()
    finally:
        cache.close()


@contextmanager
def _new_journal(transaction: Anchor, name: str) -> Iterator[int]:
    descriptor = transaction.open_file(
        name,
        os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | _nofollow_flag(),
        0o600,
    )
    try:
        _require_regular_file(descriptor, "journal")
        _assert_regular_entry_identity(transaction, name, descriptor, "journal")
        os.fsync(descriptor)
        _fsync_live_directory(transaction)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _new_transaction_directory(
    transaction_root: Anchor,
    configured: Path,
    transaction_id: str,
) -> Iterator[tuple[Anchor, Path, int]]:
    transaction_root.mkdir(transaction_id, 0o700)
    _fsync_live_directory(transaction_root)
    transaction = transaction_root.open_child(transaction_id)
    try:
        _assert_directory_identity(configured, transaction, "transaction")
        _fsync_live_directory(transaction)
        with _new_journal(transaction, "journal.jsonl") as journal_fd:
            yield transaction, _descriptor_path(transaction), journal_fd
    finally:
        transaction.close()


def _open_root(root: Path) -> Anchor:
    return open_anchor(root)


def _file_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _fsync_file_flags() -> int:
    """Flags for opening a file that will only be `os.fsync`ed.

    `O_RDWR`, not `_file_flags`'s `O_RDONLY`: on Windows, `_commit()` (what
    `os.fsync` maps to there) raises `OSError: [Errno 9] Bad file descriptor`
    for a handle opened without write access. See `_fsync_entry`'s docstring
    for the same gap on the `pathlib.Path.open` side.
    """
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_parent(
    root: Anchor,
    member: str,
    *,
    create: bool = False,
    touched: set[str] | None = None,
    must_create: set[str] | None = None,
) -> tuple[Anchor, str]:
    """Open *member*'s parent by walking beneath *root* without following links."""
    pure = _lexical_member(member)
    cursor = root.duplicate()
    traversed: list[str] = []
    try:
        for part in pure.parts[:-1]:
            traversed.append(part)
            try:
                child = cursor.open_child(part)
                created = "/".join(traversed)
                if must_create is not None and created in must_create and (touched is None or created not in touched):
                    child.close()
                    raise ValueError(f"{created}: directory changed since planning (now exists); re-plan")
            except FileNotFoundError:
                if not create:
                    raise
                cursor.mkdir(part)
                created = "/".join(traversed)
                if touched is not None:
                    touched.add(created)
                _fsync_live_directory(cursor)
                child = cursor.open_child(part)
                _fsync_live_directory(child)
            except OSError as exc:
                raise ValueError(f"{member}: unsafe ancestor {part!r}: {exc}") from exc
            cursor.close()
            cursor = child
    except Exception:
        cursor.close()
        raise
    return cursor, pure.name


def _lstat_at(root: Anchor, member: str) -> os.stat_result:
    parent, name = _open_parent(root, member)
    try:
        return parent.lstat(name)
    finally:
        parent.close()


def _lexists_at(root: Anchor, member: str) -> bool:
    try:
        _lstat_at(root, member)
    except FileNotFoundError:
        return False
    return True


def _read_bytes_at(root: Anchor, member: str) -> bytes:
    parent, name = _open_parent(root, member)
    try:
        descriptor = parent.open_file(name, _file_flags())
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read()
        finally:
            os.close(descriptor)
    finally:
        parent.close()


def _readlink_at(root: Anchor, member: str) -> str:
    parent, name = _open_parent(root, member)
    try:
        return parent.readlink(name)
    finally:
        parent.close()


def _fsync_live_file(root: Anchor, member: str) -> None:
    parent, name = _open_parent(root, member)
    try:
        descriptor = parent.open_file(name, _fsync_file_flags())
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        parent.close()


def _fsync_live_directory(anchor: Anchor) -> None:
    # A delegator, not a re-export.  See the note above `_open_parent`.
    anchor.fsync()


def _assert_root_identity(root: Path, anchor: Anchor) -> None:
    anchor.assert_identity(root, "bundle root")


def _fsync_entry(path: Path) -> None:
    """Fsync *path* (or, recursively, every non-symlink file beneath it).

    Opens each file `"r+b"` rather than `"rb"`: on Windows, `_commit()` (what
    `os.fsync` maps to there) raises `OSError: [Errno 9] Bad file descriptor`
    for a handle opened without write access, even though nothing here is
    written -- these are freshly copied backups this process already owns, so
    reopening read-write costs nothing and Windows accepts the fsync.
    """
    if path.is_symlink():
        return
    if not path.is_dir():
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())
        return
    pending = [path]
    directories: list[Path] = []
    while pending:
        directory = pending.pop()
        directories.append(directory)
        for entry in directory.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                pending.append(entry)
            else:
                with entry.open("r+b") as stream:
                    os.fsync(stream.fileno())
    for directory in reversed(directories):
        _fsync_directory(directory)


def _lexical_member(member: str) -> PurePosixPath:
    pure = PurePosixPath(member)
    if (
        not member
        or pure.is_absolute()
        or pure.as_posix() != member
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"{member}: effect path is not a canonical bundle-relative path")
    return pure


def _manifest_field(hasher: _HashSink, content: bytes) -> None:
    hasher.update(len(content).to_bytes(8, "big"))
    hasher.update(content)


def _entry_manifest_at(root: Anchor, member: str, *, include_mode: bool) -> str:
    """Hash one anchored entry tree without following any symlink."""
    hasher = hashlib.sha256()
    pending = [(".", member)]
    while pending:
        relative, current = pending.pop()
        info = _lstat_at(root, current)
        if stat.S_ISDIR(info.st_mode):
            kind = b"directory"
            payload = b""
        elif stat.S_ISREG(info.st_mode):
            kind = b"file"
            payload = hashlib.sha256(_read_bytes_at(root, current)).digest()
        elif stat.S_ISLNK(info.st_mode):
            kind = b"symlink"
            payload = os.fsencode(_readlink_at(root, current))
        else:
            raise ValueError(f"{current}: unsupported filesystem entry type")
        _manifest_field(hasher, relative.encode("utf-8", "surrogateescape"))
        _manifest_field(hasher, kind)
        _manifest_field(hasher, payload)
        if include_mode:
            _manifest_field(hasher, stat.S_IMODE(info.st_mode).to_bytes(4, "big"))
        if kind == b"directory":
            parent, name = _open_parent(root, current)
            try:
                directory = parent.open_child(name)
                try:
                    names = sorted(directory.listdir(), key=os.fsencode, reverse=True)
                finally:
                    directory.close()
            finally:
                parent.close()
            pending.extend(
                (
                    name if relative == "." else f"{relative}/{name}",
                    f"{current}/{name}",
                )
                for name in names
            )
    return hasher.hexdigest()


def _entry_fingerprint_at(root: Anchor, member: str) -> str:
    return _entry_manifest_at(root, member, include_mode=True)


def _effective_members(plan: WorkMutationPlan) -> tuple[str, ...]:
    members = {
        *plan.mkdirs,
        *plan.deletes,
        *plan.validate_paths,
        *(condition.member for condition in plan.directory_preconditions),
        *(write.member for write in plan.writes),
        *((write.source_member or write.member) for write in plan.writes),
        *(move.source for move in plan.moves),
        *(move.dest for move in plan.moves),
    }
    if plan.move_plan is not None:
        members.update(plan.move_plan.digests)
        members.update(move.source for move in plan.move_plan.moves)
        members.update(move.dest for move in plan.move_plan.moves)
    return tuple(sorted(members))


def _validate_ancestor_chain(root: Anchor, member: str) -> None:
    pure = _lexical_member(member)
    cursor = root.duplicate()
    try:
        for part in pure.parts[:-1]:
            try:
                child = cursor.open_child(part)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ValueError(f"{member}: unsafe ancestor {part!r}: {exc}") from exc
            cursor.close()
            cursor = child
    finally:
        cursor.close()


def _projected_symlink_is_internal(root: Anchor, destination: str, link: PurePosixPath) -> bool:
    """Whether *link* -- read as a moved symlink's still-relative target --
    still resolves inside the bundle root once its destination has moved.

    Resolves through `Anchor.resolve_descendant` rather than opening the
    target and resolving the descriptor: see Task 10 for the equivalence
    check this rewrite required on the POSIX arm.

    That equivalence is not total: the old `os.open(..., dir_fd=...)` idiom
    opened the target, which needs read permission on it and can block on a
    special file (a FIFO, say). `Path.resolve(strict=True)` only `stat`s
    ancestors and never opens the target, so it needs search permission
    on directories, not read permission on the target -- a target that
    exists but is unreadable (`chmod 000`) now resolves instead of raising
    `OSError`, which narrows this function's refusal surface for degenerate
    targets. It does not weaken the escape-containment guarantee: whichever
    path got the target open or `stat`ed, the containment check runs on the
    same canonical path either way.
    """
    resolved_root = _descriptor_path(root).resolve(strict=True)
    if link.is_absolute():
        projected = Path(link.as_posix())
    else:
        projected_pure = PurePosixPath(*PurePosixPath(destination).parent.parts, *link.parts)
        projected = root.resolve_descendant(projected_pure.as_posix())
    try:
        resolved_target = projected.resolve(strict=True)
    except FileNotFoundError:
        resolved_target = projected.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"{destination}: moved symlink target cannot be resolved safely: {exc}") from exc
    return resolved_target.is_relative_to(resolved_root)


def _expand_directory_members(root: Anchor, effect_paths: dict[str, Path]) -> tuple[str, ...]:
    """Every planned member, plus every existing descendant of a planned member that is a directory.

    `_effective_members` names only the plan's own literal members -- it has
    no reason to know what already lives inside a directory it mkdirs,
    deletes, or validates. An offending name (a reserved device name, a
    trailing-dot/space name, a symlink) staged inside such a directory is
    otherwise invisible to `_refuse_unsupported_shapes`, which only inspects
    the names it is given. This closes that gap without changing what
    `_effective_members` itself means.

    Refuses to recurse into a Windows directory junction (a reparse point
    reachable through a regular directory listing). `stat.S_ISDIR` is true
    for a junction -- it is not a symlink, so `stat.S_ISLNK` never catches
    it -- and a junction can point at an ancestor of the walk (an immediate
    cycle) or at an arbitrarily large tree outside the bundle entirely.
    Descending into one would make this walk loop forever or scan unbounded
    external storage; `os.path.isjunction` (a no-op, always-`False` check
    off Windows) is checked before every recursive step. A visited-identity
    guard (`st_dev`/`st_ino`) backs this up in case some other reparse
    shape reports `S_ISDIR` without `os.path.isjunction` recognizing it, so
    a cycle can never be walked twice even if the junction check itself
    ever misses one.

    The junction's own name is still included in the returned scan set, but
    this closes only the traversal-safety hole (no infinite loop / unbounded
    walk from descending into it) -- it does not claim to close any
    content-safety hole around junctions themselves. `_refuse_unsupported_shapes`
    (via `Anchor.refused_members`) currently has no refusal rule for
    junctions specifically, only symlinks (`is_symlink()` is `False` for a
    junction), so a junction that reaches this point is scanned, found, and
    passes through with no refusal at all. That gap is pre-existing and
    undocumented elsewhere; it is not addressed by this function.
    """
    scan: set[str] = set(effect_paths)
    pending = list(effect_paths)
    visited: set[tuple[int, int]] = set()
    while pending:
        member = pending.pop()
        if not _lexists_at(root, member):
            continue
        info = _lstat_at(root, member)
        if not stat.S_ISDIR(info.st_mode):
            continue
        identity = (info.st_dev, info.st_ino)
        if identity in visited:
            continue
        visited.add(identity)
        if os.path.isjunction(root.resolve_descendant(member)):
            continue
        parent, name = _open_parent(root, member)
        try:
            directory = parent.open_child(name)
            try:
                names = directory.listdir()
            finally:
                directory.close()
        finally:
            parent.close()
        for entry_name in names:
            child = f"{member}/{entry_name}"
            if child not in scan:
                scan.add(child)
                pending.append(child)
    return tuple(sorted(scan))


def _preflight(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    root: Anchor | None = None,
    manifest_scratch: Path | None = None,
) -> dict[str, Path]:
    if not plan.ok:
        details = "; ".join(f"{refusal.path}: {refusal.kind}: {refusal.detail}" for refusal in plan.refusals)
        raise ValueError(f"cannot apply a refused work mutation: {details}")
    try:
        layout_root = layout.bundle_dir.resolve(strict=True)
        plan_root = plan.root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve mutation bundle root: {exc}") from exc
    if plan_root != layout_root:
        raise ValueError("mutation plan belongs to a different bundle root")

    close_root = root is None
    if root is None:
        root = _open_root(layout.bundle_dir)
    owns_manifest_scratch = manifest_scratch is None
    if manifest_scratch is None:
        manifest_scratch = layout.cache_dir / "work-mutations" / f".preflight-{uuid.uuid4().hex}"
    effect_paths = {member: plan.root.joinpath(*_lexical_member(member).parts) for member in _effective_members(plan)}
    try:
        scan_members = _expand_directory_members(root, effect_paths)
        _refuse_unsupported_shapes(root, scan_members)
        for member in effect_paths:
            _validate_ancestor_chain(root, member)

        _preflight_anchored(plan, root, manifest_scratch)
    finally:
        if owns_manifest_scratch:
            _remove_entry(manifest_scratch)
        if close_root:
            root.close()
    return effect_paths


def _line_ending_cause(member: str) -> str:
    """The cause clause appended to a stale-preflight refusal when translating
    line endings recovers the planned digest.

    Bidirectional by construction (D-095): a planned-LF member found CRLF on
    disk, and a planned-CRLF member found LF on disk, both fire this. A
    one-directional check would misdiagnose the reverse drift with false
    confidence -- `okf-io` supports CRLF documents by contract (see
    `packages/okf-io/tests/fixtures/edge/encoding/crlf.md`), so this is not
    merely defensive.
    """
    return (
        f" -- the on-disk bytes of {member} differ from the planned bytes ONLY in line endings. "
        "git status cannot see this: .gitattributes normalises on read, so git believes the file "
        "is unchanged. Run `gw util line-endings --fix` to restore LF, then retry."
    )


def _line_ending_recovers_digest(current_bytes: bytes, expected_digest: str) -> bool:
    """True when translating *current_bytes*' line endings (either direction)
    recovers *expected_digest* -- the bidirectional test D-095 requires.
    """
    if hashlib.sha256(current_bytes.replace(b"\r\n", b"\n")).hexdigest() == expected_digest:
        return True
    return b"\r\n" not in current_bytes and (
        hashlib.sha256(current_bytes.replace(b"\n", b"\r\n")).hexdigest() == expected_digest
    )


def _line_ending_recovers_body_digest(current_text: str, expected_digest: str) -> bool:
    """The move-plan-body sibling of `_line_ending_recovers_digest`: a body
    digest is over `Document.body`, a `str`, not raw bytes.
    """
    if body_digest(current_text.replace("\r\n", "\n")) == expected_digest:
        return True
    return "\r\n" not in current_text and (body_digest(current_text.replace("\n", "\r\n")) == expected_digest)


def _preflight_anchored(plan: WorkMutationPlan, root: Anchor, manifest_scratch: Path) -> None:
    conditions = [condition.member for condition in plan.directory_preconditions]
    if len(conditions) != len(set(conditions)):
        raise ValueError("directory preconditions contain duplicate members")

    for index, condition in enumerate(plan.directory_preconditions):
        if condition.before_digest is None:
            if _lexists_at(root, condition.member):
                raise ValueError(f"{condition.member}: directory changed since planning (now exists); re-plan")
            continue
        if not _lexists_at(root, condition.member):
            raise ValueError(f"{condition.member}: directory changed since planning (now missing); re-plan")
        try:
            manifest_scratch.mkdir(parents=True, exist_ok=True)
            anchored_copy = manifest_scratch / f"{index:06d}"
            _copy_live_entry(root, condition.member, anchored_copy)
            current_manifest = directory_manifest_digest(anchored_copy)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{condition.member}: directory changed since planning ({exc}); re-plan") from exc
        if current_manifest != condition.before_digest:
            raise ValueError(f"{condition.member}: directory changed since planning; re-plan")

    for write in plan.writes:
        preimage_member = write.source_member or write.member
        if write.source_member == write.member:
            raise ValueError(f"{write.member}: source_member must name a distinct preimage")
        if write.before_digest is None:
            if write.source_member is not None:
                raise ValueError(f"{preimage_member}: invalid plan without a source digest")
            if _lexists_at(root, write.member):
                raise ValueError(f"{write.member}: changed since planning (now exists); re-plan")
            continue
        try:
            current_bytes = _read_bytes_at(root, preimage_member)
        except OSError as exc:
            raise ValueError(f"{preimage_member}: changed since planning ({exc}); re-plan") from exc
        if hashlib.sha256(current_bytes).hexdigest() != write.before_digest:
            cause = ""
            if _line_ending_recovers_digest(current_bytes, write.before_digest):
                cause = _line_ending_cause(preimage_member)
            raise ValueError(f"{preimage_member}: changed since planning; re-plan{cause}")
        if write.source_member is not None and _lexists_at(root, write.member):
            raise ValueError(f"{write.member}: changed since planning (now exists); re-plan")

    if plan.move_plan is not None:
        for member, expected in plan.move_plan.digests.items():
            try:
                content = _read_bytes_at(root, member).decode("utf-8")
                current_document = parse(content, path=plan.root / member)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"{member}: changed since planning ({exc}); re-plan") from exc
            if body_digest(current_document.body) != expected:
                cause = ""
                if _line_ending_recovers_body_digest(current_document.body, expected):
                    cause = _line_ending_cause(member)
                raise ValueError(f"{member}: changed since planning; re-plan{cause}")

    destinations: set[str] = set()
    for move in plan.moves:
        if not _lexists_at(root, move.source):
            raise ValueError(f"{move.source}: direct move changed since planning (source missing); re-plan")
        if _lexists_at(root, move.dest):
            raise ValueError(f"{move.dest}: direct move changed since planning (destination exists); re-plan")
        if move.dest in destinations:
            raise ValueError(f"{move.dest}: multiple effects claim the same move destination")
        destinations.add(move.dest)
        if stat.S_ISLNK(_lstat_at(root, move.source).st_mode):
            link = PurePosixPath(_readlink_at(root, move.source))
            if not _projected_symlink_is_internal(root, move.dest, link):
                raise ValueError(f"{move.dest}: moved symlink would escape the bundle root")

    write_members = [write.member for write in plan.writes]
    if len(write_members) != len(set(write_members)):
        raise ValueError("planned writes contain duplicate targets")
    claimed = destinations.intersection(write_members)
    if claimed:
        raise ValueError(f"multiple effects claim final targets: {', '.join(sorted(claimed))}")


def _snapshot_targets(plan: WorkMutationPlan) -> tuple[str, ...]:
    members = {
        *plan.mkdirs,
        *plan.deletes,
        *(condition.member for condition in plan.directory_preconditions),
        *(write.member for write in plan.writes),
        *(move.source for move in plan.moves),
        *(move.dest for move in plan.moves),
    }
    return tuple(sorted(members))


def _snapshot_members(plan: WorkMutationPlan) -> tuple[str, ...]:
    ordered = sorted(_snapshot_targets(plan), key=lambda member: (member.count("/"), member))
    selected: list[str] = []
    for member in ordered:
        if any(member == parent or member.startswith(f"{parent}/") for parent in selected):
            continue
        selected.append(member)
    return tuple(selected)


def _copy_entry(source: Path, destination: Path) -> None:
    mode = source.lstat().st_mode
    if stat.S_ISLNK(mode):
        destination.symlink_to(source.readlink())
    elif stat.S_ISREG(mode):
        shutil.copy2(source, destination, follow_symlinks=False)
    elif not stat.S_ISDIR(mode):
        raise ValueError(f"unsupported filesystem entry type at {source}")
    else:
        destination.mkdir()
        directories = [(source, destination)]
        pending = [(source, destination)]
        while pending:
            source_directory, destination_directory = pending.pop()
            entries = sorted(source_directory.iterdir(), key=lambda entry: os.fsencode(entry.name), reverse=True)
            for source_entry in entries:
                destination_entry = destination_directory / source_entry.name
                entry_mode = source_entry.lstat().st_mode
                if stat.S_ISLNK(entry_mode):
                    destination_entry.symlink_to(source_entry.readlink())
                elif stat.S_ISREG(entry_mode):
                    shutil.copy2(source_entry, destination_entry, follow_symlinks=False)
                elif stat.S_ISDIR(entry_mode):
                    destination_entry.mkdir()
                    directories.append((source_entry, destination_entry))
                    pending.append((source_entry, destination_entry))
                else:
                    raise ValueError(f"unsupported filesystem entry type at {source_entry}")
        for source_directory, destination_directory in reversed(directories):
            shutil.copystat(source_directory, destination_directory, follow_symlinks=False)


def _copy_live_file(root: Anchor, member: str, destination: Path, mode: int, *, apply_mode: bool = True) -> None:
    parent, name = _open_parent(root, member)
    try:
        source_fd = parent.open_file(name, _file_flags())
        try:
            with os.fdopen(os.dup(source_fd), "rb") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                if apply_mode:
                    anchors.set_mode(target.fileno(), destination, stat.S_IMODE(mode))
                os.fsync(target.fileno())
        finally:
            os.close(source_fd)
    finally:
        parent.close()


def _copy_live_entry(root: Anchor, member: str, destination: Path, *, apply_mode: bool = True) -> None:
    root_info = _lstat_at(root, member)
    if stat.S_ISLNK(root_info.st_mode):
        destination.symlink_to(_readlink_at(root, member))
        return
    if stat.S_ISREG(root_info.st_mode):
        _copy_live_file(root, member, destination, root_info.st_mode, apply_mode=apply_mode)
        return
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(f"unsupported filesystem entry type at {member}")

    destination.mkdir(mode=0o700)
    directories = [(member, destination, stat.S_IMODE(root_info.st_mode))]
    pending = [(member, destination)]
    while pending:
        source_directory, destination_directory = pending.pop()
        parent, name = _open_parent(root, source_directory)
        try:
            child = parent.open_child(name)
            try:
                names = sorted(child.listdir(), key=os.fsencode, reverse=True)
            finally:
                child.close()
        finally:
            parent.close()
        for entry_name in names:
            source_entry = f"{source_directory}/{entry_name}"
            destination_entry = destination_directory / entry_name
            entry_mode = _lstat_at(root, source_entry).st_mode
            if stat.S_ISLNK(entry_mode):
                destination_entry.symlink_to(_readlink_at(root, source_entry))
            elif stat.S_ISREG(entry_mode):
                _copy_live_file(root, source_entry, destination_entry, entry_mode, apply_mode=apply_mode)
            elif stat.S_ISDIR(entry_mode):
                destination_entry.mkdir(mode=0o700)
                directories.append((source_entry, destination_entry, stat.S_IMODE(entry_mode)))
                pending.append((source_entry, destination_entry))
            else:
                raise ValueError(f"unsupported filesystem entry type at {source_entry}")
    if apply_mode:
        for _, directory, mode in reversed(directories):
            directory.chmod(mode)


def _apply_captured_mode(root: Anchor, member: str, backup: Path) -> None:
    """Stamp the live member's mode onto its already-fsynced backup.

    Called after `_fsync_entry`, never before -- see `_copy_live_file`'s
    `apply_mode` flag. A read-only source must not become a read-only
    backup until after this process is done writing (and fsyncing) it.
    """
    info = _lstat_at(root, member)
    if stat.S_ISLNK(info.st_mode):
        return
    if stat.S_ISREG(info.st_mode):
        with backup.open("r+b") as stream:
            anchors.set_mode(stream.fileno(), backup, stat.S_IMODE(info.st_mode))
        return
    # Mirror `_copy_live_entry`'s bottom-up ordering: collect (destination, mode)
    # pairs while walking, and apply every directory chmod only after the whole
    # walk has completed, deepest-first. Chmodding a directory as it is popped
    # (top-down, during the walk) can strip the owner-execute/read bits the
    # walk still needs to `iterdir()` into that directory's own children.
    directories: list[tuple[Path, int]] = [(backup, stat.S_IMODE(info.st_mode))]
    pending = [(member, backup)]
    while pending:
        source_directory, destination_directory = pending.pop()
        for entry in sorted(destination_directory.iterdir(), key=lambda entry: os.fsencode(entry.name)):
            source_entry = f"{source_directory}/{entry.name}"
            entry_info = _lstat_at(root, source_entry)
            if stat.S_ISLNK(entry_info.st_mode):
                continue
            if stat.S_ISDIR(entry_info.st_mode):
                directories.append((entry, stat.S_IMODE(entry_info.st_mode)))
                pending.append((source_entry, entry))
            else:
                with entry.open("r+b") as stream:
                    anchors.set_mode(stream.fileno(), entry, stat.S_IMODE(entry_info.st_mode))
    for directory, mode in reversed(directories):
        directory.chmod(mode)


def _create_snapshot(
    plan: WorkMutationPlan,
    transaction_dir: Path,
    root: Anchor | None = None,
) -> tuple[_SnapshotEntry, ...]:
    backup_root = transaction_dir / "backups"
    backup_root.mkdir()
    close_root = root is None
    if root is None:
        root = _open_root(plan.root)
    entries: list[_SnapshotEntry] = []
    try:
        for index, member in enumerate(_snapshot_members(plan)):
            if not _lexists_at(root, member):
                entries.append(_SnapshotEntry(member, False, None, None))
                continue
            backup = backup_root / f"{index:06d}"
            fingerprint = _entry_fingerprint_at(root, member)
            _copy_live_entry(root, member, backup, apply_mode=False)
            _fsync_entry(backup)
            _apply_captured_mode(root, member, backup)
            backup_root_anchor = _open_root(backup_root)
            try:
                backup_fingerprint = _entry_fingerprint_at(backup_root_anchor, backup.name)
            finally:
                backup_root_anchor.close()
            if backup_fingerprint != fingerprint or _entry_fingerprint_at(root, member) != fingerprint:
                raise ValueError(f"{member}: changed while snapshotting; re-plan")
            entries.append(_SnapshotEntry(member, True, backup, fingerprint))
        _fsync_directory(backup_root)
        _fsync_directory(transaction_dir)
        return tuple(entries)
    finally:
        if close_root:
            root.close()


def _remove_entry(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if not path.is_dir() or path.is_symlink():
        path.unlink()
        return
    pending = [path]
    directories: list[Path] = []
    while pending:
        directory = pending.pop()
        directories.append(directory)
        for entry in directory.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
            else:
                entry.unlink()
    for directory in reversed(directories):
        directory.rmdir()


def _remove_live_entry(root: Anchor, member: str) -> None:
    if not _lexists_at(root, member):
        return
    info = _lstat_at(root, member)
    if not stat.S_ISDIR(info.st_mode):
        parent, name = _open_parent(root, member)
        try:
            _clear_read_only_before_unlink(parent, name)
            parent.unlink(name)
            _fsync_live_directory(parent)
        finally:
            parent.close()
        return

    pending = [member]
    directories: list[str] = []
    while pending:
        directory = pending.pop()
        directories.append(directory)
        parent, name = _open_parent(root, directory)
        try:
            child_directory = parent.open_child(name)
            try:
                names = sorted(child_directory.listdir(), key=os.fsencode, reverse=True)
            finally:
                child_directory.close()
        finally:
            parent.close()
        for entry_name in names:
            child = f"{directory}/{entry_name}"
            child_info = _lstat_at(root, child)
            if stat.S_ISDIR(child_info.st_mode):
                pending.append(child)
            else:
                child_parent, child_name = _open_parent(root, child)
                try:
                    _clear_read_only_before_unlink(child_parent, child_name)
                    child_parent.unlink(child_name)
                    _fsync_live_directory(child_parent)
                finally:
                    child_parent.close()
    for directory in reversed(directories):
        parent, name = _open_parent(root, directory)
        try:
            current_mode = stat.S_IMODE(_lstat_at(root, directory).st_mode)
            if not current_mode & stat.S_IWRITE:
                parent.chmod_child_directory(name, current_mode | stat.S_IWRITE)
            parent.rmdir(name)
            _fsync_live_directory(parent)
        finally:
            parent.close()


def _chmod_directory_at(root: Anchor, member: str, mode: int) -> None:
    parent, name = _open_parent(root, member)
    try:
        parent.chmod_child_directory(name, mode)
        _fsync_live_directory(parent)
    finally:
        parent.close()


def _copy_backup_file(root: Anchor, source: Path, member: str, mode: int) -> None:
    """Restore *source* (a backup) onto the live filesystem as *member*.

    *mode* may carry a read-only bit captured from the live member's own
    preimage (`_apply_captured_mode`). Stamping that mode before this
    function's own `_fsync_live_file` call -- which reopens the just-written
    name `O_RDWR` to fsync it -- would leave the restored file read-only
    before that reopen, and Windows refuses a write-capable reopen of a
    read-only file (the same Defect A1 shape `_live_temporary` guards
    against on the commit path). Stay writable through creation and both
    fsyncs; stamp the final captured mode only after `_fsync_live_file`
    succeeds.
    """
    parent, name = _open_parent(root, member, create=True)
    try:
        descriptor = parent.open_file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(mode) | stat.S_IWRITE)
        try:
            with source.open("rb") as incoming, os.fdopen(os.dup(descriptor), "wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_live_file(root, member)
        _fsync_live_directory(parent)
    finally:
        parent.close()
    _stamp_live_mode(root, member, stat.S_IMODE(mode))


def _copy_backup_to_live(root: Anchor, source: Path, member: str) -> None:
    mode = source.lstat().st_mode
    parent: Anchor | None
    parent, name = _open_parent(root, member, create=True)
    try:
        if stat.S_ISLNK(mode):
            parent.symlink(os.fspath(source.readlink()), name)
            _fsync_live_directory(parent)
            return
        if stat.S_ISREG(mode):
            parent.close()
            parent = None
            _copy_backup_file(root, source, member, mode)
            return
        if not stat.S_ISDIR(mode):
            raise ValueError(f"unsupported filesystem entry type at {source}")
        parent.mkdir(name, 0o700)
        _fsync_live_directory(parent)
    finally:
        if parent is not None:
            parent.close()

    directories = [(source, member, stat.S_IMODE(mode))]
    pending = [(source, member)]
    while pending:
        source_directory, destination_directory = pending.pop()
        entries = sorted(source_directory.iterdir(), key=lambda entry: os.fsencode(entry.name), reverse=True)
        for source_entry in entries:
            destination_entry = f"{destination_directory}/{source_entry.name}"
            entry_mode = source_entry.lstat().st_mode
            if stat.S_ISDIR(entry_mode) and not stat.S_ISLNK(entry_mode):
                destination_parent, destination_name = _open_parent(root, destination_entry)
                try:
                    destination_parent.mkdir(destination_name, 0o700)
                    _fsync_live_directory(destination_parent)
                finally:
                    destination_parent.close()
                directories.append((source_entry, destination_entry, stat.S_IMODE(entry_mode)))
                pending.append((source_entry, destination_entry))
            elif stat.S_ISLNK(entry_mode):
                destination_parent, destination_name = _open_parent(root, destination_entry)
                try:
                    destination_parent.symlink(os.fspath(source_entry.readlink()), destination_name)
                    _fsync_live_directory(destination_parent)
                finally:
                    destination_parent.close()
            elif stat.S_ISREG(entry_mode):
                _copy_backup_file(root, source_entry, destination_entry, entry_mode)
            else:
                raise ValueError(f"unsupported filesystem entry type at {source_entry}")
    for _, destination_directory, directory_mode in reversed(directories):
        _chmod_directory_at(root, destination_directory, directory_mode)


def _selected_snapshot_entries(
    entries: Sequence[_SnapshotEntry],
    touched: set[str] | None,
    protected: Mapping[str, str] | None = None,
) -> tuple[_SnapshotEntry, ...]:
    protected_members = frozenset(() if protected is None else protected)
    if touched is None:
        return tuple(entry for entry in entries if entry.member not in protected_members)
    return tuple(
        entry
        for entry in entries
        if entry.member not in protected_members
        and any(
            member == entry.member or member.startswith(f"{entry.member}/") or entry.member.startswith(f"{member}/")
            for member in touched
        )
    )


def _restore_snapshot(
    bundle_root: Path,
    entries: Sequence[_SnapshotEntry],
    *,
    root: Anchor | None = None,
    touched: set[str] | None = None,
    protected: Mapping[str, str] | None = None,
) -> None:
    close_root = root is None
    if root is None:
        root = _open_root(bundle_root)
    selected = _selected_snapshot_entries(entries, touched, protected)
    try:
        for entry in sorted(selected, key=lambda current: current.member.count("/"), reverse=True):
            _remove_live_entry(root, entry.member)
        for entry in sorted(selected, key=lambda current: current.member.count("/")):
            if not entry.existed:
                continue
            assert entry.backup is not None
            _copy_backup_to_live(root, entry.backup, entry.member)
        _fsync_live_directory(root)
    finally:
        if close_root:
            root.close()


def _verify_snapshot(
    root: Anchor,
    entries: Sequence[_SnapshotEntry],
    protected: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    protected = {} if protected is None else protected
    failures = [
        f"{member}: external entry preserved; captured preimage remains in quarantine {quarantine}"
        for member, quarantine in sorted(protected.items())
    ]
    for entry in entries:
        if entry.member in protected:
            continue
        try:
            exists = _lexists_at(root, entry.member)
        except (OSError, ValueError) as exc:
            failures.append(f"{entry.member}: restored entry cannot be located safely: {exc}")
            continue
        if not entry.existed:
            if exists:
                failures.append(f"{entry.member}: originally absent target remains")
            continue
        if not exists:
            failures.append(f"{entry.member}: original entry is missing")
            continue
        assert entry.fingerprint is not None
        try:
            fingerprint = _entry_fingerprint_at(root, entry.member)
        except (OSError, ValueError) as exc:
            failures.append(f"{entry.member}: restored entry cannot be verified: {exc}")
            continue
        if fingerprint != entry.fingerprint:
            failures.append(f"{entry.member}: restored entry differs from snapshot")
    return tuple(failures)


def _stage_writes(plan: WorkMutationPlan, transaction_dir: Path, root: Anchor | None = None) -> dict[str, Path]:
    staging = transaction_dir / "staging"
    staging.mkdir()
    close_root = root is None
    if root is None:
        root = _open_root(plan.root)
    staged: dict[str, Path] = {}
    try:
        for index, write in enumerate(sorted(plan.writes, key=lambda current: current.member)):
            path = staging / f"{index:06d}"
            with path.open("xb") as stream:
                stream.write(write.after)
                if write.before_digest is not None:
                    preimage_mode = _lstat_at(root, write.source_member or write.member).st_mode
                    anchors.set_mode(stream.fileno(), path, stat.S_IMODE(preimage_mode))
                stream.flush()
                os.fsync(stream.fileno())
            staged[write.member] = path
        _fsync_directory(staging)
        _fsync_directory(transaction_dir)
        return staged
    finally:
        if close_root:
            root.close()


def _mapped_directory_modes(plan: WorkMutationPlan, root: Anchor) -> tuple[_DirectoryMode, ...]:
    ordered_mapping = sorted(
        plan.path_mapping.items(),
        key=lambda item: len(PurePosixPath(item[1]).parts),
        reverse=True,
    )
    modes: list[_DirectoryMode] = []
    for destination in plan.mkdirs:
        for source_root, destination_root in ordered_mapping:
            if destination == destination_root:
                source = source_root
            elif destination.startswith(f"{destination_root}/"):
                source = f"{source_root}{destination[len(destination_root) :]}"
            else:
                continue
            if _lexists_at(root, source):
                info = _lstat_at(root, source)
                if stat.S_ISDIR(info.st_mode):
                    modes.append(_DirectoryMode(source, destination, stat.S_IMODE(info.st_mode)))
            break
    return tuple(sorted(modes, key=lambda item: (item.destination.count("/"), item.destination)))


def _verify_directory_modes(root: Anchor, modes: Sequence[_DirectoryMode]) -> None:
    """Refuse if a mapped source directory's mode drifted since planning.

    Compares each captured `S_IMODE` against the directory's current one and
    raises before the first live effect on any mismatch. On the strong
    (POSIX) tier this resolves every mode bit; on the weak (Windows) tier it
    resolves only `S_IWRITE`, since that is the only bit NTFS's `chmod`
    honours -- see ADR 2026-08-27-two-declared-durability L8. `_apply_directory_modes` restores the
    captured mode onto the destination after the effects land, at the same
    per-tier resolution.
    """
    for captured in modes:
        try:
            current = _lstat_at(root, captured.source)
        except OSError as exc:
            raise ValueError(f"{captured.source}: directory mode changed since planning ({exc}); re-plan") from exc
        if not stat.S_ISDIR(current.st_mode) or stat.S_IMODE(current.st_mode) != captured.mode:
            raise ValueError(f"{captured.source}: directory mode changed since planning; re-plan")


def _effects(plan: WorkMutationPlan, staged: dict[str, Path], root: Anchor | None = None) -> tuple[_Effect, ...]:
    mkdirs = tuple(
        _Effect("mkdir", member) for member in sorted(plan.mkdirs, key=lambda current: (current.count("/"), current))
    )
    moves = tuple(_Effect("move", move.source, destination=move.dest) for move in sorted(plan.moves, key=_move_key))
    writes = tuple(_Effect("write", member, staged=path) for member, path in sorted(staged.items()))
    non_directories: list[_Effect] = []
    directories: list[_Effect] = []
    for member in plan.deletes:
        effect = _Effect("delete", member)
        if root is None:
            target = plan.root / member
            is_directory = target.is_dir() and not target.is_symlink()
        else:
            try:
                is_directory = stat.S_ISDIR(_lstat_at(root, member).st_mode)
            except FileNotFoundError:
                is_directory = False
        (directories if is_directory else non_directories).append(effect)
    deletes = (
        *sorted(non_directories, key=lambda effect: effect.member),
        *sorted(directories, key=lambda effect: (-effect.member.count("/"), effect.member)),
    )
    return (*mkdirs, *moves, *writes, *deletes)


def _move_key(move: Move) -> tuple[str, str]:
    return (move.source, move.dest)


def _rename_noreplace_at(source: Anchor, source_name: str, destination: Anchor, destination_name: str) -> None:
    source.rename_noreplace(source_name, destination, destination_name)


def _write_all(descriptor: int, stream: IO[bytes]) -> None:
    while chunk := stream.read(1024 * 1024):
        offset = 0
        while offset < len(chunk):
            offset += os.write(descriptor, chunk[offset:])


def _live_temporary(parent: Anchor, target_name: str, staged: Path) -> str:
    """Write the staged bytes to a fresh, writable sibling of *target_name*.

    *staged*'s mode may carry a read-only bit copied from the live member's own
    preimage (`_stage_writes`). Stamping that mode onto this temporary file --
    whether at creation or immediately after the write -- leaves the temporary
    (and, once linked, the published member sharing its inode) read-only before
    `_commit_write` gets a chance to `_fsync_live_file` the published name, which
    needs a fresh write-capable reopen of it. Stay writable here; the caller
    stamps the captured mode only after that final fsync succeeds (`_commit_write`
    -> `_stamp_live_mode`) -- the same reordering `_apply_captured_mode` applies on
    the snapshot/backup path, one level up.
    """
    temporary = f".{target_name}.{uuid.uuid4().hex}.tmp"
    mode = stat.S_IMODE(staged.stat().st_mode)
    descriptor = parent.open_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode | stat.S_IWRITE)
    try:
        with staged.open("rb") as stream:
            _write_all(descriptor, stream)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_live_directory(parent)
    return temporary


def _stamp_live_mode(root: Anchor, member: str, mode: int) -> None:
    """Stamp *member*'s final captured mode, once it is safe to (after its last fsync).

    Reopens with write access deliberately: on this tier `anchors.set_mode`
    prefers `os.fchmod` (Python 3.13+), which needs a handle already carrying
    write-attribute access, not a read-only one -- see `_clear_read_only_before_unlink`'s
    docstring for the same fchmod/read-only chicken-and-egg on the delete path.
    """
    parent, name = _open_parent(root, member)
    try:
        descriptor = parent.open_file(name, _fsync_file_flags())
        try:
            anchors.set_mode(descriptor, lambda: parent.resolve_descendant(name), mode)
        finally:
            os.close(descriptor)
    finally:
        parent.close()


def _digest_in_directory(parent: Anchor, name: str) -> str:
    descriptor = parent.open_file(name, _file_flags())
    try:
        hasher = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            hasher.update(chunk)
        return hasher.hexdigest()
    finally:
        os.close(descriptor)


def _quarantine_member(member: str) -> tuple[str, str]:
    pure = _lexical_member(member)
    name = f".{pure.name}.{uuid.uuid4().hex}.quarantine"
    parent = pure.parent.as_posix()
    return name, name if parent == "." else f"{parent}/{name}"


def _take_custody(
    root: Anchor,
    member: str,
    touched: set[str],
    *,
    defer_fsync: bool = False,
) -> tuple[Anchor, str, str, str, bool]:
    """Move *member* to a unique adjacent name before inspecting its bytes."""
    parent, name = _open_parent(root, member)
    quarantine_name, quarantine_member = _quarantine_member(member)
    previously_touched = member in touched
    try:
        _rename_noreplace_at(parent, name, parent, quarantine_name)
    except FileNotFoundError as exc:
        parent.close()
        raise ValueError(f"{member}: changed since planning (now missing); re-plan") from exc
    touched.add(member)
    if not defer_fsync:
        _fsync_live_directory(parent)
    return parent, name, quarantine_name, quarantine_member, previously_touched


def _preserve_conflict(
    member: str,
    quarantine_member: str,
    protected: dict[str, str],
    detail: str,
) -> _CustodyConflict:
    protected[member] = quarantine_member
    return _CustodyConflict(
        f"{member}: {detail}; external entry preserved and captured preimage retained in quarantine {quarantine_member}"
    )


def _restore_custody_or_raise(
    parent: Anchor,
    name: str,
    quarantine_name: str,
    *,
    member: str,
    quarantine_member: str,
    touched: set[str],
    protected: dict[str, str],
    previously_touched: bool,
    detail: str,
) -> None:
    """Restore captured bytes only when their original name is still absent."""
    captured_identity = _identity_at(parent, quarantine_name)
    try:
        _rename_noreplace_at(parent, quarantine_name, parent, name)
    except FileExistsError as exc:
        raise _preserve_conflict(member, quarantine_member, protected, detail) from exc
    if _identity_at(parent, name) != captured_identity:
        protected[member] = member
        raise _CustodyConflict(f"{member}: restored custody identity could not be verified")
    _fsync_live_directory(parent)
    protected.pop(member, None)
    protected.pop(quarantine_member, None)
    if not previously_touched:
        touched.discard(member)
    raise ValueError(f"{member}: {detail}; re-plan")


def _identity_at(parent: Anchor, name: str) -> tuple[int, int]:
    return parent.identity(name)


def _clear_read_only_before_unlink(parent: Anchor, name: str) -> None:
    """Clear a Windows-only read-only attribute before deleting an entry this process already owns.

    `_take_custody` renames a live member aside to its quarantine name before
    this engine inspects or discards it, but renaming does not clear
    `FILE_ATTRIBUTE_READONLY` -- so a read-only source's quarantined preimage
    is itself still read-only, and Windows refuses `unlink()`/`rmdir()` on it.
    The same species of bug `_apply_captured_mode` guards against on the
    snapshot path, here on the live-commit quarantine-cleanup path instead.
    POSIX permission bits never block `unlink()`/`rmdir()` (the containing
    directory's permissions govern, not the entry's own mode), so this is a
    no-op there.

    Dispatches on entry kind: a file gets a direct path `chmod`, not
    `anchors.set_mode` -- `set_mode` prefers `os.fchmod` on Python 3.13+, but
    `fchmod` needs a handle opened with write-attribute access, and a
    still-read-only file refuses exactly that access (the same
    chicken-and-egg `_apply_captured_mode` sidesteps by only ever running on
    a backup this process just wrote, never a live file that started out
    read-only). A directory instead goes through `Anchor.chmod_child_directory`,
    which already carries the per-tier direct-path-chmod approach for
    directories -- see its docstring: NTFS `chmod` only ever honours the
    read-only bit here anyway.
    """
    if sys.platform != "win32":
        return
    info = parent.lstat(name)
    mode = stat.S_IMODE(info.st_mode)
    if mode & stat.S_IWRITE:
        return
    if stat.S_ISDIR(info.st_mode):
        parent.chmod_child_directory(name, mode | stat.S_IWRITE)
    else:
        parent.resolve_descendant(name).chmod(mode | stat.S_IWRITE)


def _entry_identity(info: os.stat_result) -> _EntryIdentity:
    return _EntryIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        kind=stat.S_IFMT(info.st_mode),
        mode=stat.S_IMODE(info.st_mode),
    )


def _commit_write(
    root: Anchor,
    effect: _Effect,
    expected_digest: str | None,
    touched: set[str],
    protected: dict[str, str],
) -> None:
    assert effect.staged is not None
    parent, name = _open_parent(root, effect.member)
    temporary = _live_temporary(parent, name, effect.staged)
    try:
        if expected_digest is None:
            try:
                parent.link(temporary, name)
            except FileExistsError as exc:
                raise ValueError(f"{effect.member}: changed since planning (now exists); re-plan") from exc
            touched.add(effect.member)
        else:
            parent.close()
            parent, name, quarantine, quarantine_member, previously_touched = _take_custody(
                root, effect.member, touched
            )
            try:
                actual = _digest_in_directory(parent, quarantine)
            except Exception as exc:
                _restore_custody_or_raise(
                    parent,
                    name,
                    quarantine,
                    member=effect.member,
                    quarantine_member=quarantine_member,
                    touched=touched,
                    protected=protected,
                    previously_touched=previously_touched,
                    detail=f"changed since planning ({exc})",
                )
                raise AssertionError("custody recovery must raise") from exc
            # No line-ending diagnosis here, deliberately (D-095): the preflight above
            # already revalidated every digest over the same members immediately before
            # the first live effect, so a miss reaching this commit-time check means the
            # bytes changed *between* preflight and commit -- a genuine concurrent write,
            # which a line-ending story would misdescribe. Diagnosing here would also cost
            # a second read: this check digests through a descriptor and never holds the
            # raw bytes the way the preflight's write-preimage check does.
            if actual != expected_digest:
                _restore_custody_or_raise(
                    parent,
                    name,
                    quarantine,
                    member=effect.member,
                    quarantine_member=quarantine_member,
                    touched=touched,
                    protected=protected,
                    previously_touched=previously_touched,
                    detail="changed since planning",
                )
            try:
                parent.link(temporary, name)
            except FileExistsError as exc:
                raise _preserve_conflict(
                    effect.member, quarantine_member, protected, "name was recreated during commit"
                ) from exc
            touched.add(effect.member)
            if _identity_at(parent, name) != _identity_at(parent, temporary):
                raise _preserve_conflict(
                    effect.member, quarantine_member, protected, "installed name changed during commit"
                )
            _clear_read_only_before_unlink(parent, quarantine)
            parent.unlink(quarantine)
        # `temporary` and `name` are hard-linked to the same inode at this point,
        # and NTFS's FILE_ATTRIBUTE_READONLY is shared across hard links -- so the
        # temp name must be unlinked (and, since it is a name this process still
        # owns, cleared of read-only first) BEFORE `_stamp_live_mode` stamps the
        # captured mode onto the published name below. Clearing read-only via the
        # temp name *after* the stamp would silently clear it right back off the
        # published name too. `_fsync_live_file` also needs a write-capable reopen
        # of the published name, so it stays after this unlink as well.
        _clear_read_only_before_unlink(parent, temporary)
        parent.unlink(temporary)
        _fsync_live_file(root, effect.member)
        _stamp_live_mode(root, effect.member, stat.S_IMODE(effect.staged.stat().st_mode))
    finally:
        with suppress(FileNotFoundError):
            _clear_read_only_before_unlink(parent, temporary)
            parent.unlink(temporary)
        _fsync_live_directory(parent)
        parent.close()


def _recover_move_custody_or_raise(
    root: Anchor,
    effect: _Effect,
    *,
    source: Anchor,
    source_name: str,
    quarantine: str,
    quarantine_member: str,
    destination: Anchor | None,
    destination_name: str,
    captured_at_destination: bool,
    captured_identity: tuple[int, int],
    expected_fingerprint: str,
    touched: set[str],
    protected: dict[str, str],
    previously_touched: bool,
    destination_was_touched: bool,
    original: Exception,
) -> None:
    """Recover one captured move without overwriting any concurrent entry."""
    assert effect.destination is not None
    if captured_at_destination:
        assert destination is not None
        try:
            if _identity_at(destination, destination_name) != captured_identity:
                raise ValueError("captured destination identity changed")
            _rename_noreplace_at(destination, destination_name, source, quarantine)
            _fsync_live_directory(destination)
            _fsync_live_directory(source)
        except Exception as recovery_exc:
            raise _CustodyConflict(
                f"{effect.member}: move custody recovery failed; source, destination, and quarantine "
                f"{quarantine_member} were preserved: {recovery_exc}"
            ) from recovery_exc
        captured_at_destination = False
        if not destination_was_touched:
            touched.discard(effect.destination)
        try:
            destination_absent = not _lexists_at(root, effect.destination)
        except OSError:
            destination_absent = False
        if destination_absent:
            protected.pop(effect.destination, None)

    if not captured_at_destination:
        try:
            destination_absent = not _lexists_at(root, effect.destination)
        except OSError:
            destination_absent = False
        if destination_absent:
            protected.pop(effect.destination, None)
        try:
            if _identity_at(source, quarantine) != captured_identity:
                raise ValueError("quarantine identity changed")
            _rename_noreplace_at(source, quarantine, source, source_name)
        except FileExistsError as recovery_exc:
            raise _CustodyConflict(
                f"{effect.member}: move custody recovery found a recreated source; external entry preserved "
                f"and captured preimage retained in quarantine {quarantine_member}"
            ) from recovery_exc
        except Exception as recovery_exc:
            raise _CustodyConflict(
                f"{effect.member}: move custody recovery failed; source and quarantine {quarantine_member} "
                f"were preserved: {recovery_exc}"
            ) from recovery_exc
        _fsync_live_directory(source)
        try:
            if _identity_at(source, source_name) != captured_identity:
                raise ValueError("restored source identity changed")
            if _entry_fingerprint_at(root, effect.member) != expected_fingerprint:
                raise ValueError("restored source content changed")
            if _lexists_at(root, quarantine_member):
                raise ValueError("quarantine remains after source restoration")
        except Exception as recovery_exc:
            protected[effect.member] = effect.member
            protected.pop(quarantine_member, None)
            raise _CustodyConflict(
                f"{effect.member}: restored move custody could not be verified: {recovery_exc}"
            ) from recovery_exc
        protected.pop(effect.member, None)
        protected.pop(quarantine_member, None)
        if not previously_touched:
            touched.discard(effect.member)
        raise original

    raise AssertionError("captured move entry has no recoverable location")


def _commit_move(
    root: Anchor,
    effect: _Effect,
    expected_fingerprint: str,
    expected_identity: _EntryIdentity,
    touched: set[str],
    protected: dict[str, str],
) -> None:
    assert effect.destination is not None
    source, source_name, quarantine, quarantine_member, previously_touched = _take_custody(
        root,
        effect.member,
        touched,
        defer_fsync=True,
    )
    captured_identity = (expected_identity.device, expected_identity.inode)
    protected[effect.member] = quarantine_member
    protected[quarantine_member] = quarantine_member
    protected[effect.destination] = effect.destination
    destination: Anchor | None = None
    destination_name = PurePosixPath(effect.destination).name
    captured_at_destination = False
    destination_was_touched = effect.destination in touched
    try:
        _fsync_live_directory(source)
        captured_info = source.lstat(quarantine)
        if _entry_identity(captured_info) != expected_identity:
            raise ValueError(f"{effect.member}: changed since planning; re-plan")
        if _entry_fingerprint_at(root, quarantine_member) != expected_fingerprint:
            raise ValueError(f"{effect.member}: changed since planning; re-plan")
        if _lexists_at(root, effect.member):
            raise _CustodyConflict(
                f"{effect.member}: name was recreated during custody verification; "
                f"external entry preserved and captured preimage retained in quarantine {quarantine_member}"
            )
        destination, destination_name = _open_parent(root, effect.destination)
        _rename_noreplace_at(source, quarantine, destination, destination_name)
        captured_at_destination = True
        touched.update((effect.member, effect.destination))
        if _identity_at(destination, destination_name) != captured_identity:
            raise _CustodyConflict(f"{effect.member}: captured entry changed while moving")
        if _entry_fingerprint_at(root, effect.destination) != expected_fingerprint:
            raise _CustodyConflict(f"{effect.member}: captured entry changed while moving")
        if _lexists_at(root, effect.member):
            raise _CustodyConflict(f"{effect.member}: name was recreated during move")
        destination_mode = destination.lstat(destination_name).st_mode
        if stat.S_ISREG(destination_mode):
            _fsync_live_file(root, effect.destination)
        _fsync_live_directory(source)
        if destination is not source:
            _fsync_live_directory(destination)
    except Exception as exc:
        _recover_move_custody_or_raise(
            root,
            effect,
            source=source,
            source_name=source_name,
            quarantine=quarantine,
            quarantine_member=quarantine_member,
            destination=destination,
            destination_name=destination_name,
            captured_at_destination=captured_at_destination,
            captured_identity=captured_identity,
            expected_fingerprint=expected_fingerprint,
            touched=touched,
            protected=protected,
            previously_touched=previously_touched,
            destination_was_touched=destination_was_touched,
            original=exc,
        )
        raise AssertionError("move custody recovery must raise") from exc
    else:
        protected.pop(effect.member, None)
        protected.pop(quarantine_member, None)
        protected.pop(effect.destination, None)
    finally:
        if destination is not None:
            destination.close()
        source.close()


def _commit_effect(
    bundle_root: Path,
    effect: _Effect,
    *,
    root: Anchor | None = None,
    write_digests: dict[str, str | None] | None = None,
    expected_fingerprints: dict[str, str] | None = None,
    expected_identities: dict[str, _EntryIdentity] | None = None,
    absent_directories: set[str] | None = None,
    touched: set[str] | None = None,
    protected: dict[str, str] | None = None,
) -> None:
    close_root = root is None
    if root is None:
        root = _open_root(bundle_root)
    if touched is None:
        touched = set()
    if protected is None:
        protected = {}
    try:
        if effect.kind == "mkdir":
            parent, name = _open_parent(
                root,
                effect.member,
                create=True,
                touched=touched,
                must_create=absent_directories,
            )
            try:
                try:
                    info = parent.lstat(name)
                except FileNotFoundError:
                    parent.mkdir(name)
                    touched.add(effect.member)
                    directory = parent.open_child(name)
                    try:
                        _fsync_live_directory(directory)
                    finally:
                        directory.close()
                    _fsync_live_directory(parent)
                else:
                    if absent_directories is not None and effect.member in absent_directories:
                        raise ValueError(f"{effect.member}: directory changed since planning (now exists); re-plan")
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError(f"{effect.member}: mkdir target is not a directory")
                    directory = parent.open_child(name)
                    directory.close()
            finally:
                parent.close()
            return
        if effect.kind == "move":
            assert expected_fingerprints is not None
            assert expected_identities is not None
            _commit_move(
                root,
                effect,
                expected_fingerprints[effect.member],
                expected_identities[effect.member],
                touched,
                protected,
            )
            return
        if effect.kind == "write":
            assert write_digests is not None
            _commit_write(root, effect, write_digests[effect.member], touched, protected)
            return
        expected = None if expected_fingerprints is None else expected_fingerprints.get(effect.member)
        if not _lexists_at(root, effect.member):
            if expected is not None:
                raise ValueError(f"{effect.member}: changed since planning (now missing); re-plan")
            return
        parent, name, quarantine, quarantine_member, previously_touched = _take_custody(root, effect.member, touched)
        try:
            info = parent.lstat(quarantine)
            if expected_identities is not None and _entry_identity(info) != expected_identities[effect.member]:
                _restore_custody_or_raise(
                    parent,
                    name,
                    quarantine,
                    member=effect.member,
                    quarantine_member=quarantine_member,
                    touched=touched,
                    protected=protected,
                    previously_touched=previously_touched,
                    detail="changed since planning",
                )
            if (
                expected is not None
                and not stat.S_ISDIR(info.st_mode)
                and _entry_fingerprint_at(root, quarantine_member) != expected
            ):
                _restore_custody_or_raise(
                    parent,
                    name,
                    quarantine,
                    member=effect.member,
                    quarantine_member=quarantine_member,
                    touched=touched,
                    protected=protected,
                    previously_touched=previously_touched,
                    detail="changed since planning",
                )
            if _lexists_at(root, effect.member):
                raise _preserve_conflict(
                    effect.member, quarantine_member, protected, "name was recreated during custody verification"
                )
            try:
                _clear_read_only_before_unlink(parent, quarantine)
                if stat.S_ISDIR(info.st_mode):
                    parent.rmdir(quarantine)
                else:
                    parent.unlink(quarantine)
            except OSError as exc:
                _restore_custody_or_raise(
                    parent,
                    name,
                    quarantine,
                    member=effect.member,
                    quarantine_member=quarantine_member,
                    touched=touched,
                    protected=protected,
                    previously_touched=previously_touched,
                    detail=f"could not delete captured entry ({exc})",
                )
                raise AssertionError("custody recovery must raise") from exc
            touched.add(effect.member)
            _fsync_live_directory(parent)
        finally:
            parent.close()
    finally:
        if close_root:
            root.close()


def _apply_directory_modes(root: Anchor, modes: Sequence[_DirectoryMode], touched: set[str]) -> None:
    """Restore each captured mode onto its destination. See `_verify_directory_modes`."""
    for captured in sorted(modes, key=lambda item: item.destination.count("/"), reverse=True):
        current = _lstat_at(root, captured.destination)
        if not stat.S_ISDIR(current.st_mode):
            raise ValueError(f"{captured.destination}: mapped directory is no longer a directory")
        if stat.S_IMODE(current.st_mode) != captured.mode:
            touched.add(captured.destination)
            _chmod_directory_at(root, captured.destination, captured.mode)


def _affected_index_members(plan: WorkMutationPlan) -> tuple[str, ...]:
    members = {
        member
        for member in (
            *(write.member for write in plan.writes),
            *(move.dest for move in plan.moves),
        )
        if _is_lane_index(member)
    }
    return tuple(sorted(members))


def _is_lane_index(member: str) -> bool:
    pure = PurePosixPath(member)
    if pure.name != "index.md":
        return False
    lane = pure.parent
    if lane.as_posix() in {"work", "work/_archive"}:
        return True
    parts = lane.parts
    if parts[-1:] == ("children",):
        owner = PurePosixPath(*parts[:-1]).as_posix()
    elif parts[-2:] == ("children", "_archive"):
        owner = PurePosixPath(*parts[:-2]).as_posix()
    else:
        return False
    return parse_item_path(owner) is not None


def _map_member(path_mapping: Mapping[str, str], member: str) -> str:
    """*member*'s path after the mutation, per the plan's item-path mapping.

    Mirrors `work_tracker_okf.mutation._member_mapping`'s matching rule --
    deepest source first, so a nested child that moves independently of its
    ancestor wins -- but answers for one member without a bundle in hand,
    which is what the pre-mutation baseline has.
    """
    for source in sorted(path_mapping, key=lambda path: (path.count("/"), len(path)), reverse=True):
        destination = path_mapping[source]
        if member == f"{source}.md":
            return f"{destination}.md"
        if member.startswith(f"{source}/"):
            return f"{destination}{member[len(source) :]}"
    return member


def _item_conditions(item: WorkItem, by_path: dict[str, WorkItem]) -> tuple[tuple[str, str], ...]:
    """The structural problems *item* carries, as `(kind, message)` pairs.

    The kind is what the differential gate counts: an operation is at fault
    only when it raises the number of problems of one kind on one item.  The
    message stays free-form because it is only ever reported, never compared.
    """
    conditions: list[tuple[str, str]] = []
    if item.parent_path is not None and item.parent_path not in by_path:
        conditions.append(("parent-missing", f"{item.path}: parent {item.parent_path!r} is missing after mutation"))
    for edge in item.dependency_edges:
        if edge.path not in by_path:
            conditions.append(
                ("dependency-missing", f"{item.path}: dependency target {edge.path!r} is missing after mutation")
            )
    for issue in item.dependency_issues:
        conditions.append((f"dependency-{issue.code}", f"{item.path}: dependency {issue.code}: {issue.detail}"))
    return tuple(conditions)


@dataclass(frozen=True, slots=True)
class _ValidationState:
    """One bundle revision's validation outcome, counted and forward-mapped.

    Both halves are multisets rather than sets: what makes an operation
    culpable is *raising the count* of a `(path, code)` or `(path, kind)`
    pair, not the pair existing.  Keys carry no message -- a message embeds
    canonical paths that a move rewrites unevenly, so message identity would
    mark surviving pre-existing failures as new.
    """

    findings: Mapping[tuple[str, str], int]
    conditions: Mapping[tuple[str, str], int]


def _extra_rules(
    layout: WorkspaceLayout, repo_root: Path | None, repo_roots: tuple[Path, ...] = ()
) -> tuple[Rule, ...]:
    """The one rule set both sides of the differential gate validate against.

    *repo_root* and *repo_roots* together are the code roots a repo path may
    resolve under (any one suffices); with neither, `layout.repo_root`.
    """
    validation_root = layout.bundle_dir
    declarations_dir = layout.config_dir if (layout.config_dir / "schema").is_dir() else validation_root
    roots = repo_roots if repo_root is None else (repo_root, *repo_roots)
    if not roots and layout.repo_root is not None:
        roots = (layout.repo_root,)
    return rule_set(
        validation_root,
        repo_roots=roots,
        vault_root=layout.root,
        declarations_dir=declarations_dir,
    )


def _gate_scope(plan: WorkMutationPlan) -> frozenset[str]:
    """The members the postcondition gate actually reads.

    Identical to the `targeted_members` set `_validate_postconditions` has
    always filtered its report down to. Handing it to `okf_io.validate` as
    `scope=` means the per-document rules stop *computing* the findings the
    filter was going to discard -- 10.9s to 0.7s on a 1,700-concept vault. The
    filter stays: cross-document rules are deliberately unscoped, so they can
    still report against a member outside this set.
    """
    members = {f"{path}.md" for path in plan.validate_paths}
    members.update(_affected_index_members(plan))
    return frozenset(members)


def _baseline_scope(plan: WorkMutationPlan) -> frozenset[str]:
    """The pre-mutation members whose findings the gate will consult.

    The baseline is captured before any effect, and `_capture_validation_state`
    keys every finding by `_map_member(plan.path_mapping, finding.path)` -- the
    path the member will have *after* the mutation. So the members worth
    validating are exactly the pre-image of `_gate_scope(plan)` under that
    mapping, which for a move is not the same set.

    Inverting the mapping is right rather than merely cheap: scanning the corpus
    for members that map into the gate scope would be O(bundle), and the whole
    point is not to touch the bundle. `path_mapping` is a handful of entries.

    A member that the mapping does not move is its own pre-image, so a plan with
    no moves yields exactly `_gate_scope(plan)`.
    """
    gate = _gate_scope(plan)
    members = {member for member in gate if _map_member(plan.path_mapping, member) in gate}
    for source, destination in plan.path_mapping.items():
        source_member = f"{source}.md"
        if _map_member(plan.path_mapping, source_member) in gate:
            members.add(source_member)
        for member in gate:
            if member.startswith(f"{destination}/"):
                members.add(f"{source}/{member[len(destination) + 1 :]}")
    return frozenset(members)


def _capture_validation_state(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    root: Anchor,
    *,
    repo_root: Path | None,
    repo_roots: tuple[Path, ...] = (),
    bundle: Bundle | None = None,
) -> _ValidationState:
    """Validate the bundle as it stands and key the outcome post-mutation.

    Called under the held bundle lock while the bundle is still pristine.
    Raising is safe here: no effect has been committed yet.

    This function computes two different things, and they are sound for two
    different reasons -- do not conflate them:

    - The **findings** half (`validate(bundle, ..., scope=_baseline_scope(plan))`)
      may reuse a caller-supplied *bundle* (see below), because the pass is
      scoped to `_baseline_scope(plan)` and `_preflight` digest-verifies
      exactly those members under the held lock before any effect runs. A
      concurrent writer can make a reused bundle stale only in regions the
      gate no longer reads.
    - The **conditions** half (`items = load_items(...)` feeding the
      whole-corpus `conditions` Counter via `_item_conditions`) checks
      `parent-missing`/`dependency-missing` against the *full* item map, not
      scoped to the plan. A caller-supplied *bundle* is never used for this
      half, regardless of whether one was passed in: it may have been loaded
      before the lock was taken, and nothing digest-verifies items outside
      `_baseline_scope(plan)`, so a concurrent edit to a wholly unrelated
      item's parent/dependency could otherwise leave this Counter reflecting
      stale corpus-wide state. `items` is therefore always loaded fresh, under
      the lock, via the same `_load_bundle_through(..., ignore=IGNORE)` call
      used when no *bundle* is supplied at all -- `load_items` is cheap
      (~0.01s), so this costs nothing worth avoiding.

    *bundle* lets the caller hand over a bundle it already loaded with the same
    `ignore=IGNORE` set, skipping a second full load *for the findings half
    only*. It was loaded before the lock was taken, which is safe there only
    because the pass is scoped as described above. **Never pass a bundle
    loaded with a different `ignore` set** -- that would silently validate a
    different corpus.
    """
    validation_root = layout.bundle_dir
    _assert_root_identity(validation_root, root)
    bundle_supplied = bundle is not None
    if bundle is None:
        bundle = _load_bundle_through(root, validation_root, ignore=IGNORE)
    _assert_root_identity(validation_root, root)
    # The conditions half always loads fresh under the lock -- see the
    # docstring above. It is never derived from a caller-supplied `bundle`.
    # When no `bundle` was supplied, the load just above already happened
    # fresh under the lock, so it doubles as this load too -- only the
    # caller-supplied-bundle case pays for a second one.
    if bundle_supplied:
        conditions_bundle = _load_bundle_through(root, validation_root, ignore=IGNORE)
        _assert_root_identity(validation_root, root)
    else:
        conditions_bundle = bundle
    items = load_items(conditions_bundle)
    # `links=` deliberately left unwired: this function builds no `LinkGraph`
    # of its own before this call, and the postcondition pass in
    # `_validate_postconditions` validates a different bundle state (the
    # post-mutation bundle, not this pristine one) -- sharing a `LinkGraph`
    # between the two passes would be unsound, not merely un-optimized.
    report = validate(
        bundle,
        today=date.max,
        extra_rules=_extra_rules(layout, repo_root, repo_roots),
        scope=_baseline_scope(plan),
    )
    _assert_root_identity(validation_root, root)
    by_path = {item.path: item for item in items}
    findings = Counter(
        (_map_member(plan.path_mapping, finding.path), finding.code)
        for finding in report.errors
        if finding.path is not None
    )
    conditions: Counter[tuple[str, str]] = Counter()
    for item in items:
        mapped = plan.path_mapping.get(item.path, item.path)
        conditions.update((mapped, kind) for kind, _message in _item_conditions(item, by_path))
    return _ValidationState(findings=dict(findings), conditions=dict(conditions))


def _validate_postconditions(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    root: Anchor | None = None,
    *,
    repo_root: Path | None = None,
    repo_roots: tuple[Path, ...] = (),
    baseline: _ValidationState | None = None,
    excused: list[str] | None = None,
    allowed_new_findings: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    """Fail on what *this* mutation broke, not on what was already broken.

    With a *baseline* in hand the gate is differential: a `(path, code)` or
    `(path, kind)` pair fails only where its post-mutation count exceeds the
    baseline count, and the surplus-free remainder is appended to *excused*
    for the caller to report. Explicit *allowed_new_findings* budgets excuse
    surplus findings separately, only with a baseline. Without one it is the
    absolute gate it always was. The lane-index staleness check is absolute
    either way -- a stale index after the mutation is always the mutation's fault.
    """
    close_root = root is None
    if root is None:
        root = _open_root(layout.bundle_dir)
    validation_root = layout.bundle_dir
    try:
        _assert_root_identity(validation_root, root)
        bundle = _load_bundle_through(root, validation_root, ignore=IGNORE)
        _assert_root_identity(validation_root, root)
        items = load_items(bundle)
    except (OSError, ValueError) as exc:
        reload_failures = (f"postcondition reload failed: {exc}",)
        if close_root:
            root.close()
        return reload_failures
    by_path = {item.path: item for item in items}
    gate_scope = _gate_scope(plan)
    targeted_members = set(gate_scope)
    try:
        # `links=` deliberately left unwired: this function builds no
        # `LinkGraph` of its own before this call, and `_capture_validation_state`
        # validates a different bundle state (the pre-mutation bundle, not this
        # post-mutation one) -- sharing a `LinkGraph` between the two passes
        # would be unsound, not merely un-optimized.
        report = validate(
            bundle,
            today=date.max,
            extra_rules=_extra_rules(layout, repo_root, repo_roots),
            scope=gate_scope,
        )
        _assert_root_identity(validation_root, root)
    except (OSError, ValueError) as exc:
        if close_root:
            root.close()
        return (f"postcondition validation setup failed: {exc}",)
    notes = excused if excused is not None else []
    count_before = len(notes)
    intentional_notes: list[str] = []
    # An unavailable baseline disables even explicit exceptions: fail closed.
    intentional_allowance = Counter(allowed_new_findings) if baseline is not None else Counter()
    finding_allowance = Counter(baseline.findings) if baseline is not None else Counter()
    condition_allowance = Counter(baseline.conditions) if baseline is not None else Counter()

    failures: list[str] = []
    for finding in report.errors:
        if finding.path is None or finding.path not in targeted_members:
            continue
        detail = f"{finding.path}: {finding.code}: {finding.message}"
        key = (finding.path, finding.code)
        if finding_allowance[key] > 0:
            finding_allowance[key] -= 1
            notes.append(f"pre-existing, not caused by this operation: {detail}")
            continue
        if intentional_allowance[key] > 0:
            intentional_allowance[key] -= 1
            intentional_notes.append(
                f"operation-specific validation allowance: {detail}; remains an error in `gw lint`"
            )
            continue
        failures.append(detail)
    for path in plan.validate_paths:
        if parse_item_path(path) is None:
            failures.append(f"{path}: validate path is not canonical")
            continue
        item = by_path.get(path)
        if item is None:
            failures.append(f"{path}: final work item did not reload")
            continue
        for kind, message in _item_conditions(item, by_path):
            key = (path, kind)
            if condition_allowance[key] > 0:
                condition_allowance[key] -= 1
                notes.append(f"pre-existing, not caused by this operation: {message}")
                continue
            failures.append(message)
    excused_here = len(notes) - count_before
    if excused_here:
        notes.append(f"{excused_here} pre-existing validation failure(s) excused; run `gw lint` for bundle health")

    notes.extend(intentional_notes)

    for member in _affected_index_members(plan):
        lane = PurePosixPath(member).parent.as_posix()
        try:
            current = _read_bytes_at(root, member).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(f"{member}: postcondition read failed: {exc}")
            continue
        direct = tuple(
            sorted(
                (
                    item
                    for item in items
                    if (location := parse_item_path(item.path)) is not None and location.lane == lane
                ),
                key=lambda item: item.basename,
            )
        )
        expected = reconcile_entries(current, tuple(render_entry(item) for item in direct))
        if current != expected:
            failures.append(f"{member}: generated direct-descendant inventory is stale")
    try:
        _assert_root_identity(validation_root, root)
    except (OSError, ValueError) as exc:
        failures.append(f"postcondition validation failed: {exc}")
    if close_root:
        root.close()
    return tuple(failures)


def _application(
    transaction_id: str,
    journal: Path,
    plan: WorkMutationPlan,
    *,
    moved: Sequence[tuple[str, str]] = (),
    written: Sequence[str] = (),
    created: Sequence[str] = (),
    failures: Sequence[str] = (),
    rolled_back: bool = False,
    warnings: Sequence[str] | None = None,
) -> MutationApplication:
    return MutationApplication(
        transaction_id=transaction_id,
        journal=journal,
        moved=tuple(moved),
        written=tuple(written),
        created_directories=tuple(created),
        warnings=plan.warnings if warnings is None else tuple(warnings),
        failures=tuple(failures),
        rolled_back=rolled_back,
    )


#: `MutationApplication.transaction_id` for a plan that had nothing to apply.
#: An empty string, never a uuid, so "no transaction directory was opened" is
#: a value a caller can test rather than a directory it has to go looking for.
EMPTY_TRANSACTION_ID = ""

#: How many terminal (complete/rolled-back) transaction directories the prune
#: sweep keeps, newest first by `journal.jsonl` mtime. A module constant, not
#: a literal, so tests can name it.
RETAINED_TRANSACTIONS = 10

#: Every transaction directory name is `uuid.uuid4().hex` -- this is the
#: allow-list that keeps the prune sweep from ever touching a non-transaction
#: sibling (`executor.lock`, a stray `.DS_Store`, ...) in the cache root.
_TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

#: Journal states the prune sweep treats as terminal and therefore prunable.
_PRUNABLE_JOURNAL_STATES = frozenset({"complete", "rolled-back"})


def _terminal_journal_state(content: bytes) -> str | None:
    """The last journal record's `state`, or `None` if it cannot be trusted.

    Deliberately tolerant: any parse failure, a non-dict record, or a missing
    trailing newline (the durability contract every `_append_journal` call
    honors) reads as "no terminal record" -- which pins the directory rather
    than risking pruning rollback evidence.
    """
    try:
        if not content.endswith(b"\n"):
            return None
        lines = content.decode("utf-8").splitlines()
        if not lines:
            return None
        record = json.loads(lines[-1])
    except (UnicodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    state = record.get("state")
    return state if isinstance(state, str) else None


def _prunable_transaction_candidates(transaction_root_anchor: Anchor) -> list[tuple[str, float]]:
    """`(transaction_id, journal_mtime)` pairs eligible for pruning.

    A candidate must be a real directory (never a symlink -- `lstat` is
    checked, not followed), its name must match the uuid4-hex shape, and its
    `journal.jsonl` must parse with a terminal `complete`/`rolled-back` last
    record. Ordered by the journal's own mtime (D-002c): it is
    executor-`O_APPEND`-written only, unlike the directory's mtime, which any
    external process (a Finder `.DS_Store`, an editor) can bump.

    The just-committed transaction is a candidate too -- it is the newest by
    construction, so it naturally lands inside the retained window and counts
    toward `RETAINED_TRANSACTIONS` rather than being kept as an extra.
    """
    candidates: list[tuple[str, float]] = []
    for name in transaction_root_anchor.listdir():
        if not _TRANSACTION_ID_PATTERN.match(name):
            continue
        try:
            info = transaction_root_anchor.lstat(name)
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        try:
            journal_info = _lstat_at(transaction_root_anchor, f"{name}/journal.jsonl")
        except OSError:
            continue
        if not stat.S_ISREG(journal_info.st_mode):
            continue
        try:
            content = _read_bytes_at(transaction_root_anchor, f"{name}/journal.jsonl")
        except OSError:
            continue
        if _terminal_journal_state(content) not in _PRUNABLE_JOURNAL_STATES:
            continue
        candidates.append((name, journal_info.st_mtime))
    return candidates


def _prune_completed_transactions(
    transaction_root_anchor: Anchor,
    *,
    exclude: str,
    excused: list[str],
) -> None:
    """Reclaim terminal transaction directories beyond `RETAINED_TRANSACTIONS`.

    Called only on the success path, after the `complete` journal record is
    durable, inside the same executor lock that created every directory it
    might remove -- no other executor can hold a live transaction directory
    in this cache while that lock is held, so the sweep needs no synchronisation
    of its own.

    *exclude* (the transaction that just committed) is never removed even if
    it somehow fell outside the retained window -- a defensive guard, not the
    mechanism that keeps it: its journal mtime is the newest by construction,
    so it is expected to sort inside `RETAINED_TRANSACTIONS` on its own.

    Every failure is swallowed and recorded in *excused* rather than raised:
    a housekeeping sweep must never roll back a mutation that already
    committed and validated successfully.
    """
    try:
        candidates = _prunable_transaction_candidates(transaction_root_anchor)
        candidates.sort(key=lambda candidate: (candidate[1], candidate[0]), reverse=True)
        for stale_id, _mtime in candidates[RETAINED_TRANSACTIONS:]:
            if stale_id == exclude:
                continue
            _remove_live_entry(transaction_root_anchor, stale_id)
    except Exception as exc:
        excused.append(f"transaction cache prune failed: {exc}")


def _is_wholly_empty(plan: WorkMutationPlan) -> bool:
    """No effects **and** no claims on the filesystem.

    The effect fields alone are not enough: `run_regen_indexes` attaches
    `directory_preconditions` for lanes that were absent when the planner read
    them, and a precondition is an assertion the transaction exists to check.
    A plan carrying one is not empty even when it writes nothing.
    """
    return not (plan.writes or plan.mkdirs or plan.moves or plan.deletes or plan.directory_preconditions)


def _apply_mutation_locked(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    locked_root: Anchor,
    *,
    repo_root: Path | None = None,
    repo_roots: tuple[Path, ...] = (),
    baseline_bundle: Bundle | None = None,
    allowed_new_findings: tuple[tuple[str, str], ...] = (),
    validate_read_set: Callable[[], None] | None = None,
) -> MutationApplication:
    transaction_root = layout.cache_dir / "work-mutations"
    resolved_bundle = layout.bundle_dir.resolve(strict=True)
    resolved_transactions = transaction_root.resolve(strict=False)
    if resolved_transactions.is_relative_to(resolved_bundle):
        raise ValueError("work mutation journal must live outside the bundle")
    with _locked_transaction_root(layout.cache_dir, transaction_root) as (
        cache,
        transaction_root_anchor,
    ):
        if _is_wholly_empty(plan):
            # Equivalence argument (narrowed form -- see `apply_mutation`'s
            # docstring for the original, wider version this replaces):
            #
            # What STILL happens above this point, unconditionally, for every
            # plan including this one: the bundle root is opened and
            # `_bundle_root_lock` is held (in `apply_mutation`, before this
            # function is even entered), and -- just above, via
            # `_locked_transaction_root` -- the `work-mutations` cache
            # directory is opened (creating it on first use) and the
            # cross-process executor lock is acquired and released cleanly by
            # that context manager's own `finally` blocks. Two existing tests
            # (`test_executor_lock_name_swap_never_redirects_lock_io_into_bundle`,
            # `test_work_mutations_open_failure_does_not_leak_cache_descriptor`)
            # exercise exactly that acquisition/open for an all-default,
            # wholly-empty plan and must keep observing it -- which is why the
            # short-circuit sits here, inside `_locked_transaction_root`,
            # rather than before it.
            #
            # What is skipped from this point on: `_new_transaction_directory`
            # (no per-mutation transaction subdirectory, no journal file is
            # created under it) and everything `_apply_mutation_locked` would
            # otherwise do inside that directory -- preflight, snapshotting,
            # staging writes, committing effects, baseline capture, and the
            # postcondition `validate()` pass. `_is_wholly_empty` does not
            # inspect `validate_paths`, so this holds even for a plan with no
            # effects but a non-empty `validate_paths`: `_effects(plan, ...)`
            # yields nothing to commit either way, and `_affected_index_members(plan)`
            # is empty so the absolute lane-index staleness check has nothing
            # to check. `_gate_scope(plan)` is `{f"{p}.md" for p in
            # plan.validate_paths} | _affected_index_members(plan)` --
            # `validate_paths` WIDENS the gate scope, it never narrows it --
            # so for an effect-free plan the members it names would be
            # validated by a full pass against an identical, unmutated
            # baseline, and only the two absolute (never baseline-excused)
            # `validate_paths`-specific checks in `_validate_postconditions`
            # ("validate path is not canonical", "final work item did not
            # reload") plus preflight's path-safety walk are foregone by
            # skipping validation entirely for such a plan. No shipped caller
            # today builds an effect-free plan with a non-empty
            # `validate_paths` (every call site that sets `validate_paths`
            # also attaches at least one write), so this is a latent gap in
            # the short-circuit's coverage, not an observed regression --
            # `_is_wholly_empty` is the agreed predicate and is not changed
            # here to close it. For every plan `_is_wholly_empty` actually
            # admits today, returning here changes no observable outcome: no
            # caller can distinguish "transaction opened and immediately
            # closed with nothing done" from "no transaction directory was
            # opened", since `MutationApplication` carries no such directory
            # reference back and the journal path returned below was never
            # created.
            return _application(
                EMPTY_TRANSACTION_ID,
                transaction_root,
                plan,
            )
        transaction_id = uuid.uuid4().hex
        configured_transaction_dir = transaction_root / transaction_id
        with _new_transaction_directory(
            transaction_root_anchor,
            configured_transaction_dir,
            transaction_id,
        ) as (transaction, transaction_dir, journal_fd):
            journal = configured_transaction_dir / "journal.jsonl"
            journal_storage = transaction_dir / "journal.jsonl"
            planned_details: dict[str, object] = {
                "deletes": list(plan.deletes),
                "mkdirs": list(plan.mkdirs),
                "moves": [{"source": move.source, "destination": move.dest} for move in plan.moves],
                "operation": plan.operation,
                "transaction_id": transaction_id,
                "validate_paths": list(plan.validate_paths),
                "writes": [write.member for write in plan.writes],
            }
            if allowed_new_findings:
                planned_details["allowed_new_findings"] = [list(key) for key in allowed_new_findings]
            planned_record = _journal_record("planned", planned_details)
            _append_journal(
                journal_storage,
                "planned",
                _parent=transaction,
                _journal_fd=journal_fd,
                **planned_details,
            )

            snapshots: tuple[_SnapshotEntry, ...] = ()
            moved: list[tuple[str, str]] = []
            written: list[str] = []
            created: list[str] = []
            touched: set[str] = set()
            protected: dict[str, str] = {}
            effect_attempted = False
            phase = "preflight"
            baseline: _ValidationState | None = None
            excused: list[str] = []
            root = locked_root.duplicate()
            try:
                try:
                    _assert_directory_identity(layout.cache_dir, cache, "cache")
                    _assert_directory_identity(configured_transaction_dir, transaction, "transaction")
                    _assert_root_identity(layout.bundle_dir, root)
                    preflight_initial_scratch = transaction_dir / "preflight-initial"
                    try:
                        _preflight(layout, plan, root, preflight_initial_scratch)
                    finally:
                        _remove_entry(preflight_initial_scratch)
                    created = [member for member in plan.mkdirs if not _lexists_at(root, member)]
                    snapshot_records = [
                        {"member": member, "existed": _lexists_at(root, member)} for member in _snapshot_targets(plan)
                    ]
                    directory_modes = _mapped_directory_modes(plan, root)
                    snapshots = _create_snapshot(plan, transaction_dir, root)
                    staged = _stage_writes(plan, transaction_dir, root)
                    preflight_final_scratch = transaction_dir / "preflight-final"
                    try:
                        _preflight(layout, plan, root, preflight_final_scratch)
                    finally:
                        _remove_entry(preflight_final_scratch)
                    _verify_directory_modes(root, directory_modes)
                    try:
                        baseline = _capture_validation_state(
                            layout, plan, root, repo_root=repo_root, repo_roots=repo_roots, bundle=baseline_bundle
                        )
                    except (OSError, ValueError) as exc:
                        baseline = None
                        excused.append(
                            "baseline capture failed, falling back to absolute postcondition gate "
                            f"for this mutation: {exc}"
                        )
                    _assert_root_identity(layout.bundle_dir, root)
                    expected_fingerprints = {
                        member: _entry_fingerprint_at(root, member)
                        for member in {*(move.source for move in plan.moves), *plan.deletes}
                        if _lexists_at(root, member)
                    }
                    expected_identities = {
                        member: _entry_identity(_lstat_at(root, member))
                        for member in {*(move.source for move in plan.moves), *plan.deletes}
                        if _lexists_at(root, member)
                    }
                    absent_directories = {
                        condition.member
                        for condition in plan.directory_preconditions
                        if condition.before_digest is None
                    }
                    write_digests = {
                        write.member: write.before_digest if write.source_member is None else None
                        for write in plan.writes
                    }
                    applying_details: dict[str, object] = {
                        "backups": [
                            {
                                "member": entry.member,
                                "path": (
                                    None
                                    if entry.backup is None
                                    else entry.backup.relative_to(transaction_dir).as_posix()
                                ),
                            }
                            for entry in snapshots
                        ],
                        "snapshots": snapshot_records,
                    }
                    applying_record = _journal_record("applying", applying_details)
                    _append_journal(
                        journal_storage,
                        "applying",
                        _parent=transaction,
                        _journal_fd=journal_fd,
                        **applying_details,
                    )
                    phase = "apply"
                    _assert_directory_identity(layout.cache_dir, cache, "cache")
                    _assert_directory_identity(configured_transaction_dir, transaction, "transaction")
                    if validate_read_set is not None:
                        validate_read_set()
                    for effect in _effects(plan, staged, root):
                        effect_attempted = True
                        _commit_effect(
                            plan.root,
                            effect,
                            root=root,
                            write_digests=write_digests,
                            expected_fingerprints=expected_fingerprints,
                            expected_identities=expected_identities,
                            absent_directories=absent_directories,
                            touched=touched,
                            protected=protected,
                        )
                        if effect.kind == "move":
                            assert effect.destination is not None
                            moved.append((effect.member, effect.destination))
                        elif effect.kind == "write":
                            written.append(effect.member)
                    _apply_directory_modes(root, directory_modes, touched)
                    _assert_root_identity(layout.bundle_dir, root)
                    _assert_directory_identity(layout.cache_dir, cache, "cache")
                    _assert_directory_identity(configured_transaction_dir, transaction, "transaction")
                    _fsync_live_directory(root)
                    phase = "validation"
                    validating_details: dict[str, object] = {"validate_paths": list(plan.validate_paths)}
                    validating_record = _journal_record("validating", validating_details)
                    _append_journal(
                        journal_storage,
                        "validating",
                        _parent=transaction,
                        _journal_fd=journal_fd,
                        **validating_details,
                    )
                    postcondition_failures = _validate_postconditions(
                        layout,
                        plan,
                        root,
                        repo_root=repo_root,
                        repo_roots=repo_roots,
                        baseline=baseline,
                        excused=excused,
                        allowed_new_findings=allowed_new_findings,
                    )
                    if postcondition_failures:
                        raise ValueError("; ".join(postcondition_failures))
                    _assert_root_identity(layout.bundle_dir, root)
                    _assert_directory_identity(layout.cache_dir, cache, "cache")
                    _assert_directory_identity(configured_transaction_dir, transaction, "transaction")
                    _fsync_live_directory(root)
                    complete_record = _complete_record(transaction_id, moved, written, created)
                    try:
                        _append_journal(
                            journal_storage,
                            "complete",
                            _parent=transaction,
                            _journal_fd=journal_fd,
                            **{key: value for key, value in complete_record.items() if key != "state"},
                        )
                    except Exception:
                        if not _journal_has_terminal_complete(
                            journal_storage,
                            (planned_record, applying_record, validating_record, complete_record),
                            journal_fd=journal_fd,
                        ):
                            raise
                    _prune_completed_transactions(
                        transaction_root_anchor,
                        exclude=transaction_id,
                        excused=excused,
                    )
                    return _application(
                        transaction_id,
                        journal,
                        plan,
                        moved=moved,
                        written=written,
                        created=created,
                        warnings=(*plan.warnings, *excused),
                    )
                except Exception as exc:
                    failure = f"{phase} failed: {exc}"
                    recovery_failures: list[str] = []
                    try:
                        _append_journal(
                            journal_storage,
                            "rolling-back",
                            _parent=transaction,
                            _journal_fd=journal_fd,
                            failure=failure,
                        )
                    except Exception as journal_exc:
                        recovery_failures.append(f"rollback journal failed: {journal_exc}")
                    rollback_failures: list[str] = []
                    if snapshots and effect_attempted:
                        try:
                            _restore_snapshot(
                                plan.root,
                                snapshots,
                                root=root,
                                touched=touched,
                                protected=protected,
                            )
                        except Exception as rollback_exc:
                            rollback_failures.append(f"rollback failed: {rollback_exc}")
                        try:
                            verification_failures = _verify_snapshot(root, snapshots, protected)
                        except Exception as verification_exc:
                            rollback_failures.append(f"rollback verification failed: {verification_exc}")
                        else:
                            rollback_failures.extend(
                                f"rollback verification failed: {detail}" for detail in verification_failures
                            )
                        try:
                            _assert_root_identity(layout.bundle_dir, root)
                        except Exception as identity_exc:
                            rollback_failures.append(f"rollback verification failed: {identity_exc}")
                    recovery_failures.extend(rollback_failures)
                    try:
                        _append_journal(
                            journal_storage,
                            "rolled-back",
                            _parent=transaction,
                            _journal_fd=journal_fd,
                            failures=recovery_failures,
                            complete=not recovery_failures,
                        )
                    except Exception as journal_exc:
                        recovery_failures.append(f"rollback journal failed: {journal_exc}")
                    return _application(
                        transaction_id,
                        journal,
                        plan,
                        moved=moved,
                        written=written,
                        created=created,
                        failures=(failure, *recovery_failures),
                        rolled_back=bool(touched) and not rollback_failures,
                        warnings=(*plan.warnings, *excused),
                    )

            finally:
                root.close()


def apply_mutation(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    *,
    repo_root: Path | None = None,
    repo_roots: tuple[Path, ...] = (),
    baseline_bundle: Bundle | None = None,
    allowed_new_findings: tuple[tuple[str, str], ...] = (),
    validate_read_set: Callable[[], None] | None = None,
) -> MutationApplication:
    """Apply *plan* atomically, retaining durable recovery evidence in cache.

    *repo_root* overrides `layout.repo_root` for postcondition validation
    (e.g. `targets.affects-missing`). `layout.repo_root` is a `.git` walk-up
    from the workspace root, which resolves to the workspace's own repo in a
    split topology -- workspace and code repo separate. A caller that already
    resolved the code repo (`workspace.repos.resolve_repo`) passes it here so
    validation checks `affects` paths against the code repo, not the vault.
    *repo_roots* is the multi-repository form: a caller validating against
    every declared repo (`workspace.repos.resolve_repos`) passes them all, and
    a path resolving under any one of them -- or under *repo_root* -- is good.
    With neither given, validation falls back to `layout.repo_root`.

    *baseline_bundle* is an optimisation with a hard precondition: it MUST have
    been loaded from `layout.bundle_dir` with `ignore=work_tracker_okf.items.IGNORE`.
    Callers in `work/commands.py` that load with `ignore=()` or a lane-narrowed
    set (`run_reparent`, `run_release_adoption`) must NOT pass one; omitting
    it restores the second load and is always correct.

    *allowed_new_findings* is an explicit operation-specific exception to
    ADR 2026-08-25-the-work-mutation's default no-surplus guarantee. Each (post-mutation member, code)
    occurrence permits one surplus error after consuming the real baseline;
    it never excuses structural conditions, reload failures or stale indexes.
    The caller must establish the operation's semantic preconditions under its
    lock. Currently only intentional finish-hold filing grants an exception.
    Baseline capture failure disables exceptions, preserving the absolute
    fallback. Used allowances are warnings distinct from pre-existing errors;
    the planned journal records the requested budget before any live effect.
    Rollback and terminal-complete evidence verification retain that record;
    it grants no authority to replay or complete an interrupted transaction.

    *validate_read_set* optionally revalidates caller-owned semantic inputs
    from fresh reads under the bundle and executor locks immediately before
    live effects. It must only read, acquire no additional locks, and raise
    on stale input. Failure is journaled without applying effects. It is not
    called for wholly empty plans, which have no commit to authorize. Like
    ordinary preimages, it cannot serialize external writers that ignore locks.

    A plan with no writes, mkdirs, moves, deletes or directory preconditions
    (`_is_wholly_empty`) still opens the bundle root, takes `_bundle_root_lock`,
    opens the `work-mutations` cache directory and acquires the cross-process
    executor lock -- but does no bundle work: no transaction subdirectory or
    journal is created, and preflight/snapshot/staging/postcondition `validate()`
    never run. It returns immediately with
    `transaction_id == EMPTY_TRANSACTION_ID`. This is equivalence for every
    plan `_is_wholly_empty` admits today, not a heuristic: with no effects
    `_effects(plan, ...)` yields nothing to commit and
    `_affected_index_members(plan)` is empty, so the absolute lane-index
    check has nothing to check. `validate_paths` widens the gate scope, not
    narrows it -- the members it names would be validated against an
    identical (unmutated) baseline for an effect-free plan, so only the two
    absolute `validate_paths`-specific checks (canonical path, item reload)
    are foregone by skipping validation entirely -- and no shipped caller
    today produces an effect-free plan that carries a non-empty
    `validate_paths`, so this is a latent gap, not an observed regression.
    A full preflight/snapshot/validate pass over an actually-empty plan would
    only ever validate the bundle against itself. See the comment at the
    short-circuit site in `_apply_mutation_locked` for the fuller derivation,
    including why the lock/cache-open survives the short-circuit while the
    per-mutation transaction directory does not.
    """
    root = _open_root(layout.bundle_dir)
    try:
        with _bundle_root_lock(root):
            return _apply_mutation_locked(
                layout,
                plan,
                root,
                repo_root=repo_root,
                repo_roots=repo_roots,
                baseline_bundle=baseline_bundle,
                allowed_new_findings=allowed_new_findings,
                validate_read_set=validate_read_set,
            )
    finally:
        root.close()


__all__ = ["EMPTY_TRANSACTION_ID", "MutationApplication", "apply_mutation"]
