#!/usr/bin/env python3
"""Move whole bundle directories per a declarative rules file, repairing every
inbound OKF reference.

One reusable, dry-run-first entry point for bundle restructures, so the next
relocation is a reviewed YAML diff rather than a bespoke script. Its argument
is the bundle directory; it never resolves a workspace itself.

The name avoids `migrate` deliberately: `okf_io/migrate.py` is the unrelated
OKF v0.1 -> v0.2 format migration.

Design notes
------------
* **One `plan_move_many` over the union of every rule, never a
  `plan_move_dir` loop.** A reference *between* two members that both move
  has a new form depending on both the new base and the new target
  (`okf_ext/moves/plan.py:784-786`), so cross-linking lanes must expand into
  one mapping handed to one call. Destination collisions then come free from
  `_validate`'s `claimed` tracking as `dest-exists` -- nothing here
  re-implements them.
* **Two refusals the engine cannot see**, because both are facts about the
  *rules* rather than about the bundle: `rule-overlap` (two rules claim one
  source with different destinations, which a plain `dict` would let the
  later rule silently win) and `empty-rule` (a rule matched nothing *and* its
  destination holds nothing either -- the `references` versus `reference`
  slip). The destination half of `empty-rule` is what keeps a second run a
  clean no-op rather than a spurious refusal.
* **Reserved members are carried here, not by the engine.** `index.md` and
  `log.md` under a rule are refused as `reserved-source`
  (`plan.py:155-159`), and any refusal invalidates the whole plan
  (`MovePlan.ok`), so a naive directory expansion moves *nothing*. A lane
  index's entries are **relative**, so it moves correctly on its own: it is
  excluded from the mapping, hidden from the planning load via `ignore=`,
  renamed directly, and its inbound references repaired by a `plan_repair`
  computed against a **reloaded** bundle.
* **Directory rules only.** Glob-with-template and regex rule kinds are out
  of scope for v1; `expand` is the seam they would be added behind.
* **Indexes, `log.md` and `gw config sync` are not this script's concern.**
  It moves files and repairs links, then prints the follow-up commands.
* **The reserved half needs its own copy of every invariant the engine
  enforces on the ordinary half.** The reserved set is by construction
  exactly what `_validate`'s `claimed` tracking cannot see, so "destination
  collision detection comes free from the engine" is true only of the
  mapping the engine sees -- a reserved destination that already exists, or
  two reserved sources claiming one destination, both need their own guard
  here (see `expand` and `rename_reserved`). The next change touching the
  reserved half must re-ask this question, not assume the engine covers it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from okf_ext import moves
from okf_ext.moves import MovePlan, MoveResult
from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, load_bundle
from okf_io.bundle import INDEX_NAME, LOG_NAME, canonical_id
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

#: The `ignore=` recipe every load here uses, byte-identical to both
#: `doc_wiki_okf.archive.ARCHIVE_IGNORE` (`archive.py:89`) and
#: `work_tracker_okf.items.ARCHIVE_IGNORE` (`items.py:19`) -- two packages
#: independently arriving at the same rule: when handing a bundle to
#: `okf_ext.moves`, hide nothing that is content, because the planner never
#: reads `bundle.ignored` and would otherwise leave files behind
#: (`archive.py:84-88`).
#:
#: `*/.DS_Store` stays **visible**, deliberately (`archive.py:70-76`):
#: reading, it is not content; moving, it has to stay visible or
#: `okf_ext.moves` leaves it behind and the source directory never empties
#: for `apply` to prune.
IGNORE: tuple[str, ...] = (*_SCHEMA_IGNORE, *_SECTIONS_IGNORE)

#: The two filenames `okf_ext.moves` refuses to relocate, and which this
#: script therefore carries itself.
RESERVED: frozenset[str] = frozenset({INDEX_NAME, LOG_NAME})


@dataclass(frozen=True)
class Rule:
    """One directory move. Both ends bundle-relative posix, no trailing slash."""

    directory: str
    to: str


@dataclass(frozen=True)
class Refused:
    """One reason nothing will be written.

    `kind` is this script's own closed vocabulary -- `bad-rules`, `bad-rule`,
    `rule-overlap`, `empty-rule` -- plus any `moves.RefusalKind` forwarded
    from the engine, so a reader sees one refusal shape whichever layer
    produced it.
    """

    kind: str
    detail: str


class _BadRule(Exception):
    pass


class _RenameFailed(Exception):
    """`rename_reserved` cannot proceed. Carries the `(source, dest)` pairs
    that already landed before the failing entry, so `write` rolls back
    exactly those rather than guessing from what exists on disk -- an entry
    this call never touched (e.g. a pre-existing conflicting file at `dest`)
    must never be treated as something to move back.
    """

    def __init__(self, done: tuple[tuple[str, str], ...], refusal: Refused) -> None:
        super().__init__(refusal.detail)
        self.done = done
        self.refusal = refusal


def _directory(value: object, field: str) -> str:
    """Validate one end of a rule and return it canonically, or raise."""
    if not isinstance(value, str) or not value.strip():
        raise _BadRule(f"`{field}` must be a non-empty string")
    text = value.strip()
    if text.startswith("/"):
        raise _BadRule(f"`{field}: {text}` must be bundle-relative, not absolute")
    text = text.rstrip("/")
    if not text or ".." in text.split("/"):
        raise _BadRule(f"`{field}: {value}` must not contain a `..` segment")
    return text


def load_rules(path: Path) -> tuple[list[Rule], list[Refused]]:
    """Parse the rules file and validate its shape. No bundle is read.

    An unknown key is refused rather than ignored, for the reason
    `okf_ext.tags.vocabulary` gives about its own format: a silently-ignored
    key is the same silent-no-op failure class as a typo that still loads,
    just not the way the author meant.

    A missing rules file, or a path that names a directory, is an `OSError`
    out of `read_text` -- refused the same way as bad YAML, rather than a
    raw traceback, since both are "the operator handed this a bad path" and
    a caller printing `REFUSED bad-rules: ...` should not have to also
    catch what this function raises.
    """
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return [], [Refused("bad-rules", f"{path}: cannot read rules file: {exc}")]
    except YAMLError as exc:
        return [], [Refused("bad-rules", f"{path}: not valid YAML: {exc}")]

    entries = data.get("moves") if isinstance(data, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        return [], [Refused("bad-rules", f"{path}: needs a top-level `moves:` list")]

    rules: list[Rule] = []
    refusals: list[Refused] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            refusals.append(Refused("bad-rule", f"moves[{index}] is not a mapping"))
            continue
        unknown = sorted(set(entry) - {"dir", "to"})
        if unknown:
            refusals.append(Refused("bad-rule", f"moves[{index}] has unknown key(s): {', '.join(unknown)}"))
            continue
        try:
            rules.append(Rule(_directory(entry.get("dir"), "dir"), _directory(entry.get("to"), "to")))
        except _BadRule as exc:
            refusals.append(Refused("bad-rule", f"moves[{index}]: {exc}"))
    return rules, refusals


@dataclass(frozen=True)
class Expansion:
    """What the rules mean against one bundle.

    `ordinary` is the mapping handed to `plan_move_many`. `reserved` holds the
    `index.md` / `log.md` members under a rule, which this script carries
    itself -- see the module docstring.
    """

    ordinary: Mapping[str, str]
    reserved: Mapping[str, str]
    refusals: tuple[Refused, ...]


def _member_paths(bundle: Bundle) -> tuple[str, ...]:
    """Every member as a bundle-relative posix path, sorted.

    A deliberate four-line duplicate of the private
    `okf_ext.moves.plan._all_member_paths` (`plan.py:860-866`) rather than an
    import of it: a script does not reach into another package's private
    surface, and the shape is small enough that mirroring it costs less than
    the coupling would. If it drifts, `plan_move_many` refuses the mapping as
    `not-a-member` rather than moving the wrong thing.
    """
    found = {f"{cid}.md" for cid in bundle.concepts}
    found.update(bundle.assets)
    found.update(f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME for directory in bundle.indexes)
    found.update(f"{directory}/{LOG_NAME}" if directory else LOG_NAME for directory in bundle.logs)
    return tuple(sorted(found))


def _segments(directory: str) -> list[str]:
    return [canonical_id(segment) for segment in directory.split("/") if segment]


def _rest_under(head: list[str], member: str) -> str | None:
    """The remainder of *member* beneath the canonical segments *head*, or None.

    Mirrors `plan_move_dir._under_prefix` (`plan.py:801-807`) segment by
    segment through `canonical_id`. That comparison is what anchors a rule at
    the bundle root -- a `references` rule cannot reach
    `work/<item>/references/` or `sources/references/` -- and what keeps a raw
    disk id in a different Unicode normalization form than the rule matching
    anyway (ADR 2026-08-21-member-identity).
    """
    parts = member.split("/")
    if len(parts) <= len(head):
        return None
    if [canonical_id(part) for part in parts[: len(head)]] != head:
        return None
    return "/".join(parts[len(head) :])


def expand(bundle: Bundle, rules: Sequence[Rule]) -> Expansion:
    """Turn directory rules into the `{old: new}` mapping the engine takes."""
    members = _member_paths(bundle)
    claims: dict[str, list[tuple[int, str]]] = {}
    refusals: list[Refused] = []

    for index, rule in enumerate(rules):
        head = _segments(rule.directory)
        matched = 0
        for member in members:
            rest = _rest_under(head, member)
            if rest is None:
                continue
            matched += 1
            claims.setdefault(member, []).append((index, f"{rule.to}/{rest}"))
        if matched:
            continue
        # The destination check is what keeps a second run over an
        # already-moved bundle a clean no-op rather than a spurious refusal.
        target = _segments(rule.to)
        if not any(_rest_under(target, member) is not None for member in members):
            refusals.append(
                Refused(
                    "empty-rule",
                    f"`{rule.directory}` matched no member and `{rule.to}` holds none either",
                )
            )

    mapping: dict[str, str] = {}
    for member, claimed in sorted(claims.items()):
        if len({dest for _index, dest in claimed}) > 1:
            named = ", ".join(f"`{rules[index].directory}` -> `{dest}`" for index, dest in claimed)
            refusals.append(Refused("rule-overlap", f"`{member}` is claimed by {len(claimed)} rules: {named}"))
            continue
        mapping[member] = claimed[0][1]

    reserved = {source: dest for source, dest in mapping.items() if PurePosixPath(source).name in RESERVED}

    # The reserved half of C2: two reserved sources (e.g. two lane indexes)
    # claiming one destination -- merging `explanations` and `references`
    # under `docs` both name `docs/index.md`. The engine's `claimed`
    # tracking in `_validate` never sees this, because the reserved mapping
    # never reaches the engine at all; this mirrors that tracking by hand,
    # in the same shape `rule-overlap` above uses for the ordinary case.
    # Forwarded as `dest-exists` so it renders through the one channel a
    # reader already knows, whether the engine or this script produced it.
    claimed_by: dict[str, str] = {}
    for source, dest in sorted(reserved.items()):
        if dest in claimed_by:
            refusals.append(
                Refused(
                    "dest-exists",
                    f"`{dest}` is claimed by two reserved members: `{claimed_by[dest]}` and `{source}`",
                )
            )
            continue
        claimed_by[dest] = source

    ordinary = {source: dest for source, dest in mapping.items() if source not in reserved}
    return Expansion(ordinary=ordinary, reserved=reserved, refusals=tuple(refusals))


@dataclass(frozen=True)
class Prepared:
    """A dry-run preview.

    `repair` is **provisional** -- computed against the pre-move bundle, so
    its edit count is indicative. `write` recomputes it after the move,
    because a repair plan computed up front would fail `apply`'s body-digest
    check (`MovePlan.digests`, `model.py:146-152`): the root index links both
    `/explanations/index.md` and `/explanations/foo.md`, so the repair plan
    and the move plan edit the same file.
    """

    expansion: Expansion
    planning: Bundle | None
    plan: MovePlan | None
    repair: MovePlan | None

    @property
    def refusals(self) -> tuple[Refused, ...]:
        """Every refusal, rules-level and engine-level, in one shape."""
        engine = tuple(
            Refused(refusal.kind, f"`{refusal.path}`: {refusal.detail}")
            for refusal in (self.plan.refusals if self.plan is not None else ())
        )
        return (*self.expansion.refusals, *engine)

    @property
    def ok(self) -> bool:
        return not self.refusals


def prepare(root: Path, rules: Sequence[Rule]) -> Prepared:
    """Expand the rules, build the one move plan, and preview the repair.

    Writes nothing. Two loads, under two lenses: the wide one for expansion
    and for the provisional repair, and a narrower one -- the wide recipe plus
    one glob per reserved member -- for planning. An exact bundle-relative
    path is a valid `fnmatch` glob for itself, and this is the one place
    `okf_ext.moves` never reading `bundle.ignored` is used *deliberately*
    rather than guarded against: the planner then never sees a lane index as
    a referrer and never rewrites its relative entries.

    The other half of C1's guard lives here rather than in `expand`: a
    reserved destination that already exists on disk is a fact about the
    filesystem, not about the loaded `Bundle`, and `expand` only ever sees
    the latter. Checking it here -- after expansion, before planning -- keeps
    `expand` a pure function of one `Bundle` (every existing caller, direct
    and in tests, hands it a `Bundle` with no root) while still refusing
    before anything is written. A destination that is merely the same file
    already moved there is not a conflict: `expand` only populates `reserved`
    when the *source* is still a live member, so the clean second-run no-op
    (source gone, nothing to check) never reaches this loop.
    """
    try:
        lens = load_bundle(root, ignore=IGNORE)
    except OSError as exc:
        refusal = Refused("bad-bundle", f"{root}: cannot read bundle: {exc}")
        return Prepared(Expansion({}, {}, (refusal,)), None, None, None)
    expansion = expand(lens, rules)
    collisions = tuple(
        Refused("dest-exists", f"`{dest}` already exists on disk; reserved member `{source}` would overwrite it")
        for source, dest in sorted(expansion.reserved.items())
        if (root / dest).exists()
    )
    if collisions:
        expansion = Expansion(expansion.ordinary, expansion.reserved, (*expansion.refusals, *collisions))
    if expansion.refusals:
        return Prepared(expansion, None, None, None)
    try:
        planning = load_bundle(root, ignore=(*IGNORE, *sorted(expansion.reserved)))
    except OSError as exc:
        refusal = Refused("bad-bundle", f"{root}: cannot read bundle: {exc}")
        return Prepared(Expansion(expansion.ordinary, expansion.reserved, (refusal,)), None, None, None)
    plan = moves.plan_move_many(planning, expansion.ordinary)
    repair = moves.plan_repair(lens, expansion.reserved) if expansion.reserved else None
    return Prepared(expansion, planning, plan, repair)


def rename_reserved(root: Path, reserved: Mapping[str, str]) -> list[str]:
    """Rename each reserved member, creating parents, then prune what emptied.

    A lane index's entries are **relative**, and its siblings move with it, so
    it needs no content edit at all -- only `okf_ext.moves` declines to be the
    thing that renames it. `Path.replace` is the whole operation.

    Belt and braces on top of `prepare`'s C1/C2 guards: `Path.replace` is
    *defined* to destroy an existing destination silently, so this checks
    `destination.exists()` itself rather than trusting that every caller
    ran the dry-run check first. It also catches every other `OSError` a
    rename can raise (a parent segment that is itself a file, a destination
    that is a directory) rather than letting one propagate out of a
    half-completed batch. Either way it raises `_RenameFailed` carrying the
    `(source, dest)` pairs that already landed, so `write` can roll back
    exactly those and nothing it never touched.

    The prune is this script's job rather than `apply`'s: the lane index is
    still sitting in the source directory while the ordinary batch commits, so
    `apply`'s regime-4 prune finds the directory non-empty and leaves it.
    Best-effort and bottom-up, with no failure path -- a directory that will
    not go is a directory something else still wants.
    """
    done: list[tuple[str, str]] = []
    for source, dest in sorted(reserved.items()):
        destination = root / dest
        if destination.exists():
            refusal = Refused(
                "dest-exists", f"`{dest}` already exists on disk; reserved member `{source}` would overwrite it"
            )
            raise _RenameFailed(tuple(done), refusal)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            (root / source).replace(destination)
        except OSError as exc:
            refusal = Refused("bad-rename", f"`{source}` -> `{dest}`: {exc}")
            raise _RenameFailed(tuple(done), refusal) from exc
        done.append((source, dest))

    pruned: list[str] = []
    for source in sorted(reserved, reverse=True):
        directory = (root / source).parent
        while directory != root:
            try:
                directory.rmdir()
            except OSError:
                break
            pruned.append(directory.relative_to(root).as_posix())
            directory = directory.parent
    return sorted(pruned)


def unrename_reserved(root: Path, reserved: Mapping[str, str]) -> None:
    """Move each reserved member back from `dest` to `source`. The inverse of
    `rename_reserved`, used only when the repair plan recomputed after the
    rename is refused.

    Exists so the doc's recovery promise is true: with the index back under
    its *source* directory, `expand` reclassifies it as reserved on the next
    `prepare`, the same mapping is recomputed, and the move completes once
    the operator has fixed the offending reference -- rather than leaving the
    index permanently stranded at `dest`, unreachable by any rule again.

    Best-effort, like the prune half of `rename_reserved`: a member that
    cannot be moved back is left where it is rather than raising, because the
    refusals already being returned are the real diagnosis and must not be
    masked by a rollback failure. That includes an origin that already
    exists: `Path.replace` would silently destroy it, and an origin
    reappearing mid-rollback (something else recreated it) is exactly the
    kind of surprise this best-effort pass must not compound -- it skips
    that entry rather than clobbering whatever is now sitting there.
    """
    for source, dest in sorted(reserved.items()):
        destination = root / dest
        if not destination.is_file():
            continue
        origin = root / source
        if origin.exists():
            continue
        try:
            origin.parent.mkdir(parents=True, exist_ok=True)
            destination.replace(origin)
        except OSError:
            continue


@dataclass(frozen=True)
class Applied:
    """What landed. `repair` is `None` when no reserved member was carried."""

    move: MoveResult
    pruned: tuple[str, ...]
    repair: MoveResult | None
    refusals: tuple[Refused, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals and self.move.ok and (self.repair is None or self.repair.ok)


def write(root: Path, prepared: Prepared) -> Applied:
    """Apply the four steps in order: move, rename, **reload**, repair.

    The reload is load-bearing, not tidiness. The root index links both
    `/explanations/index.md` and `/explanations/foo.md`, so the repair plan
    and the move plan edit the same file; a repair plan computed before the
    move fails `apply`'s body-digest check (`MovePlan.digests`,
    `model.py:146-152`). `Prepared.repair` is therefore provisional and is
    recomputed here.

    If the recomputed repair plan carries refusals, the reserved renames are
    rolled back to their source paths (`unrename_reserved`) before the
    refusals are returned -- the ordinary move stays applied, since it
    succeeded on its own terms; only the reserved half, whose repair failed,
    is undone. The bundle is **not** left fully consistent by this: the
    ordinary move already rewrote every OKF reference *into* the reserved
    member's now-stale source path (the root index, for instance, now links
    `/explanations/index.md` through whatever new base the ordinary rewrite
    computed), and the lane index sitting back at its source still carries
    its original **relative** entries, now pointing at siblings that live
    under the new base. Both dangle until a re-run completes the move --
    which the non-zero exit code makes visible, and which `expand`
    reclassifying the rolled-back index as reserved again makes automatic.
    See `scripts/move-bundle.md`.

    `rename_reserved` can itself fail partway -- a reserved destination that
    exists despite `prepare`'s check (a race, or a caller that skipped it),
    or an `OSError` from a filesystem shape `mkdir`/`replace` cannot handle
    (a parent segment that is a file, a destination that is a directory).
    Either raises `_RenameFailed` carrying exactly the `(source, dest)`
    pairs that already landed, so the rollback here undoes only those --
    never a pre-existing conflicting file this call never touched.
    """
    if prepared.planning is None or prepared.plan is None:
        raise ValueError("write() needs a prepared plan; check `Prepared.ok` first")

    result = moves.apply(prepared.planning, prepared.plan)
    reserved = prepared.expansion.reserved
    if not reserved or not result.ok:
        return Applied(result, (), None, ())

    try:
        pruned = tuple(rename_reserved(root, reserved))
    except _RenameFailed as exc:
        if exc.done:
            unrename_reserved(root, dict(exc.done))
        return Applied(result, (), None, (exc.refusal,))

    after = load_bundle(root, ignore=IGNORE)
    repair_plan = moves.plan_repair(after, reserved)
    if not repair_plan.ok:
        unrename_reserved(root, reserved)
        refusals = tuple(Refused(r.kind, f"`{r.path}`: {r.detail}") for r in repair_plan.refusals)
        return Applied(result, (), None, refusals)
    return Applied(result, pruned, moves.apply(after, repair_plan), ())


def render(prepared: Prepared) -> list[str]:
    """The plan, as lines. Writes nothing.

    Shape borrowed from `doc_wiki_okf.archive.ArchivePlan.diff`: refusals
    first with a leading `!`-equivalent, then moves, then reference edits,
    then the one `stranded_summary` line.
    """
    lines = [f"REFUSED {refusal.kind}: {refusal.detail}" for refusal in prepared.refusals]
    plan = prepared.plan
    if plan is not None:
        lines.extend(f"  {move.source} -> {move.dest}" for move in plan.moves)
        lines.extend(f"  ~ {edit.member}: {edit.old} -> {edit.new}" for edit in plan.edits)
        if plan.stranded:
            lines.append(moves.stranded_summary(plan.stranded))
    lines.extend(f"  [reserved] {source} -> {dest}" for source, dest in sorted(prepared.expansion.reserved.items()))
    if prepared.repair is not None:
        lines.append(f"  [reserved] {len(prepared.repair.edits)} provisional repair edit(s), recomputed on --write")
    return lines


def _counts(prepared: Prepared) -> str:
    plan = prepared.plan
    moved = len(plan.moves) if plan is not None else 0
    edits = len(plan.edits) if plan is not None else 0
    marooned = len(plan.stranded) if plan is not None else 0
    reserved = len(prepared.expansion.reserved)
    return f"{moved} moves, {edits} reference edits, {reserved} reserved, {marooned} stranded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("bundle", type=Path, help="the OKF bundle directory (e.g. <workspace>/okf)")
    parser.add_argument("--rules", type=Path, required=True, help="the YAML rules file")
    parser.add_argument("--write", action="store_true", help="apply; the default is a dry run")
    args = parser.parse_args(argv)

    rules, refusals = load_rules(args.rules)
    if refusals:
        for refusal in refusals:
            print(f"REFUSED {refusal.kind}: {refusal.detail}")
        return 1

    prepared = prepare(args.bundle, rules)
    for line in render(prepared):
        print(line)
    counts = _counts(prepared)
    if not prepared.ok:
        print(f"{counts} -- refused, nothing written")
        return 1
    if not args.write:
        print(f"{counts} -- dry run")
        return 0

    applied = write(args.bundle, prepared)
    for failure in applied.move.failed:
        print(f"FAILED {failure.path}: {failure.kind} -- {failure.error}")
    if applied.repair is not None:
        for failure in applied.repair.failed:
            print(f"FAILED {failure.path}: {failure.kind} -- {failure.error}")
    for refusal in applied.refusals:
        print(f"REFUSED {refusal.kind}: {refusal.detail}")
    if not applied.ok:
        print(f"{counts} -- attempted, see failures")
        return 1
    print(f"{counts} -- written")
    if applied.repair is not None:
        print(f"{len(applied.repair.written)} reserved-member referrer(s) repaired, {len(applied.pruned)} pruned")
    print("\nNext:")
    print("  gw wiki index")
    print("  gw wiki lint")
    print("  append to okf/log.md:")
    assert prepared.plan is not None
    print(f"    - **update** moved {len(prepared.plan.moves)} page(s) per the rules file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
