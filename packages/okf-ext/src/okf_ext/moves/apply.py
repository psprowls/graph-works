"""The one function in this capability that writes.

**Every partial outcome leaves a bundle in which no reference dangles.** That
is a consequence of the order, not of luck: destinations commit before
referrers, and a source is removed last and only once every referrer naming it
has landed. If a destination fails, no referrer has been touched. If a referrer
fails, its unchanged reference still points at the source, which still exists.

That holds for every **markdown** member without qualification. It holds for a
moved **asset** everywhere except one narrow, documented window, because an
asset renames directly and so has no orphan copy to fall back on -- see the
carve-out in `apply`'s own docstring, which states exactly when it applies and
exactly which failure cannot trigger it.

**`apply` does not update the in-memory `Bundle`.** `tags.apply` reassigns
`document.fm_raw` on success so `by_tag` stays coherent with disk. Moves cannot
do the equivalent: a move changes the bundle's **key set**, and
`Bundle.concepts` is a `MappingProxyType` built once by the walk. Rebinding
`Document.path` while the mapping still keys the document by its old id would
leave the bundle half-coherent, which is worse than not coherent. `MoveResult`
is the authority on what happened, and the caller reloads.
"""

from __future__ import annotations

import contextlib
import copy
import uuid
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath

from okf_io import Bundle, Document
from okf_io.bundle import INDEX_NAME, LOG_NAME
from ruamel.yaml.error import YAMLError

from okf_ext.body import split_lines
from okf_ext.moves.model import MoveMaterialization, MovePlan, MoveResult, RefEdit
from okf_ext.writing import ApplyResult, PendingWrite, WriteFailure, body_digest, write_all


def _members(bundle: Bundle) -> dict[str, Document]:
    """Every markdown member by bundle-relative path, reserved files included.

    Mirrors `plan._members` exactly, and for the same reason: `index.md` and
    `log.md` hold references a move must repair even though `LinkGraph` never
    treats them as link sources. A second, narrower member map here would let
    `apply` fail to find a document `plan` had already emitted edits for.
    """
    found: dict[str, Document] = {f"{cid}.md": doc for cid, doc in bundle.concepts.items()}
    for directory, document in bundle.indexes.items():
        found[f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME] = document
    for directory, document in bundle.logs.items():
        found[f"{directory}/{LOG_NAME}" if directory else LOG_NAME] = document
    return found


def _require_path(document: Document) -> Path:
    assert document.path is not None  # every bundle member is loaded from a path
    return document.path


def _abort(problems: list[WriteFailure]) -> MoveResult:
    """The shape every exit before regime 3 shares: nothing happened.

    Regimes 1 and 2 have four abort points between them, and all four report
    the identical outcome -- no file moved, no referrer written, no directory
    pruned, failures sorted because a failure list has no caller-meaningful
    order. Saying that once is what stops a fifth exit from quietly reporting
    something else. Regime 3's own abort is deliberately *not* routed through
    here: by then destinations have landed and `moved` is not empty.
    """
    problems.sort(key=lambda failure: failure.path)
    return MoveResult(moved=(), written=(), failed=tuple(problems), pruned=())


def _apply_body_edits(body: str, edits: Sequence[RefEdit]) -> str | None:
    """Splice every body edit into *body*, or None when a span went stale.

    Applied descending by `(line, column)` so an earlier column stays valid
    while a later one on the same line is rewritten.
    """
    body_lines = list(split_lines(body))
    for edit in sorted(edits, key=lambda e: (e.line or 0, e.column or 0), reverse=True):
        if edit.line is None or edit.column is None or edit.line > len(body_lines):
            return None
        text = body_lines[edit.line - 1]
        start, stop = edit.column, edit.column + len(edit.old)
        if text[start:stop] != edit.old:
            return None
        body_lines[edit.line - 1] = text[:start] + edit.new + text[stop:]
    return "".join(body_lines)


