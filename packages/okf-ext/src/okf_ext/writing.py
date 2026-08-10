"""The write engine every okf-ext capability that writes shares.

**Shared layer, not a capability.** `tags` and `tables` both need the same
probe -> stage -> commit machinery, and the independence contract forbids one
importing it from the other. Hoisting it here is what keeps a second writing
capability from duplicating it -- and duplicating *this* code, whose whole
value is the failure guarantees it makes, is how two capabilities end up
making two different promises about the same word.

Imports stdlib only. A `PendingWrite` names its target by `Path` and carries a
zero-argument `on_written` callback rather than a `Document`, so nothing here
has to know what a bundle is.

`SkipReason` and `FailureKind` are unions across every capability,
deliberately. `tags-not-a-sequence` and `duplicate-edit` stay in `FailureKind`
even though `tables` never emits them, and `section-missing` sits in
`SkipReason` even though `tags` never emits it: splitting either type per
capability buys a narrower annotation at the cost of two types every caller
has to discriminate between.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Why a member could not be considered. Content is never an exception (spec
#: §10) -- it is a `Finding` or one of these. `already-present` is `bundle`'s:
#: a file the planner found in place and deliberately left alone, which is a
#: *success* for an additive install and not a failure of any kind.
SkipReason = Literal[
    "already-present",
    "parse-error",
    "section-missing",
    "tags-not-a-sequence",
    "unreadable",
]

#: Why one document did not land, machine-readable rather than substring-
#: matched out of `WriteFailure.error`. Mirrors the three regimes:
#: `not-a-member`, `parse-error`, `tags-not-a-sequence`, `duplicate-edit`, and
#: `stale` are content failures, refused for that document alone by the
#: calling capability; `unwritable` and `stage-error` are the all-or-nothing
#: probe/staging regime; `commit-error` is the per-document commit regime.
#: `serialize-error` is content-shaped (isolated per document) but named
#: separately because it is raised by the document itself, not detected here.
#: `stale` is the one a caller can act on by re-planning; `unwritable`,
#: `stage-error`, and `commit-error` are the ones worth retrying as-is.
#:
#: The last four are `moves`': `mkdir-error` is a destination parent that
#: could not be created, `asset-replace-error` an asset's direct source ->
#: destination rename (never staged, because copying a large binary to a temp
#: doubles the I/O and buys no guarantee a single atomic rename does not
#: already give), `unlink-error` a source removal that itself failed, and
#: `source-kept` a source removal *declined* because a referrer naming it did
#: not land. The two replace kinds stay distinct from `commit-error` because
#: they come from two different mechanisms, and a caller retrying one is not
#: retrying the other. This union retains every member even where a given
#: capability never emits it -- splitting it per capability buys a narrower
#: annotation at the cost of two types every caller must discriminate between.
#:
#: `foreign-content` is `bundle`'s, and is B-E's sentence -- "a file I own
#: exists with content I did not write" -- made machine-readable rather than
#: substring-matched out of the message. It is a content failure, refused for
#: that one file while its neighbours still write, which is the whole point of
#: narrowing the old all-or-nothing refusal.
#:
#: `mkdir-error` and `stale` are also what a `create=True` `PendingWrite`
#: emits -- a parent that could not be made, and a target that appeared
#: between plan and apply. Both were already in this union; the create path
#: widens nothing.
FailureKind = Literal[
    "not-a-member",
    "parse-error",
    "tags-not-a-sequence",
    "duplicate-edit",
    "foreign-content",
    "stale",
    "serialize-error",
    "unwritable",
    "stage-error",
    "commit-error",
    "mkdir-error",
    "asset-replace-error",
    "unlink-error",
    "source-kept",
]


@dataclass(frozen=True, slots=True)
class Skipped:
    """A member a capability could not read, and why."""

    concept_id: str
    path: str  # bundle-relative posix
    reason: SkipReason
    detail: str


@dataclass(frozen=True, slots=True)
class WriteFailure:
    """A document that did not land, the rendered reason, and its `kind`.

    `kind` is the machine-readable discriminator; `error` stays the rendered
    prose. A caller that wants to retry an I/O failure but re-plan on a stale
    one needs `kind`, not a substring match against `error` -- see
    `FailureKind`. Stores a rendered message rather than a live exception so
    every one of these survives `json.dumps`.
    """

    path: str
    kind: FailureKind
    error: str  # str(exc) -- the message, not the live exception


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What landed, what did not, and what was never considered."""

    written: tuple[str, ...]
    failed: tuple[WriteFailure, ...]
    skipped: tuple[Skipped, ...]  # carried from the plan

    @property
    def ok(self) -> bool:
        return not self.failed


