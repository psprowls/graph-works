"""Rewrite an old-dialect proposal into `okf_ext.proposals` shape.

**A pure frontmatter rewriter.** It never touches a body, never needs a
`LaneSet`, and never moves a file itself.

**The trigger is `target_slug`.** A document is old-dialect exactly when its
frontmatter carries `target_slug` and does not carry `type`. `okf_io.migrate`'s
trigger is membership in `doc.fm.fallbacks` -- the same set `_rules/legacy.py`
keys off -- so reader, validator and writer can never disagree about what
counts as v0.1; this takes the same posture with the one key available to it.
`target_slug` is the only key in the old dialect with no successor and no other
meaning anywhere in the vault. A document carrying `type: Proposal` **already**
is skipped, not refused: that is what makes re-running an empty plan rather
than an error.

**Scope is the whole bundle**, not `proposals/`. The trigger is content-based,
so the migrator never asks where a proposal *lives*, only what it *says* -- and
a stray old-dialect proposal outside `proposals/` migrates correctly and is
re-placed within its own directory.

**The body is carried verbatim, structurally.** The rewrite is key surgery on
the parsed original -- `set` the new keys, `delete` the old ones, `serialize()`
-- never a freshly rendered document. So the body's bytes, its newline dialect
and any BOM survive without a single assertion needed to defend them, and
`title` keeps its original quoting and position. okf-io's byte-fidelity
contract does the work rather than being re-implemented.

**Re-placement is computed here and performed by `okf_ext.moves`**, which buys
inbound reference repair for free and keeps this module inside its own
competence. The order is rewrite, reload, move -- the plan's writes address the
paths the documents are at when the plan is computed.

Rejected: re-rendering each body through `ReviewRenderer`. It would repair two
real staleness problems (the `## Suggested Action` verb, the legacy wikilinks
in Origins) but would also overwrite the archived proposals' bodies, which the
capability considers human-owned once `page_status` leaves `proposed`. The
stale wording is repaired the next time a source merges, through the renderer
that owns it.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from okf_ext import moves
from okf_ext.moves import MovePlan, MoveResult
from okf_ext.proposals import PAGE_STATUSES, PROPOSAL_TYPE, proposal_path
from okf_ext.writing import ApplyResult, PendingWrite, WriteFailure, body_digest, write_all
from okf_io import Bundle, Document, load_bundle

#: The old dialect's trigger key -- present, with no `type`, means old.
TRIGGER_KEY = "target_slug"

#: The `type` key whose presence means a document has already been migrated.
MIGRATED_KEY = "type"

#: `kind` -> the directory its `target_slug` resolves under. `concept` is here
#: on purpose: `docs/cutover-key-mapping.md` says `concept` has no successor,
#: but that is a statement about the `--json` **lane** field, not about the
#: target. `target` is a path, and a proposal's own type is `Proposal`
#: regardless of what it argues for. Promoting a `concepts/` target needs a
#: lane this workspace deliberately does not declare yet; migrating one does
#: not.
TARGET_DIRECTORIES: Mapping[str, str] = {"adr": "adrs/", "concept": "concepts/"}

#: Keys the rewrite removes. `mode` is derived from bundle membership and never
#: stored; `tokens` is a document-level producer count with no home in
#: `sources[]` (attaching it to `sources[0]` would make its ownership an
#: artifact of file order); `origins` is replaced by `sources`.
DROPPED_KEYS = ("kind", "mode", TRIGGER_KEY, "status", "tokens", "origins")

#: Origin keys with no successor. `ref` becomes `resource`; `source: ingest` is
#: a producer tag with no home in `sources[]` -- C3's ruling in
#: `docs/cutover-key-mapping.md`. Every other key rides through verbatim.
DROPPED_ORIGIN_KEYS = frozenset({"ref", "source"})

#: Why one document was declined. Closed and small: every refusal the migrator
#: can produce is one of these.
RefusalKind = Literal[
    "unreadable",
    "unknown-kind",
    "unrecognised-status",
    "unresolvable-target",
]


@dataclass(frozen=True, slots=True)
class Refusal:
    """One document declined, and why. All-or-nothing for that document."""

    member: str  # bundle-relative posix
    kind: RefusalKind
    detail: str


@dataclass(frozen=True, slots=True)
class MigrationWrite:
    """One document rewritten in place, and where the move step puts it after.

    `member` is the **old** path: the rewrite happens where the document
    already sits, and relocation is a separate step through `okf_ext.moves`.
    `digest` is `body_digest` of the body this was planned against, which
    `apply` refuses a drifted document against.
    """

    member: str
    text: str  # the whole file, exactly as it will land
    digest: str
    placement: str  # where the move step puts it afterwards


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A preview you can inspect and filter before anything is written.

    A value rather than a `dry_run=` flag, per
    `adrs/tier-2-writers-return-plans-not-dry-run-flags` and matching
    `plan_file`, `plan_promote`, `MovePlan` and `RegenerationPlan`.

    **`ok` is informational, not a gate.** `apply` deliberately does not check
    it, unlike `moves.apply` which raises on a non-ok plan. That guard is right
    there and wrong here: `moves` computes every edit against the whole
    mapping, so dropping one bad move would apply edits computed against a
    mapping that is no longer the one being applied. Nothing here is computed
    across documents, so a refused document simply contributes no write and the
    others still land. Refusals are always reported, never silent.
    """

    root: Path
    writes: tuple[MigrationWrite, ...]
    refusals: tuple[Refusal, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.writes

    @property
    def placements(self) -> Mapping[str, str]:
        """Old member -> new member, ready for `moves.plan_move_many`.

        **Intra-batch slug collisions are `moves`' to arbitrate.**
        `placement` resolves a collision against the bundle as it stands, which
        cannot see the other destinations this same plan claims. Two proposals
        whose targets slug alike therefore produce one duplicated destination
        and `plan_move_many` refuses it as `dest-exists`, naming both -- which
        is the honest place for it, since destination arbitration is that
        planner's job and not this one's. None of the 13 live proposals
        collide.
        """
        return {write.member: write.placement for write in self.writes}


def _require_aware(at: datetime) -> str:
    """*at* as an ISO-8601 stamp, refusing a naive datetime.

    Only `cli.py` reads the clock in this package, which is okf-io's own rule
    for `validate()` and `append_log_entry`. A naive instant written into
    `generated.at` is a value okf-io cannot coerce, so it raises rather than
    landing a `provenance.*` finding on every document this writes -- the call
    `okf_ext.proposals` makes, for the same reason.
    """
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(
            f"`at` must be timezone-aware, got {at!r}. Only `cli.py` reads the clock: the caller supplies "
            f"the instant, and a naive one lands in `generated.at` as a value okf-io cannot coerce."
        )
    return at.isoformat()


def _parent(member: str) -> str:
    """The directory *member* sits in, `""` at the bundle root."""
    parent = PurePosixPath(member).parent.as_posix()
    return "" if parent == "." else parent


def _target(kind: str, slug: str) -> str:
    """`{concepts|adrs}/<slug>.md`, or `""` when the slug is unusable.

    Refused rather than repaired: a slug that is blank, that climbs, or that
    carries an empty segment does not name a page anyone can point at, and
    guessing one would be prose surgery by heuristic.
    """
    cleaned = slug.strip().replace("\\", "/").strip("/")
    if not cleaned or any(part in {"", ".", ".."} for part in cleaned.split("/")):
        return ""
    return f"{TARGET_DIRECTORIES[kind]}{cleaned}.md"


def _source(entry: Mapping[str, Any]) -> dict[str, Any]:
    """One `origins[]` entry as a `sources[]` entry.

    `resource` is the ref with `.md` appended when it carries no extension --
    `resource` in OKF means a bundle member path, which is what `_merge_sources`
    dedups on. All 19 live refs are extensionless `sources/...` paths.

    `id` is the resource's stem. `okf_ext.proposals` dedups by `resource` and
    never by `id`, so it is optional -- but `ReviewRenderer._label` falls back
    `title` -> `id` -> `resource`, so omitting it would make every Origins
    heading print a full path. A blank ref carries neither key: tolerance, not
    a refusal -- content is never an exception on this path.
    """
    ref = str(entry.get("ref") or "").strip()
    carried = {key: value for key, value in entry.items() if key not in DROPPED_ORIGIN_KEYS}
    if not ref:
        return carried
    resource = ref if PurePosixPath(ref).suffix else f"{ref}.md"
    return {"id": PurePosixPath(resource).stem, "resource": resource, **carried}


def _sources(value: Any) -> list[dict[str, Any]]:  # noqa: ANN401 -- reads an arbitrary raw YAML value
    """*value* as `sources[]`. A non-list, or a non-mapping element, is content
    to tolerate rather than an error to raise on -- okf-io's own rule."""
    if not isinstance(value, list):
        return []
    return [_source(entry) for entry in value if isinstance(entry, Mapping)]


def _rewrite(
    document: Document,
    *,
    target: str,
    page_status: str,
    sources: Sequence[Mapping[str, Any]],
    by: str,
    stamp: str,
) -> str:
    """*document* with its keys swapped, rendered. Key surgery, not a re-render.

    A private scratch copy -- `copy.deepcopy` of `fm_raw` onto a
    `dataclasses.replace` clone -- so planning leaves the shared in-memory
    `Bundle` exactly as it found it. `okf_ext.proposals.apply._rendered` makes
    the same copy for the same reason.

    Deleted first, then set: `Document.set` computes a new key's position from
    the keys present, so removing the old ones first is what makes the result
    `PREFERRED_KEY_ORDER`'s -- `type, title, description, generated, sources`,
    then the two extension keys the core does not name. `title` is never
    touched, so it keeps its original quoting and position; so does any key the
    machine does not name.
    """
    scratch = replace(document, fm_raw=copy.deepcopy(document.fm_raw))
    for key in DROPPED_KEYS:
        scratch.delete(key)
    scratch.set(MIGRATED_KEY, PROPOSAL_TYPE)
    scratch.set("description", "")
    scratch.set("generated", {"by": by, "at": stamp})
    scratch.set("sources", [dict(source) for source in sources])
    scratch.set("target", target)
    scratch.set("page_status", page_status)
    return scratch.serialize()


def _refuse(member: str, kind: RefusalKind, detail: str) -> Refusal:
    return Refusal(member=member, kind=kind, detail=detail)


def _plan_one(
    bundle: Bundle, member: str, document: Document, *, by: str, stamp: str
) -> MigrationWrite | Refusal | None:
    """One document's outcome: a write, a refusal, or `None` for "not mine".

    Checked in the order `RefusalKind` declares, first match winning, because a
    refusal is all-or-nothing per document: there is no half-migrated proposal.
    """
    if document.parse_error is not None:
        # `fm_raw` is `{}` for every parse error, so the trigger cannot fire
        # from the frontmatter. Scanning the raw text is allowed to do exactly
        # one thing here -- turn a hidden old-dialect proposal into a refusal
        # instead of a silent skip -- and never to place an edit; nothing can
        # safely rewrite a document that does not parse. `moves`'
        # `_mentions_moved_set` makes the same narrow call.
        if TRIGGER_KEY in document.raw_text:
            return _refuse(
                member,
                "unreadable",
                f"mentions `{TRIGGER_KEY}` but failed to parse "
                f"({document.parse_error.kind}): {document.parse_error.message}",
            )
        return None

    data = document.fm_data()
    if TRIGGER_KEY not in data or MIGRATED_KEY in data:
        return None

    kind = str(data.get("kind") or "").strip()
    if kind not in TARGET_DIRECTORIES:
        return _refuse(member, "unknown-kind", f"`kind` is {kind!r}; expected one of {sorted(TARGET_DIRECTORIES)}")

    page_status = str(data.get("status") or "").strip()
    if page_status not in PAGE_STATUSES:
        return _refuse(
            member, "unrecognised-status", f"`status` is {page_status!r}; expected one of {list(PAGE_STATUSES)}"
        )

    target = _target(kind, str(data.get(TRIGGER_KEY) or ""))
    if not target:
        return _refuse(
            member,
            "unresolvable-target",
            f"`{TRIGGER_KEY}` is {data.get(TRIGGER_KEY)!r}, which names no page inside the bundle",
        )

    sources = _sources(data.get("origins"))
    return MigrationWrite(
        member=member,
        text=_rewrite(document, target=target, page_status=page_status, sources=sources, by=by, stamp=stamp),
        digest=body_digest(document.body),
        placement=proposal_path(bundle, target, directory=_parent(member)),
    )


def plan_migrate(bundle: Bundle, *, by: str, at: datetime) -> MigrationPlan:
    """Plan rewriting every old-dialect proposal in *bundle*. Writes nothing.

    `by` and `at` stamp `generated`; both are required, because this package
    never reads the clock outside `cli.py`.
    """
    stamp = _require_aware(at)
    writes: list[MigrationWrite] = []
    refusals: list[Refusal] = []

    for concept_id in sorted(bundle.concepts):
        outcome = _plan_one(bundle, f"{concept_id}.md", bundle.concepts[concept_id], by=by, stamp=stamp)
        if isinstance(outcome, MigrationWrite):
            writes.append(outcome)
        elif isinstance(outcome, Refusal):
            refusals.append(outcome)

    return MigrationPlan(root=bundle.root, writes=tuple(writes), refusals=tuple(refusals))


@dataclass(frozen=True, slots=True)
class MigrationOutcome:
    """What the whole rewrite-then-move sequence did.

    Four results rather than one merged summary, because the four steps fail
    for different reasons and a caller acting on the outcome needs to know
    which one did: a `stale` in `rewrite` is re-planned, a `dest-exists` in
    `move_plan` is a mapping to fix, an `unlink-error` in `move` leaves an
    orphan copy to clean up.
    """

    plan: MigrationPlan
    rewrite: ApplyResult
    move_plan: MovePlan
    move: MoveResult

    @property
    def ok(self) -> bool:
        return self.plan.ok and self.rewrite.ok and self.move_plan.ok and self.move.ok


def apply(bundle: Bundle, plan: MigrationPlan) -> ApplyResult:
    """Write *plan* against *bundle*. Rewrites in place; moves nothing.

    **It does not gate on `plan.ok`**, and that is the one place this
    deliberately departs from `moves.apply`, which raises `ValueError` on a
    non-ok plan. That guard is right there because `moves` computes every edit
    against the whole mapping, so applying a filtered subset would apply edits
    computed against a mapping that is no longer the one being applied. Nothing
    here is computed across documents: a refused document simply contributed no
    `MigrationWrite`, its neighbours still land, and `MigrationPlan.ok` is the
    signal a caller reports rather than a gate it must pass.

    The two I/O regimes -- probe/staging all-or-nothing, and per-document
    commit -- belong to `okf_ext.writing.write_all` and are inherited
    unchanged. Content failures are refused per document: `not-a-member`,
    `parse-error` and `stale`, every one already a member of `FailureKind`.

    **It does not update the in-memory `Bundle`.** A whole-file rewrite would
    need the document re-parsed to stay coherent, and the caller reloads
    anyway before the move step -- the same call `okf_ext.proposals.apply` and
    `moves.apply` both document for their own writes.

    Raises `ValueError` for a plan built against a different bundle: its
    digests mean nothing anywhere else.
    """
    if Path(plan.root).resolve() != Path(bundle.root).resolve():
        raise ValueError(
            f"Plan was built against a different bundle ({plan.root}), not {bundle.root}. "
            f"A plan's digests mean nothing outside the bundle it was planned against."
        )

    failed: list[WriteFailure] = []
    pending: list[PendingWrite] = []

    for write in plan.writes:
        concept_id = write.member[: -len(".md")] if write.member.endswith(".md") else write.member
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
                        f"cannot rewrite a document that failed to parse "
                        f"({document.parse_error.kind}): {document.parse_error.message}"
                    ),
                )
            )
            continue
        if body_digest(document.body) != write.digest:
            failed.append(
                WriteFailure(
                    path=write.member,
                    kind="stale",
                    error="stale plan: the body changed since it was planned; re-plan against the current bundle",
                )
            )
            continue
        pending.append(
            PendingWrite(
                member=write.member,
                path=document.path,
                rendered=write.text,
                on_written=lambda: None,
            )
        )

    return write_all(pending, failed=failed)


