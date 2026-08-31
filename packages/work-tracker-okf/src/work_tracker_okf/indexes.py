"""Plan direct-descendant indexes while preserving human-authored prose."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import child_lane, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES

GENERATED_START = "<!-- graph-works:work-items:start -->"
GENERATED_END = "<!-- graph-works:work-items:end -->"


@dataclass(frozen=True, slots=True)
class LaneIndexPlan:
    lane: str
    path: Path
    before: str | None
    after: str
    entries: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.before != self.after


def _escape_link_text(text: str) -> str:
    """Backslash-escape what CommonMark reads as link-text structure.

    The backslash must go first, or a title already carrying one would be
    escaped twice. Only `\\`, `[`, and `]` are touched — other markdown
    metacharacters (`*`, `_`, backtick) are left alone on purpose, so they
    still render as markdown inside the link text; that is deliberate
    policy, not an oversight.

    Called only when the plain, unescaped rendering does not read back
    through `parse_entry` as the intended link (see `render_entry`).
    Escaping unconditionally would be wrong: CommonMark does not process
    backslash escapes inside a code span, so a title like
    ``Replace `[[entities/x]]` syntax`` would come back through as a
    literal `\\[` in the rendered text instead of a real bracket. Escaping
    only when needed leaves such titles readable.
    """
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def render_entry(item: WorkItem) -> str:
    """Render one lane-local index entry.

    Prefers the plain, unescaped rendering and falls back to
    `_escape_link_text` only when the plain form does not read back through
    `parse_entry` as the entry's own target — e.g. a title containing an
    unbalanced bracket outside a code span. This is not airtight: a handful
    of exotic titles (one embedding a complete markdown link like
    `[a](b)`, or a code span containing an *unbalanced* bracket) read back
    through `parse_entry` on the plain form even though that plain form is
    not a valid CommonMark link. `parse_entry` still agrees with itself in
    those cases, so the lint rule never raises a false finding — only the
    rendered *appearance* is affected, for titles nobody actually writes.
    """
    phase = item.phase or "not started"
    text = f"{item.type}: {item.title}"
    target = f"{item.basename}.md"
    plain = f"- [{text}]({target}) — {item.work_status} · {phase}"
    if parse_entry(plain) == target:
        return plain
    return f"- [{_escape_link_text(text)}]({target}) — {item.work_status} · {phase}"


#: The bullet and the opening `[` of an entry. Everything after it is scanned
#: rather than matched: link text may nest balanced brackets, which no regular
#: expression can describe.
_BULLET_RE = re.compile(r"^\s*-\s+\[")


def parse_entry(line: str) -> str | None:
    """The destination of one rendered index entry, or `None` if *line* is not one.

    The inverse of `render_entry` and deliberately its neighbour: the lane index
    rule reads entries back through this function, so the writer and the rule
    cannot disagree about what an entry is. `okf_io.log` states the same
    invariant for dated log sections.

    Link text may carry balanced brackets and backslash escapes, matching
    CommonMark -- and matching what `render_entry` writes.
    """
    opening = _BULLET_RE.match(line)
    if opening is None:
        return None
    index = opening.end()
    depth = 1
    while index < len(line):
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                break
        index += 1
    if depth != 0 or not line.startswith("](", index):
        return None
    start = index + 2
    end = line.find(")", start)
    if end < 0:
        return None
    return line[start:end] or None


def _required_lanes(items: Sequence[WorkItem]) -> tuple[str, ...]:
    """The lanes an index reconcile must cover.

    The archived child lane is required only for a parent that **has** archived
    children. Under the root-only archive policy an active parent can no longer
    acquire one, so requiring it unconditionally would grow an empty
    `children/_archive/index.md` under every new epic forever. Parents that
    already hold archived children -- the legacy shape -- keep theirs.

    Existing empty `children/_archive/index.md` files under parents with no
    archived children become inert. Pruning them is a `regen-index` concern and
    is deliberately out of scope; they are harmless.
    """
    lanes = {"work", "work/_archive"}
    for item in items:
        if not item.archived and item.type in PARENT_TYPES:
            lanes.add(child_lane(item.path))
            if item.archived_child_paths:
                lanes.add(child_lane(item.path, archived=True))
    return tuple(sorted(lanes))


def _direct_items(items: Sequence[WorkItem], lane: str) -> tuple[WorkItem, ...]:
    direct: list[WorkItem] = []
    for item in items:
        location = parse_item_path(item.path)
        if location is not None and location.lane == lane:
            direct.append(item)
    return tuple(sorted(direct, key=lambda item: item.basename))


def _render_region(entries: Sequence[str]) -> str:
    body = "\n".join(entries)
    if body:
        body = f"{body}\n"
    return f"{GENERATED_START}\n{body}{GENERATED_END}"


def reconcile_marked_index(before: str | None, entries: Sequence[str]) -> str:
    """Merge generated *entries* into already-repaired optional index text."""
    region = _render_region(entries)
    if before is None or not before:
        return f"{region}\n"
    start = before.find(GENERATED_START)
    end = before.find(GENERATED_END, start + len(GENERATED_START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        end += len(GENERATED_END)
        return f"{before[:start]}{region}{before[end:]}"
    prose = before.rstrip("\n")
    separator = "\n\n" if prose else ""
    return f"{prose}{separator}{region}\n"


def plan_indexes(
    root: Path,
    items: Sequence[WorkItem],
    *,
    lanes: Iterable[str] | None = None,
) -> tuple[LaneIndexPlan, ...]:
    """Plan marked regions for requested lanes or every required work lane."""
    selected = tuple(sorted(set(lanes))) if lanes is not None else _required_lanes(items)
    plans: list[LaneIndexPlan] = []
    for lane in selected:
        path = root / lane / "index.md"
        try:
            before = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            before = None
        direct = _direct_items(items, lane)
        entries = tuple(render_entry(item) for item in direct)
        plans.append(LaneIndexPlan(lane, path, before, reconcile_marked_index(before, entries), entries))
    return tuple(plans)


__all__ = [
    "GENERATED_END",
    "GENERATED_START",
    "LaneIndexPlan",
    "parse_entry",
    "plan_indexes",
    "reconcile_marked_index",
    "render_entry",
]
