"""Plan the four transitions, and read the ledger they act on.

**Identity is the target path.** A member's id in OKF is its path, so two
proposals naming the same target are the same proposal and merge. Reading goes
by `type: Proposal` plus `target`, never by file path -- `proposals/<slug>.md`
is a default placement and nothing depends on it.

**Nothing is stored that can be derived.** Create-vs-update comes from whether
`target` is a member right now, so it cannot contradict the bundle; there is no
`mode:` key to go stale.

**Malformed proposals are listed, not dropped.** A proposal nobody can see is a
proposal nobody fixes, so a missing `target` or an unknown `page_status` comes
back with `Proposal.malformed` set rather than being filtered out -- and every
planner refuses one with `malformed-proposal` rather than guessing.

This module imports `okf_io` and its own capability's model. It imports no
sibling capability. (Later tasks in this plan append planners to this file that
also import this capability's `render` module and the shared `okf_ext.writing`
layer.)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from okf_io import Bundle, Document

from okf_ext.proposals.model import (
    OWNED_PROVENANCE_KEYS,
    PAGE_STATUSES,
    PROPOSAL_TYPE,
    Decision,
    DecisionPlan,
    Mode,
    PagePlan,
    PageRender,
    PageStatus,
    Proposal,
    ProposalPlan,
    Refusal,
    RefusalKind,
    Write,
)
from okf_ext.proposals.render import BodyRenderer, render_body
from okf_ext.splice import dominant_newline
from okf_ext.writing import body_digest


def _normalize_target(raw: str) -> str:
    """*raw* as a bundle-relative posix path, or `""` when it is unusable.

    A leading `/` is bundle-root-relative, matching okf-io's own link
    resolution; anything climbing above the root is refused rather than
    clamped. A proposal may freely *cite* an out-of-bundle resource in
    `sources[]`, but it may only ever *create* a bundle member.
    """
    cleaned = raw.strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(cleaned).parts:
        if part == ".":
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _entries(value: Any) -> tuple[Mapping[str, Any], ...]:  # noqa: ANN401 -- reads an arbitrary raw YAML value
    """*value* as a tuple of mappings, or empty for anything else.

    `fm_data()` returns whatever was on disk, and a `sources:` that is a string
    or an int is a content problem, not an exception -- the core's tolerance
    rule applies here too.
    """
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _read(concept_id: str, document: Any) -> Proposal:  # noqa: ANN401 -- an okf_io Document
    data = document.fm_data()
    target = _normalize_target(str(data.get("target") or ""))
    raw_status = str(data.get("page_status") or "").strip()
    status = raw_status if raw_status in PAGE_STATUSES else None

    malformed: str | None = None
    if not target:
        malformed = "missing or unusable `target`: a proposal's identity is the page it argues for"
    elif status is None:
        malformed = f"`page_status` {raw_status!r} is outside {list(PAGE_STATUSES)}"

    return Proposal(
        member=f"{concept_id}.md",
        concept_id=concept_id,
        target=target,
        title=str(data.get("title") or "").strip(),
        description=str(data.get("description") or "").strip(),
        page_status=status,
        raw_page_status=raw_status,
        sources=_entries(data.get("sources")),
        verified=_entries(data.get("verified")),
        malformed=malformed,
    )


def list_proposals(bundle: Bundle, *, page_status: PageStatus | None = None) -> tuple[Proposal, ...]:
    """Every proposal in *bundle*, sorted by member, malformed ones flagged.

    Enumeration is `Bundle.by_type("Proposal")` -- the capability owns the
    `type` outright, so there is no ambiguity about what counts.

    A `page_status=` filter matches on the **coerced** value, so a malformed
    proposal (whose `page_status` is `None`) never satisfies one. That is
    deliberate: a filter is how a caller picks documents to act on, and a
    proposal nobody can read is not one to act on.
    """
    found = [_read(concept_id, bundle.concepts[concept_id]) for concept_id in bundle.by_type(PROPOSAL_TYPE)]
    if page_status is not None:
        found = [proposal for proposal in found if proposal.page_status == page_status]
    return tuple(sorted(found, key=lambda proposal: proposal.member))


def mode(bundle: Bundle, proposal: Proposal) -> Mode:
    """Whether promoting *proposal* would create its target or update it.

    Derived from the bundle, never stored. `has_member` counts assets and
    ignored members too -- `ignore=` declares "this is not a concept", not
    "this is not there", so writing over one would still be clobbering a file
    that exists.
    """
    return "update" if bundle.has_member(proposal.target) else "create"


def _require_aware(at: datetime) -> str:
    """*at* as an ISO-8601 stamp, refusing a naive datetime.

    These packages never read the clock, so the caller supplies the instant --
    and a naive one written into `generated.at` is a value okf-io cannot coerce
    to an aware datetime, which would trade this capability's zero-`validate()`
    -impact promise for a `provenance.*` finding on every document it writes.
    A caller error, so it raises, following `plan_regenerate`'s precedent.
    """
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(
            f"`at` must be timezone-aware, got {at!r}. These packages never read the clock: the caller supplies "
            f"the instant, and a naive one lands in `generated.at` as a value okf-io cannot coerce."
        )
    return at.isoformat()


def _slug(target: str) -> str:
    """A filename stem for *target*. Placement only -- never identity.

    Deterministic and lossy on purpose: two targets can slug the same, and the
    caller of `proposal_path` disambiguates rather than refusing, because where a
    proposal file sits is cosmetic and nothing reads it.
    """
    stem = target[:-3] if target.endswith(".md") else target
    kept = [character.lower() if character.isalnum() else "-" for character in stem]
    collapsed = "-".join(part for part in "".join(kept).split("-") if part)
    return collapsed or "proposal"


def proposal_path(bundle: Bundle, target: str, *, directory: str = "proposals") -> str:
    """Where a new proposal for *target* goes, avoiding an occupied path.

    `<directory>/<slug>.md`, then `-2`, `-3`, ... until free. A collision is
    worked around rather than refused: identity is the target, and refusing a
    file over a cosmetic name clash would make placement load-bearing.

    *directory* is bundle-relative, leading and trailing slashes optional;
    `""` places at the bundle root. It defaults to `proposals`, which is what
    `plan_propose` passes and the only value that existed while this was
    private. Public because there is now a second caller --
    `doc_wiki_okf.proposals.migrate`, which re-places an old-dialect proposal
    inside its **own** parent directory so an archived one stays archived --
    and two definitions of where a proposal file goes is exactly the drift
    this workspace argues against. The rule is substrate-neutral: a slug plus
    a collision loop, no lane vocabulary, so it stays at tier 2.
    """
    prefix = directory.strip("/")
    base = f"{prefix}/{_slug(target)}" if prefix else _slug(target)
    candidate = f"{base}.md"
    counter = 2
    while bundle.has_member(candidate):
        candidate = f"{base}-{counter}.md"
        counter += 1
    return candidate


#: Deprecated alias, kept for one minor version (ADR-0030). Removed at 0.5.0
#: alongside the `okf_ext.sections` shim. No runtime warning -- see README.
placement = proposal_path


def _render_document(*, body: str, frontmatter: Mapping[str, Any]) -> str:
    """A whole new file, rendered through okf-io's own emitter.

    Built by parsing an empty frontmatter block and `set`ting each key rather
    than by formatting YAML by hand: `Document.set` gives every key the
    position `PREFERRED_KEY_ORDER` says it should have, and the core's emitter
    is the one thing guaranteed to produce something the core can read back.
    """
    document = Document.parse("---\n---\n")
    for key, value in frontmatter.items():
        document.set(key, value)
    document.set_body(body)
    return document.serialize()


def _merge_sources(
    existing: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    """Union *incoming* onto *existing*, deduped by `resource`.

    Returns the merged list and whether anything actually changed, so the
    caller can plan nothing rather than plan a no-op write.

    **Dedup is by `resource`, not by `id`.** A resource is what a source *is*;
    an id is what this document calls it. So a second source arriving under a
    taken id keeps its resource and gives up its id -- suffixed `-2`, `-3`,
    ... -- rather than being folded into a different resource entirely, which
    is the failure a naive id-keyed dedup produces.
    """
    merged = [dict(source) for source in existing]
    by_resource = {str(source.get("resource") or "").strip(): source for source in merged}
    taken = {str(source.get("id") or "").strip() for source in merged}
    changed = False

    for source in incoming:
        resource = str(source.get("resource") or "").strip()
        if resource and resource in by_resource:
            continue
        entry = dict(source)
        identifier = str(entry.get("id") or "").strip()
        if identifier and identifier in taken:
            counter = 2
            while f"{identifier}-{counter}" in taken:
                counter += 1
            identifier = f"{identifier}-{counter}"
            entry["id"] = identifier
        if identifier:
            taken.add(identifier)
        merged.append(entry)
        if resource:
            by_resource[resource] = entry
        changed = True

    return merged, changed


def _malformed(proposal: Proposal) -> Refusal:
    return Refusal(path=proposal.member, kind="malformed-proposal", detail=proposal.malformed or "unreadable proposal")


def plan_propose(
    bundle: Bundle,
    target: str,
    sources: Sequence[Mapping[str, Any]],
    *,
    title: str,
    description: str,
    by: str,
    at: datetime,
    render: BodyRenderer = render_body,
) -> ProposalPlan:
    """Plan filing a proposal for *target*, or merging into the live one.

    An **upsert on `target`**, because that is identity: no live proposal plans
    a new document; a `proposed` one plans a merge -- the union of `sources[]`
    deduped by `resource`, with the body re-rendered -- and a decided one is
    refused. A decided proposal is never silently reopened.

    Idempotence surfaces as an empty plan: incoming sources already present,
    with the title and description unchanged, plan nothing at all.

    *render* defaults to this capability's own `render_body`, so every
    pre-existing caller is unaffected. It is used on both the create body and
    the merge body -- always from the merged/deduped `sources[]`, never the
    raw incoming ones, so an injected renderer sees the same ledger a reader
    of the resulting document would.
    """
    stamp = _require_aware(at)
    normalized = _normalize_target(target)
    root = bundle.root
    proposals = list_proposals(bundle)

    if not normalized:
        return ProposalPlan(
            root=root,
            target=target,
            proposal="",
            writes=(),
            refusals=(
                Refusal(
                    path=target,
                    kind="target-escapes-bundle",
                    detail=(
                        "a proposal may cite an out-of-bundle resource in `sources[]`, "
                        "but it may only create a bundle member"
                    ),
                ),
            ),
        )

    live = next(
        (proposal for proposal in proposals if proposal.target == normalized and proposal.malformed is None),
        None,
    )
    if live is None:
        malformed = next(
            (proposal for proposal in proposals if proposal.malformed is not None and proposal.target == normalized),
            None,
        )
        if malformed is not None:
            return ProposalPlan(
                root=root, target=normalized, proposal=malformed.member, writes=(), refusals=(_malformed(malformed),)
            )

    if live is not None and live.page_status != "proposed":
        return ProposalPlan(
            root=root,
            target=normalized,
            proposal=live.member,
            writes=(),
            refusals=(
                Refusal(
                    path=live.member,
                    kind="already-decided",
                    detail=(
                        f"`{live.member}` is already `{live.raw_page_status}`; "
                        f"a decided proposal is never silently reopened"
                    ),
                ),
            ),
        )

    if live is None:
        member = proposal_path(bundle, normalized)
        deduped, _changed = _merge_sources((), sources)
        body = render(description=description, sources=deduped)
        text = _render_document(
            body=body,
            frontmatter={
                "type": PROPOSAL_TYPE,
                "title": title,
                "description": description,
                "generated": {"by": by, "at": stamp},
                "sources": deduped,
                "target": normalized,
                "page_status": "proposed",
            },
        )
        return ProposalPlan(
            root=root,
            target=normalized,
            proposal=member,
            writes=(Write(member=member, mode="create", text=text),),
            refusals=(),
        )

    merged, changed = _merge_sources(live.sources, sources)
    metadata_changed = (title.strip() and title.strip() != live.title) or (
        description.strip() and description.strip() != live.description
    )
    if not changed and not metadata_changed:
        return ProposalPlan(root=root, target=normalized, proposal=live.member, writes=(), refusals=())

    document = bundle.concepts[live.concept_id]
    frontmatter: dict[str, Any] = {"sources": merged}
    if title.strip():
        frontmatter["title"] = title.strip()
    if description.strip():
        frontmatter["description"] = description.strip()
    body = render(
        description=frontmatter.get("description", live.description),
        sources=merged,
        newline=dominant_newline(document.body),
    )
    return ProposalPlan(
        root=root,
        target=normalized,
        proposal=live.member,
        writes=(
            Write(
                member=live.member,
                mode="update",
                frontmatter=frontmatter,
                body=body,
                digest=body_digest(document.body),
            ),
        ),
        refusals=(),
    )


def plan_decide(
    bundle: Bundle,
    proposal: Proposal,
    decision: Decision,
    *,
    by: str,
    at: datetime,
) -> DecisionPlan:
    """Plan approving or rejecting *proposal*.

    Two edits, one document: the `page_status` flip, and a `verified` append
    naming the deciding actor. There is deliberately no `decided:` key -- a
    decision *is* OKF §5.2's `verified` event, and inventing a second key to
    say the same thing is the `sources: <int>` drift pattern.

    **No body is planned, now or ever again.** After this transition the body
    stops being machine-owned, and the flip is structural rather than a flag:
    nothing in this capability re-renders a decided proposal.
    """
    stamp = _require_aware(at)
    root = bundle.root

    if proposal.malformed is not None:
        return DecisionPlan(
            root=root, proposal=proposal.member, decision=decision, writes=(), refusals=(_malformed(proposal),)
        )
    if proposal.page_status != "proposed":
        return DecisionPlan(
            root=root,
            proposal=proposal.member,
            decision=decision,
            writes=(),
            refusals=(
                Refusal(
                    path=proposal.member,
                    kind="not-proposed",
                    detail=f"`page_status` is `{proposal.raw_page_status}`; only a `proposed` proposal can be decided",
                ),
            ),
        )

    return DecisionPlan(
        root=root,
        proposal=proposal.member,
        decision=decision,
        writes=(
            Write(
                member=proposal.member,
                mode="update",
                frontmatter={
                    "page_status": decision,
                    "verified": [*(dict(entry) for entry in proposal.verified), {"by": by, "at": stamp}],
                },
            ),
        ),
        refusals=(),
    )


def _page_text(
    render: PageRender,
    *,
    sources: Sequence[Mapping[str, Any]],
    by: str,
    stamp: str,
    verified: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The one writer. Both doors land here and nowhere else.

    The caller's `type` and frontmatter go in first, then what this capability
    owns: `sources[]`, `generated`, and -- for a promotion only -- the
    `verified` the proposal carried. A promoted page is born human-reviewed and
    a directly requested one is not, and that single difference is the whole
    distinction between the two doors.
    """
    frontmatter: dict[str, Any] = {"type": render.type}
    frontmatter.update({key: value for key, value in render.frontmatter.items()})
    frontmatter["generated"] = {"by": by, "at": stamp}
    if verified:
        frontmatter["verified"] = [dict(entry) for entry in verified]
    if sources:
        frontmatter["sources"] = [dict(source) for source in sources]
    return _render_document(body=render.body, frontmatter=frontmatter)


