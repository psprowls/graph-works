"""Helpers every lane topic module shares, and the one injected value they read.

Not a rule module: the leading underscore keeps it out of the registry, which is
`okf_io._rules._common`'s reason for the same prefix.

`LaneConfig` lives here rather than in `_rules/__init__.py` because every topic
module takes one and `_rules/__init__.py` imports every topic module -- putting
it there would be a cycle for no gain.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from okf_io import Document, RuleContext

from work_tracker_okf.items import WorkItem, load_items


@dataclass(frozen=True, slots=True)
class LaneConfig:
    """What a lane rule may be handed that `RuleContext` cannot carry.

    `RuleContext` deliberately touches no filesystem, and two of the 25 codes
    (`targets.affects-missing`, `plan.action-target-missing`) are questions about
    a repository. `repo_root=None` **skips** both rather than reporting them:
    not knowing where the repo is says nothing about whether the paths are good.
    """

    repo_root: Path | None = None


def items(ctx: RuleContext) -> tuple[WorkItem, ...]:
    """Every item in the bundle, archived included.

    Called once per rule function rather than memoized (C5-K): measured at 93 us
    for 7 items, so about 30 ms across all twelve functions on a 100-item vault.
    Twelve independent passes, no shared state, and no cache whose invalidation
    nobody can see.
    """
    return load_items(ctx.bundle)


def active(ctx: RuleContext) -> Iterator[WorkItem]:
    """The items every per-item rule runs over: the unarchived ones, in slug order.

    An archived page is frozen. Reporting that it is archive-eligible, or that
    its slug disagrees with its `type`, asks nobody to do anything. Cross-item
    *resolution* still runs over the whole set -- see `graph.references`.
    """
    for item in items(ctx):
        if not item.archived:
            yield item


def with_documents(ctx: RuleContext) -> Iterator[tuple[WorkItem, Document]]:
    """Active items paired with their page, skipping pages okf-io could not parse.

    One habit per module, borrowed from `okf_ext.health`: okf-io already emitted
    `frontmatter.unparseable` for those, and a rule that re-reports them adds a
    second voice saying the same thing.
    """
    for item in active(ctx):
        document = ctx.bundle.concept(item.path.removesuffix(".md"))
        if document is None or document.parse_error is not None:
            continue
        yield item, document


def text_key(document: Document, key: str) -> str:
    """A top-level frontmatter string the projection does not carry.

    `mitigation` and `rationale` are read here rather than added to `WorkItem`:
    two rules want them, nothing else does, and widening child 1's projection for
    two `if` statements is the wrong trade. Read through `fm_data(dates="iso")`
    for the reason `items._project` gives -- ruamel hands back a mix of types
    depending only on how a human quoted the value.
    """
    value = document.fm_data(dates="iso").get(key)
    return value.strip() if isinstance(value, str) else ""


def days_since(value: str, today: date) -> int:
    """Days from the `YYYY-MM-DD` prefix of *value* to *today*; `0` when it does
    not parse.

    `work_io.lifecycle_lint._days_since` called `date.today()` inside itself. The
    clock is an argument here, as it is everywhere else in this package.
    """
    try:
        return (today - date.fromisoformat(value[:10])).days
    except (TypeError, ValueError):
        return 0