def _set_key(raw: object, key: str, old: str, new: str) -> bool:
    """Rewrite a dotted key path, or False when it no longer holds *old*.

    Position-free, so there is nothing to go stale about *where* -- but the
    value itself can still have changed, and re-reading it is the check.

    **A digit segment is always a sequence index, never a mapping key.** The
    generality the dotted syntax implies is bounded by its one caller:
    `plan._reference_key_paths` emits `resource`, `computation`,
    `executor.resource`, `attester.resource` and `sources.<n>.resource`, and
    the only digits in that set index `sources`. A frontmatter mapping keyed
    by an integer-looking string is therefore unreachable from here, and this
    would decline it rather than rewrite the wrong node -- which is the safe
    direction, and the reason it is stated rather than guarded.
    """
    segments = key.split(".")
    current = raw
    for segment in segments[:-1]:
        if segment.isdigit():
            if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
                return False
            index = int(segment)
            if index >= len(current):
                return False
            current = current[index]
            continue
        if not isinstance(current, Mapping) or segment not in current:
            return False
        current = current[segment]
    leaf = segments[-1]
    if leaf.isdigit():
        if not isinstance(current, MutableSequence):
            return False
        index = int(leaf)
        if index >= len(current) or current[index] != old:
            return False
        current[index] = new
        return True
    if not isinstance(current, MutableMapping) or current.get(leaf) != old:
        return False
    current[leaf] = new
    return True


def _build(
    member: str, document: Document, edits: Sequence[RefEdit], digest: str | None
) -> tuple[str | None, WriteFailure | None]:
    """Render *document* carrying *edits*, or say why it cannot be.

    *member* is the bundle-relative posix name a failure is reported under.
    Passed in rather than derived from `document.path`, which is absolute: a
    `WriteFailure.path` is compared against `MovePlan.members` and against
    `MoveResult.moved`, both of which are bundle-relative.

    Content is built on a `copy.deepcopy` of `fm_raw` plus `set_body` on a
    `dataclasses.replace` clone -- never `doc.set()`, which only reaches
    top-level keys (a §6.2 reference lives at `sources.0.resource` as often as
    at `resource`) and would have to replace the whole `sources` value
    wholesale, dropping the `CommentedSeq` with its flow style and inline
    comments; and never `okf_io.document.rendered_with_body`, whose docstring
    documents an `fm_raw` aliasing hazard that holds only because `set_body`
    never touches frontmatter. Moves touches both.

    The deepcopy is what keeps a *failed* build from mutating the live bundle:
    `_set_key` writes into the scratch map, and a later edit in the same batch
    refusing must not leave the earlier one's rewrite on the in-memory
    document. `dataclasses.replace` then gives `set_body` its own `Document`
    to dirty -- `Split` is frozen, so `set_body`'s `replace(self._split, ...)`
    rebinds rather than mutates, and the live document's own `_split`, `body`
    and `_dirty` are never touched.
    """
    if digest is not None and body_digest(document.body) != digest:
        return None, WriteFailure(
            path=member,
            kind="stale",
            error="the body changed since this plan was computed; re-plan against the current bundle",
        )

    body_edits = [edit for edit in edits if edit.where == "body"]
    fm_edits = [edit for edit in edits if edit.where == "frontmatter"]

    new_body = _apply_body_edits(document.body, body_edits) if body_edits else document.body
    if new_body is None:
        return None, WriteFailure(
            path=member,
            kind="stale",
            error="a recorded span no longer holds the text it claims; re-plan against the current bundle",
        )

    scratch_fm = copy.deepcopy(document.fm_raw)
    for edit in fm_edits:
        if not _set_key(scratch_fm, edit.key or "", edit.old, edit.new):
            return None, WriteFailure(
                path=member,
                kind="stale",
                error=f"frontmatter key `{edit.key}` no longer holds `{edit.old}`; re-plan",
            )

    scratch = replace(document, fm_raw=scratch_fm)
    try:
        scratch.set_body(new_body)
        rendered = scratch.serialize()
    except (YAMLError, ValueError, RecursionError) as exc:
        return None, WriteFailure(path=member, kind="serialize-error", error=str(exc))
    return rendered, None


def _prune(root: Path, directories: Sequence[str]) -> tuple[str, ...]:
    """Remove emptied source directories, deepest first. Best-effort, no failure path.

    Deepest first matters: one batch can remove sources from both `a/b` and
    `a/b/c`, and `a/b` only empties once `a/b/c` is gone.

    **Only directories that directly held a removed source are considered.** A
    proper *ancestor* that empties as a consequence -- move everything out of
    `a/b/c` and `a/b` and `a` empty too, though neither ever held a source
    file -- is left in place. Deliberate, not an oversight: an empty directory
    is invisible to `load_bundle`, which walks files, so leaving one costs
    nothing, while climbing the tree would have this function deleting
    directories no move ever named.
    """
    pruned: list[str] = []
    for relative in sorted(set(directories), key=lambda value: value.count("/"), reverse=True):
        if not relative:
            continue
        target = root / relative
        try:
            if target.is_dir() and not any(target.iterdir()):
                target.rmdir()
                pruned.append(relative)
        except OSError:
            continue  # best-effort: a failure to prune is not a failure of the move
    return tuple(pruned)


