"""Trace I/O for the fan-out pool: the JSONL record writer and its renderer.

Houses the shared JSONL trace-record writer (`write_trace_record`), the shared
line renderer (`render_trace_record`), and the per-model USD cost computation
(`_compute_cost_usd`). They live here rather than in `pool.py` so every call
site — the pool, and callers that write records with no pool at all — emits
identically-shaped records without duplicating the construction logic.

Key invariants, unchanged from the pool this was extracted from:
- schema_version: 1 on every record
- usage_metadata is None-guarded — a throttled or content-filtered response
  returns None where a successful one returns a dict
- OSError on write is caught + logged WARNING — never raises. A trace failure
  must not mask a successful task result.
- cost_usd is computed from (model_id, tokens_in, tokens_out) through a
  CALLER-SUPPLIED lookup. This package never imports a price table; see the
  README's "Cost accounting is opt-in".

The read half lives here too: `read_trace_records` and the aggregation over
what it returns. The package that stamps `schema_version` at write time owns
classifying it at read time -- leaving that in a CLI, as the legacy renderer
did, put the format's own version policy outside the package that writes it
(D-039).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: The logger `pool.py` emits per-item completion lines to, published so a
#: consumer binds to a symbol rather than to a string literal. The two halves
#: are deliberately independent — `pool.py` derives its logger from `__name__`
#: and never reads this constant — and
#: `test_trace_logger_name_matches_the_logger_the_pool_writes_to` is what keeps
#: them from drifting apart. A rename that misses one half fails that test
#: instead of silently detaching a downstream handler.
TRACE_LOGGER_NAME = "subagents_io.pool.trace"

#: A caller-supplied cost function: (model_id, usage) -> USD. `models_io.pricing.cost_for_usage`
#: satisfies it. `usage` is a `Mapping` because callable parameters are
#: contravariant — a function declaring `dict` would not be assignable here.
PriceLookup = Callable[[str, Mapping[str, int]], float]

#: The highest `schema_version` this reader was authored against. Records above
#: it still render -- a lenient consumer -- but the file warns once. Promoted
#: out of the legacy CLI into the module that stamps the integer at write time,
#: so the two halves can no longer drift (D-039).
KNOWN_SCHEMA_VERSION = 1

#: What a reader can complain about. Never an exit code; the CLI decides how
#: these read on stderr.
TraceWarningKind = Literal["unversioned", "future-version", "malformed-line"]


@dataclass(frozen=True, slots=True)
class TraceWarning:
    """One complaint about a trace file. `line` is 1-based, and set only for
    `malformed-line`; `observed` is the version seen (0 for an unversioned
    record, which is the shape the reader infers)."""

    kind: TraceWarningKind
    line: int | None = None
    observed: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TraceFile:
    """A parsed trace file: what could be read, and what was wrong with it."""

    path: Path
    records: tuple[Mapping[str, Any], ...]
    warnings: tuple[TraceWarning, ...]


def read_trace_records(path: Path) -> TraceFile:
    """Read a JSONL trace file, tolerantly.

    Blank lines are skipped. A line that is not JSON, or that is JSON but not
    an object, is dropped with one `malformed-line` warning naming its line
    number. Version warnings dedupe per file -- at most one `unversioned` and
    one `future-version` however many records qualify, matching the legacy
    renderer's once-per-file rule. A non-integer `schema_version` warns nothing
    and the record is kept (T-09-15).

    **Records stay `Mapping[str, Any]`.** The record shape is open by
    contract, which is what the leniency policy means; typing one would fight
    the tolerance this function exists to provide. Only the aggregate is typed.

    `OSError` from reading *path* propagates: a missing trace file is a caller
    error, not file content, and the caller checked.
    """
    records: list[Mapping[str, Any]] = []
    warnings: list[TraceWarning] = []
    warned_unversioned = False
    warned_future = False

    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(TraceWarning(kind="malformed-line", line=number, detail=exc.msg))
            continue
        if not isinstance(record, dict):
            warnings.append(TraceWarning(kind="malformed-line", line=number, detail="line is not a JSON object"))
            continue
        if "schema_version" not in record:
            if not warned_unversioned:
                warnings.append(TraceWarning(kind="unversioned", observed=0))
                warned_unversioned = True
        else:
            observed = record["schema_version"]
            if isinstance(observed, int) and observed > KNOWN_SCHEMA_VERSION and not warned_future:
                warnings.append(TraceWarning(kind="future-version", observed=observed))
                warned_future = True
        records.append(record)

    return TraceFile(path=path, records=tuple(records), warnings=tuple(warnings))


def write_trace_record(
    path: Path,
    role: str,
    model_id: str,
    item: Any,  # noqa: ANN401 -- the caller's per-item input; this module only reads .id or str()
    status: str,
    latency_ms: int,
    response: Any,  # noqa: ANN401 -- whatever the provider SDK returned; only usage_metadata is read
    *,
    error: str | None = None,
    price_lookup: PriceLookup | None = None,
) -> dict[str, Any]:
    """Write one JSONL trace record. Never raises.

    Args:
        path: Per-run JSONL trace file (caller chooses the filename).
        role: Logical role name (e.g. "scanner", "ingestor", "synthesizer").
        model_id: Model ID resolved at call time.
        item: Per-item input — id-bearing object preferred, else str(item).
        status: "success" | "cancelled" | "error".
        latency_ms: Wall-clock latency for this invocation.
        response: Provider response object, or None on error/cancel.
        error: Exception string when status == "error".
        price_lookup: Cost function. Omit it and `cost_usd` is null in every
            record written — see the README.

    Token fields come from the response's usage_metadata dict:
    {"input_tokens": N, "output_tokens": N, "total_tokens": N}. usage_metadata
    is None on error responses — guarded explicitly.
    """
    tokens_in: int | None = None
    tokens_out: int | None = None
    if response is not None and hasattr(response, "usage_metadata"):
        meta = response.usage_metadata  # None on throttling / content filter
        # Defensive isinstance(dict) check — guards against bare-MagicMock test
        # responses where usage_metadata auto-resolves to a MagicMock object that
        # is neither None nor dict-like. Real provider responses are always
        # dict or None.
        if isinstance(meta, dict):
            tokens_in = meta.get("input_tokens")
            tokens_out = meta.get("output_tokens")

    record: dict[str, Any] = {
        "schema_version": 1,  # every record self-describing
        "role": role,
        "model_id": model_id,
        "prompt_hash": None,  # caller may set; None until computed upstream
        "item_id": getattr(item, "id", None) or str(item),
        "status": status,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": _compute_cost_usd(model_id, tokens_in, tokens_out, price_lookup),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if error:
        record["error"] = error

    try:
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Trace write failed (data loss): %s", exc)

    return record


def render_trace_record(record: dict[str, Any]) -> str:
    """Return a single-line human-readable representation of a trace record.

    Single source of truth for the per-record line format, shared by the live
    fan-out log (subagents_io.pool) and any post-hoc viewer.

    Fields: timestamp role model_id(last 30 chars) item_id(first 40 chars)
            status latency_ms tokens_in -> tokens_out
    Error records append: ERROR: <error message>
    Missing fields are substituted with '-' so .get() never raises KeyError.
    """
    timestamp = record.get("timestamp", "-")
    role = record.get("role", "-")
    model_id = record.get("model_id", "-")
    model_short = model_id[-30:] if model_id != "-" else "-"
    item_id = record.get("item_id", "-")
    item_short = item_id[:40] if item_id != "-" else "-"
    status = record.get("status", "-")
    latency_ms = record.get("latency_ms", "-")
    tokens_in = record.get("tokens_in", "-")
    tokens_out = record.get("tokens_out", "-")

    line = f"[{timestamp}] {role} {model_short} {item_short} {status} {latency_ms}ms {tokens_in}->{tokens_out}"
    if record.get("status") == "error":
        line += f"  ERROR: {record.get('error', '')}"
    return line


def _compute_cost_usd(
    model_id: str,
    tokens_in: int | None,
    tokens_out: int | None,
    price_lookup: PriceLookup | None,
) -> float | None:
    """Compute USD cost from token counts through the caller's price lookup.

    Returns None for three reasons through one path: no lookup was supplied,
    the token counts are unavailable, or the lookup does not price this model.
    """
    if price_lookup is None or tokens_in is None or tokens_out is None:
        return None
    try:
        # models_io.pricing.UnknownModelError subclasses KeyError, so catching
        # KeyError covers it without this package naming that type.
        return price_lookup(model_id, {"input": tokens_in, "output": tokens_out})
    except KeyError:
        return None


@dataclass(frozen=True, slots=True)
class RoleTotals:
    count: int
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True, slots=True)
class RoleModelTotals:
    role: str
    model_id: str
    count: int
    tokens_in: int
    tokens_out: int
    cost_usd_sum: float
    unknown_cost_count: int

    @property
    def fully_unknown(self) -> bool:
        """No record in this group carried a cost -- the `$n/a` case."""
        return self.count == self.unknown_cost_count


@dataclass(frozen=True, slots=True)
class TraceAggregate:
    """Per-role and per-(role, model) rollups over one trace.

    `by_role_model` is keyed by `tuple[str, str]`, not the legacy
    `"<role>|<model_id>"` string: the join was a workaround for dict keys and
    is ambiguous the day a role contains `|`.
    """

    by_role: Mapping[str, RoleTotals]
    by_role_model: Mapping[tuple[str, str], RoleModelTotals]
    total_records: int
    total_tokens_in: int
    total_tokens_out: int

    def cost_rollup(self) -> tuple[RoleModelTotals, ...]:
        """Groups ordered for the cost table: at least one known cost first, by
        descending `cost_usd_sum`; fully-unknown groups last; ties broken on
        ascending `(role, model_id)`.

        A fact about the aggregate, not about how a terminal prints it -- which
        is why it is here and not in the twelve lines of CLI it replaces.
        """
        groups = tuple(self.by_role_model.values())
        known = sorted(
            (totals for totals in groups if not totals.fully_unknown),
            key=lambda totals: (-totals.cost_usd_sum, totals.role, totals.model_id),
        )
        unknown = sorted(
            (totals for totals in groups if totals.fully_unknown),
            key=lambda totals: (totals.role, totals.model_id),
        )
        return (*known, *unknown)


@dataclass(slots=True)
class _RoleAccumulator:
    count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(slots=True)
class _RoleModelAccumulator:
    role: str
    model_id: str
    count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd_sum: float = 0.0
    unknown_cost_count: int = 0


def is_groupable(record: Mapping[str, Any]) -> bool:
    """A record is groupable iff it carries no `event` and no `kind` key (D-11).

    Those two are discriminators for records that describe something other
    than one model invocation, and they have no `role` to bucket under.
    """
    return "event" not in record and "kind" not in record


def aggregate_trace(records: Sequence[Mapping[str, Any]]) -> TraceAggregate:
    """Roll a trace up per role and per (role, model_id).

    `None` token values count as 0 and no input record is mutated.

    **`total_records` counts every record while both breakdowns skip
    non-groupable ones.** That asymmetry is the WR-02 fix, kept deliberately:
    it holds the Summary block's "Total records" line backward-compatible while
    stopping a `kind:`-bearing record -- which has no `role` -- from
    synthesizing a phantom `unknown:` bucket in the per-role breakdown.

    A non-numeric `cost_usd` raises rather than silently mis-summing (T-09-06):
    production writers emit `float` or `None`, so a string means a malformed
    producer and hiding it would be worse than failing on it.
    """
    by_role: dict[str, _RoleAccumulator] = {}
    by_role_model: dict[tuple[str, str], _RoleModelAccumulator] = {}
    total_tokens_in = 0
    total_tokens_out = 0

    for record in records:
        tokens_in = record.get("tokens_in") or 0
        tokens_out = record.get("tokens_out") or 0
        total_tokens_in += tokens_in
        total_tokens_out += tokens_out

        if not is_groupable(record):
            continue

        role = record.get("role", "unknown")
        model_id = record.get("model_id", "unknown")

        role_totals = by_role.setdefault(role, _RoleAccumulator())
        role_totals.count += 1
        role_totals.tokens_in += tokens_in
        role_totals.tokens_out += tokens_out

        totals = by_role_model.setdefault(
            (role, model_id),
            _RoleModelAccumulator(role=role, model_id=model_id),
        )
        totals.count += 1
        totals.tokens_in += tokens_in
        totals.tokens_out += tokens_out
        cost = record.get("cost_usd")
        if cost is None:
            totals.unknown_cost_count += 1
        else:
            totals.cost_usd_sum += float(cost)

    return TraceAggregate(
        by_role=MappingProxyType(
            {
                role: RoleTotals(
                    count=accumulated.count,
                    tokens_in=accumulated.tokens_in,
                    tokens_out=accumulated.tokens_out,
                )
                for role, accumulated in by_role.items()
            }
        ),
        by_role_model=MappingProxyType(
            {
                key: RoleModelTotals(
                    role=accumulated.role,
                    model_id=accumulated.model_id,
                    count=accumulated.count,
                    tokens_in=accumulated.tokens_in,
                    tokens_out=accumulated.tokens_out,
                    cost_usd_sum=accumulated.cost_usd_sum,
                    unknown_cost_count=accumulated.unknown_cost_count,
                )
                for key, accumulated in by_role_model.items()
            }
        ),
        total_records=len(records),
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
    )


def collapse_runs(records: Sequence[Mapping[str, Any]]) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    """Partition *records* into maximal runs of consecutive groupable records
    sharing `(role, model_id)`.

    Every record lands in exactly one run, in file order: a non-groupable
    record and an isolated groupable record are each their own one-element
    run, so a caller renders `render_collapsed_group(run)` when
    `len(run) >= 2` and `render_trace_record(run[0])` otherwise, and needs no
    other branch.

    Keyed on `(role, model_id)` rather than `role` alone (CR-01) so a
    mixed-model fan-out renders as distinct lines and stays in parity with the
    cost rollup.
    """
    runs: list[tuple[Mapping[str, Any], ...]] = []
    current: list[Mapping[str, Any]] = []

    for record in records:
        if not is_groupable(record):
            if current:
                runs.append(tuple(current))
                current = []
            runs.append((record,))
            continue
        if (
            current
            and current[-1].get("role") == record.get("role")
            and current[-1].get("model_id") == record.get("model_id")
        ):
            current.append(record)
            continue
        if current:
            runs.append(tuple(current))
        current = [record]

    if current:
        runs.append(tuple(current))
    return tuple(runs)


def render_collapsed_group(records: Sequence[Mapping[str, Any]]) -> str:
    """One summary line for a run of records sharing `(role, model_id)` (D-13).

        [<ts_first> .. <ts_last>] <role> / <model_short> x<N>: <statuses>, <tin>-><tout> tokens, <cost>

    `model_short` is the last 30 characters of `model_id`, mirroring the cost
    rollup's convention. The status breakdown lists only nonzero categories in
    canonical order -- success, error, cancelled, then `other`, which exists so
    a producer-added status surfaces instead of silently vanishing (WR-03).
    Cost is `$<sum:.6f>`, gaining ` (+<K> unknown)` when some records carry no
    cost, and `$n/a (<N> unknown)` when none of them do.

    Timestamps are the literal `timestamp` fields of the first and last records
    in the run, as written.
    """
    total = len(records)
    first_timestamp = records[0].get("timestamp", "-")
    last_timestamp = records[-1].get("timestamp", "-")
    role = records[0].get("role", "-")
    model_id = records[0].get("model_id", "-")
    model_short = model_id[-30:] if model_id and model_id != "-" else "-"

    counts = {"success": 0, "error": 0, "cancelled": 0, "other": 0}
    for record in records:
        status = record.get("status")
        if status in ("success", "error", "cancelled"):
            counts[status] += 1
        else:
            counts["other"] += 1
    parts = [f"{counts[name]} {name}" for name in ("success", "error", "cancelled", "other") if counts[name]]
    breakdown = " / ".join(parts) if parts else f"{total} unknown"

    tokens_in = sum((record.get("tokens_in") or 0) for record in records)
    tokens_out = sum((record.get("tokens_out") or 0) for record in records)

    cost_sum = 0.0
    unknown = 0
    for record in records:
        cost = record.get("cost_usd")
        if cost is None:
            unknown += 1
        else:
            cost_sum += float(cost)

    if unknown == total:
        cost = f"$n/a ({total} unknown)"
    elif unknown:
        cost = f"${cost_sum:.6f} (+{unknown} unknown)"
    else:
        cost = f"${cost_sum:.6f}"

    return (
        f"[{first_timestamp} .. {last_timestamp}] {role} / {model_short} x{total}: "
        f"{breakdown}, {tokens_in}->{tokens_out} tokens, {cost}"
    )
