"""Wiki ownership, resource lookup and defining-module APIs."""

from __future__ import annotations

import importlib
from pathlib import Path

import gw_wiki
import pytest
from gw_kernel.workspace.errors import QueryError, ScanError, WorkspaceError

MODULES = [
    "scan",
    "ingest",
    "query",
    "lint_drift",
    "proposals",
    "wiki_page",
    "code_read",
    "repositories",
    "agent_config",
    "util",
    "agent_substrate",
    "prompts",
]
DEFINING_APIS = [
    ("gw_wiki.agent_substrate.agent_loop", "ToolLoopResult"),
    ("gw_wiki.agent_substrate.agent_loop", "coerce_tool_name"),
    ("gw_wiki.agent_substrate.agent_loop", "run_tool_loop"),
    ("gw_wiki.agent_substrate.agent_tools", "SourceChunks"),
    ("gw_wiki.agent_substrate.agent_tools", "build_catalog"),
    ("gw_wiki.agent_substrate.agent_tools", "chunk_text"),
    ("gw_wiki.agent_substrate.agent_tools", "filter_graph_tools"),
    ("gw_wiki.agent_substrate.agent_tools", "read_bounded_page"),
    ("gw_wiki.agent_substrate.agent_tools", "search_catalog"),
    ("gw_wiki.agent_substrate.agent_tools", "truncate_text"),
    ("gw_wiki.agent_substrate.roles", "make_llm"),
    ("gw_wiki.agent_substrate.roles", "role_binding"),
    ("gw_wiki.agent_substrate.roles", "role_spec"),
    ("gw_wiki.ingest.commands", "IngestResult"),
    ("gw_wiki.ingest.commands", "plan_ingest_brief"),
    ("gw_wiki.ingest.commands", "run_ingest_source"),
    ("gw_wiki.ingest.commands", "state_gate_adapter"),
    ("gw_wiki.ingest.entity_match", "entity_matcher"),
    ("gw_wiki.lint_drift.drift_anchor", "read_anchors"),
    ("gw_wiki.lint_drift.drift_anchor", "write_anchors"),
    ("gw_wiki.lint_drift.lanes", "Lane"),
    ("gw_wiki.lint_drift.lanes", "LaneSet"),
    ("gw_wiki.lint_drift.lanes", "compose_lanes"),
    ("gw_wiki.lint_drift.lint", "LaneReport"),
    ("gw_wiki.lint_drift.lint", "LintReport"),
    ("gw_wiki.lint_drift.lint", "ProposalBacklog"),
    ("gw_wiki.lint_drift.lint", "SemanticFinding"),
    ("gw_wiki.lint_drift.lint", "run_lint"),
    ("gw_wiki.lint_drift.lint", "run_mechanical"),
    ("gw_wiki.lint_drift.propagate_drift", "Candidate"),
    ("gw_wiki.lint_drift.propagate_drift", "DriftFinding"),
    ("gw_wiki.lint_drift.propagate_drift", "PropagateResult"),
    ("gw_wiki.lint_drift.propagate_drift", "Target"),
    ("gw_wiki.lint_drift.propagate_drift", "drift_targets"),
    ("gw_wiki.lint_drift.propagate_drift", "propagation_candidates"),
    ("gw_wiki.lint_drift.propagate_drift", "run_propagate_drift"),
    ("gw_wiki.lint_drift.propagate_drift", "write_propagation_findings"),
    ("gw_wiki.query.adapters", "LOOP_REGISTRY"),
    ("gw_wiki.query.adapters", "REGISTRY"),
    ("gw_wiki.scan.commands", "ScanResult"),
    ("gw_wiki.scan.commands", "apply_scan_results"),
    ("gw_wiki.scan.commands", "build_scan_worklist"),
    ("gw_wiki.scan.commands", "run_scan"),
    ("gw_wiki.scan.scan_contract", "ProseRefreshResult"),
    ("gw_wiki.scan.scan_contract", "ProseRefreshTask"),
    ("gw_wiki.scan.scan_contract", "ScanWorklist"),
    ("gw_wiki.util.commands", "InvalidLogSection"),
    ("gw_wiki.util.commands", "LogAppendResult"),
    ("gw_wiki.util.commands", "LogEntryRead"),
    ("gw_wiki.util.commands", "LogRead"),
    ("gw_wiki.util.commands", "SkippedPage"),
    ("gw_wiki.util.commands", "TokenStamp"),
    ("gw_wiki.util.commands", "TokensUpdate"),
    ("gw_wiki.util.commands", "run_log"),
    ("gw_wiki.util.commands", "run_log_read"),
    ("gw_wiki.util.commands", "run_tokens_update"),
    ("gw_wiki.util.platform", "Capability"),
    ("gw_wiki.util.platform", "PlatformReport"),
    ("gw_wiki.util.platform", "ProbeResult"),
    ("gw_wiki.util.platform", "build_report"),
]


