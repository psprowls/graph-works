"""Plan/apply over HTTP: ADR 2026-08-18-mutation-surfaces-plan's plan-by-default contract carried across a
stateless boundary.

`plan` returns a dry-run projection, the instant it was planned at (`as_of`)
and a digest of both. `apply` re-plans at that same instant under one
process-wide lock, and writes only if the digest still matches. What the
client was shown is exactly what gets applied, or the client gets `409` and a
fresh plan. Framework-free: `app.py` adapts `Outcome` to a response.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_serve.context import Reply
from graph_works_serve.errors import refusal
from graph_works_serve.params import Param, ParamError, parse_body

TTL = timedelta(minutes=15)
APPLY_FIELDS = (
    Param("as_of", "str", required=True, location="body"),
    Param("digest", "str", required=True, location="body"),
)
_INSTANT = "%Y-%m-%dT%H:%M:%SZ"
_INSTANT_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# Serializes re-plan -> compare -> apply across every apply route. Handlers run
# in Starlette's thread pool; without this, two applies could both pass the
# digest check and interleave their writes. Out-of-process writers (the CLI,
# an editor) are checked against the actual core candidate by before_apply;
# existing core snapshot preconditions still guard its subsequent application.
_LOCK = threading.Lock()

Params = Mapping[str, Any]
BeforeApply = Callable[[object], None]
ErrorMap = tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...]
DEFAULT_ERRORS: ErrorMap = ((WorkspaceError, "workspace"), (ValueError, "unresolved"), (OSError, "io"))


def now() -> datetime:
    """The module's only clock read. Tests replace it; core never sees it."""
    return datetime.now(UTC)


def format_instant(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_INSTANT)


def parse_instant(raw: str) -> datetime | None:
    if _INSTANT_SHAPE.fullmatch(raw) is None:
        return None
    try:
        return datetime.strptime(raw, _INSTANT).replace(tzinfo=UTC)
    except ValueError:
        return None


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(route: str, params: Mapping[str, object], as_of: str, plan: object) -> str:
    document = {"route": route, "params": dict(params), "as_of": as_of, "plan": plan}
    return "sha256:" + hashlib.sha256(canonical_json(document)).hexdigest()


def incomplete(projection: Mapping[str, Any]) -> bool:
    """The CLI's own incomplete-apply test (`work_cli/main.py:394`)."""
    return bool(projection.get("applied")) and bool(projection.get("rolled_back") or projection.get("failures"))


def _accept(_params: Params) -> None:
    return None


@dataclass(frozen=True)
class MutationSpec:
    route: str
    command: str
    summary: str
    params: tuple[Param, ...]
    run: Callable[[WorkspaceLayout, Params, datetime, bool, BeforeApply | None], object]
    project: Callable[[object, Params, bool], dict[str, Any]]
    refused: Callable[[Mapping[str, Any], Params], str | None]
    validate: Callable[[Params], None] = _accept
    errors: ErrorMap = DEFAULT_ERRORS


# Public name retained for mutation route adapters.
Outcome = Reply


class _Refusal(Exception):
    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.status)
        self.outcome = outcome


def _error(spec: MutationSpec, reason: str, message: str, payload: object, *, status: int | None = None) -> Outcome:
    return refusal(spec.command, reason, message, payload=payload, status=status)


def _is_json(content_type: str | None) -> bool:
    return content_type is not None and content_type.split(";", 1)[0].strip().lower() == "application/json"


def _parse(spec: MutationSpec, fields: tuple[Param, ...], body: bytes, content_type: str | None) -> dict[str, object]:
    if not _is_json(content_type):
        raise _Refusal(_error(spec, "usage", "mutation routes take `Content-Type: application/json`", None, status=415))
    try:
        params = parse_body(fields, body)
        spec.validate(params)
    except ParamError as exc:
        raise _Refusal(_error(spec, "usage", str(exc), None)) from exc
    return params


def _call(
    spec: MutationSpec,
    layout: WorkspaceLayout,
    params: Params,
    as_of: datetime,
    dry_run: bool,
    before_apply: BeforeApply | None = None,
) -> dict[str, Any]:
    try:
        return spec.project(spec.run(layout, params, as_of, dry_run, before_apply), params, dry_run)
    except BaseException as exc:
        for kinds, reason in spec.errors:
            if isinstance(exc, kinds):
                raise _Refusal(_error(spec, reason, str(exc), None)) from exc
        raise


def _planned(spec: MutationSpec, layout: WorkspaceLayout, params: Params, as_of: datetime) -> dict[str, Any]:
    plan = _call(spec, layout, params, as_of, True)
    stamp = format_instant(as_of)
    return {"as_of": stamp, "digest": digest(spec.route, params, stamp, plan), "plan": plan}


def _pinned() -> datetime:
    return now().astimezone(UTC).replace(microsecond=0)


def plan(spec: MutationSpec, layout: WorkspaceLayout, body: bytes, content_type: str | None) -> Outcome:
    """`200 {as_of, digest, plan}`. A refused plan is still `200`: it is the answer."""
    try:
        params = _parse(spec, spec.params, body, content_type)
        return Outcome(200, _planned(spec, layout, params, _pinned()))
    except _Refusal as refusal:
        return refusal.outcome


def apply(spec: MutationSpec, layout: WorkspaceLayout, body: bytes, content_type: str | None) -> Outcome:
    try:
        return _apply(spec, layout, body, content_type)
    except _Refusal as refusal:
        return refusal.outcome


def _apply(spec: MutationSpec, layout: WorkspaceLayout, body: bytes, content_type: str | None) -> Outcome:
    params = _parse(spec, (*spec.params, *APPLY_FIELDS), body, content_type)
    claimed = str(params.pop("digest"))
    raw = str(params.pop("as_of"))
    as_of = parse_instant(raw)
    if as_of is None:
        return _error(spec, "usage", f"`as_of` {raw!r} is not an instant of the form YYYY-MM-DDTHH:MM:SSZ", None)
    current = now()
    if not current - TTL <= as_of <= current:
        fresh = _planned(spec, layout, params, current.astimezone(UTC).replace(microsecond=0))
        return _error(
            spec, "stale-plan", "the plan's `as_of` is outside the apply window; review the fresh plan", fresh
        )
    with _LOCK:
        fresh = _planned(spec, layout, params, as_of)
        if fresh["digest"] != claimed:
            return _error(spec, "stale-plan", "the plan changed since it was shown; review the fresh plan", fresh)
        reason = spec.refused(fresh["plan"], params)
        if reason is not None:
            return _error(spec, reason, f"{spec.command}: plan is {reason}; nothing was applied", fresh["plan"])

        def check_candidate(candidate: object) -> None:
            # This is core's actual candidate, not another independent dry run.
            projection = spec.project(candidate, params, True)
            stamp = format_instant(as_of)
            prepared = {"as_of": stamp, "digest": digest(spec.route, params, stamp, projection), "plan": projection}
            if prepared["digest"] != claimed:
                raise _Refusal(
                    _error(spec, "stale-plan", "the plan changed before apply; review the fresh plan", prepared)
                )

        result = _call(spec, layout, params, as_of, False, check_candidate)
        if incomplete(result):
            return _error(spec, "incomplete-apply", f"{spec.command}: apply was incomplete", result)
        # Live-only gates and core snapshot preconditions can still refuse
        # after the candidate matched; they do not select a new candidate.
        reason = spec.refused(result, params)
        if reason is not None:
            return _error(spec, reason, f"{spec.command}: apply was {reason}; nothing was applied", result)
    return Outcome(200, {"as_of": fresh["as_of"], "digest": fresh["digest"], "result": result})
