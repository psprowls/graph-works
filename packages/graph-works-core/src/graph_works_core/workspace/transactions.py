"""Durable, rollback-journaled application of work mutation plans.

The domain package plans immutable bundle-relative effects.  This module is
the workspace-aware commit boundary: it revalidates the complete plan before
the first live effect, keeps recovery evidence outside the bundle, and either
lands every effect plus its targeted postconditions or restores the snapshot.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from ctypes import CDLL, c_char_p, c_int, c_uint, get_errno
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import IO, Literal, Protocol

from okf_ext.moves import Move
from okf_ext.writing import body_digest
from okf_io import Finding, parse, validate
from okf_io.bundle import _load_at as _load_bundle_at
from work_tracker_okf.compose import rule_set
from work_tracker_okf.indexes import reconcile_marked_index, render_entry
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import WorkMutationPlan, directory_manifest_digest
from work_tracker_okf.paths import parse_item_path

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
    _parent_fd: int | None = None,
    _journal_fd: int | None = None,
    **details: object,
) -> None:
    record = _journal_record(state, details)
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if _journal_fd is not None:
        if _parent_fd is None:
            raise ValueError("retained journal requires its anchored parent descriptor")
        _assert_regular_entry_identity(_parent_fd, journal.name, _journal_fd, "journal")
        _write_bytes(_journal_fd, encoded)
        os.fsync(_journal_fd)
        _fsync_live_directory(_parent_fd)
        return
    if _parent_fd is None:
        stream = journal.open("a", encoding="utf-8")
    else:
        descriptor = os.open(
            journal.name,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
            dir_fd=_parent_fd,
        )
        stream = os.fdopen(descriptor, "a", encoding="utf-8")
    with stream:
        stream.write(encoded.decode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    if _parent_fd is None:
        _fsync_directory(journal.parent)
    else:
        _fsync_live_directory(_parent_fd)


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
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _nofollow_flag() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _require_regular_file(descriptor: int, label: str) -> os.stat_result:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} is not a regular file")
    return info


def _assert_regular_entry_identity(parent_fd: int, name: str, descriptor: int, label: str) -> None:
    expected = _require_regular_file(descriptor, label)
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"{label} changed during mutation: {exc}") from exc
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError(f"{label} changed during mutation")


@contextmanager
def _executor_lock(transaction_root: Path, *, root_fd: int | None = None) -> Iterator[None]:
    """Serialize mutation executors that honor the workspace transaction boundary."""
    if root_fd is None:
        transaction_root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(transaction_root / "executor.lock", os.O_RDWR | os.O_CREAT | _nofollow_flag(), 0o600)
    else:
        descriptor = os.open(
            "executor.lock",
            os.O_RDWR | os.O_CREAT | _nofollow_flag(),
            0o600,
            dir_fd=root_fd,
        )
    locked = False
    try:
        _require_regular_file(descriptor, "executor lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        if root_fd is not None:
            _assert_regular_entry_identity(root_fd, "executor.lock", descriptor, "executor lock")
        yield
    finally:
        if locked:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _bundle_root_lock(root_fd: int) -> Iterator[None]:
    """Serialize every executor holding this anchored bundle directory."""
    if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
        raise NotADirectoryError("bundle root descriptor is not a directory")
    locked = False
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            with suppress(OSError):
                fcntl.flock(root_fd, fcntl.LOCK_UN)


def _open_absolute_directory(path: Path) -> int:
    """Open an absolute directory by walking every component without following links."""
    if not path.is_absolute():
        raise ValueError(f"cache directory must be absolute: {path}")
    descriptor = os.open(path.anchor, _directory_flags())
    try:
        for part in path.parts[1:]:
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_or_create_directory(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        _fsync_live_directory(parent_fd)
        child = os.open(name, _directory_flags(), dir_fd=parent_fd)
        _fsync_live_directory(child)
        return child


def _assert_directory_identity(path: Path, descriptor: int, label: str) -> None:
    expected = os.fstat(descriptor)
    try:
        current = os.stat(path, follow_symlinks=False)  # noqa: PTH116 -- identity check must not follow links
    except OSError as exc:
        raise ValueError(f"{label} directory changed during mutation: {exc}") from exc
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError(f"{label} directory changed during mutation")


def _descriptor_path(descriptor: int) -> Path:
    """Return the current namespace path of an open directory descriptor."""
    if sys.platform == "darwin":
        raw = fcntl.fcntl(descriptor, 50, bytes(1024))
        return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
    return Path(f"/proc/self/fd/{descriptor}").readlink()


def _directory_descriptor_alias(descriptor: int) -> Path:
    """A descriptor-rooted namespace path suitable for resolving descendants."""
    if sys.platform == "darwin":
        return _descriptor_path(descriptor)
    return Path(f"/proc/self/fd/{descriptor}")


@contextmanager
def _locked_transaction_root(cache_dir: Path, transaction_root: Path) -> Iterator[tuple[int, int]]:
    """Anchor the cache namespace before acquiring the cross-process executor lock."""
    try:
        cache_fd = _open_absolute_directory(cache_dir)
    except FileNotFoundError:
        parent_fd = _open_absolute_directory(cache_dir.parent)
        try:
            cache_fd = _open_or_create_directory(parent_fd, cache_dir.name)
        finally:
            os.close(parent_fd)
    try:
        transaction_root_fd = _open_or_create_directory(cache_fd, transaction_root.name)
        try:
            with _executor_lock(transaction_root, root_fd=transaction_root_fd):
                _assert_directory_identity(cache_dir, cache_fd, "cache")
                _assert_directory_identity(transaction_root, transaction_root_fd, "transaction cache")
                yield cache_fd, transaction_root_fd
        finally:
            os.close(transaction_root_fd)
    finally:
        os.close(cache_fd)


@contextmanager
def _new_journal(transaction_fd: int, name: str) -> Iterator[int]:
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | _nofollow_flag(),
        0o600,
        dir_fd=transaction_fd,
    )
    try:
        _require_regular_file(descriptor, "journal")
        _assert_regular_entry_identity(transaction_fd, name, descriptor, "journal")
        os.fsync(descriptor)
        _fsync_live_directory(transaction_fd)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _new_transaction_directory(
    transaction_root_fd: int,
    configured: Path,
    transaction_id: str,
) -> Iterator[tuple[int, Path, int]]:
    os.mkdir(transaction_id, 0o700, dir_fd=transaction_root_fd)
    _fsync_live_directory(transaction_root_fd)
    descriptor = os.open(transaction_id, _directory_flags(), dir_fd=transaction_root_fd)
    try:
        _assert_directory_identity(configured, descriptor, "transaction")
        _fsync_live_directory(descriptor)
        with _new_journal(descriptor, "journal.jsonl") as journal_fd:
            yield descriptor, _descriptor_path(descriptor), journal_fd
    finally:
        os.close(descriptor)


def _open_root(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(root, flags)


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _file_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_parent(
    root_fd: int,
    member: str,
    *,
    create: bool = False,
    touched: set[str] | None = None,
    must_create: set[str] | None = None,
) -> tuple[int, str]:
    """Open *member*'s parent by walking beneath *root_fd* without following links."""
    pure = _lexical_member(member)
    descriptor = os.dup(root_fd)
    traversed: list[str] = []
    try:
        for part in pure.parts[:-1]:
            traversed.append(part)
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
                created = "/".join(traversed)
                if must_create is not None and created in must_create and (touched is None or created not in touched):
                    os.close(child)
                    raise ValueError(f"{created}: directory changed since planning (now exists); re-plan")
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, dir_fd=descriptor)
                created = "/".join(traversed)
                if touched is not None:
                    touched.add(created)
                _fsync_live_directory(descriptor)
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
                _fsync_live_directory(child)
            except OSError as exc:
                raise ValueError(f"{member}: unsafe ancestor {part!r}: {exc}") from exc
            os.close(descriptor)
            descriptor = child
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, pure.name


