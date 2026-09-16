"""Rules for parent-capable and self-owned lone-item decision ledgers (D-001).

The fifth topic. Its prefix had to clear eighteen taken names — okf-io's eight
(`computation`, `frontmatter`, `legacy`, `lifecycle`, `links`, `provenance`,
`reserved`, `trust`), okf-ext's six (`health`, `placement`, `render`, `schemas`,
`sections`, `tags`) and this lane's own four (`graph`, `plan`, `state`,
`targets`) — and `decisions` clears all of them.

`RuleContext` carries no filesystem, but `Bundle` carries a root, and the ledger
is a bundle member rather than a repo path: `repo_root` injection exists because
the *code repo* is unknown to a bundle, not because a rule may not do I/O
(`targets.affects-missing` is an `.exists()` call). So this topic injects
nothing. `ledger-missing` does no I/O at all — `ctx.bundle.has_member` sees
ignored members under `references/` — and the content rules read
ledger members plus validated, bundle-contained checkpoint paths.

`_SPEC` cites this module rather than a spec section: a parent whose ledger has
a gap is a perfectly conformant OKF v0.2 document, and inventing a section
number for a lane invariant would be a false citation that outlives whoever
wrote it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from okf_io import Finding, Rule, RuleContext, Severity

from work_tracker_okf import checkpoints
from work_tracker_okf._rules._common import LaneConfig, active, items
from work_tracker_okf.decisions import HOLD_PHASES, HOLD_SHAPES, VALID_STATUSES, Decision, LedgerParse, id_number, load
from work_tracker_okf.hierarchy import decision_owner
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import MANAGED_ARTIFACTS, ArtifactRef, artifact_ref, checkpoint_ref, parse_item_path
from work_tracker_okf.vocabulary import PARENT_TYPES, SPEC_SOURCE_ID

CODES: tuple[str, ...] = (
    "decisions.ledger-missing",
    "decisions.entry-invalid",
    "decisions.cite-missing",
    "decisions.open-at-finish",
    "decisions.supersedes-invalid",
    "decisions.hold-invalid",
    "decisions.hold-phase-stale",
    "decisions.checkpoint-invalid",
)

_SPEC = "work_tracker_okf._rules.decisions"

#: The phases by which a parent that has moved past design has a ledger.
_LEDGER_PHASES = frozenset({"plan", "execute", "finish", "done"})

#: A `D-nnn` citation anywhere in a design spec. Deliberately bare: a matcher
#: restricted to fenced or bracketed ids would miss the prose citation this rule
#: exists to catch. The known cost, recorded rather than solved, is that a
#: document *about* the ledger format trips on its own illustrative ids — loud,
#: rare, and confined to documents about the ledger itself.
_ID_RE = re.compile(r"\bD-\d+\b")

#: Parser warnings the explicit sub-checks already surface at `error`. Filtered
#: out of the `warn` passthrough, so one bad entry is one root cause and one
#: finding, at the higher severity.
_ALREADY_REPORTED = ("invalid status", "missing status", "duplicate id", "invalid hold", "invalid phase")


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    """Every code in this module reports the epic's (or the citing item's) page:
    the ledger has no page of its own, and a finding against a member nothing
    links to is a finding nobody would find."""
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.page_path, line=None)


def _ledger_ref(item_path: str) -> ArtifactRef:
    return artifact_ref(item_path, MANAGED_ARTIFACTS["decisions"])


def _entry_findings(item: WorkItem, parsed: LedgerParse) -> Iterator[Finding]:
    """33: explicit metadata checks at `error`, then residual warnings at `warn`."""
    seen: set[int] = set()
    numbers: list[int] = []
    for entry in parsed.entries:
        numbers.append(entry.number)
        # Hold-shaped entries report phase errors through `_hold_findings`.
        # Questions need a replacement too before we suppress parser warnings.
        if not _is_hold_shaped(entry) and entry.phase is not None and entry.phase not in HOLD_PHASES:
            yield _finding(
                "decisions.entry-invalid",
                "error",
                item,
                f"decision {entry.id}: phase {entry.phase!r} not in {sorted(HOLD_PHASES)}",
            )
        if entry.status not in VALID_STATUSES:
            # `parse` blanks the status when the raw text was missing or
            # unrecognized, so the raw value is not recoverable here — reuse the
            # parser's own warning, which carries it, rather than printing the
            # blank.
            detail = next(
                (
                    warning.split(": ", 1)[1]
                    for warning in parsed.warnings
                    if warning.startswith(f"{entry.id}: ")
                    and ("invalid status" in warning or "missing status" in warning)
                ),
                None,
            )
            message = (
                f"decision {entry.id}: {detail}"
                if detail is not None
                else f"decision {entry.id} has an invalid or missing status not in {sorted(VALID_STATUSES)}"
            )
            yield _finding("decisions.entry-invalid", "error", item, message)
        if entry.number in seen:
            yield _finding(
                "decisions.entry-invalid", "error", item, f"decision id {entry.id!r} is duplicated in the ledger"
            )
        seen.add(entry.number)
    if numbers:
        missing = sorted(set(range(min(numbers), max(numbers) + 1)) - set(numbers))
        if missing:
            yield _finding(
                "decisions.entry-invalid",
                "error",
                item,
                f"decision id sequence has a gap: {missing!r} missing between "
                f"D-{min(numbers):03d} and D-{max(numbers):03d}",
            )
    for warning in parsed.warnings:
        if any(marker in warning for marker in _ALREADY_REPORTED):
            continue
        yield _finding("decisions.entry-invalid", "warn", item, f"ledger parse warning: {warning}")


def _supersedes_findings(item: WorkItem, parsed: LedgerParse) -> Iterator[Finding]:
    """36: resolved by **number**, not string.

    It mirrors `decisions.id_number`'s padded/unpadded unification, so a
    hand-typed `supersedes: D-1` still resolves against the zero-padded entry it
    names instead of reporting a phantom dangling reference.
    """
    by_number = {entry.number: entry for entry in parsed.entries}
    for entry in parsed.entries:
        if not entry.supersedes:
            continue
        try:
            target = by_number.get(id_number(entry.supersedes))
        except ValueError:
            target = None
        if target is None:
            yield _finding(
                "decisions.supersedes-invalid",
                "error",
                item,
                f"decision {entry.id} supersedes {entry.supersedes!r}, which does not exist in the ledger",
            )
        elif target.status != "superseded":
            yield _finding(
                "decisions.supersedes-invalid",
                "error",
                item,
                f"decision {entry.id} supersedes {entry.supersedes!r} but that entry's status is "
                f"{target.status!r}, not 'superseded'",
            )


def _is_hold_shaped(entry: Decision) -> bool:
    return entry.hold is not None or entry.checkpoint is not None


def _hold_findings(
    ctx: RuleContext, item: WorkItem, parsed: LedgerParse, by_path: dict[str, WorkItem]
) -> Iterator[Finding]:
    for entry in parsed.entries:
        if not _is_hold_shaped(entry):
            continue
        problems: list[str] = []
        if entry.hold is not None and entry.hold not in HOLD_SHAPES:
            problems.append(f"hold {entry.hold!r} not in {sorted(HOLD_SHAPES)}")
        if entry.hold == "park" and not entry.checkpoint:
            problems.append("a park carries no checkpoint")
        if entry.hold == "skip" and entry.checkpoint:
            problems.append("a skip carries a checkpoint")
        if len(entry.affects) != 1:
            problems.append(f"a hold names exactly one item, not {len(entry.affects)}")
        if entry.phase is None or entry.phase not in HOLD_PHASES:
            problems.append(f"phase {entry.phase!r} not in {sorted(HOLD_PHASES)}")
        elif entry.hold == "park" and entry.phase == "entry":
            problems.append("a park cannot sit at entry")
        for problem in problems:
            yield _finding("decisions.hold-invalid", "error", item, f"decision {entry.id}: {problem}")
        target = by_path.get(entry.affects[0]) if len(entry.affects) == 1 else None
        if (
            entry.status == "open"
            and entry.hold is not None
            and target is not None
            and entry.phase in HOLD_PHASES
            and (target.phase or "entry") != entry.phase
        ):
            yield _finding(
                "decisions.hold-phase-stale",
                "warn",
                item,
                f"open decision {entry.id} holds {target.path} at {entry.phase!r}, "
                f"but it is at {target.phase or 'entry'!r}",
            )
        if entry.checkpoint:
            yield from _checkpoint_findings(ctx, item, entry)


def _checkpoint_findings(ctx: RuleContext, item: WorkItem, entry: Decision) -> Iterator[Finding]:
    """Validate content-derived identity and containment before opening a file."""
    try:
        if len(entry.affects) != 1 or parse_item_path(entry.affects[0]) is None:
            raise ValueError("checkpoint requires exactly one valid item path")
        ref = checkpoint_ref(entry.affects[0], entry.phase or "", entry.id)
        if entry.checkpoint != ref.resource:
            raise ValueError(f"checkpoint resource {entry.checkpoint!r} must be {ref.resource!r}")
        root = ctx.bundle.root.resolve()
        path = ref.path(root).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"checkpoint {entry.checkpoint!r} resolves outside the bundle")
    except (OSError, RuntimeError, ValueError) as error:
        yield _finding("decisions.checkpoint-invalid", "error", item, f"decision {entry.id}: {error}")
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        yield _finding(
            "decisions.checkpoint-invalid",
            "error",
            item,
            f"decision {entry.id}: checkpoint {entry.checkpoint!r} is missing or unreadable",
        )
        return
    problems = checkpoints.validate(
        checkpoints.parse(text),
        item_path=entry.affects[0],
        phase=entry.phase or "",
        decision_id=entry.id,
    )
    for problem in problems:
        yield _finding("decisions.checkpoint-invalid", "error", item, f"decision {entry.id}: checkpoint {problem}")


def ledger(ctx: RuleContext) -> Iterable[Finding]:
    """32-36 (except citations), plus hold-invalid, hold-phase-stale and
    checkpoint-invalid: one ledger read per active owner.

    Parent-capable items and existing self-owned lone-item ledgers are checked.
    Only parents require a ledger; archived pages and their ledgers are frozen.
    """
    by_path = {entry.path: entry for entry in items(ctx)}
    for item in active(ctx):
        ref = _ledger_ref(item.path)
        if item.type not in PARENT_TYPES and not ctx.bundle.has_member(ref.rel):
            continue
        if item.type in PARENT_TYPES and item.phase in _LEDGER_PHASES and not ctx.bundle.has_member(ref.rel):
            subject = "`type: Epic`" if item.type == "Epic" else f"`type: {item.type}`"
            yield _finding(
                "decisions.ledger-missing",
                "warn",
                item,
                f"{subject} at `phase: {item.phase}` has no decisions ledger at `{ref.rel}`",
            )
        parsed = load(ref.path(ctx.bundle.root))
        yield from _entry_findings(item, parsed)
        open_ids = [entry.id for entry in parsed.entries if entry.status == "open"]
        if item.phase == "finish" and open_ids:
            yield _finding(
                "decisions.open-at-finish",
                "error",
                item,
                f"`phase: finish` with {len(open_ids)} open decision(s): {', '.join(open_ids)}",
            )
        yield from _supersedes_findings(item, parsed)
        yield from _hold_findings(ctx, item, parsed, by_path)


def _spec_text(ctx: RuleContext, item: WorkItem) -> str | None:
    """The item's `design` artifact as text, or `None` when there is none to
    read. Never raises: an unreadable spec is simply not citation-checked, the
    same as an absent or unstamped one."""
    for source in item.sources:
        if source.id != SPEC_SOURCE_ID or not source.resource:
            continue
        path = ctx.bundle.root / source.resource.removeprefix("/")
        root = ctx.bundle.root.resolve()
        owner = (root / item.path).resolve()
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_relative_to(owner):
                return None
            return resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return None
    return None


def citations(ctx: RuleContext) -> Iterable[Finding]:
    """34: a design spec citing a `D-nnn` its owning epic's ledger has not got.

    Runs over **any** item with a resolvable epic ancestor, not only epics —
    that is the rule's whole point, since a child's spec is what cites the
    parent's decisions. Resolution walks the whole item set, archived included
    (an archived ancestor still resolves); only active items are reported on,
    matching `graph.references`.

    Resolved by **number**, not string, mirroring `_supersedes_findings`: a
    hand-typed `D-1` still resolves against the ledger's zero-padded `D-001`
    instead of reporting a phantom missing citation.
    """
    everything = items(ctx)
    by_path = {item.path: item for item in everything}
    known: dict[str, frozenset[int]] = {}
    for item in everything:
        if item.archived:
            continue
        text = _spec_text(ctx, item)
        if text is None:
            continue
        owner_path = decision_owner(everything, item.path)
        if owner_path is None:
            continue
        if owner_path not in known:
            owner = by_path[owner_path]
            ref = _ledger_ref(owner.path)
            known[owner_path] = frozenset(entry.number for entry in load(ref.path(ctx.bundle.root)).entries)
        for cite in sorted({match.group(0) for match in _ID_RE.finditer(text)}):
            try:
                number = id_number(cite)
            except ValueError:
                continue
            if number in known[owner_path]:
                continue
            yield _finding(
                "decisions.cite-missing",
                "error",
                item,
                f"design spec cites {cite!r}, which is not in owner {owner_path!r}'s ledger",
            )


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """Uniform with every other topic, including the three that inject nothing
    (C5-E). The ledger is a bundle member, so its root arrives with the context."""
    del config  # this topic injects nothing
    return (ledger, citations)
