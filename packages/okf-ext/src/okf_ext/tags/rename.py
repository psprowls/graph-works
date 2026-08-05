"""Bulk tag rename: four planners plus `apply`.

**Plan and apply are two calls, not a `dry_run` flag.** okf-io's writers take
`dry_run=True` and return a description; this diverges deliberately. A rename
touching 40 files produces a preview you want to inspect and filter, and
"apply 38 of these 40" is a thing a boolean cannot express and a plan-as-value
can.

All four planners are pure reads: no writes, and no document left dirty.
`apply` is the one function in this module that writes.

**Known asymmetry with `inventory()`.** `okf_io`'s coercion turns a non-string
scalar tag (an int `42`, a float, a bool) into the string `'42'` in `fm.tags`
with no `coercion_failures` entry -- `_as_str` accepts `bool | int | float`
silently, unlike its refusal of a mapping or a list. `inventory()` therefore
counts `'42'` as a real tag. Every planner here, however, reads positions from
`fm_raw` through `_raw_tags`, which turns that same non-string element into a
`None` sentinel precisely so no rename can ever match it (see `_raw_tags`) --
so `plan_rename(bundle, "42", "…")` against that same bundle returns an empty
plan, silently. The empty plan is not a bug: matching a coerced string would
risk rewriting a value that was never a string on disk. But nothing in
`RenamePlan` explains *why* it is empty, because this is not a per-document
content problem `scan()` can name (the document parses fine, `tags` is a
proper sequence) -- it is a mismatch between two different readings of one
position. A caller that inventories a bundle, sees a suspicious numeric-
looking tag, and gets an empty plan back has no signal from either function
pointing at the other. Recorded as a known limitation (package README)
rather than patched here: closing it needs either `inventory()` to expose
raw-type information it currently has no reason to carry, or a fifth
`SkipReason` for "counted but not a string at that position" threaded through
every planner -- both are a bigger surface change than this deferral
warrants, and `okf_io`'s coercion is itself the root cause, which this
package does not modify.
"""

from __future__ import annotations

import contextlib
import copy
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from okf_io import Bundle, Document
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from okf_ext.context import ExtContext
from okf_ext.tags.inventory import scan
from okf_ext.tags.model import ApplyResult, RenamePlan, Skipped, TagEdit, Vocabulary, WriteFailure
from okf_ext.tags.normalize import canonical


def _raw_tags(bundle: Bundle, concept_id: str) -> list[str | None] | None:
    """The raw tag sequence, position for position, or None when there is none.

    Read from `fm_raw` rather than `fm.tags` because the planner records
    *positions*, and positions only mean anything in the sequence `apply` will
    actually edit -- `fm.tags` silently drops null and non-coercible entries,
    so the two sequences can have different lengths for the same document.

    A non-`str` element becomes `None` here rather than being converted with
    a blanket `str()`. `okf_io.models._str_tuple` refuses that same
    conversion and says why almost verbatim: it turns a YAML `null` into the
    literal string `"None"`, indistinguishable from a real tag someone spelled
    that way, and a nested mapping into a plausible-looking string
    (`"{'a': 1}"`) with no trace it was ever a mapping. `None` is a sentinel
    no planner can ever match -- every mapping this module builds has `str`
    keys, so `None not in mapping` always holds -- and it is emitted in
    place, never dropped, which is what keeps every later index aligned with
    `fm_raw` rather than shifting past a skipped entry.

    The `CommentedSeq` check is belt and braces over `scan`, which already
    excluded the coercion failures.
    """
    sequence = bundle.concepts[concept_id].fm_raw.get("tags")
    if not isinstance(sequence, CommentedSeq):
        return None
    return [value if isinstance(value, str) else None for value in sequence]


