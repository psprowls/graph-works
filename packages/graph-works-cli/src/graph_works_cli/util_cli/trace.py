"""`gw util trace` — the read side of the traces core writes to `<cache_dir>/traces/`.

Every domain rule lives in `subagents_io.trace`: the reader dedupes its warnings, the
aggregate computes the totals, `cost_rollup()` owns the ordering, and `collapse_runs`
owns the maximal-run detection. What is left here is the choice of stream (warnings to
stderr, records to stdout) and the cost strings.

This is the one verb with no `--workspace`: its only input is an explicit file path, so
there is nothing for a workspace to resolve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import typer
from subagents_io.trace import (
    TraceAggregate,
    TraceWarning,
    aggregate_trace,
    collapse_runs,
    read_trace_records,
    render_collapsed_group,
    render_trace_record,
)

from graph_works_cli.errors import exit_error


def _render_warning(warning: TraceWarning) -> str:
    """One stderr line per warning. Never touches the exit code — leniency is the point."""
    parts = [f"[warn] {warning.kind}"]
    if warning.line is not None:
        parts.append(f"line {warning.line}")
    if warning.observed is not None:
        parts.append(f"observed {warning.observed}")
    if warning.detail:
        parts.append(warning.detail)
    return ": ".join(parts) if len(parts) > 1 else parts[0]


def _render_cost(cost_usd_sum: float, unknown_cost_count: int, *, fully_unknown: bool) -> str:
    """`$n/a (N unknown)` when nothing is priced, `$X.XXXX (+K unknown)` when some of it is."""
    if fully_unknown:
        return f"$n/a ({unknown_cost_count} unknown)"
    if unknown_cost_count:
        return f"${cost_usd_sum:.4f} (+{unknown_cost_count} unknown)"
    return f"${cost_usd_sum:.4f}"


def _render_summary(aggregate: TraceAggregate) -> str:
    return "\n".join(
        [
            "",
            "Summary",
            f"  Total records: {aggregate.total_records}",
            f"  Total tokens in: {aggregate.total_tokens_in}",
            f"  Total tokens out: {aggregate.total_tokens_out}",
        ]
    )


def _render_rollup(aggregate: TraceAggregate) -> str:
    """Iterate `cost_rollup()` as given: the sort is a fact about the aggregate, not
    about how a terminal prints it."""
    lines = ["", "Cost by role and model"]
    for totals in aggregate.cost_rollup():
        cost = _render_cost(totals.cost_usd_sum, totals.unknown_cost_count, fully_unknown=totals.fully_unknown)
        lines.append(f"  {totals.role} {totals.model_id} {totals.count} calls {cost}")
    return "\n".join(lines)


def trace(
    file: Path = typer.Argument(..., help="Path to a trace JSONL file."),  # noqa: B008 -- Typer declares CLI arguments in defaults
    expand: bool = typer.Option(False, "--expand", help="Render every record instead of collapsing runs."),
) -> None:
    """Render a subagent trace file with a summary and a per-role cost rollup."""
    try:
        trace_file = read_trace_records(file)
    except (OSError, ValueError) as exc:
        exit_error(str(exc), cause=exc)

    for warning in trace_file.warnings:
        typer.echo(_render_warning(warning), err=True)

    records: Sequence[Mapping[str, Any]] = trace_file.records
    if expand:
        for record in records:
            typer.echo(render_trace_record(dict(record)))
    else:
        for run in collapse_runs(records):
            typer.echo(render_collapsed_group(run) if len(run) >= 2 else render_trace_record(dict(run[0])))

    aggregate = aggregate_trace(records)
    typer.echo(_render_summary(aggregate))
    typer.echo(_render_rollup(aggregate))
