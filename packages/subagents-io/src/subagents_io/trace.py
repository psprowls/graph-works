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
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

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
