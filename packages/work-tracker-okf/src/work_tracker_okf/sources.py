"""Stamping an artifact into a work item's `sources[]`.

Idempotent on `ref.source_id`. Mutates the in-memory `Document` and returns
whether anything changed; **the caller saves**. That is the shape
`advance.apply` already has, and it is what lets a composing CLI make one save
out of a frontmatter transition and a stamp.

Taking an `ArtifactRef` rather than `(id, resource)` is the point of the carrier:
the id and the resource arrive from one composition and cannot be mismatched at
the call site (C2-G).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from okf_io import Document

from work_tracker_okf.paths import ArtifactRef


def upsert(
    document: Document,
    ref: ArtifactRef,
    *,
    title: str,
    last_modified: date | None = None,
) -> bool:
    """Merge *ref* into *document*'s `sources[]`. Returns whether it changed.

    **The merge is on raw data.** The existing list is read through `fm_data()`
    and set back whole with `Document.set`; it is never re-projected through
    `okf_io.Source`, which would silently drop any key the dataclass does not
    model — and `Source.extra` exists precisely because entries carry more than
    the seven typed fields. An existing entry is updated in place with its other
    keys intact; a new one appends. Order is otherwise untouched, so the result
    is deterministic.

    `last_modified` is written as an **ISO string**, not a `date`. `fm_data()`
    projects dates to ISO strings, so a `date` written on the first pass would
    come back as a `str` on the second and the idempotence comparison would never
    converge. It also matches what every authored page already shows.

    One cost, stated: `Document.set` replaces the whole `sources` key, so a
    one-field change re-renders the whole block. okf-io's splice keeps the byte
    diff to that block, so neighbours are untouched — but this is the one place in
    the lane that is minimal at block granularity rather than key granularity.

    Raises `ValueError` for a *ref* carrying no `source_id`. That is caller
    error — an item page or a `references/` directory is not an artifact — not
    bundle content.
    """
    if ref.source_id is None:
        raise ValueError(f"{ref.rel}: an ArtifactRef with no source_id cannot be stamped as a source")

    entry: dict[str, Any] = {"id": ref.source_id, "resource": ref.resource, "title": title}
    if last_modified is not None:
        entry["last_modified"] = last_modified.isoformat()

    existing = document.fm_data().get("sources")
    entries: list[Any] = list(existing) if isinstance(existing, list) else []

    for index, current in enumerate(entries):
        if not isinstance(current, dict) or current.get("id") != ref.source_id:
            continue
        merged = {**current, **entry}
        if merged == current:
            return False
        entries[index] = merged
        document.set("sources", entries)
        return True

    entries.append(entry)
    document.set("sources", entries)
    return True


__all__ = ["upsert"]
