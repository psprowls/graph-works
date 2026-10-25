"""Durable return intent shared by dispatch readers and the return writer.

A pending record is an admission fence, including after canonical publication.
Exact committed publication can clear it automatically. Local publication may
also clear after every operation-owned preimage and index entry is restored.
Malformed, changed or unknown evidence requires operator inspection. No lifecycle
inference is made from a phase alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gw_kernel.workspace.anchors import DIRECTORY_FSYNC_HONORED
from gw_kernel.workspace.commits import item_stem
from gw_kernel.workspace.layout import WorkspaceLayout
from okf_ext.locking import locked
from work_tracker_okf.paths import item_page
from work_tracker_okf.returns import ExecuteReturn, parse_execute_return


@contextmanager
def return_dispatch_admission(layout: WorkspaceLayout, path: str) -> Iterator[None]:
    """Serialize return intent with the full effect-bearing dispatch operation.

    Lock order: dispatch key (dispatch only), this item admission lock, short
    decision-owner locks, then content/canonical bundle locks. Returns never
    take a dispatch-key lock. No decision-owner lock spans an Orca call, so
    dispatch journal and placement writers may acquire their existing locks.
    This OS lock is deliberately not reentrant and is never taken by dry runs.
    """
    item_page(path)
    key = hashlib.sha256(path.encode("utf-8")).hexdigest()
    with locked(layout.cache_dir / "execute-return-admission" / f"{key}.lock"):
        yield


def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


@dataclass(frozen=True, slots=True)
class LocalPublication:
    kind: Literal["same-root-return", "completion"]
    head: str
    subject: str
    members: tuple[tuple[str, str], ...]

    def to_data(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "head": self.head,
            "subject": self.subject,
            "members": [list(pair) for pair in self.members],
        }


@dataclass(frozen=True, slots=True)
class ReturnOperation:
    path: str
    return_id: str = ""
    plan_sha256: str | None = None
    record: ExecuteReturn | None = None
    plan_member: str = ""
    malformed: bool = False
    local: LocalPublication | None = None
    retired_worktree: str | None = None

    def to_data(self) -> dict[str, object]:
        if self.local is None:
            raise ValueError("only a local publication is journaled")
        return {
            "version": 2,
            "path": self.path,
            "record": self.record.to_data() if self.record else None,
            "plan_member": self.plan_member,
            "plan_sha256": self.plan_sha256,
            "publication": self.local.to_data(),
        }


def journal_path(layout: WorkspaceLayout, path: str) -> Path:
    # The full path stored in the record detects same-stem collisions fail-closed.
    item_page(path)
    return layout.cache_dir / "execute-returns" / f"{item_stem(path)}.json"


def _sha(value: object, *, optional: bool = False, git: bool = False) -> bool:
    return (optional and value is None) or (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}" if git else r"[0-9a-f]{64}", value) is not None
    )


def _member(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not Path(value).is_absolute()
        and ".." not in Path(value).parts
        and "\\" not in value
    )


_RETIRED_KEYS = ("worktree", "branch", "identity")


def pending_return(layout: WorkspaceLayout, path: str) -> ReturnOperation | None:
    try:
        raw = json.loads(journal_path(layout, path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return ReturnOperation(path, malformed=True)
    malformed = ReturnOperation(path, malformed=True)
    if not isinstance(raw, dict):
        return malformed
    # Journals written before placement was computed named a workspace worktree.
    # One naming the primary checkout itself is still an ordinary local fence.
    retired = _retired_worktree(layout, raw)
    if retired is not None:
        return ReturnOperation(path, malformed=True, retired_worktree=retired)
    if type(raw.get("version")) is int and raw["version"] == 2:
        return _local_operation(path, {k: v for k, v in raw.items() if k not in _RETIRED_KEYS})
    return malformed


def _retired_worktree(layout: WorkspaceLayout, raw: dict[str, object]) -> str | None:
    destination = raw.get("destination")
    worktree = destination.get("worktree") if isinstance(destination, dict) else raw.get("worktree")
    if isinstance(worktree, str) and worktree and worktree != str(layout.root.resolve()):
        return worktree
    if raw.get("version") == 1 and isinstance(destination, dict):
        return worktree if isinstance(worktree, str) else ""
    return None


def _local_operation(path: str, raw: dict[str, object]) -> ReturnOperation:
    malformed = ReturnOperation(path, malformed=True)
    if set(raw) != {"version", "path", "record", "plan_member", "plan_sha256", "publication"} or raw["path"] != path:
        return malformed
    record, invalid = parse_execute_return(raw["record"])
    publication = raw["publication"]
    plan_member, plan_sha = raw["plan_member"], raw["plan_sha256"]
    if (
        invalid
        or record is None
        or not record.active
        or not isinstance(plan_member, str)
        or not _member(plan_member)
        or not (plan_sha is None or (isinstance(plan_sha, str) and _sha(plan_sha)))
        or not isinstance(publication, dict)
        or set(publication) != {"kind", "head", "subject", "members"}
    ):
        return malformed
    kind, head, subject = publication["kind"], publication["head"], publication["subject"]
    members = publication["members"]
    if (
        kind not in ("same-root-return", "completion")
        or not isinstance(head, str)
        or not _sha(head, git=True)
        or not isinstance(subject, str)
        or not subject
        or "\n" in subject
        or not isinstance(members, list)
        or not members
        or any(
            not isinstance(pair, list) or len(pair) != 2 or not _member(pair[0]) or not _sha(pair[1])
            for pair in members
        )
    ):
        return malformed
    pairs = tuple((pair[0], pair[1]) for pair in members)
    if len(dict(pairs)) != len(pairs) or item_page(path).rel not in dict(pairs):
        return malformed
    if kind == "same-root-return" and record.coverage.lstrip("/") not in dict(pairs):
        return malformed
    return ReturnOperation(
        path,
        return_id=record.id,
        record=record,
        plan_member=plan_member,
        plan_sha256=plan_sha,
        local=LocalPublication(kind, head, subject, pairs),
    )


def _sync_dir(path: Path) -> None:
    if DIRECTORY_FSYNC_HONORED:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_operation(layout: WorkspaceLayout, op: ReturnOperation) -> None:
    target = journal_path(layout, op.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _sync_dir(target.parent.parent)
    descriptor, name = tempfile.mkstemp(prefix=".return-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(op.to_data(), stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(target)
        _sync_dir(target.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def finish_return(layout: WorkspaceLayout, op: ReturnOperation) -> None:
    target = journal_path(layout, op.path)
    if pending_return(layout, op.path) != op:
        raise ValueError("return journal changed; inspect before clearing")
    target.unlink()
    _sync_dir(target.parent)
