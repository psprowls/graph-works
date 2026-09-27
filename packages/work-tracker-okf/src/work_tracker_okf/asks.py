"""Pure build, naming, and rendering for the typed worker ask (`gw.ask/1`).

Core writes the payload file and passes the instant in. Content validation
returns closed-vocabulary refusals. No I/O or clock is used here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from work_tracker_okf.vocabulary import EFFORTS, PHASES

ASK_SCHEMA = "gw.ask/1"
ASK_KINDS: tuple[str, ...] = ("spec-review", "choice", "free")
#: The last line of every typed ask's Orca question; the coordinator keys on it.
ASK_MARKER = "gw-ask: "
#: The directory under an item's `references/` that holds its payloads.
ASKS_DIR = "asks"
SUMMARY_MAX = 300
MIN_CHOICES = 2
MAX_CHOICES = 8
#: No comma and no space: the property Orca's comma-split `--options` needs.
TOKEN_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
_TOKEN_RE = re.compile(TOKEN_PATTERN)
_NAME_RE = re.compile(r"^(?P<phase>[a-z]+)-(?P<number>[0-9]{3,})-(?P<kind>spec-review|choice|free)\.json$")

ASK_REFUSALS: frozenset[str] = frozenset(
    {
        "kind-invalid",
        "summary-invalid",
        "question-empty",
        "option-token-invalid",
        "option-duplicate",
        "option-count",
        "spec-missing",
        "options-fixed",
        "unknown-item",
        "payload-invalid",
        "already-answered",
        "answer-choice-invalid",
        "answer-effort-required",
        "answer-effort-invalid",
        "answer-notes-required",
        "answer-by-invalid",
    }
)


@dataclass(frozen=True, slots=True)
class AskOption:
    token: str
    label: str


SPEC_REVIEW_OPTIONS: tuple[AskOption, ...] = (
    AskOption("approve", "Approve the spec"),
    AskOption("changes", "Request changes"),
)


@dataclass(frozen=True, slots=True)
class AskAnswer:
    choice: str | None
    effort: str | None
    notes: str | None
    at: str
    by: str


@dataclass(frozen=True, slots=True)
class AskPayload:
    item: str
    phase: str
    kind: str
    created: str
    summary: str
    question: str
    spec: str | None
    current_effort: str | None
    options: tuple[AskOption, ...]
    answer: AskAnswer | None = None

    def to_data(self) -> dict[str, Any]:
        """The on-disk document, key by key -- never `asdict()` (the file is a contract)."""
        answer = self.answer
        return {
            "schema": ASK_SCHEMA,
            "item": self.item,
            "phase": self.phase,
            "kind": self.kind,
            "created": self.created,
            "summary": self.summary,
            "question": self.question,
            "spec": self.spec,
            "current_effort": self.current_effort,
            "options": [{"token": option.token, "label": option.label} for option in self.options],
            "answer": None
            if answer is None
            else {
                "choice": answer.choice,
                "effort": answer.effort,
                "notes": answer.notes,
                "at": answer.at,
                "by": answer.by,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_data(), indent=2, ensure_ascii=False) + "\n"


@dataclass(frozen=True, slots=True)
class AskBuild:
    payload: AskPayload | None
    refusals: tuple[str, ...]


def iso_instant(value: datetime) -> str:
    """An aware instant as `YYYY-MM-DDTHH:MM:SSZ` in UTC."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summary_ok(summary: str) -> bool:
    return bool(summary.strip()) and "\n" not in summary and "\r" not in summary and len(summary) <= SUMMARY_MAX