@dataclass(frozen=True, slots=True)
class PendingWrite:
    """One document that serialized cleanly and is queued to land.

    `on_written` runs **only** after that document's own commit succeeds,
    which is what keeps an in-memory bundle coherent with disk for exactly the
    documents written and no others.

    **`on_written` must not raise.** It runs outside the `try/except OSError`
    around the commit it follows -- deliberately, not by oversight: that
    `except` exists to catch *filesystem* conditions a caller can act on
    (retry, re-plan, report), and `on_written` is not one of those. It is the
    calling capability's own in-memory state sync -- `tags.rename._commit_tags`
    swapping a scratch `fm_raw` onto the live `Document`, say -- work that
    should never fail against an object the caller itself just built. Catching
    it here would misfile a caller bug as a write failure and hand back a
    `WriteFailure.kind` (`"commit-error"`, most plausibly) that means "the
    filesystem refused this," when what actually happened is "the caller's own
    callback is broken." If `on_written` does raise: the file has already
    landed on disk (the rename that preceded it succeeded), that document
    never makes it into `ApplyResult.written` because the exception unwinds
    past the line that would have appended it, every item still queued behind
    it in the commit loop is abandoned mid-batch with no further probing or
    reporting, and the exception itself propagates out of `write_all` rather
    than landing in `ApplyResult.failed`. That is a real gap between disk and
    the caller's bookkeeping -- but it is the caller's bug to fix, not a
    condition for this function to paper over with a `try/except` that would
    only hide it. What `write_all` *does* still own, even mid-raise, is the
    temp-file promise: every item still queued behind the one that raised
    already has a staged `.tmp` sibling on disk, and those are removed on a
    best-effort basis (an `OSError` during that cleanup is suppressed, not
    left to mask the original exception) before it propagates. The failing
    item itself needs no such cleanup -- its own temp file is already gone,
    replaced onto its target by the rename that preceded the callback.
    """

    member: str  # bundle-relative posix, for reporting
    path: Path  # the live target
    rendered: str  # the whole file, as it will be written
    on_written: Callable[[], None]
    create: bool = False
    """Whether this write brings a **new** member into being.

    The probe regime below cannot speak for a file that is not there yet:
    `open("r+b")` fails for exactly the case a create is. A create item is
    therefore probed the other way round -- its parent is made, and an already
    occupied target is refused -- while staging and commit are the same two
    steps an update goes through. Defaulting to `False` keeps every existing
    construction site meaning what it meant.
    """