def _check_render(render: PageRender) -> None:
    supplied = sorted(key for key in OWNED_PROVENANCE_KEYS if key in render.frontmatter)
    if supplied:
        raise ValueError(
            f"`render.frontmatter` supplies {supplied}, which this capability owns "
            f"({list(OWNED_PROVENANCE_KEYS)}). The capability owns the merge, never the render: a caller "
            f"writing provenance would make the one-writer invariant a hand-maintained convention. "
            f"Pass sources through `sources=` instead, and let `by`/`at` stamp the rest."
        )


def plan_create(
    bundle: Bundle,
    target: str,
    render: PageRender,
    *,
    by: str,
    at: datetime,
    sources: Sequence[Mapping[str, Any]] = (),
) -> PagePlan:
    """Plan writing *target* directly, with no proposal behind it.

    The **direct-request door**. Exposed rather than kept private so that both
    entrypoints provably share one writer: a promotion-only surface would leave
    "these two produce the same page" a convention nobody can test.

    `verified` is absent by construction -- the page is human-*requested*, not
    human-*reviewed*.

    Raises `ValueError` when *render* supplies provenance this capability owns.
    """
    stamp = _require_aware(at)
    _check_render(render)
    normalized = _normalize_target(target)
    root = bundle.root

    if not normalized:
        return PagePlan(
            root=root,
            target=target,
            mode="create",
            proposal=None,
            writes=(),
            refusals=(
                Refusal(
                    path=target, kind="target-escapes-bundle", detail="a page may only be created inside the bundle"
                ),
            ),
        )
    if bundle.has_member(normalized):
        return PagePlan(
            root=root,
            target=normalized,
            mode="create",
            proposal=None,
            writes=(),
            refusals=(
                Refusal(
                    path=normalized,
                    kind="target-exists",
                    detail="already a member; this door creates, it never overwrites",
                ),
            ),
        )

    return PagePlan(
        root=root,
        target=normalized,
        mode="create",
        proposal=None,
        writes=(
            Write(
                member=normalized,
                mode="create",
                text=_page_text(render, sources=sources, by=by, stamp=stamp),
            ),
        ),
        refusals=(),
    )


