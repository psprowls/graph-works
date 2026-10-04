"""Shared Git-derived evidence of siblings landed since a spec baseline.

Content and Git failures degrade to warnings, never routing authorization.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.affects import code_affects
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

from graph_works_core.workspace import provenance
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_item_repo


@dataclass(frozen=True, slots=True)
class LandedSibling:
    """One item that has both gone terminal and left a ref behind."""

    path: str
    resolved_in: str | None
    affects: tuple[str, ...]


def has_landed(item: WorkItem) -> bool:
    """Terminal AND carrying a ref. Terminal alone is not enough: a `wontfix`
    sibling changed no code and cannot have invalidated anything.

    Checks `work_status`, not `status`: `TERMINAL_STATUSES` is the
    work-lifecycle vocabulary (`resolved`/`wontfix`/`superseded`), while
    `WorkItem.status` is OKF's own document status (`draft`/`stable`/
    `deprecated`, `DOCUMENT_STATUSES`) -- a different axis entirely. Every
    other terminality check in this codebase (`hierarchy.py`,
    `dependencies.py`, `filing.py`, `workflow.py`, `_rules/state.py`,
    `_rules/graph.py`, `orchestrate/commands.py`) reads `work_status` for
    the same reason.
    """
    return item.work_status in TERMINAL_STATUSES and bool(item.resolved_in)


def landed_siblings(items: Sequence[WorkItem], item: WorkItem) -> tuple[LandedSibling, ...]:
    """`depends_on` union landed structural siblings whose `affects` overlap.

    The declared arm is the coupling the author wrote down. The overlap arm
    catches undeclared coupling — two children editing the same files — while
    staying fully mechanical: a path-set intersection makes no judgment about
    which sibling "seems relevant", which is exactly what makes widening the
    scope safe. Structural containment, not decision-ledger ownership, defines
    this arm, so an item under a different parent never enters it.

    Union by canonical path, so an item matching both arms appears once. Order follows
    *items*, which `load_items` returns sorted — the result is deterministic.
    """
    own_affects = set(code_affects(item.affects))
    declared = {edge.path for edge in item.dependency_edges}
    selected: dict[str, LandedSibling] = {}
    for other in items:
        if other.path == item.path or not has_landed(other):
            continue
        overlaps = other.parent_path == item.parent_path and bool(own_affects & set(code_affects(other.affects)))
        if other.path in declared or overlaps:
            selected[other.path] = LandedSibling(path=other.path, resolved_in=other.resolved_in, affects=other.affects)
    return tuple(selected.values())


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """`new` is True when the ref is not an ancestor of the baseline, False when
    it is, None when undetermined (`missing` ref, or a probe failure with `cause`)."""

    new: bool | None
    missing: bool = False
    cause: str | None = None


def compare_to_baseline(repo: Path, ref: str, baseline: str) -> BaselineComparison:
    """One `merge-base --is-ancestor <ref> <baseline>` probe, after checking *ref* is a commit."""
    if not provenance.commit_exists(repo, ref):
        return BaselineComparison(None, missing=True)
    outcome = provenance.probe_git(repo, "merge-base", "--is-ancestor", ref, baseline)
    if outcome.returncode == 0:
        return BaselineComparison(False)
    if outcome.returncode == 1:
        return BaselineComparison(True)
    return BaselineComparison(None, cause=outcome.cause)


@dataclass(frozen=True, slots=True)
class LandedEntry:
    sibling: LandedSibling
    overlaps: bool


@dataclass(frozen=True, slots=True)
class LandedSince:
    code_baseline: str | None
    workspace_baseline: str | None
    repo: Path | None
    entries: tuple[LandedEntry, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def stale(self) -> tuple[str, ...]:
        return tuple(entry.sibling.path for entry in self.entries if entry.overlaps)


def landed_since(layout: WorkspaceLayout, items: Sequence[WorkItem], item: WorkItem) -> LandedSince:
    """Verified candidate commits not ancestral to the baseline.

    No code baseline or candidates means no repository or Git probes.
    """
    baseline = item.spec_baseline
    code = baseline.code if baseline is not None else None
    workspace = baseline.workspace if baseline is not None else None
    candidates = landed_siblings(items, item)
    if code is None or not candidates:
        return LandedSince(code, workspace, None)
    by_path = {candidate.path: candidate for candidate in items}
    try:
        repo = resolve_item_repo(layout, item, by_path).path
    except WorkspaceError as exc:
        return LandedSince(code, workspace, None, warnings=(f"landed-since: {exc}",))
    if repo is None:
        return LandedSince(code, workspace, None, warnings=("landed-since: no code repository resolved",))
    own = set(code_affects(item.affects))
    entries: list[LandedEntry] = []
    warnings: list[str] = []
    comparisons: dict[str, BaselineComparison] = {}
    for sibling in candidates:
        ref = sibling.resolved_in or ""
        if ref not in comparisons:
            comparisons[ref] = compare_to_baseline(repo, ref, code)
        comparison = comparisons[ref]
        if comparison.missing:
            warnings.append(f"landed-since: {sibling.path} resolved_in {ref!r} is not a commit in {repo}; skipped")
            continue
        if comparison.new is False:
            continue
        if comparison.new is None:
            warnings.append(f"landed-since: could not compare {ref} with baseline {code} ({comparison.cause}); skipped")
            continue
        overlaps = bool(own & set(code_affects(sibling.affects)))
        entries.append(LandedEntry(sibling, overlaps))
    return LandedSince(code, workspace, repo, tuple(entries), tuple(warnings))


def stale_spec_for(layout: WorkspaceLayout, items: Sequence[WorkItem], item: WorkItem) -> tuple[str, ...]:
    """Routing fact: empty off plan, otherwise overlapping landed entries."""
    if item.phase != "plan":
        return ()
    return landed_since(layout, items, item).stale


def stale_by_path(
    layout: WorkspaceLayout, items: Sequence[WorkItem], paths: Iterable[str]
) -> Mapping[str, tuple[str, ...]]:
    """Compute staleness for requested items, keeping non-empty results."""
    by_path = {candidate.path: candidate for candidate in items}
    result: dict[str, tuple[str, ...]] = {}
    for path in paths:
        item = by_path.get(path)
        if item is not None and (stale := stale_spec_for(layout, items, item)):
            result[path] = stale
    return result


__all__ = [
    "BaselineComparison",
    "LandedEntry",
    "LandedSibling",
    "LandedSince",
    "compare_to_baseline",
    "has_landed",
    "landed_siblings",
    "landed_since",
    "stale_by_path",
    "stale_spec_for",
]
