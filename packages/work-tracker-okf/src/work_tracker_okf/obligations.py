"""Pure finish-obligation parsing, coverage derivation, and plan/apply writing.

Coverage caveats and steps deferred to finish share one durable frontmatter
list. Entries remain as history after resolve; they do not gate transitions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

from okf_io import Document

from work_tracker_okf.vocabulary import TERMINAL_STATUSES

if TYPE_CHECKING:
    from work_tracker_okf.items import WorkItem

KEY: Final = "finish_obligations"
ORIGINS: Final[frozenset[str]] = frozenset({"coverage", "deferred"})
_FIELDS: Final = frozenset({"text", "origin", "recorded"})
_UNCHECKED = re.compile(r"^\s*- \[ \](?:\s+(?P<rest>.*))?$")

ObligationRefusal = Literal["unknown-path", "empty-text", "terminal-item"]


@dataclass(frozen=True, slots=True)
class Obligation:
    text: str
    origin: str
    recorded: str

    def to_data(self) -> dict[str, str]:
        return {"text": self.text, "origin": self.origin, "recorded": self.recorded}


def _single_line(value: object) -> str | None:
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        return None
    stripped = value.strip()
    return stripped or None


def _iso_date(value: object) -> str | None:
    if type(value) is date:
        return value.isoformat()
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_obligations(raw: object) -> tuple[tuple[Obligation, ...], bool]:
    """Return well-formed entries and a malformed-input flag without raising."""
    if raw is None:
        return (), False
    if not isinstance(raw, list):
        return (), True
    kept: list[Obligation] = []
    malformed = False
    for entry in raw:
        if isinstance(entry, dict) and set(entry) == _FIELDS:
            text = _single_line(entry["text"])
            recorded = _iso_date(entry["recorded"])
            origin = entry["origin"]
            if text is not None and recorded is not None and isinstance(origin, str) and origin in ORIGINS:
                kept.append(Obligation(text, origin, recorded))
                continue
        malformed = True
    return tuple(kept), malformed


def unchecked_lines(coverage_text: str) -> tuple[str, ...]:
    """Return non-empty text of unchecked dash-bullet coverage lines, in order."""
    found: list[str] = []
    for line in coverage_text.splitlines():
        match = _UNCHECKED.match(line)
        if match is not None and (rest := (match.group("rest") or "").strip()):
            found.append(rest)
    return tuple(found)


def derive(existing: Sequence[Obligation], coverage_text: str | None, *, on: date) -> tuple[Obligation, ...]:
    """Keep deferred entries, then derive distinct unchecked coverage caveats."""
    deferred = [entry for entry in existing if entry.origin == "deferred"]
    prior = {entry.text: entry for entry in existing if entry.origin == "coverage"}
    coverage: list[Obligation] = []
    seen: set[str] = set()
    for text in unchecked_lines(coverage_text) if coverage_text is not None else ():
        if text in seen:
            continue
        seen.add(text)
        coverage.append(prior.get(text) or Obligation(text, "coverage", on.isoformat()))
    return (*deferred, *coverage)


@dataclass(frozen=True, slots=True)
class ObligationPlan:
    """A planned frontmatter rewrite, with no filesystem effects."""

    path: str
    before: tuple[Obligation, ...]
    after: tuple[Obligation, ...]
    refusal: ObligationRefusal | None = None
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.refusal is None and self.before != self.after


def plan_add(items: Sequence[WorkItem], path: str, text: str, *, on: date) -> ObligationPlan:
    item = next((candidate for candidate in items if candidate.path == path), None)
    if item is None:
        return ObligationPlan(path, (), (), "unknown-path", f"unknown work item {path!r}")
    before = item.finish_obligations
    cleaned = _single_line(text)
    if cleaned is None:
        return ObligationPlan(path, before, before, "empty-text", "--text must be one non-empty line")
    if item.work_status in TERMINAL_STATUSES or item.phase == "done":
        return ObligationPlan(
            path, before, before, "terminal-item", f"{path} is {item.work_status} at phase {item.phase!r}"
        )
    return ObligationPlan(path, before, (*before, Obligation(cleaned, "deferred", on.isoformat())))


def plan_derive(item: WorkItem, coverage_text: str | None, *, on: date) -> ObligationPlan:
    before = item.finish_obligations
    return ObligationPlan(item.path, before, derive(before, coverage_text, on=on))


def apply_obligations(document: Document, plan: ObligationPlan) -> None:
    """Set or remove the frontmatter key through okf-io's minimal splice."""
    if plan.refusal is not None:
        raise ValueError(f"refused obligation plan ({plan.refusal}) must not be applied")
    if not plan.after:
        if KEY in document.fm_raw:
            document.delete(KEY)
        return
    document.set(
        KEY,
        [
            {"text": entry.text, "origin": entry.origin, "recorded": date.fromisoformat(entry.recorded)}
            for entry in plan.after
        ],
    )
