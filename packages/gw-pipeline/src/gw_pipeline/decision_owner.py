"""Decision ownership for the work verticals: who owns a ledger, which open
decisions hold which items, and (Task 6) the lock filing and advance share.

Layer 0 on purpose. `gw_work` (filing, `gw work next`) and
`gw_orchestrate` (`gw work advance`, the planner) both need it,
and the slice boundary forbids the kernel from importing orchestration.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from gw_kernel.workspace.bundle import load_workspace_bundle
from gw_kernel.workspace.layout import WorkspaceLayout
from okf_ext.locking import locked as _locked_file
from okf_io import Bundle, Document, parse
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.hierarchy import decision_owner
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import item_page
from work_tracker_okf.snapshot import WorkSnapshot
from work_tracker_okf.sources import upsert

#: A real reparent settles within one retry; a third consecutive owner change
#: is concurrent reparenting a human should look at.
MAX_OWNER_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class DecisionOwner:
    """The decision owner (nearest Release/Epic/Feature, else the item itself)."""

    owner_path: str
    redirected_from: str | None
    ledger: Path


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """One bundle projection and its decision owner for a command."""

    owner: DecisionOwner
    bundle: Bundle
    items: WorkSnapshot


@dataclass(frozen=True, slots=True)
class HoldReport:
    """One open decision holding a planned item, wherever its ledger lives."""

    path: str
    owner_path: str
    ledger_path: str
    decision: _decisions.Decision


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def planned_write(
    member: str,
    before: bytes | None,
    after: bytes,
    *,
    source_member: str | None = None,
) -> PlannedWrite:
    """Retain the planner's preimage instead of resnapshotting live state."""
    return PlannedWrite(member, _digest(before) if before is not None else None, after, source_member)


def write_plan(
    root: Path,
    operation: Literal["file", "indexes"],
    writes: Sequence[PlannedWrite],
    *,
    mkdirs: Sequence[str] = (),
    validate_paths: Sequence[str] = (),
    warnings: Sequence[str] = (),
    directory_preconditions: Sequence[DirectoryPrecondition] = (),
) -> WorkMutationPlan:
    planned = tuple(sorted(writes, key=lambda write: write.member))
    directories = tuple(
        sorted(set(mkdirs) | {Path(write.member).parent.as_posix() for write in planned if "/" in write.member})
    )
    return WorkMutationPlan(
        root=root,
        operation=operation,
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=planned,
        deletes=(),
        mkdirs=directories,
        warnings=tuple(warnings),
        refusals=(),
        validate_paths=tuple(sorted(set(validate_paths))),
        directory_preconditions=tuple(sorted(directory_preconditions, key=lambda condition: condition.member)),
    )


def decision_mutation(
    context: DecisionContext,
    plan: _decisions.DecisionPlan,
    ledger_before: bytes | None,
    extra_writes: Sequence[PlannedWrite] = (),
    *,
    page_edits: Mapping[str, Callable[[Document], None]] = MappingProxyType({}),
    validate_paths: Sequence[str] = (),
    directory_preconditions: Sequence[DirectoryPrecondition] = (),
) -> WorkMutationPlan:
    """The ledger write, the owner page's ledger link and any further page edits as one plan.

    The owner page gets the ledger `upsert`, then its `page_edits` entry; every
    other `page_edits` key is read from the bundle, edited and written. Writes
    whose bytes are unchanged are dropped.
    """
    owner_path = context.owner.owner_path
    root = context.bundle.root
    ledger_member = context.owner.ledger.relative_to(root).as_posix()
    ledger_text = _decisions.render(_decisions.parse(plan.snapshot.text).preamble, plan.after)
    candidates: list[tuple[str, bytes | None, bytes]] = [(ledger_member, ledger_before, ledger_text.encode("utf-8"))]
    for member_path in dict.fromkeys((owner_path, *page_edits)):
        page = item_page(member_path)
        before = (
            page.path(root).read_bytes()
            if member_path == owner_path
            else context.bundle.concepts[member_path].serialize().encode("utf-8")
        )
        document = parse(before.decode("utf-8"), path=page.path(root))
        if member_path == owner_path:
            upsert(document, _decisions.ledger_ref(owner_path), title="Decisions")
        edit = page_edits.get(member_path)
        if edit is not None:
            edit(document)
        candidates.append((page.rel, before, document.serialize().encode("utf-8")))
    writes = tuple(
        planned_write(member, before, after) for member, before, after in candidates if before != after
    ) + tuple(extra_writes)
    return write_plan(
        root,
        "file",
        writes,
        validate_paths=validate_paths or (owner_path,),
        warnings=plan.warnings,
        directory_preconditions=directory_preconditions,
    )


