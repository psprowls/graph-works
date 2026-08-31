"""Rules for permanent item placement, ownership, sources, and lane indexes."""

from __future__ import annotations

import posixpath
from collections import Counter
from collections.abc import Iterable
from pathlib import PurePosixPath

from okf_io import Finding, Rule, RuleContext, Severity

from work_tracker_okf._rules._common import LaneConfig, items
from work_tracker_okf.indexes import GENERATED_END, GENERATED_START, parse_entry, plan_indexes
from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.paths import child_lane, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES, ROOT_ONLY_TYPES, SLUG_PREFIXES

CODES: tuple[str, ...] = (
    "structure.prefix-type-mismatch",
    "structure.illegal-lane",
    "structure.release-nested",
    "structure.children-on-leaf",
    "structure.owned-directory-missing",
    "structure.source-id-duplicate",
    "structure.source-escape",
    "structure.source-missing",
    "structure.index-entry-missing",
    "structure.index-entry-stale",
    "structure.index-entry-duplicate",
    "structure.index-entry-non-direct",
    "structure.index-entry-unreadable",
)

_SPEC = "work_tracker_okf._rules.structure"


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.page_path, line=None)


def layout(ctx: RuleContext) -> Iterable[Finding]:
    """Validate the path grammar and which item types may own child lanes."""
    work_items = items(ctx)
    by_path = item_index(work_items)
    for concept_id in sorted(ctx.bundle.concepts):
        if concept_id.startswith("work/") and parse_item_path(concept_id) is None:
            yield Finding(
                code="structure.illegal-lane",
                severity="error",
                message="work-item page is outside a root, `children`, or `_archive` lane",
                spec=_SPEC,
                path=f"{concept_id}.md",
                line=None,
            )

    for item in work_items:
        if item.parent_path is not None and item.parent_path not in by_path:
            yield _finding(
                "structure.illegal-lane",
                "error",
                item,
                f"child lane owner {item.parent_path!r} is not a work item",
            )
        expected = SLUG_PREFIXES.get(item.type)
        remainder = item.basename
        if expected is not None and remainder != expected and not remainder.startswith(f"{expected}-"):
            yield _finding(
                "structure.prefix-type-mismatch",
                "warn",
                item,
                f"basename carries no `{expected}` prefix, which `type: {item.type}` implies",
            )
        if item.type in ROOT_ONLY_TYPES and item.parent_path is not None:
            yield _finding(
                "structure.release-nested",
                "error",
                item,
                f"`type: {item.type}` is root-only but this item has parent {item.parent_path!r}",
            )

        owned = ctx.bundle.root / item.path
        children = ctx.bundle.root / child_lane(item.path)
        if item.type not in PARENT_TYPES and (children.exists() or children.is_symlink()):
            yield _finding(
                "structure.children-on-leaf",
                "error",
                item,
                f"`type: {item.type}` may not own the child lane `{child_lane(item.path)}`",
            )
        if not item.archived and item.type in PARENT_TYPES and not owned.is_dir():
            yield _finding(
                "structure.owned-directory-missing",
                "error",
                item,
                f"parent-capable item has no owned directory at `{item.path}`",
            )


def sources(ctx: RuleContext) -> Iterable[Finding]:
    """Keep item-local source identifiers unique and resources confined."""
    root = ctx.bundle.root.resolve()
    for item in items(ctx):
        ids = Counter(source.id for source in item.sources if source.id is not None)
        for source_id in sorted(source_id for source_id, count in ids.items() if count > 1):
            yield _finding(
                "structure.source-id-duplicate",
                "error",
                item,
                f"`sources[].id` {source_id!r} is duplicated within this item",
            )

        owner = (root / item.path).resolve()
        for index, source in enumerate(item.sources):
            if not source.resource:
                continue
            relative = source.resource.removeprefix("/")
            pure = PurePosixPath(relative)
            candidate = root.joinpath(*pure.parts)
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError):
                resolved = candidate
            escaped = (
                not relative
                or pure.is_absolute()
                or ".." in pure.parts
                or not resolved.is_relative_to(root)
                or not resolved.is_relative_to(owner)
            )
            if escaped:
                yield _finding(
                    "structure.source-escape",
                    "error",
                    item,
                    f"`sources[{index}].resource` {source.resource!r} escapes its owner or bundle",
                )
                continue
            if not resolved.exists() or not ctx.bundle.has_member(relative):
                yield _finding(
                    "structure.source-missing",
                    "warn",
                    item,
                    f"`sources[{index}].resource` {source.resource!r} is not a bundle member",
                )


def _region_entries(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Targets parsed from the generated region, and the lines that would not parse.

    An unparseable line used to be dropped in silence, which is what let a
    writer/reader disagreement present as `index-entry-missing` for months.
    Blank lines are not lines: the region body opens with a newline, so
    `splitlines` always yields a leading empty string.
    """
    start = text.find(GENERATED_START)
    end = text.find(GENERATED_END, start + len(GENERATED_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        return (), ()
    targets: list[str] = []
    unreadable: list[str] = []
    for line in text[start + len(GENERATED_START) : end].splitlines():
        if not line.strip():
            continue
        target = parse_entry(line)
        if target is None:
            unreadable.append(line.strip())
        else:
            targets.append(target)
    return tuple(targets), tuple(unreadable)


def _target_page(lane: str, target: str) -> str:
    path = target.split("#", 1)[0].split("?", 1)[0]
    if path.startswith("/"):
        return path.removeprefix("/")
    return posixpath.normpath(f"{lane}/{path}")


def indexes(ctx: RuleContext) -> Iterable[Finding]:
    """Require each generated region to list exactly the lane's direct items."""
    work_items = items(ctx)
    all_pages = {item.page_path for item in work_items}
    for plan in plan_indexes(ctx.bundle.root, work_items):
        expected = {
            item.page_path
            for item in work_items
            if (location := parse_item_path(item.path)) is not None and location.lane == plan.lane
        }
        document = ctx.bundle.indexes.get(plan.lane)
        targets, unreadable = ((), ()) if document is None else _region_entries(document.raw_text)
        pages = tuple(_target_page(plan.lane, target) for target in targets)
        counts = Counter(pages)
        for page in sorted(expected - set(pages)):
            yield Finding(
                code="structure.index-entry-missing",
                severity="warn",
                message=f"generated index region does not list direct item `{page}`",
                spec=_SPEC,
                path=f"{plan.lane}/index.md",
                line=None,
            )
        for line in unreadable:
            yield Finding(
                code="structure.index-entry-unreadable",
                severity="warn",
                message=f"generated index region line is not a readable entry: {line!r}",
                spec=_SPEC,
                path=f"{plan.lane}/index.md",
                line=None,
            )
        for page, count in sorted(counts.items()):
            if count > 1:
                yield Finding(
                    code="structure.index-entry-duplicate",
                    severity="warn",
                    message=f"generated index region lists `{page}` {count} times",
                    spec=_SPEC,
                    path=f"{plan.lane}/index.md",
                    line=None,
                )
            if page in expected:
                continue
            code = "structure.index-entry-non-direct" if page in all_pages else "structure.index-entry-stale"
            detail = "is not a direct descendant of this lane" if page in all_pages else "names no work item"
            yield Finding(
                code=code,
                severity="warn",
                message=f"generated index entry `{page}` {detail}",
                spec=_SPEC,
                path=f"{plan.lane}/index.md",
                line=None,
            )


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    del config
    return (layout, sources, indexes)
