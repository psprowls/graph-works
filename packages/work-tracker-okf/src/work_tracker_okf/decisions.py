"""Parent-owned decision ledgers: parse, render, query, and locked mutation.

The ledger records questions surfaced during a parent item's design fan-out:
`answered` (human-decided, authoritative), `assumed` (worker-decided to keep
moving, carrying an `**If wrong:**` blast-radius line), `open` (surfaced,
unanswered), and `superseded` (replaced by a later entry, never deleted).

Three layers, one module, because they are cohesive rather than merely
co-located: `render` is `parse`'s inverse, planners hold immutable snapshots,
and `apply_plan` enforces each snapshot under the same lock as the replacement
write.

    text (pure)    parse / render / prose helpers — never raises on bad input
    mutation       plan_* (write-free) / apply_plan (stale-safe)
    file           exclusive flock at the caller-provided cache path, then a
                   temp file + `Path.replace`
    query (pure)   query / counts over already-parsed entries

`parse` is deliberately tolerant, which is the package rule ("nothing on the
content path raises") applied here: one bad hand-append must not make the ledger
unreadable to the consumers that depend on it. Malformed input produces a
warning and parsing continues.

`append`, `set_fields` and `supersede` raise `ValueError` on **caller**
error — an unknown status, an id naming no entry, editing an entry that has been
superseded. That is the door `paths.source_id_for` already opened, and the same
category: arguments a caller composed, not text a vault contained.

It imports `work_tracker_okf.paths` and nothing else from the package. The
module never discovers a workspace root: every file function takes a resolved
ledger `Path` and an explicit cache lock `Path`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Literal

from okf_ext.locking import locked as _locked_file

from work_tracker_okf.paths import MANAGED_ARTIFACTS, ArtifactRef, artifact_ref

VALID_STATUSES = frozenset({"answered", "assumed", "open", "superseded"})

DecisionRefusal = Literal[
    "answer-required",
    "if-wrong-required",
    "status-disallowed",
    "transition-disallowed",
    "unknown-decision",
    "superseded-decision",
]


def ledger_ref(owner_path: str) -> ArtifactRef:
    """The canonical decision ledger owned by a Release, Epic, or Feature."""
    return artifact_ref(owner_path, MANAGED_ARTIFACTS["decisions"])


#: Recognized keys, in canonical render order. Anything else round-trips through
#: `Decision.extra_keys` rather than being dropped.
RECOGNIZED_KEYS = ("status", "affects", "decided", "supersedes")

#: `## D-014 — question`. The separator/question tail is optional; an em dash, an
#: en dash, or one-or-more hyphens all work, because agents type all three.
_HEADING_RE = re.compile(r"^##\s+D-(\d+)(?:\s*[—–-]+\s*(.*?))?\s*$")  # noqa: RUF001 -- en/em dash intentional
#: `(?!//)` rejects a bare URL's scheme (`https://…`) so it isn't mistaken for
#: a compact `key:value` — everything else after the colon, spaced or not, is
#: a legitimate value (`status:open` as much as `status: open`).
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?!//)\s*(.*)$")
_ID_RE = re.compile(r"\bD-\d+\b")

#: Values that mean "unset" for `decided` / `supersedes`.
_EMPTY_VALUES = {"", "—", "–", "-", "none", "None"}  # noqa: RUF001 -- en/em dash intentional


@dataclass(frozen=True)
class Decision:
    """One ledger entry.

    `prose` is held verbatim rather than decomposed into Answer / Rationale /
    If-wrong fields: entries carry arbitrary extra prose, and a lossy
    decomposition would drop it on the first round trip.
    """

    #: The literal text parsed, so a hand-written `D-14` stays `D-14`.
    id: str
    #: The sort and allocation key.
    number: int
    question: str = ""
    #: `""` when missing or unrecognized — `parse` warns, never raises.
    status: str = ""
    affects: tuple[str, ...] = ()
    #: Opaque, e.g. `"2026-08-11 by user"`.
    decided: str | None = None
    supersedes: str | None = None
    prose: str = ""
    #: Unknown keys, order preserved.
    extra_keys: tuple[tuple[str, str], ...] = ()
    #: Which of `affects`/`decided`/`supersedes` were actually present in the
    #: source (or set via `_present_keys_for` when a Decision is built in code
    #: rather than parsed). `status` always renders regardless. Omitting this on
    #: a code-constructed Decision silently drops that field's value on
    #: `render()` — route every construction site through `_present_keys_for`.
    _present_keys: frozenset[str] = field(default_factory=frozenset, compare=False, repr=False)


@dataclass
class LedgerParse:
    """Everything before the first entry, the entries in file order, and every
    tolerance warning raised along the way."""

    preamble: str = ""
    entries: list[Decision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """The exact ledger text a mutation plan was derived from."""

    text: str


@dataclass(frozen=True, slots=True)
class DecisionPlan:
    """An inspectable, write-free decision-ledger mutation."""

    ledger: Path
    snapshot: LedgerSnapshot
    before: tuple[Decision, ...]
    after: tuple[Decision, ...]
    primary: Decision | None
    superseded: Decision | None
    warnings: tuple[str, ...]
    refusal: DecisionRefusal | None
    detail: str


@dataclass(frozen=True, slots=True)
class DecisionApplication:
    """The observable result of applying a decision plan."""

    entries: tuple[Decision, ...] = ()
    written: bool = False
    stale: bool = False


# ---------------------------------------------------------------------------
# Text layer (pure)
# ---------------------------------------------------------------------------


def _parse_affects(raw: str) -> tuple[str, ...]:
    """Accept both `[a, b]` and a bare `a, b`."""
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _optional(raw: str) -> str | None:
    return None if raw.strip() in _EMPTY_VALUES else raw.strip()


def _present_keys_for(
    *, affects: Sequence[str] = (), decided: str | None = None, supersedes: str | None = None
) -> frozenset[str]:
    """`Decision._present_keys` for an entry built by CODE, not parsed from text.

    `status` always renders regardless of this set — only `affects`, `decided`
    and `supersedes` are conditional. A key counts as present when given a
    meaningful value (a non-empty `affects`, or a non-`None`
    `decided`/`supersedes`). Every construction site routes through here, which
    is what retires `work-io`'s hand-maintained duplicate of this rule.
    """
    keys: set[str] = set()
    if affects:
        keys.add("affects")
    if decided is not None:
        keys.add("decided")
    if supersedes is not None:
        keys.add("supersedes")
    return frozenset(keys)


def _parse_entry(block: list[str]) -> tuple[Decision, list[str]]:
    """One `## D-nnn` section. Returns `(entry, warnings)`; never raises."""
    warnings: list[str] = []
    match = _HEADING_RE.match(block[0])
    assert match is not None  # callers only pass blocks that matched
    digits = match.group(1)
    question = (match.group(2) or "").strip()
    entry_id = f"D-{digits}"
    if not question:
        warnings.append(f"{entry_id}: heading has no question text")

    # Bare `key: value` lines are consumed ONLY in the contiguous run directly
    # beneath the heading. The first blank (or non-key) line ends the key block
    # and everything after it is prose.
    pairs: list[tuple[str, str]] = []
    index = 1
    while index < len(block):
        line = block[index]
        if not line.strip():
            break
        key_match = _KEY_RE.match(line)
        if key_match is None:
            break
        pairs.append((key_match.group(1), (key_match.group(2) or "").strip()))
        index += 1

    known: dict[str, str] = {}
    extra: list[tuple[str, str]] = []
    present_keys: set[str] = set()
    for key, value in pairs:
        if key in RECOGNIZED_KEYS and key not in known:
            known[key] = value
            present_keys.add(key)
        else:
            extra.append((key, value))

    # `status` alone tolerates a trailing `# ...` comment, because the epic
    # template's illustrative entry carries one and agents copy it. Warned, then
    # dropped: comments hold no data and are not re-emitted.
    status = known.get("status", "")
    if "#" in status:
        status = status.split("#", 1)[0].strip()
        warnings.append(f"{entry_id}: dropped trailing comment from status (comments are not part of the format)")
    if not status:
        warnings.append(f"{entry_id}: missing status; expected one of {sorted(VALID_STATUSES)}")
    elif status not in VALID_STATUSES:
        warnings.append(f"{entry_id}: invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
        status = ""

    prose = "\n".join(block[index:]).strip("\n")

    return (
        Decision(
            id=entry_id,
            number=int(digits),
            question=question,
            status=status,
            affects=_parse_affects(known.get("affects", "")),
            decided=_optional(known.get("decided", "")),
            supersedes=_optional(known.get("supersedes", "")),
            prose=prose,
            extra_keys=tuple(extra),
            _present_keys=frozenset(present_keys),
        ),
        warnings,
    )


def parse(text: str) -> LedgerParse:
    """Parse ledger markdown. Never raises: a malformed section produces a
    warning and parsing continues. Entries come back in file order."""
    result = LedgerParse()
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _HEADING_RE.match(line)]
    if not starts:
        result.preamble = text
        return result

    result.preamble = "\n".join(lines[: starts[0]])
    bounds = [*starts[1:], len(lines)]
    seen: set[int] = set()
    for start, end in zip(starts, bounds, strict=True):
        entry, warnings = _parse_entry(lines[start:end])
        if entry.number in seen:
            warnings.append(f"{entry.id}: duplicate id")
        seen.add(entry.number)
        result.entries.append(entry)
        result.warnings.extend(warnings)
    return result


def _render_entry(decision: Decision) -> str:
    heading = f"## {decision.id}"
    if decision.question:
        heading += f" — {decision.question}"
    lines = [heading]

    # Recognized keys in canonical order, but only the ones actually present.
    # `status` is unconditional: every entry has one, even a blank one.
    lines.append(f"status: {decision.status}")
    if "affects" in decision._present_keys:
        lines.append(f"affects: [{', '.join(decision.affects)}]")
    if "decided" in decision._present_keys:
        lines.append(f"decided: {decision.decided or '—'}")
    if "supersedes" in decision._present_keys:
        lines.append(f"supersedes: {decision.supersedes or '—'}")

    lines.extend(f"{key}: {value}" for key, value in decision.extra_keys)
    body = "\n".join(lines)
    prose = decision.prose.strip("\n")
    if prose:
        body += "\n\n" + prose
    return body + "\n"


def render(preamble: str, entries: Sequence[Decision]) -> str:
    """The inverse of `parse`.

    Byte-stable for text this module wrote. For hand-written text it normalizes
    whitespace and key order — `RECOGNIZED_KEYS` first, then `extra_keys` as
    found — without altering content. Entries render in ascending `number`.
    """
    parts: list[str] = []
    head = preamble.strip("\n")
    if head:
        parts.append(head + "\n")
    parts.extend(_render_entry(decision) for decision in sorted(entries, key=lambda d: d.number))
    return "\n".join(parts)


def prose_block(decision: Decision, label: str) -> str | None:
    """A bold-labelled prose block's text — `prose_block(d, "If wrong")` or
    `prose_block(d, "**If wrong:**")`. `None` when the label is absent.

    Ships unused by this module's own logic, so `decisions.entry-invalid`'s
    sibling checks and any future "an `assumed` entry has an `**If wrong:**`
    line" rule need no second parser.
    """
    name = label.strip().strip("*").rstrip(":").strip()
    marker = f"**{name}:**"
    lines = decision.prose.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith(marker):
            continue
        collected = [stripped[len(marker) :].strip()]
        for follow in lines[index + 1 :]:
            if not follow.strip():
                break
            collected.append(follow.strip())
        return "\n".join(part for part in collected if part).strip()
    return None


_PROSE_LABELS = ("Answer", "Rationale", "If wrong")


def compose_prose(*, answer: str | None = None, rationale: str | None = None, if_wrong: str | None = None) -> str:
    """Compose the ledger's recognized bold-labelled prose in canonical order."""
    values = (("Answer", answer), ("Rationale", rationale), ("If wrong", if_wrong))
    return "\n\n".join(f"**{label}:** {value.strip()}" for label, value in values if value is not None)