@pytest.mark.parametrize("module", MODULES)
def test_defining_modules_import(module):
    assert importlib.import_module(f"gw_wiki.{module}")


@pytest.mark.parametrize("module, name", DEFINING_APIS)
def test_defining_api_is_reachable(module, name):
    assert hasattr(importlib.import_module(module), name)


def test_catalog_is_packaged_with_wiki():
    from gw_wiki.agent_substrate.roles import packaged_roles

    assert packaged_roles()


def test_repository_generation_keeps_kernel_version():
    for name in ("commands", "adopt"):
        module = importlib.import_module(f"gw_wiki.repositories.{name}")
        assert 'version("gw-kernel")' in Path(module.__file__).read_text(encoding="utf-8")


def test_old_kernel_paths_are_absent():
    import gw_kernel

    root = Path(gw_kernel.__file__).parent
    assert not (root / "models.toml").exists()
    assert all(not (root / name).exists() for name in MODULES)


def test_wiki_has_no_front_door_exports():
    assert "_EXPORTS" not in vars(gw_wiki)
    assert "__getattr__" not in vars(gw_wiki)


def test_prompts_and_roles_stay_submodules():
    from gw_wiki import prompts
    from gw_wiki.agent_substrate import roles

    assert prompts.IRON_RULES
    assert roles.GATEWAY_API_KEY_ENV == "AI_GATEWAY_API_KEY"


def test_the_scan_file_surface_stays_a_command():
    from gw_wiki.scan import commands as scan

    for name in ("emit_scan_worklist", "load_results_dir", "apply_scan_worklist"):
        assert hasattr(scan, name), name
        assert not hasattr(gw_wiki, name), name


def test_the_prose_refresher_prompt_surface_is_reachable():
    from gw_wiki.scan import prose_refresher

    assert prose_refresher.PROSE_REFRESHER_SYSTEM
    assert callable(prose_refresher.build_prose_refresh_prompt)
    assert callable(prose_refresher.parse_prose_refresher_output)
    assert callable(prose_refresher.sanitize_prose_result)


def test_the_two_new_prompt_modules_stay_submodules():
    from gw_wiki import prompts
    from gw_wiki.lint_drift import drift_propagator, linter

    assert linter.build_linter_page_quality_system()
    assert drift_propagator.DRIFT_PROPAGATOR_SYSTEM
    assert "build_linter_page_quality_system" not in prompts.__all__


def test_the_adr_predicate_has_exactly_one_home():
    from doc_wiki_okf.proposals import is_adr
    from gw_wiki.lint_drift import lint as lint_module
    from gw_wiki.lint_drift import propagate_drift as drift_module

    assert lint_module.is_adr is is_adr
    assert drift_module.is_adr is is_adr
    assert not hasattr(lint_module, "_is_adr")


def test_errors_remain_workspace_errors():
    assert issubclass(QueryError, WorkspaceError)
    assert issubclass(ScanError, WorkspaceError)