def decision_lock_path(layout: WorkspaceLayout, owner_path: str) -> Path:
    digest = hashlib.sha256(owner_path.encode("utf-8")).hexdigest()
    return layout.cache_dir / "decisions" / f"{digest}.lock"


def decision_context(layout: WorkspaceLayout, path: str) -> DecisionContext:
    bundle = load_workspace_bundle(layout, ignore=IGNORE)
    items = load_items(bundle)
    if path not in items.by_path:
        raise ValueError(f"unknown work item {path!r}")
    owner_path = decision_owner(items, path)
    if owner_path is None:  # pragma: no cover -- a known path always has an owner (D-001)
        raise ValueError(f"unknown work item {path!r}")
    ledger = _decisions.ledger_ref(owner_path).path(bundle.root)
    return DecisionContext(DecisionOwner(owner_path, None if path == owner_path else path, ledger), bundle, items)


@contextmanager
def locked_decision_owner(layout: WorkspaceLayout, path: str) -> Iterator[DecisionContext]:
    """Take *path*'s decision-owner lock and yield a projection read inside it.

    The one lock hold filing and stage advance share, before bundle/executor
    locks. Return/dispatch admission and dispatch-key ownership, where used,
    are acquired outside this lock. A caller applies its writes before leaving
    the block. The owner is
    re-resolved under the lock; a change (a reparent) retries, at most
    `MAX_OWNER_ATTEMPTS` times, and nothing is written when the bound is hit.
    """
    for _attempt in range(MAX_OWNER_ATTEMPTS):
        candidate = decision_context(layout, path).owner.owner_path
        with _locked_file(decision_lock_path(layout, candidate)):
            current = decision_context(layout, path)
            if current.owner.owner_path != candidate:
                continue
            yield current
            return
    raise ValueError(f"decision owner of {path} changed during locking; re-run")


def hold_in(context: DecisionContext, path: str) -> HoldFact | None:
    held = _decisions.holds_for(_decisions.load(context.owner.ledger).entries, path)
    return _decisions.hold_fact(held[0], path) if held else None


def hold_for(items: Sequence[WorkItem], bundle_root: Path, path: str) -> HoldFact | None:
    """The lowest-numbered open decision naming *path* in its owner's ledger."""
    owner = decision_owner(items, path)
    if owner is None:
        return None
    held = _decisions.holds_for(_decisions.load(_decisions.ledger_ref(owner).path(bundle_root)).entries, path)
    return _decisions.hold_fact(held[0], path) if held else None


def holds_by_path(items: Sequence[WorkItem], bundle_root: Path) -> dict[str, HoldFact]:
    """Every held item's lowest open hold. One ledger read per distinct owner."""
    entries_by_owner: dict[str, tuple[_decisions.Decision, ...]] = {}
    found: dict[str, HoldFact] = {}
    for item in items:
        owner = decision_owner(items, item.path)
        if owner is None:  # pragma: no cover -- `item` came out of `items`
            continue
        if owner not in entries_by_owner:
            ledger = _decisions.ledger_ref(owner).path(bundle_root)
            entries_by_owner[owner] = tuple(_decisions.load(ledger).entries)
        held = _decisions.holds_for(entries_by_owner[owner], item.path)
        if held:
            found[item.path] = _decisions.hold_fact(held[0], item.path)
    return found


def open_holds(items: Sequence[WorkItem], bundle_root: Path, paths: Collection[str]) -> tuple[HoldReport, ...]:
    """Every open hold, sorted by path and decision number; read each owner once."""
    entries_by_owner: dict[str, tuple[_decisions.Decision, ...]] = {}
    reports: list[HoldReport] = []
    for path in sorted(set(paths)):
        owner = decision_owner(items, path)
        if owner is None:
            continue
        ledger = _decisions.ledger_ref(owner).path(bundle_root)
        if owner not in entries_by_owner:
            entries_by_owner[owner] = tuple(_decisions.load(ledger).entries)
        reports.extend(
            HoldReport(path, owner, str(ledger), decision)
            for decision in _decisions.holds_for(entries_by_owner[owner], path)
        )
    return tuple(reports)


__all__ = [
    "MAX_OWNER_ATTEMPTS",
    "DecisionContext",
    "DecisionOwner",
    "HoldReport",
    "decision_context",
    "decision_lock_path",
    "decision_mutation",
    "hold_for",
    "hold_in",
    "holds_by_path",
    "locked_decision_owner",
    "open_holds",
    "planned_write",
    "write_plan",
]