def plan_promote(
    bundle: Bundle,
    proposal: Proposal,
    render: PageRender | None = None,
    *,
    by: str,
    at: datetime,
) -> PagePlan:
    """Plan promoting an approved *proposal* into its page.

    **Mode derives from the world**, never from a stored key:

    - *target absent* -- requires *render*, feeds the same writer `plan_create`
      does, and copies the proposal's `verified` onto the page, so a promoted
      page is born human-reviewed.
    - *target present* -- plans **frontmatter-only** edits: merge `sources[]`,
      append `verified`. *render* is ignored. The body change an update
      proposal argues for is the caller's job via `generators` at tier 3.

    Either way the plan also flips the proposal to `created`. Page write and
    ledger flip are one plan: they cannot be applied separately and drift, and
    the page is ordered first so a partial failure leaves the ledger honest.
    """
    stamp = _require_aware(at)
    root = bundle.root
    target_mode = mode(bundle, proposal)
    flip = Write(member=proposal.member, mode="update", frontmatter={"page_status": "created"})

    def refuse(kind: RefusalKind, path: str, detail: str, mode_: Mode = target_mode) -> PagePlan:
        return PagePlan(
            root=root,
            target=proposal.target,
            mode=mode_,
            proposal=proposal.member,
            writes=(),
            refusals=(Refusal(path=path, kind=kind, detail=detail),),
        )

    if proposal.malformed is not None:
        return refuse("malformed-proposal", proposal.member, proposal.malformed)
    if proposal.page_status != "approved":
        return refuse(
            "not-approved",
            proposal.member,
            f"`page_status` is `{proposal.raw_page_status}`; only an `approved` proposal is promoted",
        )

    if target_mode == "create":
        if render is None:
            return refuse(
                "missing-render",
                proposal.target,
                "the target does not exist, so promoting it means writing a page; supply a `render`",
            )
        _check_render(render)
        return PagePlan(
            root=root,
            target=proposal.target,
            mode="create",
            proposal=proposal.member,
            writes=(
                Write(
                    member=proposal.target,
                    mode="create",
                    text=_page_text(render, sources=proposal.sources, by=by, stamp=stamp, verified=proposal.verified),
                ),
                flip,
            ),
            refusals=(),
        )

    concept_id = proposal.target[:-3] if proposal.target.endswith(".md") else proposal.target
    document = bundle.concepts.get(concept_id)
    if document is None or document.parse_error is not None:
        return refuse(
            "unreadable-target",
            proposal.target,
            (
                "the target is a member this capability cannot read, so merging `sources[]` onto it would "
                "compute against frontmatter nobody can see; fix the document and re-plan"
            ),
            "update",
        )

    data = document.fm_data()
    merged, _changed = _merge_sources(_entries(data.get("sources")), proposal.sources)
    verified = [*(dict(entry) for entry in _entries(data.get("verified"))), *(dict(e) for e in proposal.verified)]
    return PagePlan(
        root=root,
        target=proposal.target,
        mode="update",
        proposal=proposal.member,
        writes=(
            Write(member=proposal.target, mode="update", frontmatter={"sources": merged, "verified": verified}),
            flip,
        ),
        refusals=(),
    )


__all__ = [
    "list_proposals",
    "mode",
    "placement",
    "plan_create",
    "plan_decide",
    "plan_promote",
    "plan_propose",
    "proposal_path",
]