def _lstat_at(root_fd: int, member: str) -> os.stat_result:
    parent_fd, name = _open_parent(root_fd, member)
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)


def _lexists_at(root_fd: int, member: str) -> bool:
    try:
        _lstat_at(root_fd, member)
    except FileNotFoundError:
        return False
    return True


def _read_bytes_at(root_fd: int, member: str) -> bytes:
    parent_fd, name = _open_parent(root_fd, member)
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent_fd)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read()
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _readlink_at(root_fd: int, member: str) -> str:
    parent_fd, name = _open_parent(root_fd, member)
    try:
        return os.readlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _fsync_live_file(root_fd: int, member: str) -> None:
    parent_fd, name = _open_parent(root_fd, member)
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent_fd)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)


def _fsync_live_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _assert_root_identity(root: Path, root_fd: int) -> None:
    expected = os.fstat(root_fd)
    current = os.stat(root, follow_symlinks=False)  # noqa: PTH116 -- no-follow identity check is required
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("bundle root changed during mutation")


def _fsync_entry(path: Path) -> None:
    if path.is_symlink():
        return
    if not path.is_dir():
        with path.open("rb") as stream:
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
                with entry.open("rb") as stream:
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


def _entry_manifest_at(root_fd: int, member: str, *, include_mode: bool) -> str:
    """Hash one anchored entry tree without following any symlink."""
    hasher = hashlib.sha256()
    pending = [(".", member)]
    while pending:
        relative, current = pending.pop()
        info = _lstat_at(root_fd, current)
        if stat.S_ISDIR(info.st_mode):
            kind = b"directory"
            payload = b""
        elif stat.S_ISREG(info.st_mode):
            kind = b"file"
            payload = hashlib.sha256(_read_bytes_at(root_fd, current)).digest()
        elif stat.S_ISLNK(info.st_mode):
            kind = b"symlink"
            payload = os.fsencode(_readlink_at(root_fd, current))
        else:
            raise ValueError(f"{current}: unsupported filesystem entry type")
        _manifest_field(hasher, relative.encode("utf-8", "surrogateescape"))
        _manifest_field(hasher, kind)
        _manifest_field(hasher, payload)
        if include_mode:
            _manifest_field(hasher, stat.S_IMODE(info.st_mode).to_bytes(4, "big"))
        if kind == b"directory":
            parent_fd, name = _open_parent(root_fd, current)
            try:
                directory_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
                try:
                    names = sorted(os.listdir(directory_fd), key=os.fsencode, reverse=True)  # noqa: PTH208
                finally:
                    os.close(directory_fd)
            finally:
                os.close(parent_fd)
            pending.extend(
                (
                    name if relative == "." else f"{relative}/{name}",
                    f"{current}/{name}",
                )
                for name in names
            )
    return hasher.hexdigest()


def _entry_fingerprint_at(root_fd: int, member: str) -> str:
    return _entry_manifest_at(root_fd, member, include_mode=True)


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


def _validate_ancestor_chain(root_fd: int, member: str) -> None:
    pure = _lexical_member(member)
    descriptor = os.dup(root_fd)
    try:
        for part in pure.parts[:-1]:
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ValueError(f"{member}: unsafe ancestor {part!r}: {exc}") from exc
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _projected_symlink_is_internal(root_fd: int, destination: str, link: PurePosixPath) -> bool:
    descriptor_root = _directory_descriptor_alias(root_fd)
    resolved_root = _descriptor_path(root_fd).resolve(strict=True)
    if link.is_absolute():
        projected_text = link.as_posix()
        projected_path = Path(projected_text)
    else:
        projected_pure = PurePosixPath(*PurePosixPath(destination).parent.parts, *link.parts)
        projected_text = projected_pure.as_posix()
        projected_path = descriptor_root.joinpath(*projected_pure.parts)
    try:
        descriptor = os.open(projected_text, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0), dir_fd=root_fd)
    except FileNotFoundError:
        resolved_target = projected_path.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"{destination}: moved symlink target cannot be resolved safely: {exc}") from exc
    else:
        try:
            resolved_target = _descriptor_path(descriptor).resolve(strict=False)
        finally:
            os.close(descriptor)
    return resolved_target.is_relative_to(resolved_root)


