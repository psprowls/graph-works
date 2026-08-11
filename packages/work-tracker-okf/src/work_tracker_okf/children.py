"""The derived `children:` key: plan the drift, then write it.

The child's `parent` is the single source of truth and `children` is a
tool-refreshed projection of it. `work-io` spent 129 lines on a frontmatter
splice to insert one key without reserializing its neighbours; `Document.set`
is that, and `PREFERRED_KEY_ORDER` is its `_ANCHOR_KEYS`.

What does not fall out is the **comparison**. `load_items` fills
`WorkItem.children` with the derived value and discards whatever the page
authored, so the projection alone cannot say whether a page is in drift -- the
authored list has to be re-read off the document (C3-C, spec 6.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from okf_io import Bundle, Document

from work_tracker_okf.items import WorkItem


@dataclass(frozen=True, slots=True)
class ChildrenSync:
    """One drifted parent. Only drifted parents get one, so an empty plan is
    the healthy state and the caller needs no diff of its own."""

    slug: str
    path: str
    before: tuple[str, ...]
    after: tuple[str, ...]


def _authored_children(document: Document) -> tuple[str, ...]:
    # The same coercion `items._text_tuple` applies, for the same reason: a
    # bare string where a list belongs must not iterate into characters. It is
    # three lines, and reaching across modules for a private helper would cost
    # more than it saves.
    value = document.fm_data(dates="iso").get("children")
    if isinstance(value, str) or not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


def _concept(bundle: Bundle, path: str) -> Document | None:
    document = bundle.concept(path.removesuffix(".md"))
    if document is None or document.parse_error is not None:
        return None
    return document


def plan_children_sync(bundle: Bundle, items: Sequence[WorkItem]) -> tuple[ChildrenSync, ...]:
    """Every active parent whose authored `children` disagrees with the derived one.

    Archived pages are frozen: children are derived across active *and*
    archived items -- the relationship is permanent -- but only pages under
    `work/` are ever rewritten.
    """
    syncs: list[ChildrenSync] = []
    for item in items:
        if item.archived:
            continue
        document = _concept(bundle, item.path)
        if document is None:
            continue
        authored = _authored_children(document)
        if authored == item.children:
            continue
        syncs.append(ChildrenSync(slug=item.slug, path=item.path, before=authored, after=item.children))
    return tuple(syncs)


def apply_children_sync(bundle: Bundle, syncs: Sequence[ChildrenSync], *, dry_run: bool = True) -> tuple[str, ...]:
    """Write *syncs*. Returns the slugs the plan covers, written or not.

    An empty `after` **deletes** the key: `children:` is omitted-when-empty on
    these pages, so a parent whose last child was detached loses it rather than
    gaining `children: []`. `Document.delete` is a no-op when it is absent.
    """
    touched: list[str] = []
    for sync in syncs:
        document = _concept(bundle, sync.path)
        if document is None:
            continue
        touched.append(sync.slug)
        if dry_run:
            continue
        if sync.after:
            document.set("children", list(sync.after))
        else:
            document.delete("children")
        document.save()
    return tuple(touched)


__all__ = ["ChildrenSync", "apply_children_sync", "plan_children_sync"]
