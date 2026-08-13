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
    # Exactly these thirty. The two writers are surface because they have real
    # consumers with no pool in sight; `PriceLookup` is surface because it is
    # the type of a parameter two of those callables accept, so a caller
    # writing its own lookup cannot annotate against the API without it. The
    # dispatch seven are surface because they exist for a consumer that has not
    # been written yet — an unexported value type would be unreachable.
    #
    # The fifteen the runner port added: the three entry points and
    # `stream_and_parse` are the package's reason to exist, and the adapter and
    # role types are what a consumer implements and injects against.
    # `Closeable` is surface because it bounds `RunContext`'s type parameter and
    # a caller annotating the generic cannot name the bound without it; the type
    # parameter itself is not, being an implementation detail of the annotation.
    assert set(subagents_io.__all__) == {
        "Adapter",
        "Closeable",
        "DISPATCH_MODES",
        "FanOutResult",
        "LoopAdapter",
        "LoopOutcome",
        "ModelResolution",
        "NoGraphReader",
        "PerItemError",
        "PlannedDispatch",
        "Prepared",
        "PriceLookup",
        "RoleBinding",
        "RoleSpec",
        "RunContext",
        "RunOutcome",
        "SubagentPool",
        "TRACE_LOGGER_NAME",
        "TaskResult",
        "WORKTREE_ACTIONS",
        "WorktreeAction",
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
