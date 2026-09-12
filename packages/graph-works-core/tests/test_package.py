"""The package's own invariants: it imports, it is versioned, and it exports
what it says it exports."""

from __future__ import annotations

import tomllib
from pathlib import Path

import graph_works_core
import pytest
from graph_works_core.workspace.errors import InitError, WorkspaceError, WorkspaceNotFound


def test_version_is_static_and_matches_the_distribution():
    assert graph_works_core.__version__ == "0.6.0"


def test_private_descriptor_loader_has_a_synchronized_okf_io_floor():
    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with manifest.open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    assert "okf-io>=0.2.4,<0.3" in dependencies


def test_all_is_sorted_and_every_name_is_bound():
    assert graph_works_core.__all__ == sorted(graph_works_core.__all__, key=_ruff_key)
    for name in graph_works_core.__all__:
        assert hasattr(graph_works_core, name), name


def _ruff_key(name: str) -> tuple[int, str]:
    """RUF022's grouping: SCREAMING_SNAKE constants, then CapWords, then the
    rest, alphabetical within each group."""
    if name.isupper():
        return (0, name)
    if name[:1].isupper():
        return (1, name)
    return (2, name)


def test_every_refusal_is_a_workspace_error_and_a_value_error():
    for error in (WorkspaceNotFound, InitError):
        assert issubclass(error, WorkspaceError)
        assert issubclass(error, ValueError)


def test_the_substrate_is_reachable_from_the_front_door():
    for name in (
        "role_spec",
        "role_binding",
        "make_llm",
        "ToolLoopResult",
        "run_tool_loop",
        "coerce_tool_name",
        "build_catalog",
        "read_bounded_page",
        "search_catalog",
        "chunk_text",
        "filter_graph_tools",
        "truncate_text",
        "SourceChunks",
    ):
        assert name in graph_works_core.__all__, name


def test_prompts_and_roles_stay_submodules():
    from graph_works_core import prompts
    from graph_works_core.agent_substrate import roles

    assert prompts.IRON_RULES
    assert roles.GATEWAY_API_KEY_ENV == "AI_GATEWAY_API_KEY"


def test_the_ingest_vertical_is_exported():
    for name in ("IngestResult", "entity_matcher", "plan_ingest_brief", "run_ingest_source", "state_gate_adapter"):
        assert name in graph_works_core.__all__


def test_the_registries_are_reachable_from_the_front_door():
    for name in ("REGISTRY", "LOOP_REGISTRY"):
        assert name in graph_works_core.__all__, name


def test_the_commands_stay_submodules():
    # `run_query` reads better qualified, and hoisting `build_index` /
    # `lexical_query` / `apply_guardrails` would put a pile of generic names at
    # the front door.
    for name in ("run_query", "build_index", "lexical_query", "apply_guardrails"):
        assert name not in graph_works_core.__all__, name


def test_the_scan_vertical_is_exported():
    for name in (
        "ScanResult",
        "ScanWorklist",
        "ProseRefreshTask",
        "ProseRefreshResult",
        "ScanError",
        "run_scan",
        "build_scan_worklist",
        "apply_scan_results",
    ):
        assert name in graph_works_core.__all__, name


def test_the_scan_file_surface_stays_a_command():
    """Generic names stay behind `commands.scan.`, matching `build` / `export`."""
    from graph_works_core.scan import commands as scan

    for name in ("emit_scan_worklist", "load_results_dir", "apply_scan_worklist"):
        assert hasattr(scan, name), name
        assert name not in graph_works_core.__all__, name


def test_the_prose_refresher_prompt_surface_is_reachable():
    from graph_works_core.scan import prose_refresher

    assert prose_refresher.PROSE_REFRESHER_SYSTEM
    assert callable(prose_refresher.build_prose_refresh_prompt)
    assert callable(prose_refresher.parse_prose_refresher_output)
    assert callable(prose_refresher.sanitize_prose_result)


def test_the_lint_and_drift_surface_is_reachable_from_the_front_door():
    for name in (
        "Lane",
        "LaneSet",
        "compose_lanes",
        "LaneReport",
        "LintReport",
        "ProposalBacklog",
        "SemanticFinding",
        "run_lint",
        "run_mechanical",
        "Candidate",
        "DriftFinding",
        "PropagateResult",
        "Target",
        "drift_targets",
        "propagation_candidates",
        "run_propagate_drift",
        "write_propagation_findings",
        "read_anchors",
        "write_anchors",
    ):
        assert name in graph_works_core.__all__, name


def test_lint_all_retired_rather_than_ported():
    """47 lines that do not port: once `compose_lanes` returns the lanes and
    `run_mechanical` loops over them, a second orchestrator over that loop has
    nothing left to do. Its continue-on-error behaviour survives as the loop's
    own per-lane error capture."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("graph_works_core.commands.lint_all")


def test_the_two_new_prompt_modules_stay_submodules():
    from graph_works_core.lint_drift import drift_propagator, linter

    assert linter.build_linter_page_quality_system()
    assert drift_propagator.DRIFT_PROPAGATOR_SYSTEM
    assert "build_linter_page_quality_system" not in graph_works_core.prompts.__all__


def test_the_dispatch_seam_is_reachable_from_the_front_door():
    for name in (
        "pipeline_table",
        "entry_for",
        "PipelineEntry",
        "orchestrate_plan",
        "run_orchestrate",
        "run_stage_advance",
        "OrchestratePlan",
        "OrchestrateResult",
        # Reachable from the two hoisted result types above (OrchestratePlan.advances /
        # .blocked, and run_stage_advance's return type) -- hoisted alongside them for
        # the same reason LintReport's LaneReport/SemanticFinding are.
        "PlannedAdvance",
        "BlockedItem",
        "StageAdvance",
    ):
        assert hasattr(graph_works_core, name), name


def test_the_archive_vertical_is_exported():
    for name in ("ArchiveRun", "run_archive"):
        assert name in graph_works_core.__all__


def test_the_adr_predicate_has_exactly_one_home():
    """D7: `lint.py` and `propagate_drift.py` wrote the same two-part test
    twice, with the same explanatory comment on both copies."""
    from doc_wiki_okf.proposals import is_adr
    from graph_works_core.lint_drift import lint as lint_module
    from graph_works_core.lint_drift import propagate_drift as drift_module

    assert lint_module.is_adr is is_adr
    assert drift_module.is_adr is is_adr
    assert not hasattr(lint_module, "_is_adr")


def test_provenance_is_read_through_its_module():
    """`worktree_state` and `results_facts` stay module-qualified: three of the
    most generic names in the language do not belong at a package's front
    door, which is the rule `commands/__init__.py` already states."""
    for name in ("worktree_state", "results_facts", "write_active_work"):
        assert not hasattr(graph_works_core, name), name


def test_the_query_error_is_exported():
    from graph_works_core import QueryError
    from graph_works_core.workspace.errors import WorkspaceError

    assert issubclass(QueryError, WorkspaceError)
    assert "QueryError" in graph_works_core.__all__


def test_the_util_vertical_is_exported():
    for name in ("LogAppendResult", "SkippedPage", "TokenStamp", "TokensUpdate", "run_log", "run_tokens_update"):
        assert name in graph_works_core.__all__
