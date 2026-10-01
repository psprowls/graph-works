"""subagents-io: the band-1 half of subagent dispatch — pool, runner, roles, routing.

Band 1. This package never reads the process environment, never discovers a
workspace, and never knows a file or directory name. The trace directory is a
constructor argument; the price table is an injected callable; the graph reader
and the chat model are both caller-supplied.

    from models_io.pricing import cost_for_usage
    from subagents_io import RoleBinding, SubagentPool, resolve_role_spec

    pool = SubagentPool(trace_dir=Path("traces"), price_lookup=cost_for_usage)
    result = await pool.run_all(items, task, role="librarian", model_id=mid, max_concurrency=5)

It ships exactly one runtime dependency, `langchain-core`: `runner.py`
constructs `SystemMessage` and `HumanMessage` for real, and driving an injected
`BaseChatModel` is the runner's whole contract. That is a third-party package,
never a workspace one — `packages/subagents-io/tests/test_boundaries.py`
allowlists that single root and rejects every other, and no module here imports
`os` or a sibling package.

`adapters`, `roles` and `runner` are the generic run path: an adapter protocol
generic over a reader it knows only closes, role *resolution* with model
construction left to the caller, and the runner that streams one item or fans
out over a worklist. `dispatch` and `routing` are the planning half: the value
types a planner fills in to launch a worker, and the rules that decide which
model runs it. Both are pure — the routing functions take their vocabularies as
arguments, so nothing here has an opinion about what a `phase` or a `kind` is.

`backend` is the third piece of that planning half and the seam itself: two
Protocols a runner implements, the closed vocabularies a worker's lifecycle is
spelled in, and a discriminated event union. It ships no implementation — the
first one is `workflow-local`, a separate package, because that is where
vendor and system coupling is allowed to live and this package carves nothing
out.

Public names resolve on first access (PEP 562 `__getattr__` over `_EXPORTS`),
so `import subagents_io` does not import `langchain_core`; the `TYPE_CHECKING`
block mirrors the table for type checkers and import-linter.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.2.1"

_EXPORTS: dict[str, tuple[str, str]] = {
    "Adapter": ("subagents_io.adapters", "Adapter"),
    "Closeable": ("subagents_io.adapters", "Closeable"),
    "LoopAdapter": ("subagents_io.adapters", "LoopAdapter"),
    "LoopOutcome": ("subagents_io.adapters", "LoopOutcome"),
    "NoGraphReader": ("subagents_io.adapters", "NoGraphReader"),
    "Prepared": ("subagents_io.adapters", "Prepared"),
    "RunContext": ("subagents_io.adapters", "RunContext"),
    "EVENT_KINDS": ("subagents_io.backend", "EVENT_KINDS"),
    "WORKER_STATES": ("subagents_io.backend", "WORKER_STATES"),
    "BackendError": ("subagents_io.backend", "BackendError"),
    "DispatchBackend": ("subagents_io.backend", "DispatchBackend"),
    "DispatchSession": ("subagents_io.backend", "DispatchSession"),
    "Escalation": ("subagents_io.backend", "Escalation"),
    "Heartbeat": ("subagents_io.backend", "Heartbeat"),
    "UnknownWorker": ("subagents_io.backend", "UnknownWorker"),
    "UnsupportedMode": ("subagents_io.backend", "UnsupportedMode"),
    "WorkerDone": ("subagents_io.backend", "WorkerDone"),
    "WorkerEvent": ("subagents_io.backend", "WorkerEvent"),
    "WorkerEventBase": ("subagents_io.backend", "WorkerEventBase"),
    "WorkerQuestion": ("subagents_io.backend", "WorkerQuestion"),
    "WorkerRecord": ("subagents_io.backend", "WorkerRecord"),
    "WorktreeNotProvisioned": ("subagents_io.backend", "WorktreeNotProvisioned"),
    "DISPATCH_MODES": ("subagents_io.dispatch", "DISPATCH_MODES"),
    "WORKTREE_ACTIONS": ("subagents_io.dispatch", "WORKTREE_ACTIONS"),
    "PlannedDispatch": ("subagents_io.dispatch", "PlannedDispatch"),
    "WorktreeAction": ("subagents_io.dispatch", "WorktreeAction"),
    "FanOutResult": ("subagents_io.pool", "FanOutResult"),
    "PerItemError": ("subagents_io.pool", "PerItemError"),
    "SubagentPool": ("subagents_io.pool", "SubagentPool"),
    "TaskResult": ("subagents_io.pool", "TaskResult"),
    "RoleBinding": ("subagents_io.roles", "RoleBinding"),
    "RoleSpec": ("subagents_io.roles", "RoleSpec"),
    "resolve_role_spec": ("subagents_io.roles", "resolve_role_spec"),
    "ModelResolution": ("subagents_io.routing", "ModelResolution"),
    "resolve_model": ("subagents_io.routing", "resolve_model"),
    "validate_rules": ("subagents_io.routing", "validate_rules"),
    "RunOutcome": ("subagents_io.runner", "RunOutcome"),
    "run_all": ("subagents_io.runner", "run_all"),
    "run_loop": ("subagents_io.runner", "run_loop"),
    "run_single": ("subagents_io.runner", "run_single"),
    "stream_and_parse": ("subagents_io.runner", "stream_and_parse"),
    "KNOWN_SCHEMA_VERSION": ("subagents_io.trace", "KNOWN_SCHEMA_VERSION"),
    "TRACE_LOGGER_NAME": ("subagents_io.trace", "TRACE_LOGGER_NAME"),
    "PriceLookup": ("subagents_io.trace", "PriceLookup"),
    "RoleModelTotals": ("subagents_io.trace", "RoleModelTotals"),
    "RoleTotals": ("subagents_io.trace", "RoleTotals"),
    "TraceAggregate": ("subagents_io.trace", "TraceAggregate"),
    "TraceFile": ("subagents_io.trace", "TraceFile"),
    "TraceWarning": ("subagents_io.trace", "TraceWarning"),
    "aggregate_trace": ("subagents_io.trace", "aggregate_trace"),
    "collapse_runs": ("subagents_io.trace", "collapse_runs"),
    "is_groupable": ("subagents_io.trace", "is_groupable"),
    "read_trace_records": ("subagents_io.trace", "read_trace_records"),
    "render_collapsed_group": ("subagents_io.trace", "render_collapsed_group"),
    "render_trace_record": ("subagents_io.trace", "render_trace_record"),
    "write_trace_record": ("subagents_io.trace", "write_trace_record"),
}

if TYPE_CHECKING:
    from subagents_io.adapters import (
        Adapter,
        Closeable,
        LoopAdapter,
        LoopOutcome,
        NoGraphReader,
        Prepared,
        RunContext,
    )
    from subagents_io.backend import (
        EVENT_KINDS,
        WORKER_STATES,
        BackendError,
        DispatchBackend,
        DispatchSession,
        Escalation,
        Heartbeat,
        UnknownWorker,
        UnsupportedMode,
        WorkerDone,
        WorkerEvent,
        WorkerEventBase,
        WorkerQuestion,
        WorkerRecord,
        WorktreeNotProvisioned,
    )
    from subagents_io.dispatch import DISPATCH_MODES, WORKTREE_ACTIONS, PlannedDispatch, WorktreeAction
    from subagents_io.pool import FanOutResult, PerItemError, SubagentPool, TaskResult
    from subagents_io.roles import RoleBinding, RoleSpec, resolve_role_spec
    from subagents_io.routing import ModelResolution, resolve_model, validate_rules
    from subagents_io.runner import RunOutcome, run_all, run_loop, run_single, stream_and_parse
    from subagents_io.trace import (
        KNOWN_SCHEMA_VERSION,
        TRACE_LOGGER_NAME,
        PriceLookup,
        RoleModelTotals,
        RoleTotals,
        TraceAggregate,
        TraceFile,
        TraceWarning,
        aggregate_trace,
        collapse_runs,
        is_groupable,
        read_trace_records,
        render_collapsed_group,
        render_trace_record,
        write_trace_record,
    )

__all__ = [  # noqa: RUF022 -- sorted with plain `sorted()`, not isort's natural sort; test_all_is_sorted_and_bound holds this.
    "Adapter",
    "BackendError",
    "Closeable",
    "DISPATCH_MODES",
    "DispatchBackend",
    "DispatchSession",
    "EVENT_KINDS",
    "Escalation",
    "FanOutResult",
    "Heartbeat",
    "KNOWN_SCHEMA_VERSION",
    "LoopAdapter",
    "LoopOutcome",
    "ModelResolution",
    "NoGraphReader",
    "PerItemError",
    "PlannedDispatch",
    "Prepared",
    "PriceLookup",
    "RoleBinding",
    "RoleModelTotals",
    "RoleSpec",
    "RoleTotals",
    "RunContext",
    "RunOutcome",
    "SubagentPool",
    "TRACE_LOGGER_NAME",
    "TaskResult",
    "TraceAggregate",
    "TraceFile",
    "TraceWarning",
    "UnknownWorker",
    "UnsupportedMode",
    "WORKER_STATES",
    "WORKTREE_ACTIONS",
    "WorkerDone",
    "WorkerEvent",
    "WorkerEventBase",
    "WorkerQuestion",
    "WorkerRecord",
    "WorktreeAction",
    "WorktreeNotProvisioned",
    "aggregate_trace",
    "collapse_runs",
    "is_groupable",
    "read_trace_records",
    "render_collapsed_group",
    "render_trace_record",
    "resolve_model",
    "resolve_role_spec",
    "run_all",
    "run_loop",
    "run_single",
    "stream_and_parse",
    "validate_rules",
    "write_trace_record",
]


def __getattr__(name: str) -> Any:  # noqa: ANN401 - the PEP 562 shape
    """Resolve a public name on first access, then cache it in the module globals."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
