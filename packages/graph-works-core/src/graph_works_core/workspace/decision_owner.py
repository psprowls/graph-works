"""Decision ownership for the work verticals: who owns a ledger, which open
decisions hold which items, and (Task 6) the lock filing and advance share.

Layer 0 on purpose. `graph_works_core.work` (filing, `gw work next`) and
`graph_works_core.orchestrate` (`gw work advance`, the planner) both need it,
and the vertical-independence contract forbids either importing the other.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from okf_ext.locking import locked as _locked_file
from okf_io import Bundle, load_bundle
from work_tracker_okf import decisions as _decisions
from work_tracker_okf._selection import path_index
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.hierarchy import decision_owner
from work_tracker_okf.items import IGNORE, WorkItem, load_items

from graph_works_core.workspace.layout import WorkspaceLayout

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
    items: tuple[WorkItem, ...]


@dataclass(frozen=True, slots=True)
class HoldReport:
    """One open decision holding a planned item, wherever its ledger lives."""

    path: str
    owner_path: str
    ledger_path: str
    decision: _decisions.Decision


def decision_lock_path(layout: WorkspaceLayout, owner_path: str) -> Path:
    digest = hashlib.sha256(owner_path.encode("utf-8")).hexdigest()
    return layout.cache_dir / "decisions" / f"{digest}.lock"


def decision_context(layout: WorkspaceLayout, path: str) -> DecisionContext:
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = tuple(load_items(bundle))
    selected = path_index(items)
    if path not in selected:
        raise ValueError(f"unknown work item {path!r}")
    owner_path = decision_owner(tuple(selected.values()), path)
    if owner_path is None:  # pragma: no cover -- a known path always has an owner (D-001)
        raise ValueError(f"unknown work item {path!r}")
    ledger = _decisions.ledger_ref(owner_path).path(bundle.root)
    return DecisionContext(DecisionOwner(owner_path, None if path == owner_path else path, ledger), bundle, items)


@contextmanager
def locked_decision_owner(layout: WorkspaceLayout, path: str) -> Iterator[DecisionContext]:
    """Take *path*'s decision-owner lock and yield a projection read inside it.

    The one lock hold filing and stage advance share, first in the total
    order (owner lock -> `apply_mutation`'s bundle-root lock -> its executor
    lock). A caller applies its writes before leaving the block. The owner is
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
    "hold_for",
    "hold_in",
    "holds_by_path",
    "locked_decision_owner",
    "open_holds",
]