def _preflight(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    root_fd: int | None = None,
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

    close_root = root_fd is None
    if root_fd is None:
        root_fd = _open_root(layout.bundle_dir)
    owns_manifest_scratch = manifest_scratch is None
    if manifest_scratch is None:
        manifest_scratch = layout.cache_dir / "work-mutations" / f".preflight-{uuid.uuid4().hex}"
    effect_paths = {member: plan.root.joinpath(*_lexical_member(member).parts) for member in _effective_members(plan)}
    try:
        for member in effect_paths:
            _validate_ancestor_chain(root_fd, member)

        _preflight_anchored(plan, root_fd, manifest_scratch)
    finally:
        if owns_manifest_scratch:
            _remove_entry(manifest_scratch)
        if close_root:
            os.close(root_fd)
    return effect_paths


def _preflight_anchored(plan: WorkMutationPlan, root_fd: int, manifest_scratch: Path) -> None:
    conditions = [condition.member for condition in plan.directory_preconditions]
    if len(conditions) != len(set(conditions)):
        raise ValueError("directory preconditions contain duplicate members")

    for index, condition in enumerate(plan.directory_preconditions):
        if condition.before_digest is None:
            if _lexists_at(root_fd, condition.member):
                raise ValueError(f"{condition.member}: directory changed since planning (now exists); re-plan")
            continue
        if not _lexists_at(root_fd, condition.member):
            raise ValueError(f"{condition.member}: directory changed since planning (now missing); re-plan")
        try:
            manifest_scratch.mkdir(parents=True, exist_ok=True)
            anchored_copy = manifest_scratch / f"{index:06d}"
            _copy_live_entry(root_fd, condition.member, anchored_copy)
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
            if _lexists_at(root_fd, write.member):
                raise ValueError(f"{write.member}: changed since planning (now exists); re-plan")
            continue
        try:
            current_bytes = _read_bytes_at(root_fd, preimage_member)
        except OSError as exc:
            raise ValueError(f"{preimage_member}: changed since planning ({exc}); re-plan") from exc
        if hashlib.sha256(current_bytes).hexdigest() != write.before_digest:
            raise ValueError(f"{preimage_member}: changed since planning; re-plan")
        if write.source_member is not None and _lexists_at(root_fd, write.member):
            raise ValueError(f"{write.member}: changed since planning (now exists); re-plan")

    if plan.move_plan is not None:
        for member, expected in plan.move_plan.digests.items():
            try:
                content = _read_bytes_at(root_fd, member).decode("utf-8")
                current_document = parse(content, path=plan.root / member)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"{member}: changed since planning ({exc}); re-plan") from exc
            if body_digest(current_document.body) != expected:
                raise ValueError(f"{member}: changed since planning; re-plan")

    destinations: set[str] = set()
    for move in plan.moves:
        if not _lexists_at(root_fd, move.source):
            raise ValueError(f"{move.source}: direct move changed since planning (source missing); re-plan")
        if _lexists_at(root_fd, move.dest):
            raise ValueError(f"{move.dest}: direct move changed since planning (destination exists); re-plan")
        if move.dest in destinations:
            raise ValueError(f"{move.dest}: multiple effects claim the same move destination")
        destinations.add(move.dest)
        if stat.S_ISLNK(_lstat_at(root_fd, move.source).st_mode):
            link = PurePosixPath(_readlink_at(root_fd, move.source))
            if not _projected_symlink_is_internal(root_fd, move.dest, link):
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


def _copy_live_file(root_fd: int, member: str, destination: Path, mode: int) -> None:
    parent_fd, name = _open_parent(root_fd, member)
    try:
        source_fd = os.open(name, _file_flags(), dir_fd=parent_fd)
        try:
            with os.fdopen(os.dup(source_fd), "rb") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fchmod(target.fileno(), stat.S_IMODE(mode))
                os.fsync(target.fileno())
        finally:
            os.close(source_fd)
    finally:
        os.close(parent_fd)


def _copy_live_entry(root_fd: int, member: str, destination: Path) -> None:
    root_info = _lstat_at(root_fd, member)
    if stat.S_ISLNK(root_info.st_mode):
        destination.symlink_to(_readlink_at(root_fd, member))
        return
    if stat.S_ISREG(root_info.st_mode):
        _copy_live_file(root_fd, member, destination, root_info.st_mode)
        return
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(f"unsupported filesystem entry type at {member}")

    destination.mkdir(mode=0o700)
    directories = [(member, destination, stat.S_IMODE(root_info.st_mode))]
    pending = [(member, destination)]
    while pending:
        source_directory, destination_directory = pending.pop()
        parent_fd, name = _open_parent(root_fd, source_directory)
        try:
            directory_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            try:
                names = sorted(os.listdir(directory_fd), key=os.fsencode, reverse=True)  # noqa: PTH208
            finally:
                os.close(directory_fd)
        finally:
            os.close(parent_fd)
        for entry_name in names:
            source_entry = f"{source_directory}/{entry_name}"
            destination_entry = destination_directory / entry_name
            entry_mode = _lstat_at(root_fd, source_entry).st_mode
            if stat.S_ISLNK(entry_mode):
                destination_entry.symlink_to(_readlink_at(root_fd, source_entry))
            elif stat.S_ISREG(entry_mode):
                _copy_live_file(root_fd, source_entry, destination_entry, entry_mode)
            elif stat.S_ISDIR(entry_mode):
                destination_entry.mkdir(mode=0o700)
                directories.append((source_entry, destination_entry, stat.S_IMODE(entry_mode)))
                pending.append((source_entry, destination_entry))
            else:
                raise ValueError(f"unsupported filesystem entry type at {source_entry}")
    for _, directory, mode in reversed(directories):
        directory.chmod(mode)


def _create_snapshot(
    plan: WorkMutationPlan,
    transaction_dir: Path,
    root_fd: int | None = None,
) -> tuple[_SnapshotEntry, ...]:
    backup_root = transaction_dir / "backups"
    backup_root.mkdir()
    close_root = root_fd is None
    if root_fd is None:
        root_fd = _open_root(plan.root)
    entries: list[_SnapshotEntry] = []
    try:
        for index, member in enumerate(_snapshot_members(plan)):
            if not _lexists_at(root_fd, member):
                entries.append(_SnapshotEntry(member, False, None, None))
                continue
            backup = backup_root / f"{index:06d}"
            fingerprint = _entry_fingerprint_at(root_fd, member)
            _copy_live_entry(root_fd, member, backup)
            _fsync_entry(backup)
            backup_root_fd = _open_root(backup_root)
            try:
                backup_fingerprint = _entry_fingerprint_at(backup_root_fd, backup.name)
            finally:
                os.close(backup_root_fd)
            if backup_fingerprint != fingerprint or _entry_fingerprint_at(root_fd, member) != fingerprint:
                raise ValueError(f"{member}: changed while snapshotting; re-plan")
            entries.append(_SnapshotEntry(member, True, backup, fingerprint))
        _fsync_directory(backup_root)
        _fsync_directory(transaction_dir)
        return tuple(entries)
    finally:
        if close_root:
            os.close(root_fd)


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


