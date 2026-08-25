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

Three `import-linter` layers (root `pyproject.toml`, `[[tool.importlinter.contracts]]`):

    workspace/                                             # layer 0
      errors, layout, manifest, discovery, init, provenance, pipeline
    agent_substrate/ : graph/ : prompts/                   # layer 1, shared
    ingest/ : scan/ : query/ : lint_drift/ : archive/ : orchestrate/ : wiki_stats/ : work/   # layer 2, independent

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
reads only what it is handed a layout for; `prompts.project_context` reads a
caller-supplied `CLAUDE.md`/`AGENTS.md` directory, and
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

The util vertical hoists `LogAppendResult`, `TokenStamp`, `SkippedPage`,
`TokensUpdate`, `run_log` and `run_tokens_update` — its whole API — for the
reason the archive and wiki_stats verticals hoist theirs: they are the call
and its result shape. `VALID_OPS` and `TOKENS_KEY` stay
`util.commands.*`; both are too generic for a front door, and the only
consumer that needs them is the sub-app named for this vertical.

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
"""

from __future__ import annotations

__version__ = "0.4.0"

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
    StageAdvance,
    run_orchestrate,
    run_stage_advance,
)
from graph_works_core.orchestrate.commands import plan as orchestrate_plan
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
    LogAppendResult,
    SkippedPage,
    TokenStamp,
    TokensUpdate,
    run_log,
    run_tokens_update,
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
from graph_works_core.workspace.pipeline import PipelineEntry, entry_for, pipeline_table

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
    "DriftFinding",
    "GraphResult",
    "GraphTarget",
    "HubEntry",
    "IngestResult",
    "InitError",
    "InstallResult",
    "Installer",
    "Lane",
    "LaneReport",
    "LaneSet",
    "LintReport",
    "LogAppendResult",
    "Manifest",
    "OrchestratePlan",
    "OrchestrateResult",
    "PipelineEntry",
    "PlannedAdvance",
    "PlannedWrite",
    "PropagateResult",
    "ProposalBacklog",
    "ProseRefreshResult",
    "ProseRefreshTask",
    "QueryError",
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
    "build_scan_worklist",
    "chunk_text",
    "coerce_tool_name",
    "compose_lanes",
    "compute_stats",
    "drift_targets",
    "entity_matcher",
    "entry_for",
    "filter_graph_tools",
    "find_repo_root",
    "graph_target",
    "layout_for",
    "make_llm",
    "orchestrate_plan",
    "pipeline_table",
    "plan_ingest_brief",
    "plan_init",
    "propagation_candidates",
    "read_anchors",
    "read_bounded_page",
    "resolve",
    "role_binding",
    "role_spec",
    "run_archive",
    "run_ingest_source",
    "run_lint",
    "run_log",
    "run_mechanical",
    "run_orchestrate",
    "run_propagate_drift",
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
