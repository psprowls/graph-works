"""Key-level frontmatter ownership: two declared classes, one by omission.

Pure. Nothing here reads a file, mutates a document, or knows what a bundle
is -- it answers "what would change" and hands back values.

This module imports the shared layer and its own capability's model, and
nothing else from its own package.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from okf_ext.generators.model import KeyEdit
from okf_ext.shape import FrontmatterOwnership

#: Distinguishes "absent" from "present and `None`". `fm_raw.get(key)` cannot:
#: it answers `None` for both, which would silently drop a set of a key whose
#: new value is `None`.
_MISSING: Final = object()


def key_edits(
    fm_raw: Mapping[str, Any],
    ownership: FrontmatterOwnership,
    supplied: Mapping[str, Any],
) -> tuple[KeyEdit, ...]:
    """What *supplied* would change about *fm_raw*, given *ownership*.

    `owned` keys are **exhaustively rewritten**: an owned key present on disk
    that the run omits is deleted. `provenance` keys are written when supplied
    and left alone when not. Everything undeclared is never read, written or
    deleted -- it is not in either list, so no branch here can reach it.

    **An edit whose value already equals what is on disk is not an edit.**
    Idempotence is checked per key, not per document: a run that recomputes
    four keys and changes one plans one edit, and the other three leave no
    trace in the plan at all.

    Returns edits in declaration order -- every `owned` key, then every
    `provenance` key -- so a plan reads in the order the declaration is
    written, not in the order a caller happened to build its mapping.

    A caller supplying a `str` where the document holds a `date` will see an
    edit on every run: `fm_raw` holds real `date` objects, and
    `"2026-08-07" != date(2026, 8, 7)`. Nothing here coerces on the caller's
    behalf, for the reason `okf_io.models._str_tuple` gives about coercion
    laundering a broken value into a plausible one. The README says so too.
    """
    edits: list[KeyEdit] = []

    for key in ownership.owned:
        if key in supplied:
            if fm_raw.get(key, _MISSING) != supplied[key]:
                edits.append(KeyEdit(key=key, action="set", value=supplied[key]))
        elif key in fm_raw:
            # The run's values are the whole truth. An owned key the run omits is
            # deleted, which lets a dependency that no longer applies disappear on its own.
            edits.append(KeyEdit(key=key, action="delete", value=None))

    # Provenance keys: written only when supplied, never deleted. A run that does not
    # recompute a hash must not wipe it; folding provenance into `owned` makes that failure silent.
    for key in ownership.provenance:
        if key in supplied and fm_raw.get(key, _MISSING) != supplied[key]:
            edits.append(KeyEdit(key=key, action="set", value=supplied[key]))

    return tuple(edits)


__all__ = ["key_edits"]