def _prose_blocks(prose: str) -> list[str]:
    return [block.strip("\n") for block in re.split(r"\n[ \t]*\n", prose.strip("\n")) if block.strip()]


def _prose_label(block: str) -> str | None:
    first_line = block.splitlines()[0].lstrip()
    return next((label for label in _PROSE_LABELS if first_line.startswith(f"**{label}:**")), None)


def merge_prose(existing: str, replacement: str) -> str:
    """Replace recognized labelled blocks while preserving unrelated prose.

    A newly supplied label is placed beside the earlier canonical labels rather
    than appended after an unrelated note. Recognized labels omitted from
    *replacement* stay untouched.
    """
    blocks = _prose_blocks(existing)
    replacements = {label: block for block in _prose_blocks(replacement) if (label := _prose_label(block))}
    for label in _PROSE_LABELS:
        new_block = replacements.get(label)
        if new_block is None:
            continue
        found = [index for index, block in enumerate(blocks) if _prose_label(block) == label]
        if found:
            first = found[0]
            blocks[first] = new_block
            blocks = [block for index, block in enumerate(blocks) if index == first or _prose_label(block) != label]
            continue
        earlier = set(_PROSE_LABELS[: _PROSE_LABELS.index(label)])
        insertion = max(
            (index + 1 for index, block in enumerate(blocks) if _prose_label(block) in earlier),
            default=0,
        )
        blocks.insert(insertion, new_block)
    blocks.extend(block for block in _prose_blocks(replacement) if _prose_label(block) is None)
    return "\n\n".join(blocks)


