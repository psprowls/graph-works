"""Generic subagent invocation: stream a model call, aggregate usage, parse.

Four things the legacy runner imported are arguments here, which is the whole
of what makes this band 1:

    load_role_config(adapter.role)          -> binding.spec
    make_llm(adapter.role)                  -> binding.make_llm()
    cost_for_usage(...)                     -> price_lookup=
    ws_paths.graph_dir(ctx.workspace)       -> trace_dir=

`langchain_core.messages` is imported at runtime and is the reason this
package declares a dependency at all: `SystemMessage` and `HumanMessage` are
constructed here, not merely annotated. `tests/test_boundaries.py` allows
exactly that one third-party root and nothing else.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from subagents_io.adapters import Adapter, Closeable, LoopAdapter, LoopOutcome, RunContext
from subagents_io.pool import FanOutResult, SubagentPool, TaskResult
from subagents_io.roles import RoleBinding
from subagents_io.trace import PriceLookup


@dataclass
class RunOutcome:
    """One single-item run: the prompt, the raw text, the parse, the footer."""

    item_id: str
    role: str
    model_id: str
    region: str | None
    system: str
    human: str
    raw: str
    parsed: Any | None
    parse_error: str | None
    tokens_in: int | None
    tokens_out: int | None
    latency_s: float
    cost_usd: float | None
    interrupted: bool = False
    note: str | None = None


def _coerce_content(content: Any) -> str:  # noqa: ANN401 -- whatever the provider SDK put on the chunk
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # langchain content-block lists
        out = []
        for block in content:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                out.append(str(block.get("text", "")))
        return "".join(out)
    return str(content or "")


def _usage(agg: Any) -> tuple[int | None, int | None]:  # noqa: ANN401 -- the accumulated chunk; only usage_metadata is read
    meta = getattr(agg, "usage_metadata", None)
    if isinstance(meta, dict):
        return meta.get("input_tokens"), meta.get("output_tokens")
    return None, None


def _cost(
    price_lookup: PriceLookup | None,
    model_id: str,
    tin: int | None,
    tout: int | None,
) -> float | None:
    """Cost through the injected lookup, or None. Never raises.

    With no lookup injected the result is None — the same silence the pool
    keeps under the same condition, and the sharp edge the README names.
    """
    if price_lookup is None or tin is None or tout is None:
        return None
    try:
        # models_io.pricing.UnknownModelError subclasses KeyError.
        return price_lookup(model_id, {"input": tin, "output": tout})
    except KeyError:
        return None


async def stream_and_parse(
    llm: Any,  # noqa: ANN401 -- any BaseChatModel-shaped object exposing .astream
    *,
    system: str,
    human: str,
    parse: Callable[[str], Any] | None,
    do_parse: bool,
    on_chunk: Callable[[str], None],
) -> tuple[str, Any, str | None, int | None, int | None, float, bool]:
    """Stream the model, accumulate chunks/usage, then parse. Never raises on parse."""
    agg: Any = None
    raw_parts: list[str] = []
    interrupted = False
    start = time.monotonic()
    try:
        async for chunk in llm.astream([SystemMessage(content=system), HumanMessage(content=human)]):
            text = _coerce_content(getattr(chunk, "content", ""))
            raw_parts.append(text)
            on_chunk(text)
            agg = chunk if agg is None else agg + chunk
    except KeyboardInterrupt:
        interrupted = True
    latency = time.monotonic() - start
    raw = "".join(raw_parts)
    tin, tout = _usage(agg)
    parsed: Any | None = None
    parse_error: str | None = None
    if do_parse and parse is not None and not interrupted:
        try:
            parsed = parse(raw)
        except Exception as exc:  # parser failures are surfaced, never fatal
            parse_error = f"{type(exc).__name__}: {exc}"
    return raw, parsed, parse_error, tin, tout, latency, interrupted


async def run_single[ReaderT: Closeable](
    adapter: Adapter[ReaderT],
    ctx: RunContext[ReaderT],
    item: str,
    *,
    binding: RoleBinding,
    do_parse: bool,
    on_chunk: Callable[[str], None],
    price_lookup: PriceLookup | None = None,
) -> RunOutcome:
    """Prepare one item, stream it through the bound model, build the outcome."""
    prepared = await adapter.prepare(ctx, item)
    llm = binding.make_llm()
    raw, parsed, perr, tin, tout, latency, interrupted = await stream_and_parse(
        llm,
        system=prepared.system,
        human=prepared.human,
        parse=prepared.parse,
        do_parse=do_parse,
        on_chunk=on_chunk,
    )
    return RunOutcome(
        item_id=prepared.item_id,
        role=adapter.role,
        model_id=binding.spec.model_id,
        region=binding.spec.region,
        system=prepared.system,
        human=prepared.human,
        raw=raw,
        parsed=parsed,
        parse_error=perr,
        tokens_in=tin,
        tokens_out=tout,
        latency_s=latency,
        cost_usd=_cost(price_lookup, binding.spec.model_id, tin, tout),
        interrupted=interrupted,
        note=prepared.note,
    )


async def run_all[ReaderT: Closeable](
    adapter: Adapter[ReaderT],
    ctx: RunContext[ReaderT],
    *,
    binding: RoleBinding,
    trace_dir: Path,
    price_lookup: PriceLookup | None = None,
) -> FanOutResult:
    """Fan out over the adapter's real worklist via SubagentPool.

    The binding carries a factory rather than a model because this closure
    calls it once per item.
    """
    items = adapter.items(ctx)  # raises ValueError for single-query adapters

    async def task(item: str) -> TaskResult:
        prepared = await adapter.prepare(ctx, item)
        llm = binding.make_llm()
        resp = await llm.ainvoke([SystemMessage(content=prepared.system), HumanMessage(content=prepared.human)])
        return TaskResult(value=getattr(resp, "content", ""), response=resp)

    pool = SubagentPool(trace_dir=trace_dir, price_lookup=price_lookup)
    return await pool.run_all(
        items=list(items),
        task=task,
        role=adapter.role,
        model_id=binding.spec.model_id,
        max_concurrency=binding.spec.max_concurrency,
    )


def _newest_trace(trace_dir: Path) -> str | None:
    # `*.jsonl` is not a directory name — it is the extension the pool in this
    # same package writes.
    if not trace_dir.exists():
        return None
    traces = sorted(trace_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return str(traces[-1]) if traces else None


async def run_loop[ReaderT: Closeable](
    adapter: LoopAdapter[ReaderT],
    ctx: RunContext[ReaderT],
    item: str,
    *,
    binding: RoleBinding,
    trace_dir: Path,
) -> LoopOutcome:
    """Run a tool-loop adapter; overlay model/region/latency/trace onto its outcome."""
    start = time.monotonic()
    partial = await adapter.run(ctx, item)
    latency = time.monotonic() - start
    return dataclasses.replace(
        partial,
        model_id=binding.spec.model_id,
        region=binding.spec.region,
        latency_s=latency,
        trace_path=_newest_trace(trace_dir),
    )