def _remove_live_entry(root_fd: int, member: str) -> None:
    if not _lexists_at(root_fd, member):
        return
    info = _lstat_at(root_fd, member)
    if not stat.S_ISDIR(info.st_mode):
        parent_fd, name = _open_parent(root_fd, member)
        try:
            os.unlink(name, dir_fd=parent_fd)
            _fsync_live_directory(parent_fd)
        finally:
            os.close(parent_fd)
        return

    pending = [member]
    directories: list[str] = []
    while pending:
        directory = pending.pop()
        directories.append(directory)
        parent_fd, name = _open_parent(root_fd, directory)
        try:
            directory_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            try:
                names = sorted(os.listdir(directory_fd), key=os.fsencode, reverse=True)  # noqa: PTH208
            finally:
                os.close(directory_fd)
        finally:
            os.close(parent_fd)
        for entry_name in names:
            child = f"{directory}/{entry_name}"
            child_info = _lstat_at(root_fd, child)
            if stat.S_ISDIR(child_info.st_mode):
                pending.append(child)
            else:
                child_parent, child_name = _open_parent(root_fd, child)
                try:
                    os.unlink(child_name, dir_fd=child_parent)
                    _fsync_live_directory(child_parent)
                finally:
                    os.close(child_parent)
    for directory in reversed(directories):
        parent_fd, name = _open_parent(root_fd, directory)
        try:
            os.rmdir(name, dir_fd=parent_fd)
            _fsync_live_directory(parent_fd)
        finally:
            os.close(parent_fd)


def _chmod_directory_at(root_fd: int, member: str, mode: int) -> None:
    parent_fd, name = _open_parent(root_fd, member)
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
        try:
            os.fchmod(descriptor, mode)
            _fsync_live_directory(descriptor)
        finally:
            os.close(descriptor)
        _fsync_live_directory(parent_fd)
    finally:
        os.close(parent_fd)


def _copy_backup_file(root_fd: int, source: Path, member: str, mode: int) -> None:
    parent_fd, name = _open_parent(root_fd, member, create=True)
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(mode), dir_fd=parent_fd)
        try:
            with source.open("rb") as incoming, os.fdopen(os.dup(descriptor), "wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
            os.fchmod(descriptor, stat.S_IMODE(mode))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_live_file(root_fd, member)
        _fsync_live_directory(parent_fd)
    finally:
        os.close(parent_fd)


def _copy_backup_to_live(root_fd: int, source: Path, member: str) -> None:
    mode = source.lstat().st_mode
    parent_fd, name = _open_parent(root_fd, member, create=True)
    try:
        if stat.S_ISLNK(mode):
            os.symlink(os.fspath(source.readlink()), name, dir_fd=parent_fd)
            _fsync_live_directory(parent_fd)
            return
        if stat.S_ISREG(mode):
            os.close(parent_fd)
            parent_fd = -1
            _copy_backup_file(root_fd, source, member, mode)
            return
        if not stat.S_ISDIR(mode):
            raise ValueError(f"unsupported filesystem entry type at {source}")
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        _fsync_live_directory(parent_fd)
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)

    directories = [(source, member, stat.S_IMODE(mode))]
    pending = [(source, member)]
    while pending:
        source_directory, destination_directory = pending.pop()
        entries = sorted(source_directory.iterdir(), key=lambda entry: os.fsencode(entry.name), reverse=True)
        for source_entry in entries:
            destination_entry = f"{destination_directory}/{source_entry.name}"
            entry_mode = source_entry.lstat().st_mode
            if stat.S_ISDIR(entry_mode) and not stat.S_ISLNK(entry_mode):
                destination_parent, destination_name = _open_parent(root_fd, destination_entry)
                try:
                    os.mkdir(destination_name, 0o700, dir_fd=destination_parent)
                    _fsync_live_directory(destination_parent)
                finally:
                    os.close(destination_parent)
                directories.append((source_entry, destination_entry, stat.S_IMODE(entry_mode)))
                pending.append((source_entry, destination_entry))
            elif stat.S_ISLNK(entry_mode):
                destination_parent, destination_name = _open_parent(root_fd, destination_entry)
                try:
                    os.symlink(os.fspath(source_entry.readlink()), destination_name, dir_fd=destination_parent)
                    _fsync_live_directory(destination_parent)
                finally:
                    os.close(destination_parent)
            elif stat.S_ISREG(entry_mode):
                _copy_backup_file(root_fd, source_entry, destination_entry, entry_mode)
            else:
                raise ValueError(f"unsupported filesystem entry type at {source_entry}")
    for _, destination_directory, directory_mode in reversed(directories):
        _chmod_directory_at(root_fd, destination_directory, directory_mode)


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
    root: Path,
    entries: Sequence[_SnapshotEntry],
    *,
    root_fd: int | None = None,
    touched: set[str] | None = None,
    protected: Mapping[str, str] | None = None,
) -> None:
    close_root = root_fd is None
    if root_fd is None:
        root_fd = _open_root(root)
    selected = _selected_snapshot_entries(entries, touched, protected)
    try:
        for entry in sorted(selected, key=lambda current: current.member.count("/"), reverse=True):
            _remove_live_entry(root_fd, entry.member)
        for entry in sorted(selected, key=lambda current: current.member.count("/")):
            if not entry.existed:
                continue
            assert entry.backup is not None
            _copy_backup_to_live(root_fd, entry.backup, entry.member)
        _fsync_live_directory(root_fd)
    finally:
        if close_root:
            os.close(root_fd)


