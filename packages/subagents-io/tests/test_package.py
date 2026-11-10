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


def test_declares_zero_runtime_dependencies_and_no_extras():
    # A runtime dependency here is a scope decision, so it is asserted by exact
    # list rather than by membership. `langchain-core` is dev-only: it lets
    # mypy resolve the two TYPE_CHECKING annotations (`BaseChatModel`,
    # `RunnableConfig`) and nothing at runtime imports it (D-002, D-005).
    #
    # The extras assertion is a different rule and does NOT move: an optional
    # dependency is still a declared edge, so there is no `subagents-io[bedrock]`
    # and never will be. That is what keeps the foundation a strict layer.
    meta = _metadata()
    assert meta["project"]["dependencies"] == []
    assert "optional-dependencies" not in meta["project"]
    assert meta["dependency-groups"]["dev"] == ["langchain-core>=1.4"]


def _lock_packages() -> dict[str, dict]:
    lock = tomllib.loads((PKG_ROOT.parents[1] / "uv.lock").read_text(encoding="utf-8"))
    return {p["name"]: p for p in lock["package"]}


def _runtime_closure(root: str) -> set[str]:
    """Names reachable from `root` through runtime edges only.

    Follows each `dependencies` entry (never `dev-dependencies`) and, when an
    entry names extras, the package's `optional-dependencies` for exactly those
    extras and nothing more.
    """
    packages = _lock_packages()
    seen: set[str] = set()
    todo = [(root, ())]
    while todo:
        name, extras = todo.pop()
        pkg = packages[name]
        edges = list(pkg.get("dependencies", []))
        for extra in extras:
            edges += pkg.get("optional-dependencies", {}).get(extra, [])
        for edge in edges:
            if edge["name"] not in seen:
                seen.add(edge["name"])
                todo.append((edge["name"], tuple(edge.get("extra", ()))))
    return seen


def test_the_closure_walker_reaches_subagents_io_from_each_consumer():
    for root in ("gw-kernel", "workflow-local", "workflow-orca"):
        assert "subagents-io" in _runtime_closure(root), root


def test_the_closure_walker_finds_langchain_core_where_it_is_declared():
    # Guards the guard: gw-wiki declares langchain-core itself, so a walker that
    # finds nothing there cannot be trusted to find nothing elsewhere.
    assert "langchain-core" in _runtime_closure("gw-wiki")


def test_langchain_core_is_not_in_the_runtime_closure_of_the_dispatch_consumers():
    for root in ("gw-kernel", "workflow-local", "workflow-orca"):
        assert "langchain-core" not in _runtime_closure(root), root


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
        "WorkerDone",
        "WorkerEvent",
        "WorkerEventBase",
        "WorkerQuestion",
        "WorkerRecord",
        "WorktreePlacement",
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


def test_no_provisioning_vocabulary():
    import subagents_io
    from subagents_io.backend import DispatchBackend

    for name in ("WorktreeAction", "WORKTREE_ACTIONS", "WorktreeNotProvisioned"):
        assert not hasattr(subagents_io, name), name
    assert "provisions_worktrees" not in DispatchBackend.__annotations__
