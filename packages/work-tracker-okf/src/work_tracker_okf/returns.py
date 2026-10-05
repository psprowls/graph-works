"""Pure execute-return metadata and selection of reopened execution scope.

Core resolves paths, hashes and destination checkouts. This module validates
and renders data without filesystem, Git or workspace access.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from okf_io import Document

from work_tracker_okf.obligations import Obligation, _iso_date, _single_line

KEY: Final = "execute_return"
_ID = re.compile(r"^ret-\d{8}-[0-9a-f]{8}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_FIELDS: Final = frozenset({"id", "recorded", "state", "coverage", "scope", "plan", "plan_sha256"})
_REQUIRED: Final = frozenset({"id", "recorded", "state", "coverage", "scope"})

ScopeRefusal = Literal["return-scope-invalid", "return-scope-required"]
EvidenceRefusal = Literal["return-evidence-missing", "return-evidence-stale", "return-evidence-incomplete"]
_ROW = re.compile(r"^\s*- \[(?P<mark>[ xX])\]\s+(?P<id>R[1-9]\d*):(?:\s|$)")
_STATE = re.compile(r"^Report state:\s*(?P<state>\S+)\s*$")


@dataclass(frozen=True, slots=True)
class ScopeRow:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class ExecuteReturn:
    id: str
    recorded: str
    state: Literal["active", "completed"]
    coverage: str
    scope: tuple[ScopeRow, ...]
    plan: str | None = None
    plan_sha256: str | None = None

    @property
    def active(self) -> bool:
        return self.state == "active"

    def to_data(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "recorded": self.recorded,
            "state": self.state,
            "coverage": self.coverage,
            "scope": [{"id": row.id, "text": row.text} for row in self.scope],
        }
        if self.plan is not None:
            data["plan"] = self.plan
            data["plan_sha256"] = self.plan_sha256
        return data

    def fingerprint(self) -> str:
        """Hash execution intent independently of its date and lifecycle state."""
        body = {key: value for key, value in self.to_data().items() if key not in {"state", "recorded"}}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScopePlan:
    scope: tuple[str, ...]
    source: Literal["explicit", "coverage-obligations"] | None
    refusal: ScopeRefusal | None
    detail: str


@dataclass(frozen=True, slots=True)
class ReturnReport:
    state: str | None
    rows: tuple[tuple[str, bool], ...]
    duplicates: tuple[str, ...]
    unknown: tuple[str, ...]


def parse_execute_return(raw: object) -> tuple[ExecuteReturn | None, bool]:
    """Parse all-or-nothing, flagging malformed authored metadata as data."""
    if raw is None:
        return None, False
    if not isinstance(raw, dict) or not _REQUIRED <= set(raw) <= _FIELDS:
        return None, True
    rid, state, coverage = raw["id"], raw["state"], raw["coverage"]
    recorded = _iso_date(raw["recorded"])
    plan, sha = raw.get("plan"), raw.get("plan_sha256")
    rows_raw = raw["scope"]
    if (
        not isinstance(rid, str)
        or _ID.fullmatch(rid) is None
        or state not in ("active", "completed")
        or not isinstance(coverage, str)
        or not coverage.startswith("/")
        or recorded is None
        or (plan is None) != (sha is None)
        or (plan is not None and (not isinstance(plan, str) or not plan.startswith("/")))
        or (sha is not None and (not isinstance(sha, str) or _SHA.fullmatch(sha) is None))
        or not isinstance(rows_raw, list)
        or not rows_raw
    ):
        return None, True
    rows: list[ScopeRow] = []
    for index, entry in enumerate(rows_raw, start=1):
        if not isinstance(entry, dict) or set(entry) != {"id", "text"}:
            return None, True
        text = _single_line(entry["text"])
        if text is None or entry["id"] != f"R{index}":
            return None, True
        rows.append(ScopeRow(entry["id"], text))
    return ExecuteReturn(rid, recorded, state, coverage, tuple(rows), plan, sha), False


def plan_scope(explicit: Sequence[str], obligations: Sequence[Obligation], *, obligations_malformed: bool) -> ScopePlan:
    """Prefer explicit scope; otherwise select current coverage obligations only."""
    if explicit:
        cleaned: list[str] = []
        for value in explicit:
            text = _single_line(value)
            if text is None:
                return ScopePlan(
                    (), None, "return-scope-invalid", f"--return-scope must be one nonblank line: {value!r}"
                )
            if text not in cleaned:
                cleaned.append(text)
        return ScopePlan(tuple(cleaned), "explicit", None, "")
    hint = 'name the work with --return-scope "<what execute must do>" (repeatable)'
    if obligations_malformed:
        return ScopePlan((), None, "return-scope-required", f"finish_obligations is malformed; {hint}")
    fallback = tuple(dict.fromkeys(entry.text for entry in obligations if entry.origin == "coverage"))
    if not fallback:
        return ScopePlan((), None, "return-scope-required", f"no coverage obligations to return; {hint}")
    return ScopePlan(fallback, "coverage-obligations", None, "")


def new_record(
    return_id: str,
    scope: Sequence[str],
    *,
    on: date,
    coverage: str,
    plan: str | None,
    plan_sha256: str | None,
) -> ExecuteReturn:
    """Build an active record from resolved provenance and selected scope."""
    rows = tuple(ScopeRow(f"R{index}", text) for index, text in enumerate(scope, start=1))
    return ExecuteReturn(return_id, on.isoformat(), "active", coverage, rows, plan, plan_sha256)


def apply_execute_return(document: Document, record: ExecuteReturn) -> None:
    """Set return metadata through okf-io, writing the recorded date as YAML date."""
    document.set(KEY, {**record.to_data(), "recorded": date.fromisoformat(record.recorded)})


def section_heading(return_id: str) -> str:
    return f"## Returned scope {return_id}"


def render_reopened(text: str | None, record: ExecuteReturn) -> str:
    """Append prepared scope once, preserving authored bytes and newline style."""
    body = text or ""
    if any(line.rstrip("\r") == section_heading(record.id) for line in body.split("\n")):
        return body
    newline = "\r\n" if body.count("\r\n") * 2 > body.count("\n") else "\n"
    lines = [section_heading(record.id), "", "Report state: prepared", ""]
    lines.extend(f"- [ ] {row.id}: {row.text}" for row in record.scope)
    section = newline.join(lines) + newline
    if not body:
        return section
    if not body.endswith(newline):
        body += newline
    return body + newline + section


def read_report(text: str, return_id: str) -> ReturnReport | None:
    """Read one matching section; ambiguous section/state evidence has no state."""
    lines = [line.rstrip("\r") for line in text.split("\n")]
    starts = [index for index, line in enumerate(lines) if line == section_heading(return_id)]
    if not starts:
        return None
    if len(starts) != 1:
        return ReturnReport(None, (), (), ())
    state: str | None = None
    state_count = 0
    seen: dict[str, bool] = {}
    duplicates: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("Report state:"):
            state_count += 1
            if match := _STATE.match(line):
                state = match.group("state")
        elif match := _ROW.match(line):
            row_id = match.group("id")
            if row_id in seen:
                duplicates.append(row_id)
            seen[row_id] = match.group("mark") != " "
    if state_count != 1:
        state = None
    return ReturnReport(state, tuple(seen.items()), tuple(duplicates), ())


def verify_report(record: ExecuteReturn, text: str | None) -> tuple[EvidenceRefusal | None, str]:
    """Require a fresh report containing exactly one row per current scope ID."""
    report = read_report(text, record.id) if text is not None else None
    if report is None:
        return "return-evidence-missing", f"coverage has no `{section_heading(record.id)}` section"
    if report.state != "reported":
        return "return-evidence-stale", (
            f"return {record.id} report state is {report.state!r}; require one section with one 'reported' state"
        )
    expected = [row.id for row in record.scope]
    got = [row_id for row_id, _ in report.rows]
    missing = [row_id for row_id in expected if row_id not in got]
    unknown = [row_id for row_id in got if row_id not in expected]
    if missing or unknown or report.duplicates or report.unknown:
        return "return-evidence-incomplete", (
            f"return {record.id}: missing {missing}, unknown {unknown + list(report.unknown)}, "
            f"duplicated {list(report.duplicates)}"
        )
    return None, ""
