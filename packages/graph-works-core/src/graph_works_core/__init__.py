"""graph-works-core: what a workspace is, and where its parts live.

Band 3. Every package below receives resolved paths as arguments; nothing
below imports this one, and the import-linter contract in the root
`pyproject.toml` makes that a `just check` failure rather than a code-review
observation.

    from datetime import date
    from graph_works_core import apply_init, plan_init, resolve

    plan = plan_init(root, today=date(2026, 8, 13), topic="My Works")
    if not plan.is_empty:
        apply_init(plan)

    layout = resolve(workspace=root)
    layout.bundle_dir, layout.config_dir, layout.cache_dir

**The layout object is the deliverable, not a set of path helpers.** There is
no `graph_dir(workspace)` in this package and none below it: a consumer
receives a `WorkspaceLayout`, or one member of it, as an argument.

Four `import-linter` layers (root `pyproject.toml`, `[[tool.importlinter.contracts]]`):

    workspace/                                             # layer 0
      errors, layout, manifest, discovery, init, provenance, pipeline
    agent_substrate/ : graph/ : prompts/ : read_session/   # layer 1, shared
    guidance/                                              # between: claims index,
                                                           #   affects closure
    ingest/ : scan/ : query/ : lint_drift/ : archive/ : orchestrate/
      wiki_stats/ : work/ : proposals/                       # layer 2, independent

`guidance` sits between the verticals and the shared substrate so a vertical
(`work`'s guidance assembly) can import it without crossing the verticals'
independence contract.

Each vertical directory owns its command entry point (renamed to
`commands.py` when it shares the vertical's name — `ingest/commands.py`,
`query/commands.py`, `scan/commands.py`, `archive/commands.py`,
`orchestrate/commands.py`, `graph/commands.py`, `wiki_stats/commands.py` —
or kept flat under its own name otherwise, e.g. `lint_drift/lint.py`), its
own prompt module(s), and — for `query` only — its own agent-loop adapters; `ingest/prompts/` and
`query/prompts/` (`query/adapters/` too) are nested subpackages rather than
flattened, because `proposal_reasoner` and `query_orchestrator` each name
both a command and a prompt (and, for `query_orchestrator`, an adapter too).

`roles` and `prompts` are the two places here that read anything. `roles`
reads only what it is handed a layout for; `prompts.project_context` reads
the workspace root's `AGENTS.md` (or `CLAUDE.md`), and
`prompts.render_architecture_overview` reads a `WorkspaceLayout`'s members
(hence `prompts` importing `layout`, not stdlib alone). `manifest`, `roles`
and `prompts` stay submodules for the reason `work_tracker_okf.vocabulary`
does: `manifest.read`, `roles.packaged_roles` and `prompts.IRON_RULES` read
better qualified, and hoisting them wholesale would put a pile of generic
names at this package's front door. The graph surface follows the same rule
and for the same reason — `GraphTarget`, `GraphResult` and `graph_target` are
hoisted, `build`, `describe`, `find` and `export` stay `graph.commands.build`,
and `find` would collide with `graph_tools.find` besides. The ingest vertical
hoists `IngestResult`, `plan_ingest_brief`, `run_ingest_source`, `entity_matcher` and
`state_gate_adapter` — its whole defaulted API — for the same reason `resolve`
and `apply_init` are hoisted: they are the call, not an implementation detail.

`query.commands` and `query.query_orchestrator` stay submodules — not hoisted
to the front door — for the same reason `manifest`, `roles`, and `prompts`
already aren't: `run_query` reads better qualified, and hoisting `build_index`
/ `lexical_query` / `apply_guardrails` would put a pile of generic names at
the front door. `query.adapters` is also a leaf — REGISTRY and LOOP_REGISTRY
are the deliverables (the registries a consumer registers into), while
`LibrarianAdapter`, `SynthesizerAdapter`, and `QueryOrchestratorLoopAdapter`
stay qualified.

The scan vertical hoists `ScanResult`, `ScanWorklist`, `ProseRefreshTask`,
`ProseRefreshResult`, `run_scan`, `build_scan_worklist` and `apply_scan_results`.
The file surface — `emit_scan_worklist`, `load_results_dir`, `apply_scan_worklist` —
stays `scan.commands.*`, for the reason `build` and `export` do: it is a command,
and its bare names are too generic for a front door.

The archive vertical hoists `ArchiveRun` and `run_archive`, for the reason the
ingest and scan verticals hoist theirs: it is the call, not an implementation
detail. The file surface stays `archive.commands.*`. `provenance` stays a
submodule of `workspace` -- `clear_active_work` is this vertical's private
business, and it now has a caller, which was the whole complaint.

The wiki_stats vertical hoists `HubEntry`, `WikiStats`, and `compute_stats` —
its whole API — for the same reason the archive and ingest verticals do: it
is the call and its result shape, not an implementation detail. Unlike every
other vertical, it has no second file: `commands.py` is the entire module,
so there is nothing left to keep qualified.

The util vertical hoists `InvalidLogSection`, `LogAppendResult`, `LogEntryRead`,
`LogRead`, `TokenStamp`, `SkippedPage`, `TokensUpdate`, `run_log`, `run_log_read`
and `run_tokens_update` — and, from `platform.py`,
`Capability`, `PlatformReport`, `ProbeResult` and `build_report` — for the
reason the archive and wiki_stats verticals hoist theirs: they are the call
and its result shape. `VALID_OPS`, `TOKENS_KEY`, `PROVIDERS` and the four
provider classes stay `util.commands.*` / `util.platform.*`: the registry and
its members are the seam's internals, and the only consumer that needs them is
the sub-app named for this vertical.

The work vertical is deliberately **not** hoisted here. Its `run_lint` would
collide with the wiki lint vertical's already-hoisted `run_lint`, and no
consumer exists yet to force a naming resolution — `graph-works-cli`, the
only planned caller, is a sibling epic and CLI wiring is out of scope for the
item that added this vertical. Reachable at `graph_works_core.work.commands.*`
until that consumer decides how to disambiguate.

`agent_substrate.agent_loop` and `agent_substrate.agent_tools` are leaves:
stdlib + langchain-core + okf-io, nothing else in this package. `graph.graph_tools`
is also a leaf — it takes an open `GraphReader` and knows nothing about a
workspace, which is why the shared describe dispatch lives there, one level
below `graph.commands`.

`lint_drift.lanes` and `lint_drift.drift_anchor` are leaves that feed the lint
and drift verticals: `lanes.compose_lanes` assembles the wiki and work lanes
`lint_drift.lint.run_lint` and `.run_mechanical` iterate over, and
`drift_anchor` reads and writes the per-entity commit anchors
`lint_drift.propagate_drift` diffs against. Both verticals hoist their result
types and entry points — `LaneReport`, `LintReport`, `ProposalBacklog`,
`SemanticFinding`, `run_lint`, `run_mechanical`, `Lane`, `LaneSet`,
`compose_lanes`, `Candidate`, `DriftFinding`, `PropagateResult`, `Target`,
`drift_targets`, `propagation_candidates`, `run_propagate_drift`,
`write_propagation_findings`, `read_anchors`, `write_anchors` — for the same
reason the ingest and scan verticals do. `lint_drift.linter` and
`lint_drift.drift_propagator` stay submodules, matching `prompts.project_context`
and the rest of the shared `prompts`.

Every name in `__all__` except `__version__` resolves on first access through
`__getattr__` over `_EXPORTS`; importing this package imports no vertical. The
`TYPE_CHECKING` block mirrors the table, and `tests/test_lazy_front_door.py`
holds the table, the block and `__all__` equal.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.6.11"

_EXPORTS: dict[str, tuple[str, str]] = {
    "ToolLoopResult": ("graph_works_core.agent_substrate.agent_loop", "ToolLoopResult"),
    "coerce_tool_name": ("graph_works_core.agent_substrate.agent_loop", "coerce_tool_name"),
    "run_tool_loop": ("graph_works_core.agent_substrate.agent_loop", "run_tool_loop"),
    "SourceChunks": ("graph_works_core.agent_substrate.agent_tools", "SourceChunks"),
    "build_catalog": ("graph_works_core.agent_substrate.agent_tools", "build_catalog"),
    "chunk_text": ("graph_works_core.agent_substrate.agent_tools", "chunk_text"),
    "filter_graph_tools": ("graph_works_core.agent_substrate.agent_tools", "filter_graph_tools"),
    "read_bounded_page": ("graph_works_core.agent_substrate.agent_tools", "read_bounded_page"),
    "search_catalog": ("graph_works_core.agent_substrate.agent_tools", "search_catalog"),
    "truncate_text": ("graph_works_core.agent_substrate.agent_tools", "truncate_text"),
    "make_llm": ("graph_works_core.agent_substrate.roles", "make_llm"),
    "role_binding": ("graph_works_core.agent_substrate.roles", "role_binding"),
    "role_spec": ("graph_works_core.agent_substrate.roles", "role_spec"),
    "ArchiveRun": ("graph_works_core.archive.commands", "ArchiveRun"),
    "run_archive": ("graph_works_core.archive.commands", "run_archive"),
    "GraphResult": ("graph_works_core.graph.commands", "GraphResult"),
    "GraphTarget": ("graph_works_core.graph.commands", "GraphTarget"),
    "graph_target": ("graph_works_core.graph.commands", "graph_target"),
    "IngestResult": ("graph_works_core.ingest.commands", "IngestResult"),
    "plan_ingest_brief": ("graph_works_core.ingest.commands", "plan_ingest_brief"),
    "run_ingest_source": ("graph_works_core.ingest.commands", "run_ingest_source"),
    "state_gate_adapter": ("graph_works_core.ingest.commands", "state_gate_adapter"),
    "entity_matcher": ("graph_works_core.ingest.entity_match", "entity_matcher"),
    "read_anchors": ("graph_works_core.lint_drift.drift_anchor", "read_anchors"),
    "write_anchors": ("graph_works_core.lint_drift.drift_anchor", "write_anchors"),
    "Lane": ("graph_works_core.lint_drift.lanes", "Lane"),
    "LaneSet": ("graph_works_core.lint_drift.lanes", "LaneSet"),
    "compose_lanes": ("graph_works_core.lint_drift.lanes", "compose_lanes"),
    "LaneReport": ("graph_works_core.lint_drift.lint", "LaneReport"),
    "LintReport": ("graph_works_core.lint_drift.lint", "LintReport"),
    "ProposalBacklog": ("graph_works_core.lint_drift.lint", "ProposalBacklog"),
    "SemanticFinding": ("graph_works_core.lint_drift.lint", "SemanticFinding"),
    "run_lint": ("graph_works_core.lint_drift.lint", "run_lint"),
    "run_mechanical": ("graph_works_core.lint_drift.lint", "run_mechanical"),
    "Candidate": ("graph_works_core.lint_drift.propagate_drift", "Candidate"),
    "DriftFinding": ("graph_works_core.lint_drift.propagate_drift", "DriftFinding"),
    "PropagateResult": ("graph_works_core.lint_drift.propagate_drift", "PropagateResult"),
    "Target": ("graph_works_core.lint_drift.propagate_drift", "Target"),
    "drift_targets": ("graph_works_core.lint_drift.propagate_drift", "drift_targets"),
    "propagation_candidates": ("graph_works_core.lint_drift.propagate_drift", "propagation_candidates"),
    "run_propagate_drift": ("graph_works_core.lint_drift.propagate_drift", "run_propagate_drift"),
    "write_propagation_findings": ("graph_works_core.lint_drift.propagate_drift", "write_propagation_findings"),
    "BlockedItem": ("graph_works_core.orchestrate.commands", "BlockedItem"),
    "OrchestratePlan": ("graph_works_core.orchestrate.commands", "OrchestratePlan"),
    "OrchestrateResult": ("graph_works_core.orchestrate.commands", "OrchestrateResult"),
    "PlannedAdvance": ("graph_works_core.orchestrate.commands", "PlannedAdvance"),
    "run_orchestrate": ("graph_works_core.orchestrate.commands", "run_orchestrate"),
    "orchestrate_plan": ("graph_works_core.orchestrate.commands", "plan"),
    "PlacementRecord": ("graph_works_core.orchestrate.placement", "PlacementRecord"),
    "ReaderRecord": ("graph_works_core.orchestrate.placement", "ReaderRecord"),
    "read_reader_receipt": ("graph_works_core.orchestrate.placement", "read_reader_receipt"),
    "reader_receipt_path": ("graph_works_core.orchestrate.placement", "reader_receipt_path"),
    "run_record_baseline": ("graph_works_core.orchestrate.placement", "run_record_baseline"),
    "run_record_placement": ("graph_works_core.orchestrate.placement", "run_record_placement"),
    "run_record_reader": ("graph_works_core.orchestrate.placement", "run_record_reader"),
    "StageAdvance": ("graph_works_core.orchestrate.stage_advance", "StageAdvance"),
    "run_stage_advance": ("graph_works_core.orchestrate.stage_advance", "run_stage_advance"),
    "LOOP_REGISTRY": ("graph_works_core.query.adapters", "LOOP_REGISTRY"),
    "REGISTRY": ("graph_works_core.query.adapters", "REGISTRY"),
    "ScanResult": ("graph_works_core.scan.commands", "ScanResult"),
    "apply_scan_results": ("graph_works_core.scan.commands", "apply_scan_results"),
    "build_scan_worklist": ("graph_works_core.scan.commands", "build_scan_worklist"),
    "run_scan": ("graph_works_core.scan.commands", "run_scan"),
    "ProseRefreshResult": ("graph_works_core.scan.scan_contract", "ProseRefreshResult"),
    "ProseRefreshTask": ("graph_works_core.scan.scan_contract", "ProseRefreshTask"),
    "ScanWorklist": ("graph_works_core.scan.scan_contract", "ScanWorklist"),
    "InvalidLogSection": ("graph_works_core.util.commands", "InvalidLogSection"),
    "LogAppendResult": ("graph_works_core.util.commands", "LogAppendResult"),
    "LogEntryRead": ("graph_works_core.util.commands", "LogEntryRead"),
    "LogRead": ("graph_works_core.util.commands", "LogRead"),
    "SkippedPage": ("graph_works_core.util.commands", "SkippedPage"),
    "TokenStamp": ("graph_works_core.util.commands", "TokenStamp"),
    "TokensUpdate": ("graph_works_core.util.commands", "TokensUpdate"),
    "run_log": ("graph_works_core.util.commands", "run_log"),
    "run_log_read": ("graph_works_core.util.commands", "run_log_read"),
    "run_tokens_update": ("graph_works_core.util.commands", "run_tokens_update"),
    "Capability": ("graph_works_core.util.platform", "Capability"),
    "PlatformReport": ("graph_works_core.util.platform", "PlatformReport"),
    "ProbeResult": ("graph_works_core.util.platform", "ProbeResult"),
    "build_report": ("graph_works_core.util.platform", "build_report"),
    "HubEntry": ("graph_works_core.wiki_stats.commands", "HubEntry"),
    "WikiStats": ("graph_works_core.wiki_stats.commands", "WikiStats"),
    "compute_stats": ("graph_works_core.wiki_stats.commands", "compute_stats"),
    "find_repo_root": ("graph_works_core.workspace.discovery", "find_repo_root"),
    "resolve": ("graph_works_core.workspace.discovery", "resolve"),
    "InitError": ("graph_works_core.workspace.errors", "InitError"),
    "QueryError": ("graph_works_core.workspace.errors", "QueryError"),
    "ScanError": ("graph_works_core.workspace.errors", "ScanError"),
    "WorkspaceError": ("graph_works_core.workspace.errors", "WorkspaceError"),
    "WorkspaceNotFound": ("graph_works_core.workspace.errors", "WorkspaceNotFound"),
    "INSTALLERS": ("graph_works_core.workspace.init", "INSTALLERS"),
    "Installer": ("graph_works_core.workspace.init", "Installer"),
    "InstallResult": ("graph_works_core.workspace.init", "InstallResult"),
    "PlannedWrite": ("graph_works_core.workspace.init", "PlannedWrite"),
    "WorkspaceInit": ("graph_works_core.workspace.init", "WorkspaceInit"),
    "WorkspacePlan": ("graph_works_core.workspace.init", "WorkspacePlan"),
    "apply_init": ("graph_works_core.workspace.init", "apply_init"),
    "plan_init": ("graph_works_core.workspace.init", "plan_init"),
    "DEFAULT_WORKSPACE_NAME": ("graph_works_core.workspace.layout", "DEFAULT_WORKSPACE_NAME"),
    "MANIFEST_FILENAME": ("graph_works_core.workspace.layout", "MANIFEST_FILENAME"),
    "WorkspaceLayout": ("graph_works_core.workspace.layout", "WorkspaceLayout"),
    "layout_for": ("graph_works_core.workspace.layout", "layout_for"),
    "CATALOG": ("graph_works_core.workspace.manifest", "CATALOG"),
    "MANIFEST_VERSION": ("graph_works_core.workspace.manifest", "MANIFEST_VERSION"),
    "WORKSPACE_DIR_ENV": ("graph_works_core.workspace.manifest", "WORKSPACE_DIR_ENV"),
    "Manifest": ("graph_works_core.workspace.manifest", "Manifest"),
    "PackagedRule": ("graph_works_core.workspace.pipeline", "PackagedRule"),
}

if TYPE_CHECKING:
    from graph_works_core.agent_substrate.agent_loop import ToolLoopResult, coerce_tool_name, run_tool_loop
    from graph_works_core.agent_substrate.agent_tools import (
        SourceChunks,
        build_catalog,
        chunk_text,
        filter_graph_tools,
        read_bounded_page,
        search_catalog,
        truncate_text,
    )
    from graph_works_core.agent_substrate.roles import make_llm, role_binding, role_spec
    from graph_works_core.archive.commands import ArchiveRun, run_archive
    from graph_works_core.graph.commands import GraphResult, GraphTarget, graph_target
    from graph_works_core.ingest.commands import IngestResult, plan_ingest_brief, run_ingest_source, state_gate_adapter
    from graph_works_core.ingest.entity_match import entity_matcher
    from graph_works_core.lint_drift.drift_anchor import read_anchors, write_anchors
    from graph_works_core.lint_drift.lanes import Lane, LaneSet, compose_lanes
    from graph_works_core.lint_drift.lint import (
        LaneReport,
        LintReport,
        ProposalBacklog,
        SemanticFinding,
        run_lint,
        run_mechanical,
    )
    from graph_works_core.lint_drift.propagate_drift import (
        Candidate,
        DriftFinding,
        PropagateResult,
        Target,
        drift_targets,
        propagation_candidates,
        run_propagate_drift,
        write_propagation_findings,
    )
    from graph_works_core.orchestrate.commands import (
        BlockedItem,
        OrchestratePlan,
        OrchestrateResult,
        PlannedAdvance,
        run_orchestrate,
    )
    from graph_works_core.orchestrate.commands import plan as orchestrate_plan
    from graph_works_core.orchestrate.placement import (
        PlacementRecord,
        ReaderRecord,
        read_reader_receipt,
        reader_receipt_path,
        run_record_baseline,
        run_record_placement,
        run_record_reader,
    )
    from graph_works_core.orchestrate.stage_advance import StageAdvance, run_stage_advance
    from graph_works_core.query.adapters import LOOP_REGISTRY, REGISTRY
    from graph_works_core.scan.commands import (
        ScanResult,
        apply_scan_results,
        build_scan_worklist,
        run_scan,
    )
    from graph_works_core.scan.scan_contract import (
        ProseRefreshResult,
        ProseRefreshTask,
        ScanWorklist,
    )
    from graph_works_core.util.commands import (
        InvalidLogSection,
        LogAppendResult,
        LogEntryRead,
        LogRead,
        SkippedPage,
        TokenStamp,
        TokensUpdate,
        run_log,
        run_log_read,
        run_tokens_update,
    )
    from graph_works_core.util.platform import (
        Capability,
        PlatformReport,
        ProbeResult,
        build_report,
    )
    from graph_works_core.wiki_stats.commands import HubEntry, WikiStats, compute_stats
    from graph_works_core.workspace.discovery import find_repo_root, resolve
    from graph_works_core.workspace.errors import InitError, QueryError, ScanError, WorkspaceError, WorkspaceNotFound
    from graph_works_core.workspace.init import (
        INSTALLERS,
        Installer,
        InstallResult,
        PlannedWrite,
        WorkspaceInit,
        WorkspacePlan,
        apply_init,
        plan_init,
    )
    from graph_works_core.workspace.layout import (
        DEFAULT_WORKSPACE_NAME,
        MANIFEST_FILENAME,
        WorkspaceLayout,
        layout_for,
    )
    from graph_works_core.workspace.manifest import CATALOG, MANIFEST_VERSION, WORKSPACE_DIR_ENV, Manifest
    from graph_works_core.workspace.pipeline import PackagedRule

__all__ = [
    "CATALOG",
    "DEFAULT_WORKSPACE_NAME",
    "INSTALLERS",
    "LOOP_REGISTRY",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "REGISTRY",
    "WORKSPACE_DIR_ENV",
    "ArchiveRun",
    "BlockedItem",
    "Candidate",
    "Capability",
    "DriftFinding",
    "GraphResult",
    "GraphTarget",
    "HubEntry",
    "IngestResult",
    "InitError",
    "InstallResult",
    "Installer",
    "InvalidLogSection",
    "Lane",
    "LaneReport",
    "LaneSet",
    "LintReport",
    "LogAppendResult",
    "LogEntryRead",
    "LogRead",
    "Manifest",
    "OrchestratePlan",
    "OrchestrateResult",
    "PackagedRule",
    "PlacementRecord",
    "PlannedAdvance",
    "PlannedWrite",
    "PlatformReport",
    "ProbeResult",
    "PropagateResult",
    "ProposalBacklog",
    "ProseRefreshResult",
    "ProseRefreshTask",
    "QueryError",
    "ReaderRecord",
    "ScanError",
    "ScanResult",
    "ScanWorklist",
    "SemanticFinding",
    "SkippedPage",
    "SourceChunks",
    "StageAdvance",
    "Target",
    "TokenStamp",
    "TokensUpdate",
    "ToolLoopResult",
    "WikiStats",
    "WorkspaceError",
    "WorkspaceInit",
    "WorkspaceLayout",
    "WorkspaceNotFound",
    "WorkspacePlan",
    "__version__",
    "apply_init",
    "apply_scan_results",
    "build_catalog",
    "build_report",
    "build_scan_worklist",
    "chunk_text",
    "coerce_tool_name",
    "compose_lanes",
    "compute_stats",
    "drift_targets",
    "entity_matcher",
    "filter_graph_tools",
    "find_repo_root",
    "graph_target",
    "layout_for",
    "make_llm",
    "orchestrate_plan",
    "plan_ingest_brief",
    "plan_init",
    "propagation_candidates",
    "read_anchors",
    "read_bounded_page",
    "read_reader_receipt",
    "reader_receipt_path",
    "resolve",
    "role_binding",
    "role_spec",
    "run_archive",
    "run_ingest_source",
    "run_lint",
    "run_log",
    "run_log_read",
    "run_mechanical",
    "run_orchestrate",
    "run_propagate_drift",
    "run_record_baseline",
    "run_record_placement",
    "run_record_reader",
    "run_scan",
    "run_stage_advance",
    "run_tokens_update",
    "run_tool_loop",
    "search_catalog",
    "state_gate_adapter",
    "truncate_text",
    "write_anchors",
    "write_propagation_findings",
]


def __getattr__(name: str) -> Any:  # noqa: ANN401
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