def _without_prose_labels(prose: str, labels: frozenset[str]) -> str:
    return "\n\n".join(block for block in _prose_blocks(prose) if _prose_label(block) not in labels)


def decided_stamp(on: date, decided_by: str) -> str:
    """Format a decision stamp using caller-supplied values only."""
    return f"{on.isoformat()} by {decided_by}"


def id_number(decision_id: str) -> int:
    """`'D-014'` / `'014'` / `'14'` -> `14`. Raises `ValueError` on anything else.

    Public, unlike `work-io`'s `_id_number`: `_rules/decisions.py` resolves a
    `supersedes:` value by number for the same padded/unpadded unification, and
    a rule module reaching into another module's private helper is how the two
    end up disagreeing about what `D-14` names.
    """
    text = str(decision_id).strip()
    digits = text[2:] if text.upper().startswith("D-") else text
    if not digits.isdigit():
        raise ValueError(f"malformed decision id {decision_id!r}; expected a form like 'D-014'")
    return int(digits)


def extract_cited_decisions(text: str) -> tuple[str, ...]:
    """Every `D-nnn` id referenced in *text*, deduped, first-seen order.

    Reuses this module's `_ID_RE` rather than restating the pattern — a fourth
    copy of it in the package is what splitting the reconcile scans by concern
    exists to avoid.

    Ids come back **verbatim**, so `D-14` and `D-014` stay distinct strings;
    the caller unifies them by number through `id_number`, exactly as `query`'s
    `cites` filter already does. The scan cannot distinguish a live citation
    from an illustrative id — which is why documentation discussing the ledger
    writes the pattern with letters rather than concrete digits.
    """
    seen: set[str] = set()
    found: list[str] = []
    for match in _ID_RE.finditer(text):
        raw = match.group(0)
        if raw not in seen:
            seen.add(raw)
            found.append(raw)
    return tuple(found)


