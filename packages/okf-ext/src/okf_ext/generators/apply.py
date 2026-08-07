"""Write a `RegenerationPlan`: the one write that touches both halves.

This is the first capability to edit frontmatter and body in the same write,
and okf-io names the trap it walks into. `rendered_with_body()` is a shallow
copy -- `clone.fm_raw is document.fm_raw` -- and its own docstring says the
contract "holds today only because `set_body` never touches `fm_raw`". So this
capability does not use it. It follows `tags.apply` instead: a
`copy.deepcopy` of `fm_raw` onto a `dataclasses.replace` scratch document,
private until that document's write actually lands.

The two I/O regimes -- probe/staging all-or-nothing, and per-document commit
-- belong to `okf_ext.writing.write_all` and are inherited unchanged.

This module imports the shared layer, its own capability's model, and
`okf_io`. It imports no sibling capability.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from functools import partial
from pathlib import Path

from okf_io import Bundle, Document
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from okf_ext.generators.model import Regeneration, RegenerationPlan
from okf_ext.writing import ApplyResult, PendingWrite, WriteFailure, body_digest, write_all


def _commit(document: Document, fm_raw: CommentedMap, body: str) -> None:
    """Adopt both halves once this document's write has landed.

    `set_body` writes through to the `Split` as well as the `body` field and
    marks the document dirty, which also discards the memoized `fm` view -- so
    assigning `fm_raw` first and calling it second leaves every live reader
    agreeing with disk, with no reload, for exactly the documents written and
    no others.
    """
    document.fm_raw = fm_raw
    document.set_body(body)


def _rendered(document: Document, regeneration: Regeneration) -> tuple[str, CommentedMap, str]:
    """Serialize *regeneration* against a private scratch copy of *document*.

    Frontmatter goes through `Document.set` / `Document.delete`, never a
    direct `fm_raw` assignment: `set` gives the correct insert position for a
    key the document does not yet carry (`PREFERRED_KEY_ORDER`), edits an
    existing key in place, and never reorders an existing document. Everything
    the machine does not name keeps its comments, its quoting and its position
    -- which is the entire okf-io guarantee this capability must not undo.

    This is where `tags` diverges, and the divergence is instructive:
    `tags.apply` avoids `doc.set("tags", ...)` because `set` replaces a value
    wholesale, dropping the `CommentedSeq` and with it flow style and inline
    comments. That is right for `tags`, whose edit is one element of a
    sequence the human authored, and wrong here: an owned key's value *is*
    freshly computed data, and wholesale replacement is precisely what
    ownership means.
    """
    scratch_fm: CommentedMap = copy.deepcopy(document.fm_raw)
    scratch = replace(document, fm_raw=scratch_fm)
    for edit in regeneration.key_edits:
        if edit.action == "set":
            scratch.set(edit.key, edit.value)
        else:
            scratch.delete(edit.key)
    scratch.set_body(regeneration.after)
    return scratch.serialize(), scratch_fm, regeneration.after


def apply(bundle: Bundle, plan: RegenerationPlan) -> ApplyResult:
    """Write *plan* against *bundle*.

    **Content failures are refused per document; siblings still write.** The
    five are `not-a-member`, `parse-error`, `duplicate-edit`, `stale` and
    `serialize-error` -- every one already a member of `FailureKind`. This
    capability widens neither that union nor `SkipReason`.

    **Staleness is a body digest, and frontmatter carries none.** If a human
    edits an owned key between plan and apply, the generator's value still
    wins: that is what declaring the key owned means, and refusing would be
    the capability second-guessing its own contract. `tags` refuses in the
    analogous situation because it records *positions* in a sequence and a
    position that moved names a different tag; a key name does not move. The
    consequence -- a human edit to an owned key made between plan and apply is
    lost without a report -- is real, and the remedy is the declaration, not a
    check: a key humans edit does not belong in `owned:`.

    Raises `ValueError` for a plan built against a different bundle: its
    digests and line numbers mean nothing anywhere else.
    """
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's digests and line numbers mean nothing outside the bundle it was planned against."
        )

    grouped: dict[str, list[Regeneration]] = {}
    for regeneration in plan.regenerations:
        grouped.setdefault(regeneration.concept_id, []).append(regeneration)

    failed: list[WriteFailure] = []
    pending: list[PendingWrite] = []

    for concept_id, items in sorted(grouped.items()):
        member = f"{concept_id}.md"
        document = bundle.concepts.get(concept_id)
        if document is None or document.path is None:
            failed.append(
                WriteFailure(path=member, kind="not-a-member", error="concept is not a member of this bundle")
            )
            continue
        if document.parse_error is not None:
            # Defence in depth: `plan_regenerate` already skips these, but a
            # hand-built plan naming one directly would otherwise sail through.
            failed.append(
                WriteFailure(
                    path=member,
                    kind="parse-error",
                    error=(
                        f"cannot mutate a document that failed to parse "
                        f"({document.parse_error.kind}): {document.parse_error.message}"
                    ),
                )
            )
            continue
        if len(items) > 1:
            # Both were computed against the same body; applying them in
            # sequence would silently discard the first.
            failed.append(
                WriteFailure(
                    path=member,
                    kind="duplicate-edit",
                    error=(
                        "plan carries more than one regeneration for this concept; "
                        "refusing rather than silently applying only the last"
                    ),
                )
            )
            continue

        regeneration = items[0]
        if body_digest(document.body) != regeneration.digest:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="stale",
                    error="stale plan: the body changed since it was planned; re-plan against the current bundle",
                )
            )
            continue

        try:
            rendered, scratch_fm, body = _rendered(document, regeneration)
        except (YAMLError, ValueError, RecursionError) as exc:
            failed.append(WriteFailure(path=member, kind="serialize-error", error=str(exc)))
            continue

        pending.append(
            PendingWrite(
                member=member,
                path=document.path,
                rendered=rendered,
                on_written=partial(_commit, document, scratch_fm, body),
            )
        )

    return write_all(pending, failed=failed, skipped=plan.skipped)


__all__ = ["apply"]