def build_ask(
    *,
    item: str,
    phase: str | None,
    kind: str,
    created: datetime,
    summary: str,
    question: str,
    spec: str | None,
    current_effort: str | None,
    options: Sequence[AskOption],
) -> AskBuild:
    """A payload, or every refusal the inputs earn in one pass.

    *spec* is the already-resolved root-absolute resource, or `None` when the
    caller could not resolve one beneath the bundle -- resolution is I/O, so it
    is the caller's; this function only refuses its absence.
    """
    refusals: list[str] = []
    if kind not in ASK_KINDS:
        refusals.append("kind-invalid")
    if not _summary_ok(summary):
        refusals.append("summary-invalid")
    if not question.strip():
        refusals.append("question-empty")
    resolved: tuple[AskOption, ...]
    if kind == "spec-review":
        if options:
            refusals.append("options-fixed")
        if spec is None:
            refusals.append("spec-missing")
        resolved = SPEC_REVIEW_OPTIONS
    else:
        resolved = tuple(options)
        tokens = [option.token for option in resolved]
        if any(_TOKEN_RE.fullmatch(token) is None for token in tokens):
            refusals.append("option-token-invalid")
        if len(set(tokens)) != len(tokens):
            refusals.append("option-duplicate")
        if (kind == "choice" and not MIN_CHOICES <= len(resolved) <= MAX_CHOICES) or (kind == "free" and resolved):
            refusals.append("option-count")
    if refusals:
        return AskBuild(None, tuple(dict.fromkeys(refusals)))
    return AskBuild(
        AskPayload(
            item=item,
            phase=phase or "none",
            kind=kind,
            created=iso_instant(created),
            summary=summary,
            question=question,
            spec=spec if kind == "spec-review" else None,
            current_effort=current_effort,
            options=resolved,
        ),
        (),
    )


def next_ask_name(existing: Iterable[str], *, phase: str, kind: str) -> str:
    """`<phase>-<NNN>-<kind>.json`, `NNN` one past the highest number any payload in the directory uses."""
    numbers = [int(match["number"]) for name in existing if (match := _NAME_RE.match(name)) is not None]
    return f"{phase}-{max(numbers, default=0) + 1:03d}-{kind}.json"


def orca_question(payload: AskPayload, resource: str) -> str:
    return f"{payload.summary}\n\n{ASK_MARKER}{resource}"


def orca_options(payload: AskPayload) -> str | None:
    """Comma-joined tokens, or `None` for `free` -- the worker then omits `--options`."""
    if payload.kind == "free":
        return None
    return ",".join(option.token for option in payload.options)


def _opt_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError("expected a string or null")


def _str(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError("expected a string")


def _instant(value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def parse_payload(text: str) -> AskPayload | None:
    """A valid `gw.ask/1` document, or `None` for `payload-invalid`."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("schema") != ASK_SCHEMA:
        return None
    try:
        raw_options = data["options"]
        if not isinstance(raw_options, list):
            return None
        options = tuple(AskOption(_str(option["token"]), _str(option["label"])) for option in raw_options)
        raw_answer = data["answer"]
        answer = (
            None
            if raw_answer is None
            else AskAnswer(
                choice=_opt_str(raw_answer["choice"]),
                effort=_opt_str(raw_answer["effort"]),
                notes=_opt_str(raw_answer["notes"]),
                at=_str(raw_answer["at"]),
                by=_str(raw_answer["by"]),
            )
        )
        payload = AskPayload(
            item=_str(data["item"]),
            phase=_str(data["phase"]),
            kind=_str(data["kind"]),
            created=_str(data["created"]),
            summary=_str(data["summary"]),
            question=_str(data["question"]),
            spec=_opt_str(data["spec"]),
            current_effort=_opt_str(data["current_effort"]),
            options=options,
            answer=answer,
        )
    except (KeyError, TypeError):
        return None
    if payload.kind not in ASK_KINDS or not _instant(payload.created):
        return None
    if (
        not payload.item
        or payload.phase not in PHASES | {"none"}
        or not _summary_ok(payload.summary)
        or not payload.question.strip()
    ):
        return None
    if payload.current_effort is not None and payload.current_effort not in EFFORTS:
        return None
    if payload.kind == "spec-review":
        if not payload.spec or not payload.spec.startswith("/") or payload.options != SPEC_REVIEW_OPTIONS:
            return None
    elif payload.spec is not None:
        return None
    if payload.kind == "choice" and not MIN_CHOICES <= len(payload.options) <= MAX_CHOICES:
        return None
    if payload.kind == "free" and payload.options:
        return None
    tokens = [option.token for option in payload.options]
    if len(set(tokens)) != len(tokens) or any(_TOKEN_RE.fullmatch(token) is None for token in tokens):
        return None
    if answer is not None:
        if not _instant(answer.at) or not answer.by.strip():
            return None
        if validate_answer(payload, choice=answer.choice, effort=answer.effort, notes=answer.notes):
            return None
    return payload


def _given(notes: str | None) -> bool:
    return bool(notes and notes.strip())


def validate_answer(
    payload: AskPayload, *, choice: str | None, effort: str | None, notes: str | None, by: str = "human"
) -> tuple[str, ...]:
    """The spec's answer table (§4.1); `()` when the answer fits."""
    refusals: list[str] = []
    if not by.strip():
        refusals.append("answer-by-invalid")
    if effort is not None and (payload.kind != "spec-review" or effort not in EFFORTS):
        refusals.append("answer-effort-invalid")
    if payload.kind == "spec-review":
        if choice not in {option.token for option in SPEC_REVIEW_OPTIONS}:
            refusals.append("answer-choice-invalid")
        elif choice == "approve" and effort is None and payload.current_effort is None:
            refusals.append("answer-effort-required")
        elif choice == "changes" and not _given(notes):
            refusals.append("answer-notes-required")
    elif payload.kind == "choice":
        if choice not in {option.token for option in payload.options}:
            refusals.append("answer-choice-invalid")
    else:
        if choice is not None:
            refusals.append("answer-choice-invalid")
        if not _given(notes):
            refusals.append("answer-notes-required")
    return tuple(refusals)


def answered(
    payload: AskPayload, *, choice: str | None, effort: str | None, notes: str | None, at: datetime, by: str
) -> AskPayload:
    return replace(payload, answer=AskAnswer(choice, effort, notes or None, iso_instant(at), by))


def same_answer(existing: AskAnswer, *, choice: str | None, effort: str | None, notes: str | None) -> bool:
    """A replayed delivery: same choice, effort and notes (`at` / `by` are not compared)."""
    return (existing.choice, existing.effort, existing.notes) == (choice, effort, notes or None)


def reply_body(resource: str, answer: AskAnswer) -> str:
    """The one-line JSON the coordinator replies with."""
    return json.dumps(
        {"ask": resource, "choice": answer.choice, "effort": answer.effort, "notes": answer.notes},
        ensure_ascii=False,
    )


CHECKPOINTS_HEADING = "## Human checkpoints"
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})")
_BULLET_RE = re.compile(r"^[-*+][ \t]+(?P<text>\S.*)$")