def _verify_snapshot(
    root_fd: int,
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
            exists = _lexists_at(root_fd, entry.member)
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
            fingerprint = _entry_fingerprint_at(root_fd, entry.member)
        except (OSError, ValueError) as exc:
            failures.append(f"{entry.member}: restored entry cannot be verified: {exc}")
            continue
        if fingerprint != entry.fingerprint:
            failures.append(f"{entry.member}: restored entry differs from snapshot")
    return tuple(failures)


def _stage_writes(plan: WorkMutationPlan, transaction_dir: Path, root_fd: int | None = None) -> dict[str, Path]:
    staging = transaction_dir / "staging"
    staging.mkdir()
    close_root = root_fd is None
    if root_fd is None:
        root_fd = _open_root(plan.root)
    staged: dict[str, Path] = {}
    try:
        for index, write in enumerate(sorted(plan.writes, key=lambda current: current.member)):
            path = staging / f"{index:06d}"
            with path.open("xb") as stream:
                stream.write(write.after)
                if write.before_digest is not None:
                    preimage_mode = _lstat_at(root_fd, write.source_member or write.member).st_mode
                    os.fchmod(stream.fileno(), stat.S_IMODE(preimage_mode))
                stream.flush()
                os.fsync(stream.fileno())
            staged[write.member] = path
        _fsync_directory(staging)
        _fsync_directory(transaction_dir)
        return staged
    finally:
        if close_root:
            os.close(root_fd)


def _mapped_directory_modes(plan: WorkMutationPlan, root_fd: int) -> tuple[_DirectoryMode, ...]:
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
            if _lexists_at(root_fd, source):
                info = _lstat_at(root_fd, source)
                if stat.S_ISDIR(info.st_mode):
                    modes.append(_DirectoryMode(source, destination, stat.S_IMODE(info.st_mode)))
            break
    return tuple(sorted(modes, key=lambda item: (item.destination.count("/"), item.destination)))


def _verify_directory_modes(root_fd: int, modes: Sequence[_DirectoryMode]) -> None:
    for captured in modes:
        try:
            current = _lstat_at(root_fd, captured.source)
        except OSError as exc:
            raise ValueError(f"{captured.source}: directory mode changed since planning ({exc}); re-plan") from exc
        if not stat.S_ISDIR(current.st_mode) or stat.S_IMODE(current.st_mode) != captured.mode:
            raise ValueError(f"{captured.source}: directory mode changed since planning; re-plan")


def _effects(plan: WorkMutationPlan, staged: dict[str, Path], root_fd: int | None = None) -> tuple[_Effect, ...]:
    mkdirs = tuple(
        _Effect("mkdir", member) for member in sorted(plan.mkdirs, key=lambda current: (current.count("/"), current))
    )
    moves = tuple(_Effect("move", move.source, destination=move.dest) for move in sorted(plan.moves, key=_move_key))
    writes = tuple(_Effect("write", member, staged=path) for member, path in sorted(staged.items()))
    non_directories: list[_Effect] = []
    directories: list[_Effect] = []
    for member in plan.deletes:
        effect = _Effect("delete", member)
        if root_fd is None:
            target = plan.root / member
            is_directory = target.is_dir() and not target.is_symlink()
        else:
            try:
                is_directory = stat.S_ISDIR(_lstat_at(root_fd, member).st_mode)
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


def _rename_flags_at(source_fd: int, source: str, destination_fd: int, destination: str, flag: int) -> None:
    library = CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = library.renameatx_np
    else:
        function = library.renameat2
    function.argtypes = (c_int, c_char_p, c_int, c_char_p, c_uint)
    function.restype = c_int
    result = function(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), flag)
    if result != 0:
        error = get_errno()
        raise OSError(error, os.strerror(error), destination)


def _rename_noreplace_at(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    flag = 0x00000004 if sys.platform == "darwin" else 0x00000001
    _rename_flags_at(source_fd, source, destination_fd, destination, flag)


def _write_all(descriptor: int, stream: IO[bytes]) -> None:
    while chunk := stream.read(1024 * 1024):
        offset = 0
        while offset < len(chunk):
            offset += os.write(descriptor, chunk[offset:])


def _live_temporary(parent_fd: int, target_name: str, staged: Path) -> str:
    temporary = f".{target_name}.{uuid.uuid4().hex}.tmp"
    mode = stat.S_IMODE(staged.stat().st_mode)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=parent_fd)
    try:
        with staged.open("rb") as stream:
            _write_all(descriptor, stream)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_live_directory(parent_fd)
    return temporary


def _digest_in_directory(parent_fd: int, name: str) -> str:
    descriptor = os.open(name, _file_flags(), dir_fd=parent_fd)
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
    root_fd: int,
    member: str,
    touched: set[str],
    *,
    defer_fsync: bool = False,
) -> tuple[int, str, str, str, bool]:
    """Move *member* to a unique adjacent name before inspecting its bytes."""
    parent_fd, name = _open_parent(root_fd, member)
    quarantine_name, quarantine_member = _quarantine_member(member)
    previously_touched = member in touched
    try:
        _rename_noreplace_at(parent_fd, name, parent_fd, quarantine_name)
    except FileNotFoundError as exc:
        os.close(parent_fd)
        raise ValueError(f"{member}: changed since planning (now missing); re-plan") from exc
    touched.add(member)
    if not defer_fsync:
        _fsync_live_directory(parent_fd)
    return parent_fd, name, quarantine_name, quarantine_member, previously_touched


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
    parent_fd: int,
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
    captured_identity = _identity_at(parent_fd, quarantine_name)
    try:
        _rename_noreplace_at(parent_fd, quarantine_name, parent_fd, name)
    except FileExistsError as exc:
        raise _preserve_conflict(member, quarantine_member, protected, detail) from exc
    if _identity_at(parent_fd, name) != captured_identity:
        protected[member] = member
        raise _CustodyConflict(f"{member}: restored custody identity could not be verified")
    _fsync_live_directory(parent_fd)
    protected.pop(member, None)
    protected.pop(quarantine_member, None)
    if not previously_touched:
        touched.discard(member)
    raise ValueError(f"{member}: {detail}; re-plan")


def _identity_at(parent_fd: int, name: str) -> tuple[int, int]:
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    return info.st_dev, info.st_ino


def _entry_identity(info: os.stat_result) -> _EntryIdentity:
    return _EntryIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        kind=stat.S_IFMT(info.st_mode),
        mode=stat.S_IMODE(info.st_mode),
    )


