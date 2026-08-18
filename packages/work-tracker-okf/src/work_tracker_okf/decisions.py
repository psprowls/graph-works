"""The per-epic decisions ledger — parse, render, query, and locked mutation of
`work/<epic-slug>/references/00-decisions.md`.

The ledger records questions surfaced during an epic's design fan-out:
`answered` (human-decided, authoritative), `assumed` (worker-decided to keep
moving, carrying an `**If wrong:**` blast-radius line), `open` (surfaced,
unanswered), and `superseded` (replaced by a later entry, never deleted).

Three layers, one module, because they are cohesive rather than merely
co-located: `render` is `parse`'s inverse, and each mutator is one
read -> mutate -> render -> write cycle, so splitting text from file would put
both halves of a single round trip in different modules.

    text (pure)    parse / render / prose_block — never raises on bad input
    file           load / append / set_fields / supersede — every mutation
                   serialized behind an exclusive flock on a sibling dotfile,
                   written via temp file + `Path.replace`
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
module never discovers a path: every file function takes a resolved `Path`, and
the caller composes it as `decisions_ledger(slug).path(root)`.
"""

from __future__ import annotations

import fcntl
import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from work_tracker_okf.paths import LEDGER_FILENAME

VALID_STATUSES = frozenset({"answered", "assumed", "open", "superseded"})

#: Derived from the ledger's own filename rather than re-typed, so renaming the
#: ledger cannot orphan its lock.
_LOCK_FILENAME = f".{Path(LEDGER_FILENAME).stem}.lock"

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


def load(ledger: Path) -> LedgerParse:
    """Parse the ledger at *ledger*; an absent file reads as empty.

    Reads never fail on content or on absence — a pre-design epic legitimately
    has no ledger.
    """
    if not ledger.exists():
        return LedgerParse()
    return parse(ledger.read_text(encoding="utf-8"))


@contextmanager
def _locked(ledger: Path) -> Iterator[None]:
    """Serialize a read -> mutate -> render -> write cycle across processes.

    The lock is a sibling dotfile, not the ledger itself, for two reasons: the
    ledger may not exist on the first append (nothing to flock), and each write
    replaces the file, so two writers flocking the ledger directly could hold
    locks on different inodes and both proceed. It is created on demand and
    never unlinked, because unlinking races the next writer's open. POSIX-only,
    like the rest of the stack.
    """
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock = ledger.parent / _LOCK_FILENAME
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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


def append(
    ledger: Path,
    *,
    question: str,
    status: str,
    affects: Sequence[str] = (),
    prose: str = "",
    decided: str | None = None,
    supersedes: str | None = None,
) -> Decision:
    """Allocate the next id and append an entry.

    Read -> allocate -> write happens inside one exclusive lock, so concurrent
    fan-out writers never collide.
    """
    _check_status(status)
    with _locked(ledger):
        parsed = load(ledger)
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
        parsed.entries.append(entry)
        _write(ledger, render(parsed.preamble, parsed.entries))
    return entry


def set_fields(
    ledger: Path,
    decision_id: str,
    *,
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

    with _locked(ledger):
        parsed = load(ledger)
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
        parsed.entries[index] = updated
        _write(ledger, render(parsed.preamble, parsed.entries))
    return updated


def supersede(
    ledger: Path,
    old_id: str,
    *,
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
    with _locked(ledger):
        parsed = load(ledger)
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
        parsed.entries[index] = retired
        parsed.entries.append(replacement)
        _write(ledger, render(parsed.preamble, parsed.entries))
    return retired, replacement


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
    "LedgerParse",
    "Unset",
    "append",
    "counts",
    "id_number",
    "load",
    "parse",
    "prose_block",
    "query",
    "render",
    "set_fields",
    "supersede",
]