@dataclass(frozen=True, slots=True)
class PlanCheckpoints:
    """`declared` (bullets or exactly `None.`), `missing`, or `malformed`."""

    status: str
    items: tuple[str, ...] = ()


def plan_checkpoints(text: str) -> PlanCheckpoints:
    """Read `## Human checkpoints` through the next `## ` heading or EOF.

    Fenced blocks are skipped. Top-level bullets are items; indented lines
    continue an item without creating another one.
    """
    fence: str | None = None
    inside = found = False
    bullets: list[str] = []
    other: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        marker = _FENCE_RE.match(line)
        if fence is not None:
            if (
                marker is not None
                and marker["marker"][0] == fence[0]
                and len(marker["marker"]) >= len(fence)
                and marker.end() == len(line)
            ):
                fence = None
            continue
        if marker is not None:
            fence = marker["marker"]
            continue
        if line.startswith("## "):
            if inside:
                break
            if line == CHECKPOINTS_HEADING:
                inside = found = True
            continue
        if not inside or not line.strip() or line[:1].isspace():
            continue
        bullet = _BULLET_RE.match(line)
        if bullet is not None:
            bullets.append(bullet["text"].strip())
        else:
            other.append(line.strip())
    if not found:
        return PlanCheckpoints("missing")
    if bullets:
        return PlanCheckpoints("declared", tuple(bullets))
    if other == ["None."]:
        return PlanCheckpoints("declared")
    return PlanCheckpoints("malformed")


__all__ = [
    "ASKS_DIR",
    "ASK_KINDS",
    "ASK_MARKER",
    "ASK_REFUSALS",
    "ASK_SCHEMA",
    "CHECKPOINTS_HEADING",
    "SPEC_REVIEW_OPTIONS",
    "SUMMARY_MAX",
    "TOKEN_PATTERN",
    "AskAnswer",
    "AskBuild",
    "AskOption",
    "AskPayload",
    "PlanCheckpoints",
    "answered",
    "build_ask",
    "iso_instant",
    "next_ask_name",
    "orca_options",
    "orca_question",
    "parse_payload",
    "plan_checkpoints",
    "reply_body",
    "same_answer",
    "validate_answer",
]
