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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from okf_ext.locking import locked
from work_tracker_okf.paths import item_page
from work_tracker_okf.returns import ExecuteReturn, parse_execute_return

from graph_works_core.workspace.anchors import DIRECTORY_FSYNC_HONORED
from graph_works_core.workspace.commits import item_stem
from graph_works_core.workspace.layout import WorkspaceLayout


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
    fingerprint: str = ""
    page_sha256: str = ""
    plan_sha256: str | None = None
    worktree: str = ""
    branch: str = ""
    identity: str = ""
    coverage_member: str = ""
    coverage_before_sha256: str | None = None
    coverage_after_sha256: str = ""
    content_commit: str | None = None
    state: Literal["intent", "content-committed"] = "intent"
    record: ExecuteReturn | None = None
    plan_member: str = ""
    content_before_head: str = ""
    canonical_after_sha256: str | None = None
    malformed: bool = False
    local: LocalPublication | None = None

    def to_data(self) -> dict[str, object]:
        if self.local is not None:
            return {
                "version": 2,
                "path": self.path,
                "record": self.record.to_data() if self.record else None,
                "worktree": self.worktree,
                "branch": self.branch,
                "identity": self.identity,
                "plan_member": self.plan_member,
                "plan_sha256": self.plan_sha256,
                "publication": self.local.to_data(),
            }
        return {
            "version": 1,
            "path": self.path,
            "return_id": self.return_id,
            "fingerprint": self.fingerprint,
            "page_sha256": self.page_sha256,
            "plan_sha256": self.plan_sha256,
            "destination": {"worktree": self.worktree, "branch": self.branch, "identity": self.identity},
            "coverage_member": self.coverage_member,
            "coverage_before_sha256": self.coverage_before_sha256,
            "coverage_after_sha256": self.coverage_after_sha256,
            "content_commit": self.content_commit,
            "state": self.state,
            "record": self.record.to_data() if self.record else None,
            "plan_member": self.plan_member,
            "content_before_head": self.content_before_head,
            "canonical_after_sha256": self.canonical_after_sha256,
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


def pending_return(layout: WorkspaceLayout, path: str) -> ReturnOperation | None:
    try:
        raw = json.loads(journal_path(layout, path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return ReturnOperation(path, malformed=True)
    malformed = ReturnOperation(path, malformed=True)
    if isinstance(raw, dict) and type(raw.get("version")) is int and raw["version"] == 2:
        return _local_operation(path, raw)
    if not isinstance(raw, dict) or set(raw) != set(malformed.to_data()):
        return malformed
    destination = raw["destination"]
    record, invalid = parse_execute_return(raw["record"])
    if (
        type(raw["version"]) is not int
        or raw["version"] != 1
        or raw["path"] != path
        or invalid
        or record is None
        or not record.active
        or record.id != raw["return_id"]
        or not isinstance(destination, dict)
        or set(destination) != {"worktree", "branch", "identity"}
        or any(not isinstance(destination[k], str) or not destination[k] for k in destination)
        or not Path(destination["worktree"]).is_absolute()
        or not Path(destination["identity"]).is_absolute()
        or not all(_sha(raw[k]) for k in ("fingerprint", "page_sha256", "coverage_after_sha256"))
        or not all(
            _sha(raw[k], optional=True) for k in ("plan_sha256", "coverage_before_sha256", "canonical_after_sha256")
        )
        or not _sha(raw["content_before_head"], git=True)
        or not _sha(raw["content_commit"], optional=True, git=True)
        or not _member(raw["coverage_member"])
        or not _member(raw["plan_member"])
        or raw["state"] not in ("intent", "content-committed")
        or (raw["state"] == "content-committed") != (raw["content_commit"] is not None)
        or record.coverage != "/" + raw["coverage_member"]
        or record.plan_sha256 != raw["plan_sha256"]
        or (record.plan is not None and record.plan != "/" + raw["plan_member"])
    ):
        return malformed
    return ReturnOperation(
        path,
        raw["return_id"],
        raw["fingerprint"],
        raw["page_sha256"],
        raw["plan_sha256"],
        destination["worktree"],
        destination["branch"],
        destination["identity"],
        raw["coverage_member"],
        raw["coverage_before_sha256"],
        raw["coverage_after_sha256"],
        raw["content_commit"],
        raw["state"],
        record,
        raw["plan_member"],
        raw["content_before_head"],
        raw["canonical_after_sha256"],
    )


def _local_operation(path: str, raw: dict[str, object]) -> ReturnOperation:
    malformed = ReturnOperation(path, malformed=True)
    if (
        set(raw)
        != {"version", "path", "record", "worktree", "branch", "identity", "plan_member", "plan_sha256", "publication"}
        or raw["path"] != path
    ):
        return malformed
    record, invalid = parse_execute_return(raw["record"])
    publication = raw["publication"]
    worktree, branch, identity = raw["worktree"], raw["branch"], raw["identity"]
    plan_member, plan_sha = raw["plan_member"], raw["plan_sha256"]
    if (
        invalid
        or record is None
        or not record.active
        or not isinstance(worktree, str)
        or not Path(worktree).is_absolute()
        or not isinstance(identity, str)
        or not Path(identity).is_absolute()
        or not isinstance(branch, str)
        or not branch
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
        worktree=worktree,
        branch=branch,
        identity=identity,
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


def mark_publication(layout: WorkspaceLayout, op: ReturnOperation, after: bytes) -> ReturnOperation:
    updated = replace(op, canonical_after_sha256=digest(after))
    write_operation(layout, updated)
    return updated


def finish_return(layout: WorkspaceLayout, op: ReturnOperation) -> None:
    target = journal_path(layout, op.path)
    if pending_return(layout, op.path) != op:
        raise ValueError("return journal changed; inspect before clearing")
    target.unlink()
    _sync_dir(target.parent)
