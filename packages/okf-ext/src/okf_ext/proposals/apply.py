"""Write any of this capability's plans. One function, both write modes.

**One apply, not four.** The four planners differ in what they decide; they do
not differ in how a document lands. A per-planner apply would be four copies of
the same staleness check and the same scratch-document dance, and four places
for one of them to drift.

A **create** write is handed straight to `okf_ext.writing.write_all` with
`create=True`, and its `text` may be `str` or `bytes` -- nothing on this path
parses it, which is what lets binary material land through the same engine:
parent made, occupied target refused, staged and committed
exactly as an update is. An **update** write follows `generators.apply` --
`copy.deepcopy` of `fm_raw` onto a `dataclasses.replace` scratch document,
private until that document's own write commits -- rather than
`rendered_with_body()`, whose own docstring says its `fm_raw` aliasing contract
"holds today only because `set_body` never touches `fm_raw`". This capability
touches both halves.

The two I/O regimes -- probe/staging all-or-nothing, and per-document commit --
belong to `write_all` and are inherited unchanged. So does the **order**: the
commit loop never sorts, so a promotion's page lands before its ledger flip.

**`apply` does not update the in-memory `Bundle`.** A create write lands on
disk with no matching `bundle.concepts` entry -- there is no live `Document`
for `on_written` to adopt, unlike an update -- so a caller planning again
against the same `bundle` object after a create must reload first, exactly as
`moves.apply` documents for its own writes.

This module imports the shared layer, its own capability's model, and `okf_io`.
It imports no sibling capability.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from functools import partial
from pathlib import Path

from okf_io import Bundle, Document
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from okf_ext.proposals.model import Plan, Write
from okf_ext.writing import ApplyResult, PendingWrite, WriteFailure, body_digest, write_all


def _commit(document: Document, fm_raw: CommentedMap, body: str | None) -> None:
    """Adopt both halves once this document's write has landed.

    Assigning `fm_raw` first and calling `set_body` second leaves every live
    reader agreeing with disk, with no reload -- `set_body` writes through to
    the `Split` and discards the memoized `fm` view. A frontmatter-only write
    passes `body=None` and marks the document dirty instead, so the view is
    still rebuilt from the map that just changed.
    """
    document.fm_raw = fm_raw
    if body is None:
        document.mark_dirty()
        return
    document.set_body(body)


def _rendered(document: Document, write: Write) -> tuple[str, CommentedMap, str | None]:
    """Serialize *write* against a private scratch copy of *document*.

    Frontmatter goes through `Document.set`, never a direct `fm_raw`
    assignment: `set` gives a key the document does not yet carry the position
    `PREFERRED_KEY_ORDER` says it should have, edits an existing one in place,
    and never reorders the document. Everything the machine does not name keeps
    its comments, its quoting and its position.
    """
    scratch_fm: CommentedMap = copy.deepcopy(document.fm_raw)
    scratch = replace(document, fm_raw=scratch_fm)
    for key, value in write.frontmatter.items():
        scratch.set(key, value)
    if write.body is not None:
        scratch.set_body(write.body)
    return scratch.serialize(), scratch_fm, write.body


def apply(bundle: Bundle, plan: Plan) -> ApplyResult:
    """Write *plan* against *bundle*.

    **Content failures are refused per document; siblings still write.** The
    five are `not-a-member`, `parse-error`, `duplicate-edit`, `stale` and
    `serialize-error` -- every one already a member of `FailureKind`. This
    capability widens neither that union nor `SkipReason`.

    **Staleness is a body digest, and a frontmatter-only write carries none.**
    A key name does not move, so there is nothing positional to go stale; the
    call is `generators`', made here for the same reason.

    Raises `ValueError` twice, both caller error rather than bundle content,
    following `moves.apply`'s precedent: for a plan built against a different
    bundle (its digests mean nothing anywhere else), and for a plan whose `ok`
    is `False` (the plan already said so).
    """
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's digests mean nothing outside the bundle it was planned against."
        )
    if plan.refusals:
        raise ValueError(
            f"Plan carries {len(plan.refusals)} refusal(s) and will not be applied; "
            f"fix the inputs and re-plan. First: {plan.refusals[0].kind} on {plan.refusals[0].path}"
        )

    seen: dict[str, int] = {}
    for write in plan.writes:
        seen[write.member] = seen.get(write.member, 0) + 1

    failed: list[WriteFailure] = []
    pending: list[PendingWrite] = []

    for write in plan.writes:
        if seen[write.member] > 1:
            if not any(failure.path == write.member for failure in failed):
                failed.append(
                    WriteFailure(
                        path=write.member,
                        kind="duplicate-edit",
                        error=(
                            "plan carries more than one write for this member; "
                            "refusing rather than silently applying only the last"
                        ),
                    )
                )
            continue

        if write.mode == "create":
            pending.append(
                PendingWrite(
                    member=write.member,
                    path=Path(bundle.root) / write.member,
                    rendered=write.text,
                    on_written=lambda: None,
                    create=True,
                )
            )
            continue

        concept_id = write.member[:-3] if write.member.endswith(".md") else write.member
        document = bundle.concepts.get(concept_id)
        if document is None or document.path is None:
            failed.append(WriteFailure(path=write.member, kind="not-a-member", error="not a member of this bundle"))
            continue
        if document.parse_error is not None:
            failed.append(
                WriteFailure(
                    path=write.member,
                    kind="parse-error",
                    error=(
                        f"cannot mutate a document that failed to parse "
                        f"({document.parse_error.kind}): {document.parse_error.message}"
                    ),
                )
            )
            continue
        if write.digest is not None and body_digest(document.body) != write.digest:
            failed.append(
                WriteFailure(
                    path=write.member,
                    kind="stale",
                    error="stale plan: the body changed since it was planned; re-plan against the current bundle",
                )
            )
            continue

        try:
            rendered, scratch_fm, body = _rendered(document, write)
        except (YAMLError, ValueError, RecursionError) as exc:
            failed.append(WriteFailure(path=write.member, kind="serialize-error", error=str(exc)))
            continue

        pending.append(
            PendingWrite(
                member=write.member,
                path=document.path,
                rendered=rendered,
                on_written=partial(_commit, document, scratch_fm, body),
            )
        )

    return write_all(pending, failed=failed)


__all__ = ["apply"]
