"""The package imports, is typed, and declares zero runtime dependencies."""

from __future__ import annotations

import tomllib
from pathlib import Path

import subagents_io

PKG_ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> dict:
    return tomllib.loads((PKG_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_static():
    assert subagents_io.__version__ == _metadata()["project"]["version"]


def test_ships_a_py_typed_marker():
    assert (PKG_ROOT / "src" / "subagents_io" / "py.typed").is_file()


def test_declares_its_one_runtime_dependency_and_no_extras():
    # A runtime dependency here is a scope decision, not an implementation
    # detail — which is why it is asserted by exact list rather than by
    # membership. `runner.py` constructs SystemMessage/HumanMessage, so
    # langchain-core is that decision taken deliberately; the floor matches the
    # workspace's own resolved line rather than reaching back to a 0.x one
    # nothing here installs or tests.
    #
    # The second assertion is a different rule and does NOT move: an optional
    # dependency is still a declared edge, so there is no `subagents-io[bedrock]`
    # and never will be. That is what keeps the foundation a strict layer.
    meta = _metadata()
    assert meta["project"]["dependencies"] == ["langchain-core>=1.4"]
    assert "optional-dependencies" not in meta["project"]


def test_all_is_sorted_and_bound():
    assert subagents_io.__all__ == sorted(subagents_io.__all__)
    for name in subagents_io.__all__:
        assert hasattr(subagents_io, name), name


def test_the_public_surface_is_the_spec_s_module_table():
    # Exactly these fifty-six. The two writers are surface because they have
    # real consumers with no pool in sight; `PriceLookup` is surface because it
    # is the type of a parameter two of those callables accept. The dispatch
    # seven are surface because they exist for a consumer that has not been
    # written yet — an unexported value type would be unreachable.
    #
    # The fifteen the backend seam added: the two Protocols and the two
    # vocabularies are the contract a backend implements; the record and the
    # four event members are what a coordinator reads; the four errors are
    # what it catches, `BackendError` being the base a caller catches to get
    # all of them. `WorkerEventBase` is surface because a coordinator writing
    # a generic ack/dedupe helper annotates against it, and `WorkerEvent`
    # because that is the union it matches on.
    #
    # The eleven the read half added (D-039/D-042): `read_trace_records` plus
    # its two result types (`TraceFile`, `TraceWarning`) and the version floor
    # it warns against (`KNOWN_SCHEMA_VERSION`); the aggregate
    # (`TraceAggregate`, `RoleTotals`, `RoleModelTotals`) and the function that
    # builds it (`aggregate_trace`); `is_groupable`, shared by the aggregate
    # and by `collapse_runs`, the maximal-run partition a default-view renderer
    # consumes via `render_collapsed_group`.
    assert set(subagents_io.__all__) == {
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
    }