def _plan_mapping(
    bundle: Bundle,
    mapping: Mapping[str, str],
    skipped: Sequence[Skipped],
    usable: Sequence[str],
) -> RenamePlan:
    """The one engine behind all four planners.

    *mapping* is old -> new and may be many-to-one; identity entries are the
    caller's to filter. Within a document the first occurrence that would
    produce a tag not already claimed becomes a rename, and every later one
    becomes a removal -- which is what collapses a merge, a normalization that
    unifies two spellings, and a rename onto an existing tag, all through one
    code path that can only behave one way.
    """
    edits: list[TagEdit] = []
    for concept_id in usable:
        values = _raw_tags(bundle, concept_id)
        if values is None:
            continue
        # Tags the document keeps regardless are claimed up front, so a rename
        # onto one of them is a removal rather than a duplicate. A `None`
        # sentinel (a null or other non-string entry) is never a key of
        # *mapping*, so it always lands here, inert -- present in the set but
        # never matched or promoted.
        claimed: set[str | None] = {value for value in values if value not in mapping}
        for index, old in enumerate(values):
            if old is None or old not in mapping:
                continue
            new = mapping[old]
            path = f"{concept_id}.md"
            if new in claimed:
                edits.append(
                    TagEdit(concept_id=concept_id, path=path, index=index, old=old, new=None)
                )
            else:
                claimed.add(new)
                edits.append(
                    TagEdit(concept_id=concept_id, path=path, index=index, old=old, new=new)
                )

    edits.sort(key=lambda edit: (edit.concept_id, edit.index))
    return RenamePlan(root=bundle.root, edits=tuple(edits), skipped=tuple(skipped))


def _plan(bundle: Bundle, mapping: Mapping[str, str]) -> RenamePlan:
    usable, skipped = scan(bundle)
    live = {old: new for old, new in mapping.items() if old != new}
    return _plan_mapping(bundle, live, skipped, usable)


def plan_rename(bundle: Bundle, old: str, new: str, ctx: ExtContext | None = None) -> RenamePlan:
    """Rename every occurrence of *old* to *new*.

    A document already carrying *new* gets a removal instead of a second copy.

    *ctx* is accepted for signature uniformity across the capability and is
    deliberately unused: an exact-string rename has no normalization policy
    to apply.
    """
    return _plan(bundle, {old: new})


def plan_merge(
    bundle: Bundle, sources: Sequence[str], into: str, ctx: ExtContext | None = None
) -> RenamePlan:
    """Collapse every tag in *sources* into *into*.

    A document carrying several sources keeps one and loses the rest, so the
    result never carries *into* twice.

    *ctx* is accepted for signature uniformity across the capability and is
    deliberately unused: the source and target spellings are exact strings,
    so there is no normalization policy to apply.

    Raises `TypeError` when *sources* is a bare `str`. `Sequence[str]` accepts
    a `str` on its own terms -- `mypy --strict` sees nothing wrong with
    `plan_merge(bundle, "metrics", "metric")` -- but a `str` iterates its own
    characters, so the call above silently plans a merge of `"m"`, `"e"`,
    `"t"`, ... into `"metric"`, matches nothing, and returns an empty plan
    with no error. The fix is to wrap the single source in a list:
    `plan_merge(bundle, ["metrics"], "metric")`.
    """
    if isinstance(sources, str):
        raise TypeError(
            f"sources must be a sequence of tag names, not a bare string ({sources!r}); "
            f"wrap it in a list, e.g. plan_merge(bundle, [{sources!r}], {into!r})"
        )
    return _plan(bundle, {source: into for source in sources})


def plan_normalize(bundle: Bundle, ctx: ExtContext | None = None) -> RenamePlan:
    """Rewrite every tag to its canonical form.

    Derives its mapping directly from `canonical()`, one tag at a time --
    it does not call `inventory.clusters()`. That gives the same partition
    as `clusters()`'s `normalization` kind (a tag maps here exactly when its
    canonical form differs from itself, which is the same condition that
    kind groups on) without a data-flow dependency on it. `similarity`
    suggestions (`metrics` beside `metric`) require judgment and are never
    applied here; that split is why the two cluster kinds exist in the first
    place, even though this function only reaches for one of them by
    construction rather than by filtering `clusters()`'s output.
    """
    policy = (ctx or ExtContext()).normalization
    usable, skipped = scan(bundle)
    mapping: dict[str, str] = {}
    for concept_id in usable:
        for tag in bundle.concepts[concept_id].fm.tags:
            form = canonical(tag, policy)
            if form != tag:
                mapping[tag] = form
    return _plan_mapping(bundle, mapping, skipped, usable)