def _commit_write(
    root_fd: int,
    effect: _Effect,
    expected_digest: str | None,
    touched: set[str],
    protected: dict[str, str],
) -> None:
    assert effect.staged is not None
    parent_fd, name = _open_parent(root_fd, effect.member)
    temporary = _live_temporary(parent_fd, name, effect.staged)
    try:
        if expected_digest is None:
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ValueError(f"{effect.member}: changed since planning (now exists); re-plan") from exc
            touched.add(effect.member)
        else:
            os.close(parent_fd)
            parent_fd, name, quarantine, quarantine_member, previously_touched = _take_custody(
                root_fd, effect.member, touched
            )
            try:
                actual = _digest_in_directory(parent_fd, quarantine)
            except Exception as exc:
                _restore_custody_or_raise(
                    parent_fd,
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
            if actual != expected_digest:
                _restore_custody_or_raise(
                    parent_fd,
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
                os.link(
                    temporary,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise _preserve_conflict(
                    effect.member, quarantine_member, protected, "name was recreated during commit"
                ) from exc
            touched.add(effect.member)
            if _identity_at(parent_fd, name) != _identity_at(parent_fd, temporary):
                raise _preserve_conflict(
                    effect.member, quarantine_member, protected, "installed name changed during commit"
                )
            os.unlink(quarantine, dir_fd=parent_fd)
        _fsync_live_file(root_fd, effect.member)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent_fd)
        _fsync_live_directory(parent_fd)
        os.close(parent_fd)


def _recover_move_custody_or_raise(
    root_fd: int,
    effect: _Effect,
    *,
    source_fd: int,
    source_name: str,
    quarantine: str,
    quarantine_member: str,
    destination_fd: int | None,
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
        assert destination_fd is not None
        try:
            if _identity_at(destination_fd, destination_name) != captured_identity:
                raise ValueError("captured destination identity changed")
            _rename_noreplace_at(destination_fd, destination_name, source_fd, quarantine)
            _fsync_live_directory(destination_fd)
            _fsync_live_directory(source_fd)
        except Exception as recovery_exc:
            raise _CustodyConflict(
                f"{effect.member}: move custody recovery failed; source, destination, and quarantine "
                f"{quarantine_member} were preserved: {recovery_exc}"
            ) from recovery_exc
        captured_at_destination = False
        if not destination_was_touched:
            touched.discard(effect.destination)
        try:
            destination_absent = not _lexists_at(root_fd, effect.destination)
        except OSError:
            destination_absent = False
        if destination_absent:
            protected.pop(effect.destination, None)

    if not captured_at_destination:
        try:
            destination_absent = not _lexists_at(root_fd, effect.destination)
        except OSError:
            destination_absent = False
        if destination_absent:
            protected.pop(effect.destination, None)
        try:
            if _identity_at(source_fd, quarantine) != captured_identity:
                raise ValueError("quarantine identity changed")
            _rename_noreplace_at(source_fd, quarantine, source_fd, source_name)
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
        _fsync_live_directory(source_fd)
        try:
            if _identity_at(source_fd, source_name) != captured_identity:
                raise ValueError("restored source identity changed")
            if _entry_fingerprint_at(root_fd, effect.member) != expected_fingerprint:
                raise ValueError("restored source content changed")
            if _lexists_at(root_fd, quarantine_member):
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
    root_fd: int,
    effect: _Effect,
    expected_fingerprint: str,
    expected_identity: _EntryIdentity,
    touched: set[str],
    protected: dict[str, str],
) -> None:
    assert effect.destination is not None
    source_fd, source_name, quarantine, quarantine_member, previously_touched = _take_custody(
        root_fd,
        effect.member,
        touched,
        defer_fsync=True,
    )
    captured_identity = (expected_identity.device, expected_identity.inode)
    protected[effect.member] = quarantine_member
    protected[quarantine_member] = quarantine_member
    protected[effect.destination] = effect.destination
    destination_fd: int | None = None
    destination_name = PurePosixPath(effect.destination).name
    captured_at_destination = False
    destination_was_touched = effect.destination in touched
    try:
        _fsync_live_directory(source_fd)
        captured_info = os.stat(quarantine, dir_fd=source_fd, follow_symlinks=False)
        if _entry_identity(captured_info) != expected_identity:
            raise ValueError(f"{effect.member}: changed since planning; re-plan")
        if _entry_fingerprint_at(root_fd, quarantine_member) != expected_fingerprint:
            raise ValueError(f"{effect.member}: changed since planning; re-plan")
        if _lexists_at(root_fd, effect.member):
            raise _CustodyConflict(
                f"{effect.member}: name was recreated during custody verification; "
                f"external entry preserved and captured preimage retained in quarantine {quarantine_member}"
            )
        destination_fd, destination_name = _open_parent(root_fd, effect.destination)
        _rename_noreplace_at(source_fd, quarantine, destination_fd, destination_name)
        captured_at_destination = True
        touched.update((effect.member, effect.destination))
        if _identity_at(destination_fd, destination_name) != captured_identity:
            raise _CustodyConflict(f"{effect.member}: captured entry changed while moving")
        if _entry_fingerprint_at(root_fd, effect.destination) != expected_fingerprint:
            raise _CustodyConflict(f"{effect.member}: captured entry changed while moving")
        if _lexists_at(root_fd, effect.member):
            raise _CustodyConflict(f"{effect.member}: name was recreated during move")
        destination_mode = os.stat(destination_name, dir_fd=destination_fd, follow_symlinks=False).st_mode
        if stat.S_ISREG(destination_mode):
            _fsync_live_file(root_fd, effect.destination)
        _fsync_live_directory(source_fd)
        if destination_fd != source_fd:
            _fsync_live_directory(destination_fd)
    except Exception as exc:
        _recover_move_custody_or_raise(
            root_fd,
            effect,
            source_fd=source_fd,
            source_name=source_name,
            quarantine=quarantine,
            quarantine_member=quarantine_member,
            destination_fd=destination_fd,
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
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def _commit_effect(
    root: Path,
    effect: _Effect,
    *,
    root_fd: int | None = None,
    write_digests: dict[str, str | None] | None = None,
    expected_fingerprints: dict[str, str] | None = None,
    expected_identities: dict[str, _EntryIdentity] | None = None,
    absent_directories: set[str] | None = None,
    touched: set[str] | None = None,
    protected: dict[str, str] | None = None,
) -> None:
    close_root = root_fd is None
    if root_fd is None:
        root_fd = _open_root(root)
    if touched is None:
        touched = set()
    if protected is None:
        protected = {}
    try:
        if effect.kind == "mkdir":
            parent_fd, name = _open_parent(
                root_fd,
                effect.member,
                create=True,
                touched=touched,
                must_create=absent_directories,
            )
            try:
                try:
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    os.mkdir(name, dir_fd=parent_fd)
                    touched.add(effect.member)
                    directory_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
                    try:
                        _fsync_live_directory(directory_fd)
                    finally:
                        os.close(directory_fd)
                    _fsync_live_directory(parent_fd)
                else:
                    if absent_directories is not None and effect.member in absent_directories:
                        raise ValueError(f"{effect.member}: directory changed since planning (now exists); re-plan")
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError(f"{effect.member}: mkdir target is not a directory")
                    directory_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
                    os.close(directory_fd)
            finally:
                os.close(parent_fd)
            return
        if effect.kind == "move":
            assert expected_fingerprints is not None
            assert expected_identities is not None
            _commit_move(
                root_fd,
                effect,
                expected_fingerprints[effect.member],
                expected_identities[effect.member],
                touched,
                protected,
            )
            return
        if effect.kind == "write":
            assert write_digests is not None
            _commit_write(root_fd, effect, write_digests[effect.member], touched, protected)
            return
        expected = None if expected_fingerprints is None else expected_fingerprints.get(effect.member)
        if not _lexists_at(root_fd, effect.member):
            if expected is not None:
                raise ValueError(f"{effect.member}: changed since planning (now missing); re-plan")
            return
        parent_fd, name, quarantine, quarantine_member, previously_touched = _take_custody(
            root_fd, effect.member, touched
        )
        try:
            info = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            if expected_identities is not None and _entry_identity(info) != expected_identities[effect.member]:
                _restore_custody_or_raise(
                    parent_fd,
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
                and _entry_fingerprint_at(root_fd, quarantine_member) != expected
            ):
                _restore_custody_or_raise(
                    parent_fd,
                    name,
                    quarantine,
                    member=effect.member,
                    quarantine_member=quarantine_member,
                    touched=touched,
                    protected=protected,
                    previously_touched=previously_touched,
                    detail="changed since planning",
                )
            if _lexists_at(root_fd, effect.member):
                raise _preserve_conflict(
                    effect.member, quarantine_member, protected, "name was recreated during custody verification"
                )
            try:
                if stat.S_ISDIR(info.st_mode):
                    os.rmdir(quarantine, dir_fd=parent_fd)
                else:
                    os.unlink(quarantine, dir_fd=parent_fd)
            except OSError as exc:
                _restore_custody_or_raise(
                    parent_fd,
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
            _fsync_live_directory(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if close_root:
            os.close(root_fd)


def _apply_directory_modes(root_fd: int, modes: Sequence[_DirectoryMode], touched: set[str]) -> None:
    for captured in sorted(modes, key=lambda item: item.destination.count("/"), reverse=True):
        current = _lstat_at(root_fd, captured.destination)
        if not stat.S_ISDIR(current.st_mode):
            raise ValueError(f"{captured.destination}: mapped directory is no longer a directory")
        if stat.S_IMODE(current.st_mode) != captured.mode:
            touched.add(captured.destination)
            _chmod_directory_at(root_fd, captured.destination, captured.mode)


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


def _item_failures(item: WorkItem, by_path: dict[str, WorkItem], findings: Iterable[Finding]) -> list[str]:
    failures: list[str] = []
    if item.parent_path is not None and item.parent_path not in by_path:
        failures.append(f"{item.path}: parent {item.parent_path!r} is missing after mutation")
    for edge in item.dependency_edges:
        if edge.path not in by_path:
            failures.append(f"{item.path}: dependency target {edge.path!r} is missing after mutation")
    for issue in item.dependency_issues:
        failures.append(f"{item.path}: dependency {issue.code}: {issue.detail}")
    for finding in findings:
        if finding.severity == "error" and finding.path == item.page_path:
            failures.append(f"{finding.path}: {finding.code}: {finding.message}")
    return failures


def _validate_postconditions(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    root_fd: int | None = None,
) -> tuple[str, ...]:
    close_root = root_fd is None
    if root_fd is None:
        root_fd = _open_root(layout.bundle_dir)
    validation_root = layout.bundle_dir
    try:
        _assert_root_identity(validation_root, root_fd)
        bundle = _load_bundle_at(validation_root, root_fd, ignore=IGNORE)
        _assert_root_identity(validation_root, root_fd)
        items = load_items(bundle)
    except (OSError, ValueError) as exc:
        reload_failures = (f"postcondition reload failed: {exc}",)
        if close_root:
            os.close(root_fd)
        return reload_failures
    by_path = {item.path: item for item in items}
    targeted_members = {f"{path}.md" for path in plan.validate_paths}
    targeted_indexes = set(_affected_index_members(plan))
    targeted_members.update(targeted_indexes)
    declarations_dir = layout.config_dir if (layout.config_dir / "schema").is_dir() else validation_root
    try:
        report = validate(
            bundle,
            today=date.max,
            extra_rules=rule_set(
                validation_root,
                repo_root=layout.repo_root,
                declarations_dir=declarations_dir,
            ),
        )
        _assert_root_identity(validation_root, root_fd)
    except (OSError, ValueError) as exc:
        if close_root:
            os.close(root_fd)
        return (f"postcondition validation setup failed: {exc}",)
    findings = tuple(
        finding for finding in report.errors if finding.path is not None and finding.path in targeted_members
    )

    failures: list[str] = []
    failures.extend(f"{finding.path}: {finding.code}: {finding.message}" for finding in findings)
    for path in plan.validate_paths:
        if parse_item_path(path) is None:
            failures.append(f"{path}: validate path is not canonical")
            continue
        item = by_path.get(path)
        if item is None:
            failures.append(f"{path}: final work item did not reload")
            continue
        failures.extend(_item_failures(item, by_path, ()))

    for member in _affected_index_members(plan):
        lane = PurePosixPath(member).parent.as_posix()
        try:
            current = _read_bytes_at(root_fd, member).decode("utf-8")
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
        expected = reconcile_marked_index(current, tuple(render_entry(item) for item in direct))
        if current != expected:
            failures.append(f"{member}: generated direct-descendant inventory is stale")
    try:
        _assert_root_identity(validation_root, root_fd)
    except (OSError, ValueError) as exc:
        failures.append(f"postcondition validation failed: {exc}")
    if close_root:
        os.close(root_fd)
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
) -> MutationApplication:
    return MutationApplication(
        transaction_id=transaction_id,
        journal=journal,
        moved=tuple(moved),
        written=tuple(written),
        created_directories=tuple(created),
        warnings=plan.warnings,
        failures=tuple(failures),
        rolled_back=rolled_back,
    )


def _apply_mutation_locked(
    layout: WorkspaceLayout,
    plan: WorkMutationPlan,
    locked_root_fd: int,
) -> MutationApplication:
    transaction_id = uuid.uuid4().hex
    transaction_root = layout.cache_dir / "work-mutations"
    resolved_bundle = layout.bundle_dir.resolve(strict=True)
    resolved_transactions = transaction_root.resolve(strict=False)
    if resolved_transactions.is_relative_to(resolved_bundle):
        raise ValueError("work mutation journal must live outside the bundle")
    configured_transaction_dir = transaction_root / transaction_id
    with (
        _locked_transaction_root(layout.cache_dir, transaction_root) as (
            cache_fd,
            transaction_root_fd,
        ),
        _new_transaction_directory(
            transaction_root_fd,
            configured_transaction_dir,
            transaction_id,
        ) as (transaction_fd, transaction_dir, journal_fd),
    ):
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
        planned_record = _journal_record("planned", planned_details)
        _append_journal(
            journal_storage,
            "planned",
            _parent_fd=transaction_fd,
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
        root_fd = os.dup(locked_root_fd)
        try:
            try:
                _assert_directory_identity(layout.cache_dir, cache_fd, "cache")
                _assert_directory_identity(configured_transaction_dir, transaction_fd, "transaction")
                _assert_root_identity(layout.bundle_dir, root_fd)
                _preflight(layout, plan, root_fd, transaction_dir / "preflight-initial")
                created = [member for member in plan.mkdirs if not _lexists_at(root_fd, member)]
                snapshot_records = [
                    {"member": member, "existed": _lexists_at(root_fd, member)} for member in _snapshot_targets(plan)
                ]
                directory_modes = _mapped_directory_modes(plan, root_fd)
                snapshots = _create_snapshot(plan, transaction_dir, root_fd)
                staged = _stage_writes(plan, transaction_dir, root_fd)
                _preflight(layout, plan, root_fd, transaction_dir / "preflight-final")
                _verify_directory_modes(root_fd, directory_modes)
                expected_fingerprints = {
                    member: _entry_fingerprint_at(root_fd, member)
                    for member in {*(move.source for move in plan.moves), *plan.deletes}
                    if _lexists_at(root_fd, member)
                }
                expected_identities = {
                    member: _entry_identity(_lstat_at(root_fd, member))
                    for member in {*(move.source for move in plan.moves), *plan.deletes}
                    if _lexists_at(root_fd, member)
                }
                absent_directories = {
                    condition.member for condition in plan.directory_preconditions if condition.before_digest is None
                }
                write_digests = {
                    write.member: write.before_digest if write.source_member is None else None for write in plan.writes
                }
                applying_details: dict[str, object] = {
                    "backups": [
                        {
                            "member": entry.member,
                            "path": (
                                None if entry.backup is None else entry.backup.relative_to(transaction_dir).as_posix()
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
                    _parent_fd=transaction_fd,
                    _journal_fd=journal_fd,
                    **applying_details,
                )
                phase = "apply"
                _assert_directory_identity(layout.cache_dir, cache_fd, "cache")
                _assert_directory_identity(configured_transaction_dir, transaction_fd, "transaction")
                for effect in _effects(plan, staged, root_fd):
                    effect_attempted = True
                    _commit_effect(
                        plan.root,
                        effect,
                        root_fd=root_fd,
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
                _apply_directory_modes(root_fd, directory_modes, touched)
                _assert_root_identity(layout.bundle_dir, root_fd)
                _assert_directory_identity(layout.cache_dir, cache_fd, "cache")
                _assert_directory_identity(configured_transaction_dir, transaction_fd, "transaction")
                _fsync_live_directory(root_fd)
                phase = "validation"
                validating_details: dict[str, object] = {"validate_paths": list(plan.validate_paths)}
                validating_record = _journal_record("validating", validating_details)
                _append_journal(
                    journal_storage,
                    "validating",
                    _parent_fd=transaction_fd,
                    _journal_fd=journal_fd,
                    **validating_details,
                )
                postcondition_failures = _validate_postconditions(
                    layout,
                    plan,
                    root_fd,
                )
                if postcondition_failures:
                    raise ValueError("; ".join(postcondition_failures))
                _assert_root_identity(layout.bundle_dir, root_fd)
                _assert_directory_identity(layout.cache_dir, cache_fd, "cache")
                _assert_directory_identity(configured_transaction_dir, transaction_fd, "transaction")
                _fsync_live_directory(root_fd)
                complete_record = _complete_record(transaction_id, moved, written, created)
                try:
                    _append_journal(
                        journal_storage,
                        "complete",
                        _parent_fd=transaction_fd,
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
                return _application(
                    transaction_id,
                    journal,
                    plan,
                    moved=moved,
                    written=written,
                    created=created,
                )
            except Exception as exc:
                failure = f"{phase} failed: {exc}"
                recovery_failures: list[str] = []
                try:
                    _append_journal(
                        journal_storage,
                        "rolling-back",
                        _parent_fd=transaction_fd,
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
                            root_fd=root_fd,
                            touched=touched,
                            protected=protected,
                        )
                    except Exception as rollback_exc:
                        rollback_failures.append(f"rollback failed: {rollback_exc}")
                    try:
                        verification_failures = _verify_snapshot(root_fd, snapshots, protected)
                    except Exception as verification_exc:
                        rollback_failures.append(f"rollback verification failed: {verification_exc}")
                    else:
                        rollback_failures.extend(
                            f"rollback verification failed: {detail}" for detail in verification_failures
                        )
                    try:
                        _assert_root_identity(layout.bundle_dir, root_fd)
                    except Exception as identity_exc:
                        rollback_failures.append(f"rollback verification failed: {identity_exc}")
                recovery_failures.extend(rollback_failures)
                try:
                    _append_journal(
                        journal_storage,
                        "rolled-back",
                        _parent_fd=transaction_fd,
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
                )

        finally:
            os.close(root_fd)


def apply_mutation(layout: WorkspaceLayout, plan: WorkMutationPlan) -> MutationApplication:
    """Apply *plan* atomically, retaining durable recovery evidence in cache."""
    root_fd = _open_root(layout.bundle_dir)
    try:
        with _bundle_root_lock(root_fd):
            return _apply_mutation_locked(layout, plan, root_fd)
    finally:
        os.close(root_fd)


__all__ = ["MutationApplication", "apply_mutation"]