def _validate_plan(bundle: Bundle, plan: MovePlan) -> None:
    """Reject a plan whose spans cannot safely apply to *bundle*."""
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's spans mean nothing outside the bundle it was planned against."
        )
    if not plan.ok:
        raise ValueError(
            f"Plan carries {len(plan.refusals)} refusal(s) and will not be applied; "
            f"fix the mapping and re-plan. First: {plan.refusals[0].kind} on {plan.refusals[0].path}"
        )


def _materialize(
    bundle: Bundle, plan: MovePlan
) -> tuple[MoveMaterialization | None, dict[str, str], list[WriteFailure]]:
    """Build a plan's effects and rendered text without touching the filesystem."""
    members = _members(bundle)
    relocations = plan.moves if plan.relocate else ()
    destination_of = {move.source: move.dest for move in relocations}
    by_member: dict[str, list[RefEdit]] = {}
    for edit in plan.edits:
        by_member.setdefault(edit.member, []).append(edit)

    contents: dict[str, str] = {}
    problems: list[WriteFailure] = []
    touched = sorted(set(by_member) | {move.source for move in relocations if not move.is_asset and not move.opaque})
    for member in touched:
        document = members.get(member)
        if document is None or document.path is None:
            problems.append(WriteFailure(path=member, kind="not-a-member", error="not a member of this bundle"))
            continue
        rendered, failure = _build(member, document, by_member.get(member, ()), plan.digests.get(member))
        if failure is not None:
            problems.append(failure)
            continue
        assert rendered is not None
        contents[member] = rendered
    if problems:
        return None, contents, problems

    writes = {destination_of.get(member, member): rendered.encode("utf-8") for member, rendered in contents.items()}
    materialized = MoveMaterialization(
        writes=writes,
        renames=tuple(move for move in relocations if move.is_asset or move.opaque),
        deletes=tuple(move.source for move in relocations if not move.is_asset and not move.opaque),
    )
    return materialized, contents, []


def materialize(bundle: Bundle, plan: MovePlan) -> MoveMaterialization:
    """Return every final file effect for *plan* without writing to disk.

    Stale documents and serialization errors make it impossible to produce a
    complete effect set, so they are reported as ``ValueError``. ``apply``
    preserves its established ``MoveResult`` failure contract for those same
    conditions through the shared private build phase.
    """
    _validate_plan(bundle, plan)
    result, _contents, problems = _materialize(bundle, plan)
    if problems:
        first = problems[0]
        raise ValueError(f"Cannot materialize `{first.path}`: {first.error}")
    assert result is not None
    return result