def plan_from_vocabulary(
    bundle: Bundle, vocab: Vocabulary, ctx: ExtContext | None = None
) -> RenamePlan:
    """Apply the vocabulary's `replaced_by` instructions.

    This is where the vocabulary's dual purpose lands: the same file that
    says `kpi` is wrong says what it should become. A deprecated tag with no
    replacement is a warning only -- there is nothing to rename it to.

    It does blur validation and mutation, which is exactly why plan and apply
    stay two calls: no vocabulary edit ever writes on its own.

    *ctx* is accepted for signature uniformity across the capability and is
    deliberately unused: the vocabulary's replacements are exact strings, so
    there is no normalization policy to apply.
    """
    return _plan(
        bundle,
        {
            tag: replacement
            for tag, replacement in vocab.deprecated.items()
            if replacement is not None
        },
    )


def apply(bundle: Bundle, plan: RenamePlan) -> ApplyResult:
    """Write *plan* against *bundle*.

    **Three failure regimes, from three different mechanisms. Each gives a
    different guarantee, and none of them should be credited with another's
    job.**

    1. *Content failures are refused per document; siblings still write.* A
       concept missing from the bundle, a parse error, `tags` no longer a
       sequence, a plan naming the same index twice, or a stale plan (a
       recorded position no longer holds what it claims) -- every one of
       these is caught while building a `copy.deepcopy` of the affected
       document's `fm_raw`, before that document is even queued to write.
       One document's content problem never stops a sibling that serialized
       fine from landing.

    2. *Probe and staging failures are all-or-nothing: no live file is
       touched.* The `path.open("r+b")` probe below fails fast, with a
       clear message, for a target whose own permissions already forbid it
       or one that no longer exists -- useful, but by itself only a probe:
       a directory-level failure, or a permission change in the instant
       after the probe closes, would sail through it undetected. The actual
       guarantee comes from staging: every document that passes the probe
       is first written to a `.<name>.<uuid>.tmp` sibling in the *same*
       directory as its target -- which hits the same disk-full, quota, or
       permission failures a live write would, and can leave a truncated
       temp file behind on the way, which is why the cleanup below runs on
       *every* exit from this step, not only the successful one. A staging
       failure on any document, not only the first, aborts the whole batch
       before any live file is touched, and every temp file created --
       including the one that just failed partway -- is removed before
       `apply` returns.

    3. *Commit (`Path.replace`) failures are isolated per document, so
       partial application is possible without any crash.* Only once every
       staged write has succeeded does a second loop move each temp file
       onto its target with `Path.replace` (a single filesystem rename,
       atomic and metadata-only, so it does not fail for content or space
       reasons the way a write can). That loop does not abort on a single
       failure -- a caught `OSError` there is reported for that one
       document and the loop continues, so a plan touching three documents
       where the second one's replace fails can legitimately land the first
       and third while the second is refused. This is the one place a
       *caught* exception, not merely a crash, produces partial disk state;
       say so here rather than leaving it to be inferred from "aborts the
       whole batch" above, which is regime 2's guarantee, not this one's.

    What regime 2 and 3 together still leave undefended, because nothing
    short of okf-ext owning a journal closes it, is an abrupt process crash
    *during* the replace loop itself: that loop is a sequence of renames,
    not a sequence of content writes, so a crash partway through could still
    leave some documents moved and others not. Serialization and writing
    themselves cannot produce that outcome; only losing the process
    mid-rename can. Relatedly, and also a deliberate non-goal: nothing here
    calls `fsync` on a temp file or its containing directory, so a power
    loss (as opposed to a process crash) could still lose an already-
    replaced rename on a filesystem that does not order renames durably.
    `fsync` alone would not close that gap without the same journal, and it
    is real, measurable cost -- especially on a network filesystem -- for a
    tag-renaming tool to pay on every call. Considered and declined, not
    overlooked.

    Mutation always goes through a `copy.deepcopy` of `fm_raw`, never
    `doc.set("tags", ...)`: `set` replaces the value wholesale, dropping the
    `CommentedSeq` and with it flow style and inline comments. It stays a
    private copy, never the Bundle's live object, until a write for that
    document actually lands -- only then is the real `Document.fm_raw`
    replaced by the copy that produced it, which is what keeps
    `Bundle.by_tag` coherent with disk immediately, with no reload, for
    exactly the documents written and no others.

    Does not raise for a write failure or an unwritable target; both are
    reported in `ApplyResult.failed`, sorted by path. A caller halfway
    through a bulk edit needs the list of what did and did not land far more
    than a traceback. Each `WriteFailure.kind` names which of the above it
    came from -- `stale` is the one worth re-planning over; `unwritable`,
    `stage-error`, and `commit-error` are the ones worth retrying as-is. See
    `FailureKind`.

    Raises `ValueError` for a plan built against a different bundle: the
    positions in a plan mean nothing anywhere else.
    """
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's positions mean nothing outside the bundle it was planned against."
        )

    grouped: dict[str, list[TagEdit]] = {}
    for edit in plan.edits:
        grouped.setdefault(edit.concept_id, []).append(edit)

    failed: list[WriteFailure] = []
    # (the real document, its would-be new fm_raw, the rendered bytes, its
    # member path). The real `document.fm_raw` is never touched until the
    # write for that document has actually landed -- see the docstring.
    pending: list[tuple[Document, CommentedMap, str, str]] = []

    for concept_id, edits in sorted(grouped.items()):
        member = f"{concept_id}.md"
        document = bundle.concepts.get(concept_id)
        if document is None or document.path is None:
            failed.append(
                WriteFailure(
                    path=member, kind="not-a-member", error="concept is not a member of this bundle"
                )
            )
            continue

        if document.parse_error is not None:
            # Defence in depth. Every planner already excludes a parse-error
            # concept via `scan()`, so a plan built by `plan_rename` and
            # friends never reaches here -- but direct `fm_raw` mutation
            # bypasses `Document._require_mutable()`, so a hand-built plan
            # naming this concept directly would otherwise sail through.
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

        sequence = document.fm_raw.get("tags")
        if not isinstance(sequence, CommentedSeq):
            failed.append(
                WriteFailure(
                    path=member, kind="tags-not-a-sequence", error="`tags` is no longer a sequence"
                )
            )
            continue

        # Defence in depth, same class as the parse-error check above. No
        # planner can construct two edits for the same position -- `edits`
        # is built by iterating raw indices once each -- but a hand-built
        # plan could, and applying both in sequence (rename, then removal)
        # would silently discard the rename with nothing reported.
        index_counts = Counter(edit.index for edit in edits)
        duplicated = sorted(index for index, count in index_counts.items() if count > 1)
        if duplicated:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="duplicate-edit",
                    error=(
                        f"plan carries more than one edit for index {duplicated[0]}; "
                        f"refusing rather than silently applying both"
                    ),
                )
            )
            continue

        # A plan records positions. If the document changed underneath, those
        # positions may now name different tags -- or a non-string value the
        # planner's own sentinel-`None` convention would never have matched
        # in the first place -- so refuse the whole document rather than
        # rewrite whatever happens to sit there. `isinstance(..., str)` is
        # deliberate, not `str(...)`: the latter would turn a YAML `null`
        # into the literal string "None" and let a hand-built edit targeting
        # `old="None"` match it, exactly the coercion `_raw_tags` exists to
        # refuse.
        stale = next(
            (
                edit
                for edit in edits
                if edit.index >= len(sequence)
                or not isinstance(sequence[edit.index], str)
                or sequence[edit.index] != edit.old
            ),
            None,
        )
        if stale is not None:
            failed.append(
                WriteFailure(
                    path=member,
                    kind="stale",
                    error=(
                        f"stale plan: index {stale.index} no longer holds `{stale.old}`; "
                        f"re-plan against the current bundle"
                    ),
                )
            )
            continue

        # Mutate a private copy -- the Bundle's own `document.fm_raw` is left
        # alone until the write actually lands, further down.
        scratch_tags = copy.deepcopy(sequence)
        for edit in edits:
            if edit.new is not None:
                scratch_tags[edit.index] = edit.new
        # Descending, so each deletion leaves the earlier indices valid.
        for edit in sorted(
            (edit for edit in edits if edit.new is None),
            key=lambda edit: edit.index,
            reverse=True,
        ):
            del scratch_tags[edit.index]

        scratch_fm_raw = copy.deepcopy(document.fm_raw)
        scratch_fm_raw["tags"] = scratch_tags
        scratch = replace(document, fm_raw=scratch_fm_raw)
        scratch.mark_dirty()

        try:
            rendered = scratch.serialize()
        except (YAMLError, ValueError, RecursionError) as exc:
            failed.append(WriteFailure(path=member, kind="serialize-error", error=str(exc)))
            continue
        pending.append((document, scratch_fm_raw, rendered, member))

    # Every document that could serialize has now done so, in memory, against
    # a private copy. This probe fails fast, with a clear, specific message,
    # for a target whose own permissions already forbid it or one that no
    # longer exists on disk (`FileNotFoundError`) -- but it is a probe, not
    # the atomicity guarantee: a directory-level failure, or a permission
    # change in the instant after it closes, would sail through undetected.
    # See the staging step below for what actually provides the guarantee.
    unwritable: list[tuple[str, OSError]] = []
    for document, _scratch_fm_raw, _rendered, member in pending:
        assert document.path is not None  # guaranteed by the loop above
        try:
            with document.path.open("r+b"):
                pass
        except OSError as exc:
            unwritable.append((member, exc))

    if unwritable:
        for member, unwritable_exc in unwritable:
            failed.append(WriteFailure(path=member, kind="unwritable", error=str(unwritable_exc)))
        failed.sort(key=lambda failure: failure.path)
        return ApplyResult(written=(), failed=tuple(failed), skipped=plan.skipped)

    # Stage every write as a sibling temp file *before* any live file is
    # touched. A temp write in the same directory hits the same disk-full,
    # quota, or permission-changed-since-the-probe failures a live write
    # would -- but because nothing live has been modified yet, a failure
    # here, for any document and not only the first, aborts with every live
    # file exactly as it was. This -- not the probe above -- is what keeps a
    # failure on document N from leaving documents 1..N-1 already rewritten:
    # the failure-prone work all happens before any live file changes, and
    # every temp file created is removed on every exit from this step.
    staged: list[tuple[Document, CommentedMap, Path, str]] = []
    stage_failed = False
    for document, scratch_fm_raw, rendered, member in pending:
        assert document.path is not None
        tmp_target = document.path.with_name(f".{document.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_target.write_bytes(rendered.encode("utf-8"))
        except OSError as exc:
            # `write_bytes` opens for truncating write before it can fail --
            # a disk-full error, say, can still land after some bytes are
            # already on the temp file. Cleaning it up here (not only the
            # ones that fully succeeded, below) is what keeps this step's
            # own promise: no temp file survives an aborted batch. The
            # cleanup is best-effort and must never mask the real failure,
            # nor stop the rest of `staged` from being cleaned up too.
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
            failed.append(WriteFailure(path=member, kind="stage-error", error=str(exc)))
            stage_failed = True
            continue
        staged.append((document, scratch_fm_raw, tmp_target, member))

    if stage_failed:
        for _document, _scratch_fm_raw, tmp_target, _member in staged:
            # Best-effort, same as above: one cleanup failure must not stop
            # the rest of the batch's temp files from being cleaned up too.
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
        failed.sort(key=lambda failure: failure.path)
        return ApplyResult(written=(), failed=tuple(failed), skipped=plan.skipped)

    # Every target now has a fully-written sibling temp file, and nothing
    # live has changed yet. `Path.replace` is `os.replace` under the hood: a
    # single filesystem rename, atomic and metadata-only, so it does not
    # fail for the content- or space-related reasons a write can -- this
    # loop is a sequence of renames, not a sequence of writes. That is the
    # one window nothing short of okf-ext owning a journal closes: an
    # abrupt process crash *during* this loop could still leave some
    # documents moved and others not. Serialization and writing themselves
    # cannot produce that outcome; only losing the process mid-rename can.
    written: list[str] = []
    for document, scratch_fm_raw, tmp_target, member in staged:
        assert document.path is not None
        try:
            tmp_target.replace(document.path)
        except OSError as exc:
            failed.append(WriteFailure(path=member, kind="commit-error", error=str(exc)))
            # Best-effort cleanup: a failed `replace` on most filesystems
            # leaves the temp file exactly as it was, still worth removing,
            # but a cleanup failure here must not mask the real error above.
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
            continue
        # Only now does the shared Bundle learn about the edit: `by_tag` and
        # every other live view read `fm_raw`/`fm`, so the in-memory bundle
        # stays coherent with what is actually on disk, with no reload.
        document.fm_raw = scratch_fm_raw
        document.mark_dirty()
        written.append(member)

    failed.sort(key=lambda failure: failure.path)
    return ApplyResult(written=tuple(written), failed=tuple(failed), skipped=plan.skipped)
