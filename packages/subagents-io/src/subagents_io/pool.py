"""SubagentPool: async fan-out primitive for role-bound Bedrock model dispatch.

Dispatches N items in parallel through a caller-supplied async task function,
enforces a per-role concurrency semaphore, isolates per-item failures, and
writes a JSONL trace record for every invocation (success or error).

Usage:
    from models_io.pricing import cost_for_usage

    pool = SubagentPool(trace_dir=Path("traces"), price_lookup=cost_for_usage)
    result = await pool.run_all(
        items=pages,
        task=summarize,          # async def summarize(item) -> AIMessage
        role="librarian",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        max_concurrency=5,
    )
    # result.successes -> [(item, response), ...]
    # result.errors    -> [PerItemError(item=..., exception=...), ...]

Security note: task closures must not embed AWS credentials in prompts or
item identifiers. _write_trace reads item_id (str(item)), error=str(exc),
and structured usage_metadata fields only — no secrets unless the caller
leaks them into item repr or exception messages.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from subagents_io.trace import PriceLookup, render_trace_record, write_trace_record

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)
# Dedicated logger for per-item completion lines (trace format). Published as
# `subagents_io.TRACE_LOGGER_NAME` so a consumer attaches a bare-message
# handler by symbol rather than by string literal. Derived from __name__ here,
# on purpose: the constant and this line are independent halves, and
# test_pool_logging.py is what holds them together.
_trace_logger = logging.getLogger(__name__ + ".trace")


@dataclass
class PerItemError:
    """Captures the item and exception for a single failed fan-out task."""

    item: Any
    exception: Exception


@dataclass
class FanOutResult:
    """Aggregated result from a run_all() call.

    successes: list of (item, result) tuples for tasks that returned normally.
    errors: list of PerItemError for tasks that raised an exception.
    """

    successes: list[tuple[Any, Any]] = field(default_factory=list)
    errors: list[PerItemError] = field(default_factory=list)


@dataclass
class TaskResult:
    """Opt-in fan-out callback return type that surfaces the raw LLM response.

    Phase 16-02 G-01 closure: callbacks that want their JSONL trace record
    to carry ``tokens_in`` / ``tokens_out`` / ``cost_usd`` can return
    ``TaskResult(value=<scalar>, response=<raw_AIMessage>)``. The pool feeds
    ``response`` (carrying ``usage_metadata``) to ``write_trace_record`` and
    unwraps ``value`` into ``FanOutResult.successes`` so downstream consumers
    see the same scalar they do today.

    Detection in ``_run_one`` is strict ``isinstance(result, TaskResult)`` —
    NOT duck-typing on ``.value`` / ``.response``, which would collide with
    arbitrary user-returned objects.

    Backward-compatible: callbacks returning bare scalars (strings, lists,
    dicts) continue to work — the bare value flows into ``successes`` and the
    trace record's tokens stay ``None`` (matching prior behavior, since a
    string has no ``usage_metadata`` attribute).
    """

    value: Any
    response: Any = None


class SubagentPool:
    """Concurrent fan-out primitive: dispatches N items to a role-bound model.

    Critical design rules:
    - asyncio.Semaphore is created INSIDE run_all() (not __init__) to bind
      to the currently running event loop. Creating it in __init__ (a sync
      context) causes RuntimeError in pytest-asyncio envs that spin their
      own loops.
    - asyncio.gather(return_exceptions=True) is mandatory. Without it, the
      first task exception cancels all siblings (deepagents bug #694).
    - _write_trace catches OSError internally and logs a WARNING. It never
      raises — a trace failure must not mask a successful task result.
    - usage_metadata is None on Bedrock error responses. Always guard before
      accessing meta.get(...) to avoid AttributeError (deepagents #1698 /
      AI-SPEC Failure Mode #5).
    """

    def __init__(
        self,
        trace_dir: Path,
        *,
        default_recursion_limit: int = 100,
        price_lookup: PriceLookup | None = None,
    ) -> None:
        self._trace_dir = trace_dir
        self._default_recursion_limit = default_recursion_limit
        self._price_lookup = price_lookup
        self._trace_dir.mkdir(parents=True, exist_ok=True)

    async def run_all(
        self,
        items: list[Any],
        task: Callable[..., Awaitable[Any]],
        role: str,
        *,
        model_id: str,
        max_concurrency: int,
        recursion_limit: int | None = None,
    ) -> FanOutResult:
        """Dispatch items in parallel; return FanOutResult with partial-failure isolation.

        Args:
            items: Batch of items to process.
            task: Async callable (item) -> result. May raise; raised exception
                  is captured as PerItemError without cancelling siblings.
            role: Logical role name (e.g. "scanner") — written to trace.
            model_id: Bedrock model ID — written to trace.
            max_concurrency: Maximum simultaneous in-flight tasks.
            recursion_limit: LangGraph recursion limit injected into every
                task's RunnableConfig. Falls back to default_recursion_limit
                when None.
        """
        rlimit = recursion_limit if recursion_limit is not None else self._default_recursion_limit
        # Semaphore MUST be created here (inside the running event loop) —
        # creating it in __init__ binds to a different loop in test envs.
        semaphore = asyncio.Semaphore(max_concurrency)
        # Unix timestamp prefix preserves chronological ordering; 8-hex UUID suffix gives
        # ~4B combinations per second of collision resistance — sufficient for any realistic
        # fan-out workload (CR-02 fix: uniqueness within same wall-clock second).
        trace_file = self._trace_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jsonl"

        # WR-05 (D-05): hoist signature inspection out of the per-item hot path.
        # Computed once per fan-out instead of once per item. Tasks whose signature
        # cannot be introspected (ValueError/TypeError — e.g. C-implemented callables)
        # fall back to single-arg form, preserving prior behavior.
        try:
            _task_arity_2 = len(inspect.signature(task).parameters) >= 2
        except (ValueError, TypeError):
            _task_arity_2 = False

        async def _run_one(item: Any) -> tuple[Any, Any] | PerItemError:  # noqa: ANN401 -- one of the caller's items
            async with semaphore:
                logger.debug("-> item start: %s", getattr(item, "id", None) or str(item))
                t0 = time.monotonic()
                try:
                    # RunnableConfig is a TypedDict — this constructs the plain
                    # dict it describes, with no langchain import at runtime.
                    # The top-level key is correct; do NOT nest it under
                    # "configurable", which is ignored.
                    _config: RunnableConfig = {"recursion_limit": rlimit}
                    # SUB-04 / ROADMAP SC#2: deliver the RunnableConfig (carrying recursion_limit)
                    # to any task that declares (item, config). Single-arg tasks remain supported
                    # for backward compatibility with current unit tests and Plan 03 closures.
                    if _task_arity_2:
                        result = await task(item, _config)
                    else:
                        result = await task(item)
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    # Phase 16-02 G-01: if the callback opts into the TaskResult
                    # contract, feed `response` (carrying usage_metadata) to the
                    # trace writer and unwrap `value` into successes so downstream
                    # consumers continue to see the same scalar.
                    if isinstance(result, TaskResult):
                        trace_response = result.response
                        success_value = result.value
                    else:
                        trace_response = result
                        success_value = result
                    self._write_trace(trace_file, role, model_id, item, "success", latency_ms, trace_response)
                    return (item, success_value)
                except asyncio.CancelledError:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    self._write_trace(trace_file, role, model_id, item, "cancelled", latency_ms, None)
                    raise  # MUST re-raise — outer cancel must propagate
                except Exception as exc:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    self._write_trace(trace_file, role, model_id, item, "error", latency_ms, None, error=str(exc))
                    return PerItemError(item=item, exception=exc)

        logger.info(
            "-> fan-out start: role=%s model=%s items=%d concurrency=%d",
            role,
            model_id,
            len(items),
            max_concurrency,
        )

        # return_exceptions=True: one failure does NOT cancel siblings (deepagents #694).
        batch_t0 = time.monotonic()
        try:
            raw = await asyncio.gather(*(_run_one(i) for i in items), return_exceptions=True)
        except asyncio.CancelledError:
            wall_ms = int((time.monotonic() - batch_t0) * 1000)
            self._write_batch_terminal(
                trace_file,
                role,
                model_id,
                items_total=len(items),
                items_completed=0,
                items_cancelled=len(items),
                wall_clock_ms=wall_ms,
            )
            raise  # MUST re-raise — FastMCP anyio CancelScope expects this

        fan_result = FanOutResult()
        for r in raw:
            if isinstance(r, PerItemError):
                fan_result.errors.append(r)
            elif isinstance(r, BaseException):
                # asyncio.gather itself raised — should not happen with return_exceptions=True
                logger.error("Unexpected gather exception: %s", r)
            else:
                fan_result.successes.append(r)
        logger.info(
            "ok fan-out done: %d ok / %d err in %.2fs",
            len(fan_result.successes),
            len(fan_result.errors),
            time.monotonic() - batch_t0,
        )
        return fan_result

    def _write_trace(
        self,
        path: Path,
        role: str,
        model_id: str,
        item: Any,  # noqa: ANN401 -- the caller's per-item input; only .id or str() is read
        status: str,
        latency_ms: int,
        response: Any,  # noqa: ANN401 -- whatever the task returned; only usage_metadata is read
        *,
        error: str | None = None,
    ) -> None:
        """Thin delegate to subagents_io.trace.write_trace_record.

        Also emits the per-item completion line at INFO through the dedicated
        fan-out trace logger (`TRACE_LOGGER_NAME`) so a caller can surface live
        progress. The pool stays ignorant of verbosity — it always logs; the
        installed handler decides what shows.
        """
        record = write_trace_record(
            path,
            role,
            model_id,
            item,
            status,
            latency_ms,
            response,
            error=error,
            price_lookup=self._price_lookup,
        )
        _trace_logger.info(render_trace_record(record))

    def _write_batch_terminal(
        self,
        path: Path,
        role: str,
        model_id: str,
        *,
        items_total: int,
        items_completed: int,
        items_cancelled: int,
        wall_clock_ms: int,
    ) -> None:
        """Write the batch_cancelled summary record. Never raises.

        The ``event`` field discriminates this record from per-item trace records
        (which have no ``event`` key). Readers in Phase 9 branch on ``event`` presence.
        OSError on write is logged at WARNING and swallowed (AI-SPEC Failure Mode #2).
        """
        record: dict[str, Any] = {
            "schema_version": 1,  # Phase 9 OBS-04 D-01/D-02 — every record self-describing
            "role": role,
            "model_id": model_id,
            "event": "batch_cancelled",
            "items_total": items_total,
            "items_completed": items_completed,
            "items_cancelled": items_cancelled,
            "wall_clock_ms": wall_clock_ms,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning("Batch terminal trace write failed: %s", exc)
