"""Which bundle pages link into files an advance changed (D-003), and the proposal each gets.

Mechanical: the diff decides, not link resolution. After the detach, a link to a deleted file is broken, and that
changes nothing here. Pages anywhere in the bundle count, except this repository's own snapshots and changelog
(gw's records of the change) and `Proposal` pages (the review queue itself).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from okf_ext.proposals import PROPOSAL_TYPE, ProposalPlan, plan_propose
from okf_io import Bundle, LinkGraph

from repositories_okf.changelog import changelog_path
from repositories_okf.git import FileChange
from repositories_okf.lane import LANE_DIR
from repositories_okf.snapshots import Snapshot

if TYPE_CHECKING:
    from datetime import datetime

#: Statuses flagged for exact file links; directory links also include additions.
FLAGGED_STATUSES = frozenset({"M", "D", "R", "T"})
_VERBS = {"A": "added", "M": "modified", "D": "deleted", "T": "changed type"}


@dataclass(frozen=True, slots=True, order=True)
class FlaggedLink:
    target: str
    changed: str
    status: str
    renamed_to: str | None

    @property
    def reason(self) -> str:
        verb = f"renamed to `{self.renamed_to}`" if self.status == "R" else _VERBS.get(self.status, "changed")
        linked = "" if self.target == self.changed else f" (linked as `{self.target}`)"
        return f"`{self.changed}` {verb}{linked}"


@dataclass(frozen=True, slots=True)
class FlaggedPage:
    page: str
    title: str
    links: tuple[FlaggedLink, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(link.reason for link in self.links)


def clone_prefix(name: str) -> str:
    return f"{LANE_DIR}/{name}/references/git/"


def _excluded(bundle: Bundle, source: str, name: str) -> bool:
    if source.startswith(f"{LANE_DIR}/{name}/snapshots/") or f"{source}.md" == changelog_path(name):
        return True
    document = bundle.concepts.get(source)
    return document is not None and document.fm_data(dates="iso").get("type") == PROPOSAL_TYPE


def flag_pages(bundle: Bundle, graph: LinkGraph, name: str, changes: Sequence[FileChange]) -> tuple[FlaggedPage, ...]:
    prefix = clone_prefix(name)
    root = prefix.rstrip("/")
    found: dict[str, set[FlaggedLink]] = {}
    for link in graph.links:
        if link.external or link.target is None:
            continue
        target = link.target.rstrip("/")
        if target != root and not target.startswith(prefix):
            continue
        if _excluded(bundle, link.source, name):
            continue
        relative = "" if target == root else target[len(prefix) :]
        for change in changes:
            exact = change.path == relative and change.status in FLAGGED_STATUSES
            endpoints = (change.path, change.renamed_to) if change.renamed_to else (change.path,)
            directory = relative == "" or any(path.startswith(f"{relative}/") for path in endpoints)
            if exact or directory:
                shown = "." if relative == "" else (relative if exact else f"{relative}/")
                found.setdefault(link.source, set()).add(
                    FlaggedLink(shown, change.path, change.status, change.renamed_to)
                )
    pages: list[FlaggedPage] = []
    for source, links in sorted(found.items()):
        document = bundle.concepts.get(source)
        title = (document.fm.title if document is not None else None) or source
        pages.append(FlaggedPage(f"{source}.md", title, tuple(sorted(links))))
    return tuple(pages)


def _source_id(snapshot: Snapshot) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", snapshot.name.lower()).strip("-") or "repo"
    return f"repo-{slug}-{snapshot.commit[:7]}"


def plan_flag_proposal(bundle: Bundle, page: FlaggedPage, snapshot: Snapshot, *, by: str, at: datetime) -> ProposalPlan:
    """One proposal per flagged page, sourced from the snapshot page. Identity is the target, so a second advance
    merges."""
    source = {
        "id": _source_id(snapshot),
        "resource": f"/{snapshot.path}",
        "title": f"{snapshot.name} @ {snapshot.label}",
        "at_commit": snapshot.commit,
    }
    description = (
        f"{len(page.links)} linked file(s) in reference repository {snapshot.name} changed as of {snapshot.label}. "
        "Filed by gw repo advance; nothing was edited."
    )
    return plan_propose(bundle, page.page, [source], title=page.title, description=description, by=by, at=at)


__all__ = ["FLAGGED_STATUSES", "FlaggedLink", "FlaggedPage", "clone_prefix", "flag_pages", "plan_flag_proposal"]
