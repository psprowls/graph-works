"""What a bundle actually carries, and which tags may be the same tag.

Pure reads. Deterministic ordering throughout, matching the core's habit of
sorting output so it never depends on filesystem or dict order.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher, get_close_matches
from itertools import combinations
from types import MappingProxyType

from okf_io import Bundle, value_shape

from okf_ext.context import ExtContext
from okf_ext.tags.model import Skipped, TagCluster, TagInventory
from okf_ext.tags.normalize import canonical

#: Above this `difflib` ratio, two canonical forms are offered as a suggestion.
#: Loose enough for `metric`/`metrics`, tight enough to leave `headline-metric`
#: alone.
DEFAULT_CUTOFF = 0.8


def _concept_id(path: str) -> str:
    return path[: -len(".md")] if path.endswith(".md") else path


def scan(bundle: Bundle) -> tuple[tuple[str, ...], tuple[Skipped, ...]]:
    """Split *bundle*'s members into "tags are readable" and "they are not".

    Shared by `inventory` and `rename` so the two can never disagree about
    which concepts are in play — a disagreement would show up as a plan that
    silently skips a document the inventory counted.

    The `tags-not-a-sequence` test reads `fm.coercion_failures` rather than
    re-deriving the shape: the view already recorded it, and a second opinion
    is a second thing to keep in sync. okf-io's `frontmatter.value-malformed`
    rule reads the same seam on the validation side, for every reserved key
    rather than just `tags`; the two are intentional peers, not a duplication
    to reconcile -- `scan()` answers a per-document "can I read this?"
    question `inventory`/`rename` need directly, without running the whole
    rule catalog to get one key's answer.
    """
    skipped: list[Skipped] = []

    for path, detail in sorted(bundle.unreadable.items()):
        skipped.append(Skipped(concept_id=_concept_id(path), path=path, reason="unreadable", detail=detail))

    usable: list[str] = []
    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        path = f"{concept_id}.md"
        if document.parse_error is not None:
            skipped.append(
                Skipped(
                    concept_id=concept_id,
                    path=path,
                    reason="parse-error",
                    detail=f"{document.parse_error.kind}: {document.parse_error.message}",
                )
            )
            continue
        if "tags" in document.fm.coercion_failures:
            # `value_shape`, not `type(...).__name__`: `fm_raw` is a round-trip
            # tree, so the class here is `CommentedMap` / `CommentedSeq`, and
            # this detail is read by a human. okf-io owns the naming for the
            # same reason it owns `coercion_failures` -- a second opinion is a
            # second thing to keep in sync.
            actual = value_shape(document.fm_raw.get("tags"))
            article = "an" if actual[:1] in "aeiou" else "a"
            skipped.append(
                Skipped(
                    concept_id=concept_id,
                    path=path,
                    reason="tags-not-a-sequence",
                    detail=f"`tags` is {article} {actual}, not a sequence",
                )
            )
            continue
        usable.append(concept_id)

    skipped.sort(key=lambda item: (item.path, item.reason))
    return tuple(usable), tuple(skipped)


def inventory(bundle: Bundle, ctx: ExtContext | None = None) -> TagInventory:
    """Count what *bundle* carries.

    *ctx* is accepted for signature uniformity across the capability and is
    deliberately unused: **the inventory reports the mess, it does not
    normalize it away.** Canonicalizing here would hide the exact thing this
    function exists to reveal.

    A tag repeated inside one concept counts once — `counts` is per concept,
    not per occurrence, because "how many documents use this tag" is the
    question anyone actually asks.

    An empty string or whitespace-only tag is counted as the tag it is, not
    filtered out — hiding it would conceal exactly the authoring mistake this
    function exists to reveal.

    **A tag counted here may still yield an empty, unexplained plan from
    `rename.py`.** A non-string scalar tag (`42`) is coerced to `'42'` by
    `okf_io` with no trace it was ever a non-string, so it is counted like any
    other tag — but every `plan_*` function reads raw positions and refuses to
    match a non-string one, so `plan_rename(bundle, "42", ...)` plans nothing
    against it. See `rename`'s module docstring for the full explanation; this
    is a known, documented limitation, not a bug in either function.
    """
    counts: Counter[str] = Counter()
    concepts: dict[str, list[str]] = {}
    co_occurrence: Counter[tuple[str, str]] = Counter()
    untagged: list[str] = []

    usable, skipped = scan(bundle)
    for concept_id in usable:
        tags = tuple(dict.fromkeys(bundle.concepts[concept_id].fm.tags))
        if not tags:
            untagged.append(concept_id)
            continue
        for tag in tags:
            counts[tag] += 1
            concepts.setdefault(tag, []).append(concept_id)
        for left, right in combinations(sorted(tags), 2):
            co_occurrence[(left, right)] += 1

    return TagInventory(
        counts=MappingProxyType(dict(sorted(counts.items()))),
        concepts=MappingProxyType({tag: tuple(sorted(ids)) for tag, ids in sorted(concepts.items())}),
        untagged=tuple(untagged),
        co_occurrence=MappingProxyType(dict(sorted(co_occurrence.items()))),
        skipped=skipped,
    )


def clusters(
    inv: TagInventory,
    ctx: ExtContext | None = None,
    *,
    cutoff: float = DEFAULT_CUTOFF,
) -> tuple[TagCluster, ...]:
    """Tags that may be the same tag, at two distinct confidences.

    `normalization` clusters are mechanical — members share a canonical form,
    and `rename.plan_normalize` acts on these and only these. A lone tag whose
    canonical form differs from itself is still a cluster of one, because
    otherwise nothing could fix it.

    `similarity` clusters are computed over canonical forms, but only forms
    that are themselves real tags — members of `inv.counts` — are eligible.
    A canonical form frequently is not a tag anyone wrote (`data-quality` is
    the canonical form of `DATA_QUALITY`, but no concept carries the literal
    spelling `data-quality`); naming it in a `similarity` cluster would offer
    up a tag that appears nowhere in the bundle, which nothing downstream
    (`rename.plan_merge` takes tag names) could act on. The one invariant
    that holds without exception: every member of every cluster, of either
    kind, is a real tag carried by the bundle, never a synthetic canonical
    form.

    The two kinds are **not** guaranteed disjoint. They share a member in
    exactly one shape: a normalization cluster's canonical form that is
    itself one of that group's spellings — i.e. already a real tag — can
    *also* head a `similarity` cluster with an unrelated lookalike. That is
    honest, not a collision: normalization does not change that spelling, it
    is the *target* the other members collapse into, so the lookalike
    suggestion reads the same before and after the merge. Suppressing it
    would only discard a permanently valid suggestion for no benefit.
    """
    policy = (ctx or ExtContext()).normalization

    by_form: dict[str, list[str]] = {}
    weight: Counter[str] = Counter()
    for tag, count in inv.counts.items():
        form = canonical(tag, policy)
        by_form.setdefault(form, []).append(tag)
        weight[form] += count

    normalization = tuple(
        TagCluster(canonical=form, members=tuple(sorted(members)), kind="normalization")
        for form, members in sorted(by_form.items())
        if len(members) > 1 or members[0] != form
    )

    #: Only a canonical form that is itself a tag someone actually wrote can
    #: honestly head a `similarity` cluster — see the docstring above.
    forms = sorted(form for form in by_form if form in inv.counts)
    seen: set[tuple[str, str]] = set()
    similarity: list[TagCluster] = []
    for form in forms:
        others = [other for other in forms if other != form]
        if not others:
            # `get_close_matches` rejects n=0, and a form with no counterpart
            # has nothing to be similar to. Reachable whenever every tag in
            # the bundle shares one canonical form, or when the exclusion
            # above leaves a single survivor.
            continue
        for match in get_close_matches(form, others, n=len(others), cutoff=cutoff):
            pair = (form, match) if form < match else (match, form)
            if pair in seen:
                continue
            seen.add(pair)
            # The better-established form leads; ties break lexicographically
            # so the same bundle always names the same one.
            lead = min(pair, key=lambda name: (-weight[name], name))
            score = round(SequenceMatcher(None, pair[0], pair[1]).ratio(), 4)
            similarity.append(TagCluster(canonical=lead, members=pair, kind="similarity", score=score))

    similarity.sort(key=lambda cluster: (-(cluster.score or 0.0), cluster.canonical, cluster.members))
    return normalization + tuple(similarity)