def migrate_and_move(root: Path, *, by: str, at: datetime, ignore: Sequence[str] = ()) -> MigrationOutcome:
    """The whole sequence: rewrite, reload, move.

    Rewrite-then-move, in that order, because the plan's writes address the
    paths the documents are at when the plan is computed. The reload between
    the two steps is required: `apply` does not update the in-memory `Bundle`,
    and neither does `moves.apply` -- both document that the caller reloads.

    Performing the move is `okf_ext.moves`' job rather than this module's,
    which buys inbound reference repair for free and keeps the migrator inside
    its own competence. A move step is skipped entirely when the rewrite
    landed nothing (an all-ready-migrated bundle, most plausibly), so a re-run
    reports four empty results rather than paying for a no-op move plan.

    When the move step *is* attempted but `move_plan` comes back refused --
    two migrated proposals resolving to the same destination is the one way
    this can happen, since `proposal_path()` is computed per document against the
    bundle as it stood before any of this batch's writes landed -- `moves.apply`
    is never called: it raises on a non-ok plan, and this function reports the
    refusal through `move_plan.refusals` instead of letting that exception
    propagate.
    """
    bundle = load_bundle(root, ignore=tuple(ignore))
    plan = plan_migrate(bundle, by=by, at=at)
    rewrite = apply(bundle, plan)

    landed = {member: plan.placements[member] for member in rewrite.written if member in plan.placements}
    if not landed:
        empty_plan = MovePlan(root=Path(root), moves=(), edits=(), refusals=(), unrebased=(), digests={})
        empty_result = MoveResult(moved=(), written=(), failed=(), pruned=())
        return MigrationOutcome(plan=plan, rewrite=rewrite, move_plan=empty_plan, move=empty_result)

    reloaded = load_bundle(root, ignore=tuple(ignore))
    move_plan = moves.plan_move_many(reloaded, landed)
    if not move_plan.ok:
        return MigrationOutcome(
            plan=plan,
            rewrite=rewrite,
            move_plan=move_plan,
            move=MoveResult(moved=(), written=(), failed=(), pruned=()),
        )
    return MigrationOutcome(plan=plan, rewrite=rewrite, move_plan=move_plan, move=moves.apply(reloaded, move_plan))


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "DROPPED_KEYS",
    "DROPPED_ORIGIN_KEYS",
    "MIGRATED_KEY",
    "TARGET_DIRECTORIES",
    "TRIGGER_KEY",
    "MigrationOutcome",
    "MigrationPlan",
    "MigrationWrite",
    "Refusal",
    "RefusalKind",
    "apply",
    "migrate_and_move",
    "plan_migrate",
]