def apply(bundle: Bundle, plan: MovePlan) -> MoveResult:
    """Write *plan* against *bundle*. Four regimes, in this order.

    1. **Content build and staleness -- all-or-nothing, nothing written.** A
       stale edit or a serialize failure **aborts the batch**. This diverges
       from `tags.apply`, which refuses per document and lets siblings land.
       The divergence is deliberate: a move is a multi-file transaction and a
       tag rename is not, so a partially-applied move costs more than a
       partially-applied rename.
    2. **`mkdir` destination parents, probe referrers, then stage -- no live
       file touched.** Every destination write is staged to a
       `.<name>.<uuid>.tmp` sibling in the target's own directory, so it hits
       the same disk-full, quota and permission failures a live write would,
       before any live file changes. Every temp file created is removed on
       every exit from the step. The one residue an abort here can leave is an
       empty destination *directory* the `mkdir` already created; `load_bundle`
       walks files, so it is invisible to every reader and is left in place
       rather than unwound by a second failure-prone step.
    3. **Commit -- per file, destinations before referrers.** Destinations
       (and asset renames, which are a direct `Path.replace` because copying a
       large binary to a temp doubles the I/O and buys no guarantee a single
       atomic rename does not already give) commit first; only then does
       `write_all` handle the referrers.
    4. **Source removal last, and conditional.** A markdown source is unlinked
       only once *every* referrer edit naming it has landed -- body and
       frontmatter alike, since `RefEdit.target` names the resolved old target
       either way and a dangling `resource:` is as broken as a dangling link.
       Empty source directories are then pruned bottom-up, best-effort, with
       no failure path.

    The price of the invariant is that a failure can leave an **orphan copy**
    at the old path -- reported as a `WriteFailure` with `kind="unlink-error"`
    (the unlink itself failed) or `kind="source-kept"` (a referrer did not
    land, so removal was declined). Never a silent success. Recovery is
    `plan_repair` on the residual mapping, then deleting the orphan.

    **The one place the no-dangle invariant is weaker: an asset rename that
    commits, followed by any failure later in the same run.** An asset rename
    is a single `Path.replace`, so it consumes its source the instant it
    commits -- there is no orphan copy left behind to keep inbound references
    meaning something, and no deferred, conditional removal to decline. A
    markdown member has neither problem, because its source removal waits for
    regime 4 and is gated on `source-kept`.

    Two failures can follow a committed asset rename, and both strand it:

    - a **later asset rename in the same batch** failing, which stops regime 3
      before `write_all` repairs a single referrer; or
    - a **referrer write failing** in `write_all`, leaving that one referrer
      still naming the path the rename consumed.

    What can *not* follow it is a markdown `commit-error`: every markdown
    destination commits before the first asset is attempted, and the asset
    loop breaks the moment any problem is on the list. Without that gate a
    transient I/O hiccup on one markdown file would orphan every image in the
    batch -- the ordinary shape of a `plan_move_dir` over a directory holding
    both -- which is far wider than the window this carve-out accepts.

    Inherent to the direct rename, not fixable by reordering (writing
    referrers first would only move the dangle to the other end), and always
    reported: the result is not `ok`, `moved` says which asset landed, and
    `plan_repair` over the residual mapping is the fix.

    Raises `ValueError` twice, both caller error rather than bundle content,
    following `tags.apply`'s precedent: for a plan built against a different
    bundle (its spans mean nothing anywhere else), and for a plan whose `ok`
    is `False` (the plan already said so).
    """
    _validate_plan(bundle, plan)

    root = Path(bundle.root)
    members = _members(bundle)
    # A `plan_repair` plan carries `moves` -- they are what its edits were
    # computed from -- but nothing about it relocates a file: the move it
    # describes already happened outside this capability. Regimes 2 and 3 must
    # therefore see an empty relocation set, or staging would look for content
    # `_build` never produced (a repair plan's `touched` set is its referrers
    # alone) and the asset loop would rename a source that is already gone.
    relocations = plan.moves if plan.relocate else ()
    destination_of = {move.source: move.dest for move in relocations}

    try:
        materialized = materialize(bundle, plan)
    except ValueError:
        # `materialize` reports a stale document or serialization failure as
        # a ValueError because it cannot return a complete effect set. `apply`
        # keeps its established result-based failure contract for those same
        # plan-time failures.
        _materialized, _contents, materialize_problems = _materialize(bundle, plan)
        if materialize_problems:
            return _abort(materialize_problems)
        raise
    problems: list[WriteFailure] = []
    by_member: dict[str, list[RefEdit]] = {}
    for edit in plan.edits:
        by_member.setdefault(edit.member, []).append(edit)

    # --- regime 2: mkdir, probe, stage. No live file is touched. ---
    destination_paths = {move.dest for move in relocations}
    destination_paths.update(move.dest for move in materialized.renames)
    for destination in destination_paths:
        parent = (root / destination).parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(WriteFailure(path=destination, kind="mkdir-error", error=str(exc)))
    if problems:
        return _abort(problems)

    # `write_all` runs this same probe, but it runs it far too late to help:
    # by then every destination has already committed. Running it here is what
    # lets an unwritable referrer abort while nothing has been touched at all.
    referrers = sorted(member for member in by_member if member not in destination_of)
    for member in referrers:
        path = _require_path(members[member])
        try:
            with path.open("r+b"):
                pass
        except OSError as exc:
            problems.append(WriteFailure(path=member, kind="unwritable", error=str(exc)))
    if problems:
        return _abort(problems)

    staged: list[tuple[str, str, Path, Path]] = []  # (source, dest, tmp, live)
    stage_failed = False
    for move in relocations:
        if move.dest not in materialized.writes:
            continue
        live = root / move.dest
        tmp = live.with_name(f".{live.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(materialized.writes[move.dest])
        except OSError as exc:
            # `write_bytes` truncates before it can fail, so a partial temp
            # file can survive the raise -- clean up the one that just failed
            # as well as the ones that succeeded.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            problems.append(WriteFailure(path=move.dest, kind="stage-error", error=str(exc)))
            stage_failed = True
            continue
        staged.append((move.source, move.dest, tmp, live))

    if stage_failed:
        for _source, _dest, tmp, _live in staged:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
        return _abort(problems)

    # --- regime 3: commit destinations, then assets, then referrers ---
    moved: list[tuple[str, str]] = []
    for source, dest, tmp, live in staged:
        try:
            tmp.replace(live)
        except OSError as exc:
            problems.append(WriteFailure(path=dest, kind="commit-error", error=str(exc)))
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            continue
        moved.append((source, dest))

    for move in materialized.renames:
        if problems:
            # **Gated on the markdown commit loop above, not only on earlier
            # assets.** An asset rename consumes its source the instant it
            # commits -- there is no orphan copy to keep inbound references
            # meaning something -- and once any destination has failed, this
            # run will stop before `write_all` repairs a single referrer. So a
            # markdown `commit-error` on one file must not go on to strand
            # every image in the same batch, which is the ordinary shape of a
            # `plan_move_dir` over a directory holding both.
            break
        try:
            (root / move.source).replace(root / move.dest)
        except OSError as exc:
            problems.append(WriteFailure(path=move.dest, kind="asset-replace-error", error=str(exc)))
            continue
        moved.append((move.source, move.dest))

    if problems:
        # A destination did not land. Every markdown source is still in place
        # and no referrer has been touched, so nothing markdown-shaped dangles
        # -- stop here. See the asset caveat in this function's docstring.
        problems.sort(key=lambda failure: failure.path)
        return MoveResult(moved=tuple(sorted(moved)), written=(), failed=tuple(problems), pruned=())

    pending = [
        PendingWrite(
            member=member,
            path=_require_path(members[member]),
            rendered=materialized.writes[member].decode("utf-8"),
            on_written=lambda: None,
        )
        for member in referrers
    ]
    result: ApplyResult = write_all(pending)
    problems.extend(result.failed)

    # --- regime 4: source removal, last and conditional ---
    landed = set(result.written)
    referrers_of: dict[str, set[str]] = {}
    for edit in plan.edits:
        if edit.member in destination_of:
            continue
        referrers_of.setdefault(edit.target, set()).add(edit.member)

    removed_dirs: list[str] = []
    landed_moves = {source for source, _dest in moved}
    for source in materialized.deletes:
        if source not in landed_moves:
            continue
        dest = destination_of[source]
        outstanding = sorted(referrers_of.get(source, set()) - landed)
        if outstanding:
            problems.append(
                WriteFailure(
                    path=source,
                    kind="source-kept",
                    error=(
                        f"kept: {len(outstanding)} referrer(s) did not land ({outstanding[0]}...), so removing "
                        f"the source would break them. An orphan copy survives at `{source}`; recover with "
                        f"`plan_repair` on the residual mapping, then delete it."
                    ),
                )
            )
            continue
        try:
            (root / source).unlink()
        except OSError as exc:
            problems.append(
                WriteFailure(
                    path=source,
                    kind="unlink-error",
                    error=f"{exc}; an orphan copy survives at `{source}` and `{dest}` also exists",
                )
            )
            continue
        source_dir = PurePosixPath(source).parent.as_posix()
        removed_dirs.append("" if source_dir == "." else source_dir)

    # `removed_dirs` is passed whole, `""` (a source at the bundle root) and
    # all: `_prune` already declines it, and filtering here as well would make
    # two guards of one rule, only one of which any test can reach.
    pruned = _prune(root, removed_dirs)
    problems.sort(key=lambda failure: failure.path)
    return MoveResult(
        moved=tuple(sorted(moved)),
        written=tuple(result.written),
        failed=tuple(problems),
        pruned=pruned,
    )


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = ["apply", "materialize"]
