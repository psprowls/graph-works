"""Plan direct-descendant indexes while preserving human-authored prose."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import child_lane, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES

_LEGACY_MARKER_START = "<!-- graph-works:work-items:start -->"
_LEGACY_MARKER_END = "<!-- graph-works:work-items:end -->"
_LEGACY_MARKER_LINES = (_LEGACY_MARKER_START, _LEGACY_MARKER_END)


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
    """The lanes an index reconcile must cover: the two root lanes plus every
    active parent's `children/` lane."""
    lanes = {"work", "work/_archive"}
    for item in items:
        if not item.archived and item.type in PARENT_TYPES:
            lanes.add(child_lane(item.path))
    return tuple(sorted(lanes))


def is_direct_entry_target(target: str) -> bool:
    """Whether *target* is the shape of a direct item page in this lane.

    A direct work-tracker entry always links to a bare ``<basename>.md`` in
    the same directory as the index. Anything with a path separator (a
    subdirectory, ``children/...``, ``../sibling-lane/...``) or ``index.md``
    itself belongs to okf-io, not the work tracker, and is left alone.
    """
    return bool(target) and "/" not in target and target != "index.md" and target.endswith(".md")


def _direct_items(items: Sequence[WorkItem], lane: str) -> tuple[WorkItem, ...]:
    direct: list[WorkItem] = []
    for item in items:
        location = parse_item_path(item.path)
        if location is not None and location.lane == lane:
            direct.append(item)
    return tuple(sorted(direct, key=lambda item: item.basename))


def _new_items_section(rendered: Sequence[str]) -> str:
    if not rendered:
        return ""
    body = "\n".join(rendered)
    return f"# Items\n\n{body}\n"


def _entry_order(target: str) -> str:
    """Sort key for an entry target: its basename, matching `_direct_items`.

    Comparing raw targets would let the ``.md`` suffix decide between
    prefix-colliding slugs (``feature-x-y.md`` < ``feature-x.md``, since
    ``-`` < ``.``), so the final order would depend on insertion history.
    """
    return target.removesuffix(".md")


def _insert_missing(kept: list[str], rendered: dict[str, str], missing: Sequence[str]) -> list[str]:
    """Insert each missing target's rendered line next to the kept entries, in filename order."""
    for target in missing:
        positions = [
            (existing, idx)
            for idx, line in enumerate(kept)
            if (existing := parse_entry(line)) is not None and is_direct_entry_target(existing)
        ]
        insert_at = next(
            (idx for existing, idx in positions if _entry_order(existing) > _entry_order(target)),
            None,
        )
        if insert_at is None:
            insert_at = (positions[-1][1] + 1) if positions else len(kept)
        kept.insert(insert_at, rendered[target])
    return kept


def _append_missing_without_existing_entries(
    kept: list[str], rendered: dict[str, str], missing: Sequence[str]
) -> list[str]:
    """Add entries when no surviving entry anchors an insertion point.

    Reuses a dangling, entry-less "# Items" heading left behind by a lane
    that pruned to zero entries, instead of appending a duplicate heading.
    """
    new_lines = [rendered[target] for target in missing]
    heading_at = None
    for idx, line in enumerate(kept):
        if line.strip() == "# Items":
            heading_at = idx
    if heading_at is not None:
        j = heading_at + 1
        while j < len(kept) and kept[j] == "":
            j += 1
        if j >= len(kept) or kept[j].lstrip().startswith("#"):
            tail_sep = [""] if j < len(kept) else []
            return [*kept[: heading_at + 1], "", *new_lines, *tail_sep, *kept[j:]]
    trimmed = list(kept)
    while trimmed and trimmed[-1] == "":
        trimmed.pop()
    if trimmed:
        return [*trimmed, "", "# Items", "", *new_lines]
    return ["# Items", "", *new_lines]


def strip_legacy_markers(text: str) -> str:
    """Remove the legacy ``graph-works:work-items`` marker lines, and nothing else.

    Every other byte -- entries, prose, blank lines, the trailing newline --
    is left exactly as it was. `reconcile_entries` strips through this same
    function, so a lane index migrates identically whether it is reconciled
    or only stripped.
    """
    return "\n".join(line for line in text.split("\n") if line.strip() not in _LEGACY_MARKER_LINES)


def reconcile_entries(before: str | None, entries: Sequence[str]) -> str:
    """Merge *entries* into *before*, owning only lines whose target is a direct item page.

    Mirrors okf-io's reconciliation model one tier up: an entry is added,
    refreshed, or pruned by what it links to, not by a byte-range marker.
    Legacy ``graph-works:work-items`` marker lines are stripped on sight, so
    the first reconciliation after this change migrates any lane index that
    still carries them.
    """
    rendered: dict[str, str] = {}
    for entry in entries:
        target = parse_entry(entry)
        if target is None:
            raise ValueError(f"rendered entry does not round-trip through parse_entry: {entry!r}")
        rendered[target] = entry
    if before is None or not before:
        return _new_items_section(list(rendered.values()))
    lines = strip_legacy_markers(before).split("\n")
    if lines and lines[-1] == "" and before.endswith("\n"):
        lines.pop()
    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        target = parse_entry(line)
        if target is not None and is_direct_entry_target(target):
            if target in rendered:
                kept.append(rendered[target])
                seen.add(target)
            continue  # stale entry: drop
        kept.append(line)
    missing = sorted((target for target in rendered if target not in seen), key=_entry_order)
    if missing:
        if seen:
            kept = _insert_missing(kept, rendered, missing)
        else:
            kept = _append_missing_without_existing_entries(kept, rendered, missing)
    text = "\n".join(kept)
    return f"{text}\n" if text else ""


def plan_indexes(
    root: Path,
    items: Sequence[WorkItem],
    *,
    lanes: Iterable[str] | None = None,
) -> tuple[LaneIndexPlan, ...]:
    """Plan reconciled entries for requested lanes or every required work lane."""
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
        plans.append(LaneIndexPlan(lane, path, before, reconcile_entries(before, entries), entries))
    return tuple(plans)


__all__ = [
    "LaneIndexPlan",
    "is_direct_entry_target",
    "parse_entry",
    "plan_indexes",
    "reconcile_entries",
    "render_entry",
    "strip_legacy_markers",
]