def body_digest(body: str) -> str:
    """A stable fingerprint of *body*, for a plan to check staleness against.

    **Shared, not per-capability, because "stale" is a promise to callers.**
    `tables` refuses a splice whose body changed since it was planned and
    `moves` refuses a repair for the same reason; two capabilities computing
    that answer two ways is how one of them ends up meaning something
    subtly different by the same word. The digest is over the body only --
    frontmatter edits are position-free (they name a dotted key path) and
    are checked by re-reading the path instead.

    sha256 of the UTF-8 bytes. Not a security boundary: this guards against
    concurrent edits and stale plans, not against an adversary constructing
    a collision, and the choice is about a stable well-known digest rather
    than about collision resistance.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_all(
    pending: Sequence[PendingWrite],
    *,
    failed: Sequence[WriteFailure] = (),
    skipped: Sequence[Skipped] = (),
) -> ApplyResult:
    """Write every item in *pending*, in two regimes.

    **Commits in the order given.** The staging and commit loops both iterate
    *pending* as received and never sort it, and `ApplyResult.written` comes
    back in that same order. This is a contract, not an accident of the loop:
    `okf_ext.moves` commits a move's destinations before its referrers, and
    that ordering is the whole basis of its "no partial outcome leaves a
    dangling reference" invariant. Sorting here -- by path, say, which `tags`
    would not notice -- would silently break it. Only `problems` is sorted,
    and only because a failure list has no caller-meaningful order.

    *failed* carries the calling capability's own content failures -- the
    third regime, refused per document before anything reached here -- and is
    merged into the result. Nothing in this function raises for a write
    failure: a caller halfway through a bulk edit needs the list of what did
    and did not land far more than a traceback.

    1. *Probe and staging failures are all-or-nothing: no live file is
       touched.* The `path.open("r+b")` probe fails fast, with a clear
       message, for a target whose own permissions already forbid it or one
       that no longer exists -- useful, but by itself only a probe: a
       directory-level failure, or a permission change in the instant after
       the probe closes, would sail through it undetected. The actual
       guarantee comes from staging: every document that passes the probe is
       first written to a `.<name>.<uuid>.tmp` sibling in the *same* directory
       as its target -- which hits the same disk-full, quota, or permission
       failures a live write would, and can leave a truncated temp file behind
       on the way, which is why the cleanup runs on *every* exit from that
       step, not only the successful one. A staging failure on any document,
       not only the first, aborts the whole batch before any live file is
       touched, and every temp file created -- including the one that just
       failed partway -- is removed before this returns.

       A `create=True` item is checked the other way round instead: its parent
       directory is made (`mkdir-error` on failure) and an already-occupied
       target is refused as `stale`. Both are all-or-nothing exactly as the
       probe is, so a create that lost its race leaves no live file touched.
       The one residue such an abort can leave is an empty directory the
       `mkdir` already made; `load_bundle` walks files, so it is invisible to
       every reader and is left in place rather than unwound by a second
       failure-prone step -- the same call `moves` makes for the same reason.

    2. *Commit (`Path.replace`) failures are isolated per document, so partial
       application is possible without any crash.* Only once every staged
       write has succeeded does a second loop move each temp file onto its
       target with `Path.replace` (a single filesystem rename, atomic and
       metadata-only, so it does not fail for the content or space reasons a
       write can). That loop does not abort on a single failure -- a caught
       `OSError` there is reported for that one document and the loop
       continues, so a batch touching three documents where the second one's
       replace fails can legitimately land the first and third while the
       second is refused. This is the one place a *caught* exception, not
       merely a crash, produces partial disk state. If `on_written` itself
       raises after a successful replace, that is not caught -- see
       `PendingWrite.on_written` -- but the temp files of every item still
       queued behind it are still removed, best-effort, before the exception
       propagates.

    What the two regimes together still leave undefended, because nothing
    short of okf-ext owning a journal closes it, is an abrupt process crash
    *during* the replace loop: that loop is a sequence of renames, not of
    content writes, so a crash partway through could leave some documents
    moved and others not. Serialization and writing themselves cannot produce
    that outcome; only losing the process mid-rename can. Relatedly, and also
    a deliberate non-goal: nothing here calls `fsync` on a temp file or its
    containing directory, so a power loss (as opposed to a process crash)
    could still lose an already-replaced rename on a filesystem that does not
    order renames durably. `fsync` alone would not close that gap without the
    same journal, and it is real, measurable cost -- especially on a network
    filesystem -- to pay on every call. Considered and declined, not
    overlooked.
    """
    problems = list(failed)

    unwritable: list[WriteFailure] = []
    for item in pending:
        if item.create:
            try:
                item.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                unwritable.append(WriteFailure(path=item.member, kind="mkdir-error", error=str(exc)))
                continue
            if item.path.exists():
                # The planner already refused `target-exists`; a target that
                # appeared *since* is drift, and `stale` is the one kind a
                # caller acts on by re-planning rather than by retrying. An
                # empty directory this `mkdir` just made is left in place on
                # the abort: `load_bundle` walks files, so it is invisible to
                # every reader, and unwinding it would be a second
                # failure-prone step buying nothing.
                unwritable.append(
                    WriteFailure(
                        path=item.member,
                        kind="stale",
                        error=(
                            "the target exists; it appeared since this plan was "
                            "computed -- re-plan against the current bundle"
                        ),
                    )
                )
            continue
        try:
            with item.path.open("r+b"):
                pass
        except OSError as exc:
            unwritable.append(WriteFailure(path=item.member, kind="unwritable", error=str(exc)))

    if unwritable:
        problems.extend(unwritable)
        problems.sort(key=lambda failure: failure.path)
        return ApplyResult(written=(), failed=tuple(problems), skipped=tuple(skipped))

    staged: list[tuple[PendingWrite, Path]] = []
    stage_failed = False
    for item in pending:
        tmp_target = item.path.with_name(f".{item.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_target.write_bytes(item.rendered.encode("utf-8"))
        except OSError as exc:
            # `write_bytes` opens for truncating write before it can fail -- a
            # disk-full error, say, can still land after some bytes are
            # already on the temp file. Cleaning it up here (not only the ones
            # that fully succeeded, below) is what keeps this step's own
            # promise: no temp file survives an aborted batch. Best-effort,
            # and it must never mask the real failure.
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
            problems.append(WriteFailure(path=item.member, kind="stage-error", error=str(exc)))
            stage_failed = True
            continue
        staged.append((item, tmp_target))

    if stage_failed:
        for _item, tmp_target in staged:
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
        problems.sort(key=lambda failure: failure.path)
        return ApplyResult(written=(), failed=tuple(problems), skipped=tuple(skipped))

    written: list[str] = []
    for index, (item, tmp_target) in enumerate(staged):
        try:
            tmp_target.replace(item.path)
        except OSError as exc:
            problems.append(WriteFailure(path=item.member, kind="commit-error", error=str(exc)))
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
            continue
        # Deliberately outside the `except OSError` above: `on_written` is the
        # caller's in-memory state sync, not a filesystem operation, and its
        # contract (see `PendingWrite.on_written`) is that it must not raise.
        # If it does anyway, this item's own temp file needs no cleanup -- the
        # replace above already consumed it -- but everything still queued
        # behind it does, so that promise is kept before the exception
        # propagates past this loop.
        try:
            item.on_written()
        except Exception:
            for _pending, leftover in staged[index + 1 :]:
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)
            raise
        written.append(item.member)

    problems.sort(key=lambda failure: failure.path)
    return ApplyResult(written=tuple(written), failed=tuple(problems), skipped=tuple(skipped))


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "ApplyResult",
    "FailureKind",
    "PendingWrite",
    "SkipReason",
    "Skipped",
    "WriteFailure",
    "body_digest",
    "write_all",
]