# ---------------------------------------------------------------------------
# File layer
# ---------------------------------------------------------------------------


class Unset:
    """The sentinel that lets `set_fields` tell "not given" from "set to None".

    A class rather than an `object()` so the type checker can express
    `str | None | Unset` in a signature — which is what retires `work-io`'s
    `**fields: object` and both of its mypy suppression comments on the
    `arg-type` error that untyped kwargs forced.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()


def _read_text(ledger: Path) -> str:
    """Read exact ledger text; absence is the empty snapshot."""
    return "" if not ledger.exists() else ledger.read_bytes().decode("utf-8")


def load(ledger: Path) -> LedgerParse:
    """Parse the ledger at *ledger*; an absent file reads as empty.

    Reads never fail on content or on absence — a pre-design epic legitimately
    has no ledger.
    """
    return parse(_read_text(ledger))


@contextmanager
def _locked(lock: Path) -> Iterator[None]:
    """Serialize a read -> mutate -> render -> write cycle across processes.

    The caller supplies a stable path below ``.gw/cache`` rather than locking
    the ledger itself: the ledger may not exist on the first append, and each
    write replaces it, so two writers locking the ledger directly could hold
    different inodes and both proceed. The cache lock is created on demand and
    never unlinked because unlinking races the next writer's open, via
    `okf_ext.locking.locked`.
    """
    with _locked_file(lock):
        yield


def _write(ledger: Path, text: str) -> None:
    """Temp file + rename, so a crash mid-write cannot truncate the ledger."""
    tmp = ledger.parent / f".{ledger.name}.tmp.{os.getpid()}"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(ledger)


def _require(entries: list[Decision], decision_id: str) -> tuple[Decision, int]:
    number = id_number(decision_id)
    for index, entry in enumerate(entries):
        if entry.number == number:
            return entry, index
    known = ", ".join(d.id for d in entries) or "(the ledger is empty)"
    raise ValueError(f"no decision {decision_id!r} in this ledger; known ids: {known}")


def _next_id(entries: list[Decision]) -> tuple[str, int]:
    """Always max-plus-one, never gap-filling: a manually deleted entry's id is
    never reused, which is what makes a gap a genuine lost-entry signal."""
    number = max((d.number for d in entries), default=0) + 1
    return f"D-{number:03d}", number


def _check_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {sorted(VALID_STATUSES)}")
    return status


def _plan_refusal(
    ledger: Path,
    snapshot: LedgerSnapshot,
    parsed: LedgerParse,
    refusal: DecisionRefusal,
    detail: str,
) -> DecisionPlan:
    before = tuple(parsed.entries)
    return DecisionPlan(
        ledger=ledger,
        snapshot=snapshot,
        before=before,
        after=before,
        primary=None,
        superseded=None,
        warnings=tuple(parsed.warnings),
        refusal=refusal,
        detail=detail,
    )


def plan_append(
    ledger: Path,
    *,
    question: str,
    status: str,
    answer: str | None,
    rationale: str | None,
    if_wrong: str | None,
    affects: Sequence[str],
    on: date,
    decided_by: str,
) -> DecisionPlan:
    """Plan a decision append from an immutable byte-for-byte snapshot."""
    snapshot = LedgerSnapshot(_read_text(ledger))
    parsed = parse(snapshot.text)
    if status not in {"open", "assumed", "answered"}:
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "status-disallowed",
            f"status {status!r} cannot be appended",
        )
    if status in {"assumed", "answered"} and (answer is None or not answer.strip()):
        return _plan_refusal(ledger, snapshot, parsed, "answer-required", f"status {status!r} requires an answer")
    if status == "assumed" and (if_wrong is None or not if_wrong.strip()):
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "if-wrong-required",
            "status 'assumed' requires an if-wrong consequence",
        )

    entry_id, number = _next_id(parsed.entries)
    decided = decided_stamp(on, decided_by) if status in {"answered", "assumed"} else None
    entry = Decision(
        id=entry_id,
        number=number,
        question=question.strip(),
        status=status,
        affects=tuple(affects),
        decided=decided,
        prose=compose_prose(answer=answer, rationale=rationale, if_wrong=if_wrong),
        _present_keys=_present_keys_for(affects=affects, decided=decided),
    )
    before = tuple(parsed.entries)
    return DecisionPlan(
        ledger=ledger,
        snapshot=snapshot,
        before=before,
        after=(*before, entry),
        primary=entry,
        superseded=None,
        warnings=tuple(parsed.warnings),
        refusal=None,
        detail=f"append {entry.id}",
    )


def _find(entries: Sequence[Decision], decision_id: str) -> tuple[Decision, int] | None:
    try:
        number = id_number(decision_id)
    except ValueError:
        return None
    return next(((entry, index) for index, entry in enumerate(entries) if entry.number == number), None)


def plan_update(
    ledger: Path,
    decision_id: str,
    *,
    answer: str,
    rationale: str | None,
    on: date,
    decided_by: str,
) -> DecisionPlan:
    """Plan answering an `open` or `assumed` decision. Writes nothing."""
    snapshot = LedgerSnapshot(_read_text(ledger))
    parsed = parse(snapshot.text)
    found = _find(parsed.entries, decision_id)
    if found is None:
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "unknown-decision",
            f"no decision {decision_id!r} in this ledger",
        )
    entry, index = found
    if entry.status == "superseded":
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "superseded-decision",
            f"{entry.id} is superseded; answer the entry that replaced it",
        )
    if entry.status not in {"open", "assumed"}:
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "transition-disallowed",
            f"{entry.id} has status {entry.status!r}; only open or assumed decisions can be answered",
        )
    if not answer.strip():
        return _plan_refusal(ledger, snapshot, parsed, "answer-required", "answer must not be empty")

    retained = _without_prose_labels(entry.prose, frozenset({"Answer", "Rationale"}))
    prose = merge_prose(retained, compose_prose(answer=answer, rationale=rationale))
    updated = replace(
        entry,
        status="answered",
        decided=decided_stamp(on, decided_by),
        prose=prose,
        _present_keys=entry._present_keys | frozenset({"decided"}),
    )
    after = list(parsed.entries)
    after[index] = updated
    return DecisionPlan(
        ledger=ledger,
        snapshot=snapshot,
        before=tuple(parsed.entries),
        after=tuple(after),
        primary=updated,
        superseded=None,
        warnings=tuple(parsed.warnings),
        refusal=None,
        detail=f"answer {updated.id}",
    )


def plan_supersede(
    ledger: Path,
    old_id: str,
    *,
    question: str,
    answer: str,
    rationale: str | None,
    on: date,
    decided_by: str,
    affects: Sequence[str] | None = None,
) -> DecisionPlan:
    """Plan retiring one decision and appending its answered replacement."""
    snapshot = LedgerSnapshot(_read_text(ledger))
    parsed = parse(snapshot.text)
    found = _find(parsed.entries, old_id)
    if found is None:
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "unknown-decision",
            f"no decision {old_id!r} in this ledger",
        )
    old, index = found
    if old.status == "superseded":
        return _plan_refusal(
            ledger,
            snapshot,
            parsed,
            "superseded-decision",
            f"{old.id} is already superseded; supersede the entry that replaced it",
        )
    if not answer.strip():
        return _plan_refusal(ledger, snapshot, parsed, "answer-required", "answer must not be empty")

    entry_id, number = _next_id(parsed.entries)
    retired = replace(old, status="superseded")
    final_affects = tuple(affects) if affects is not None else old.affects
    stamp = decided_stamp(on, decided_by)
    replacement = Decision(
        id=entry_id,
        number=number,
        question=question.strip(),
        status="answered",
        affects=final_affects,
        decided=stamp,
        supersedes=old.id,
        prose=compose_prose(answer=answer, rationale=rationale),
        _present_keys=_present_keys_for(affects=final_affects, decided=stamp, supersedes=old.id),
    )
    after = list(parsed.entries)
    after[index] = retired
    after.append(replacement)
    return DecisionPlan(
        ledger=ledger,
        snapshot=snapshot,
        before=tuple(parsed.entries),
        after=tuple(after),
        primary=replacement,
        superseded=retired,
        warnings=tuple(parsed.warnings),
        refusal=None,
        detail=f"supersede {retired.id} with {replacement.id}",
    )


def _apply_plan_locked(plan: DecisionPlan) -> DecisionApplication:
    current = _read_text(plan.ledger)
    if current != plan.snapshot.text:
        return DecisionApplication(stale=True)
    _write(plan.ledger, render(parse(current).preamble, plan.after))
    return DecisionApplication(entries=plan.after, written=True)


def apply_plan(plan: DecisionPlan, *, lock: Path) -> DecisionApplication:
    """Apply a non-refused plan only when its snapshot is still current."""
    if plan.refusal is not None:
        return DecisionApplication()
    with _locked(lock):
        return _apply_plan_locked(plan)


def _apply_until_current(planner: Callable[[], DecisionPlan], *, lock: Path) -> DecisionPlan:
    """Apply a direct mutation, re-planning after concurrent writes."""
    while True:
        plan = planner()
        application = apply_plan(plan, lock=lock)
        if application.stale:
            continue
        if not application.written:
            raise AssertionError("decision mutation unexpectedly refused")
        return plan


def _apply_under_lock(planner: Callable[[], DecisionPlan], *, lock: Path) -> DecisionPlan:
    """Plan and apply once under one lock for callback-dependent mutations."""
    with _locked(lock):
        plan = planner()
        application = _apply_plan_locked(plan)
    if not application.written:
        raise AssertionError("lock-held decision mutation unexpectedly went stale")
    return plan


def append(
    ledger: Path,
    *,
    lock: Path,
    question: str,
    status: str,
    affects: Sequence[str] = (),
    prose: str = "",
    decided: str | None = None,
    supersedes: str | None = None,
) -> Decision:
    """Compatibility wrapper that plans and applies a low-level append.

    A stale application is re-planned from the newer ledger, preserving the
    original API's cross-process max-plus-one allocation guarantee.
    """
    _check_status(status)

    def plan() -> DecisionPlan:
        snapshot = LedgerSnapshot(_read_text(ledger))
        parsed = parse(snapshot.text)
        entry_id, number = _next_id(parsed.entries)
        entry = Decision(
            id=entry_id,
            number=number,
            question=question.strip(),
            status=status,
            affects=tuple(affects),
            decided=decided,
            supersedes=supersedes,
            prose=prose.strip("\n"),
            _present_keys=_present_keys_for(affects=affects, decided=decided, supersedes=supersedes),
        )
        before = tuple(parsed.entries)
        return DecisionPlan(
            ledger=ledger,
            snapshot=snapshot,
            before=before,
            after=(*before, entry),
            primary=entry,
            superseded=None,
            warnings=tuple(parsed.warnings),
            refusal=None,
            detail=f"append {entry.id}",
        )

    applied = _apply_until_current(plan, lock=lock)
    assert applied.primary is not None
    return applied.primary


def set_fields(
    ledger: Path,
    decision_id: str,
    *,
    lock: Path,
    question: str | Unset = UNSET,
    status: str | Unset = UNSET,
    affects: Sequence[str] | Unset = UNSET,
    decided: str | Unset | None = UNSET,
    supersedes: str | Unset | None = UNSET,
    prose: str | Unset = UNSET,
    prose_merge: Callable[[str], str] | None = None,
) -> Decision:
    """Update recognized fields on one entry, under lock. Refuses a superseded
    target — edit the entry that replaced it instead.

    Explicit keyword parameters with an `UNSET` sentinel, where `work-io` took
    `**fields: object`: the unknown-field check becomes a type error at the call
    site rather than a `ValueError` at runtime, both mypy suppression comments
    on that untyped kwargs' `arg-type` error go, and `_present_keys_for` is
    *called* rather than re-implemented — which retires the sync hazard
    `work-io`'s own comment named.

    Pass `prose_merge` instead of a literal `prose=` when the new value depends
    on the entry's CURRENT prose (replacing one bold-labelled block while
    keeping the others). It receives the prose as read under this same lock
    acquisition, closing the TOCTOU window a caller would otherwise open by
    composing the merge from a value read before the lock was taken.
    """
    if prose_merge is not None and not isinstance(prose, Unset):
        raise ValueError("pass either prose= or prose_merge=, not both")
    if not isinstance(status, Unset):
        _check_status(status)

    def plan() -> DecisionPlan:
        snapshot = LedgerSnapshot(_read_text(ledger))
        parsed = parse(snapshot.text)
        entry, index = _require(parsed.entries, decision_id)
        if entry.status == "superseded":
            raise ValueError(f"{entry.id} is superseded; edit the entry that replaced it instead")

        new_prose = entry.prose
        if prose_merge is not None:
            new_prose = prose_merge(entry.prose).strip("\n")
        elif not isinstance(prose, Unset):
            new_prose = prose.strip("\n")
        new_affects = entry.affects if isinstance(affects, Unset) else tuple(affects)
        new_decided = entry.decided if isinstance(decided, Unset) else decided
        new_supersedes = entry.supersedes if isinstance(supersedes, Unset) else supersedes

        # Merge, don't replace: a field newly set here might not have been
        # present on the parsed entry (answering an entry that only ever had a
        # `status`), and previously-tracked presence — an explicit `affects: []`
        # — must survive.
        updated = replace(
            entry,
            question=entry.question if isinstance(question, Unset) else question.strip(),
            status=entry.status if isinstance(status, Unset) else status,
            affects=new_affects,
            decided=new_decided,
            supersedes=new_supersedes,
            prose=new_prose,
            _present_keys=entry._present_keys
            | _present_keys_for(affects=new_affects, decided=new_decided, supersedes=new_supersedes),
        )
        after = list(parsed.entries)
        after[index] = updated
        return DecisionPlan(
            ledger=ledger,
            snapshot=snapshot,
            before=tuple(parsed.entries),
            after=tuple(after),
            primary=updated,
            superseded=None,
            warnings=tuple(parsed.warnings),
            refusal=None,
            detail=f"update {updated.id}",
        )

    applied = _apply_under_lock(plan, lock=lock) if prose_merge is not None else _apply_until_current(plan, lock=lock)
    assert applied.primary is not None
    return applied.primary


def supersede(
    ledger: Path,
    old_id: str,
    *,
    lock: Path,
    question: str,
    prose: str,
    decided: str | None = None,
    affects: Sequence[str] | None = None,
) -> tuple[Decision, Decision]:
    """Flip *old_id* to `superseded` and append its `answered` replacement.

    Both halves land inside ONE lock acquisition, so a crash cannot leave the
    pair half-applied. Returns `(retired, replacement)`; `affects` defaults to
    the old entry's.
    """

    def plan() -> DecisionPlan:
        snapshot = LedgerSnapshot(_read_text(ledger))
        parsed = parse(snapshot.text)
        old, index = _require(parsed.entries, old_id)
        if old.status == "superseded":
            raise ValueError(f"{old.id} is already superseded; supersede the entry that replaced it instead")
        entry_id, number = _next_id(parsed.entries)
        retired = replace(old, status="superseded")
        final_affects = tuple(affects) if affects is not None else old.affects
        replacement = Decision(
            id=entry_id,
            number=number,
            question=question.strip(),
            status="answered",
            affects=final_affects,
            decided=decided,
            supersedes=old.id,
            prose=prose.strip("\n"),
            _present_keys=_present_keys_for(affects=final_affects, decided=decided, supersedes=old.id),
        )
        after = list(parsed.entries)
        after[index] = retired
        after.append(replacement)
        return DecisionPlan(
            ledger=ledger,
            snapshot=snapshot,
            before=tuple(parsed.entries),
            after=tuple(after),
            primary=replacement,
            superseded=retired,
            warnings=tuple(parsed.warnings),
            refusal=None,
            detail=f"supersede {retired.id} with {replacement.id}",
        )

    applied = _apply_until_current(plan, lock=lock)
    assert applied.superseded is not None and applied.primary is not None
    return applied.superseded, applied.primary


# ---------------------------------------------------------------------------
# Query (pure, over already-parsed entries)
# ---------------------------------------------------------------------------


def _cited_numbers(decision: Decision) -> set[int]:
    """Every `D-nnn` this entry references — via `supersedes`, its question, or
    its prose. Compared by number, so `D-14` and `D-014` unify."""
    haystack = " ".join(part for part in (decision.supersedes or "", decision.question, decision.prose) if part)
    return {int(match.group(0)[2:]) for match in _ID_RE.finditer(haystack)}


def query(
    entries: Sequence[Decision],
    *,
    status: str | None = None,
    affects: str | None = None,
    cites: str | None = None,
) -> list[Decision]:
    """Filter entries. Filters combine with AND; no filters returns every entry
    in the order given.

    `cites` selects entries that **reference** the given id — what replaced it,
    what mentions it — not the entry carrying that id.
    """
    selected = list(entries)
    if status:
        selected = [d for d in selected if d.status == status]
    if affects:
        selected = [d for d in selected if affects in d.affects]
    if cites:
        try:
            wanted = id_number(cites)
        except ValueError as exc:
            raise ValueError(f"cites expects a decision id like 'D-014', got {cites!r}") from exc
        selected = [d for d in selected if wanted in _cited_numbers(d)]
    return selected


def counts(entries: Sequence[Decision]) -> dict[str, int]:
    """Per-status rollup plus `invalid` (parsed with a blank or unrecognized
    status) and `total`."""
    materialized = list(entries)
    rollup = dict.fromkeys(sorted(VALID_STATUSES), 0)
    rollup["invalid"] = 0
    for decision in materialized:
        key = decision.status if decision.status in VALID_STATUSES else "invalid"
        rollup[key] += 1
    rollup["total"] = len(materialized)
    return rollup


__all__ = [
    "RECOGNIZED_KEYS",
    "UNSET",
    "VALID_STATUSES",
    "Decision",
    "DecisionApplication",
    "DecisionPlan",
    "DecisionRefusal",
    "LedgerParse",
    "LedgerSnapshot",
    "Unset",
    "append",
    "apply_plan",
    "compose_prose",
    "counts",
    "decided_stamp",
    "extract_cited_decisions",
    "id_number",
    "ledger_ref",
    "load",
    "merge_prose",
    "parse",
    "plan_append",
    "plan_supersede",
    "plan_update",
    "prose_block",
    "query",
    "render",
    "set_fields",
    "supersede",
]
