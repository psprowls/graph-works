"""The bounded query orchestrator: plan over tools, fan out workers, validate.

Two substrate substitutions from the module this ports. `build_orchestrator_tools`
is rebuilt over C2's `build_catalog` / `read_bounded_page` / `search_catalog`,
which take a `Bundle` — so the reference's `body_without_frontmatter` has no
successor and is not ported: `Document.body` is the same thing, already parsed.
And `classify_wiki_freshness` reads `okf_io`'s frontmatter view rather than the
`frontmatter` package, which is not a rebuild dependency.

**Every failure degrades rather than raises.** Tool-loop error, tool-loop
failure, validation error, worker-batch error and cap-reached each produce a
valid `OrchestratorOutput` with a stated reason, low confidence and a
`trace_metadata["status"]` naming which one. That is what makes `run_query`'s
fallback an *exception* path rather than the normal path: an orchestrator that
merely did badly still returns, and only one that broke reaches the fixed
pipeline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from models_io.pricing import cost_for_usage
from okf_ext.bundle import SCHEMA_DIRNAME
from okf_ext.schemas import SchemaSet, load_schemas
from okf_io import Bundle, Document
from subagents_io import FanOutResult, PerItemError, SubagentPool, TaskResult

from graph_works_core.agent_substrate.agent_loop import run_tool_loop
from graph_works_core.agent_substrate.agent_tools import (
    build_catalog,
    filter_graph_tools,
    read_bounded_page,
    search_catalog,
    strip_code_fence,
)
from graph_works_core.agent_substrate.roles import make_llm, role_binding
from graph_works_core.query.commands import _read_file_bounded
from graph_works_core.query.prompts.code_reader import ORCHESTRATED_CODE_READER_SYSTEM
from graph_works_core.query.prompts.librarian import build_librarian_system
from graph_works_core.query.prompts.query_orchestrator import QUERY_ORCHESTRATOR_SYSTEM
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout

ALLOWED_SOURCE_TYPES = {"wiki", "code"}
ALLOWED_FRESHNESS = {"fresh", "stale", "unknown"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_WORKERS = ("librarian", "code_reader")
DEGRADED_STATUS_VALUES = {"degraded", "failed", "error", "blocked", "stale"}
DEGRADED_STATUS_KEYS = ("ingest_status", "proposal_status", "status")
PLACEHOLDER_MARKERS = ("todo", "placeholder", "no narrative available", "needs review")
MIN_MEANINGFUL_BODY_CHARS = 40
MAX_ORCHESTRATOR_WIKI_PAGE_CHARS = 40_000
MAX_ORCHESTRATOR_SEARCH_ROWS = 20
MAX_WORKER_WIKI_PAGE_CHARS = 80_000
ORCHESTRATED_CODE_READER_MAX_ITERS = 5
MAX_ORCHESTRATOR_TOOL_ITERS = 5
MAX_ORCHESTRATOR_WORKER_BATCHES = 5
ALLOWED_ORCHESTRATOR_GRAPH_TOOL_NAMES = {"cg_find", "cg_describe"}
#: Orchestrator statuses on which `run_query` abandons the orchestrated result
#: for the fixed pipeline. `invalid_json` and `validation_error` are
#: deliberately absent: they mean the planner broke its own output contract,
#: which a second pipeline over the same corpus does not diagnose. `capped` is
#: absent because it carries real accumulated evidence.
FALLBACK_STATUSES = frozenset({"tool_loop_error", "tool_loop_failed", "worker_batch_error"})
REQUIRED_TOP_LEVEL_KEYS = {
    "answer_markdown",
    "citations",
    "evidence",
    "answer_evidence_map",
    "worker_plan",
    "worker_results",
    "gaps",
    "confidence",
}
UNCERTAINTY_WORDS = ("uncertain", "uncertainty", "may", "might", "appears", "suggests", "possibly")
FrozenValue = Mapping[str, Any] | tuple[Any, ...] | str | int | float | bool | None


class OrchestratorValidationError(ValueError):
    """Raised when orchestrator output does not match the structured contract."""


@dataclass(frozen=True)
class InitialCandidate:
    path: str
    score: float
    excerpt: str
    freshness: str = "unknown"
    staleness_reason: str | None = None


@dataclass(frozen=True)
class OrchestratorContext:
    query: str
    bundle: Bundle
    repo_root: Path | None
    initial_candidates: tuple[InitialCandidate, ...]
    graph_tools_available: bool
    graph_tool_names: tuple[str, ...]
    worker_capabilities: Mapping[str, Mapping[str, Any]]
    answer_contract: Mapping[str, Any]


@dataclass(frozen=True)
class OrchestratorEvidence:
    id: str
    source_type: str
    path: str
    freshness: str
    staleness_reason: str | None
    excerpt: str
    line_refs: list[str]


@dataclass(frozen=True)
class AnswerEvidenceMap:
    claim: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class EvidenceGap:
    question: str
    reason: str


@dataclass(frozen=True)
class FreshnessClassification:
    freshness: str
    reason: str | None


@dataclass(frozen=True)
class WorkerTask:
    worker: str
    task_id: str
    query_focus: str
    expected_evidence: str
    page_path: str | None = None
    target_paths_or_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrchestratorOutput:
    answer_markdown: str
    citations: list[str]
    evidence: list[OrchestratorEvidence]
    answer_evidence_map: list[AnswerEvidenceMap]
    worker_plan: tuple[Mapping[str, Any], ...]
    worker_results: tuple[Mapping[str, Any], ...]
    gaps: list[EvidenceGap]
    confidence: str


@dataclass(frozen=True)
class QueryOrchestratorResult:
    output: OrchestratorOutput
    trace_metadata: Mapping[str, Any]


def _jsonable(value: Any) -> Any:  # noqa: ANN401 -- flattens an arbitrary JSON-ish value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def orchestrator_output_as_dict(output: OrchestratorOutput) -> dict[str, Any]:
    """`output` as a plain, JSON-serializable dict — `LoopOutcome.structured`."""
    return cast(dict[str, Any], _jsonable(output))


def classify_wiki_freshness(doc: Document, *, repo_head: str | None) -> FreshnessClassification:
    """Is this page's evidence fresh enough to rest a claim on?

    Takes a `Document` rather than a path: the caller already has the bundle,
    the frontmatter is already parsed, and the body is already split. That
    retires both the `frontmatter` dependency and the reference's
    `body_without_frontmatter`.

    `last_updated_commit` is not an OKF v0.2 field, so it arrives in
    `fm.extra`. `status` is, so it is read from the view and the two are merged
    into the one mapping the degraded-status rules scan.
    """
    metadata: dict[str, Any] = dict(doc.fm.extra)
    if doc.fm.status is not None:
        metadata["status"] = doc.fm.status

    last_updated_commit = metadata.get("last_updated_commit")
    if repo_head and last_updated_commit and str(last_updated_commit) != repo_head:
        return FreshnessClassification(freshness="stale", reason="last_updated_commit mismatch")

    if _has_placeholder_content(doc.body):
        return FreshnessClassification(freshness="stale", reason="placeholder content")

    if _has_degraded_status(metadata):
        return FreshnessClassification(freshness="stale", reason="degraded status")

    if repo_head and last_updated_commit and str(last_updated_commit) == repo_head:
        return FreshnessClassification(freshness="fresh", reason=None)

    return FreshnessClassification(freshness="unknown", reason=None)


def _loads_tolerant(raw: str) -> Any:  # noqa: ANN401 -- returns whatever json.loads would
    """`json.loads`, tolerant of a code fence and/or leading or trailing prose.

    Strips a fence first, then falls back to decoding from the first `{` —
    one deterministic position, never a scan — so trailing prose is free but
    a preamble containing its own brace still raises (a scan could silently
    return that decoy object instead of the real payload).
    """
    text = strip_code_fence(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        if start == -1:
            raise
        try:
            payload, _end = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError:
            raise exc from None
        return payload


def parse_orchestrator_output(
    raw: str | dict[str, Any],
    *,
    require_stale_claim_gaps: bool = True,
) -> OrchestratorOutput:
    """Parse raw orchestrator output and validate its structured contract.

    Parsing always performs structural and cross-reference validation, and by
    default also requires stale-only claim support to include an explicit gap
    or uncertainty wording. Pass require_stale_claim_gaps=False to skip that
    last rule; `validate_orchestrator_output` takes the same argument with the
    same default, so calling either directly gives identical strictness.
    """

    if isinstance(raw, str):
        try:
            payload = _loads_tolerant(raw)
        except json.JSONDecodeError as exc:
            raise OrchestratorValidationError(f"Invalid JSON orchestrator output: {exc}") from exc
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise OrchestratorValidationError("Orchestrator output must be a JSON object")
    _validate_top_level_keys(payload)

    output = OrchestratorOutput(
        answer_markdown=_required_non_empty_str(payload, "answer_markdown"),
        citations=_str_list(payload["citations"], "citations"),
        evidence=_parse_evidence_rows(payload["evidence"]),
        answer_evidence_map=_parse_answer_evidence_map(payload["answer_evidence_map"]),
        worker_plan=_object_tuple(payload["worker_plan"], "worker_plan"),
        worker_results=_object_tuple(payload["worker_results"], "worker_results"),
        gaps=_parse_gaps(payload["gaps"]),
        confidence=_required_non_empty_str(payload, "confidence"),
    )
    validate_orchestrator_output(output, require_stale_claim_gaps=require_stale_claim_gaps)
    return output


def _has_placeholder_content(body: str) -> bool:
    normalized = " ".join(body.split()).lower()
    if len(normalized) < MIN_MEANINGFUL_BODY_CHARS:
        return True
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def _has_degraded_status(metadata: Mapping[str, Any]) -> bool:
    return any(_status_value_is_stale(metadata.get(key)) for key in DEGRADED_STATUS_KEYS)


def _status_value_is_stale(value: Any) -> bool:  # noqa: ANN401 -- arbitrary raw frontmatter value
    if isinstance(value, dict):
        return any(_status_value_is_stale(item) for item in value.values())
    if isinstance(value, list | tuple | set):
        return any(_status_value_is_stale(item) for item in value)
    if value is None:
        return False
    return str(value).strip().lower() in DEGRADED_STATUS_VALUES


def _validate_top_level_keys(payload: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - payload.keys())
    if missing:
        raise OrchestratorValidationError(f"Missing required top-level orchestrator output keys: {missing}")
    extra = sorted(payload.keys() - REQUIRED_TOP_LEVEL_KEYS)
    if extra:
        raise OrchestratorValidationError(f"Unexpected top-level orchestrator output keys: {extra}")


def validate_orchestrator_output(
    output: OrchestratorOutput,
    *,
    require_stale_claim_gaps: bool = True,
) -> None:
    """Validate cross-field invariants in parsed orchestrator output."""

    if not output.answer_markdown.strip():
        raise OrchestratorValidationError("answer_markdown must be non-empty")
    if output.confidence not in ALLOWED_CONFIDENCE:
        raise OrchestratorValidationError(
            f"confidence must be one of {sorted(ALLOWED_CONFIDENCE)}; got {output.confidence!r}"
        )

    evidence_ids: set[str] = set()
    evidence_by_id: dict[str, OrchestratorEvidence] = {}
    for row in output.evidence:
        if not row.id.strip():
            raise OrchestratorValidationError("evidence id must be non-empty")
        if row.id in evidence_ids:
            raise OrchestratorValidationError(f"evidence ids must be unique; duplicate {row.id!r}")
        evidence_ids.add(row.id)
        evidence_by_id[row.id] = row

        if row.source_type not in ALLOWED_SOURCE_TYPES:
            raise OrchestratorValidationError(
                f"source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}; got {row.source_type!r}"
            )
        if row.freshness not in ALLOWED_FRESHNESS:
            raise OrchestratorValidationError(
                f"freshness must be one of {sorted(ALLOWED_FRESHNESS)}; got {row.freshness!r}"
            )
        if not row.path.strip():
            raise OrchestratorValidationError("evidence path must be non-empty")
        if row.staleness_reason is not None and not row.staleness_reason.strip():
            raise OrchestratorValidationError("evidence staleness_reason must be non-empty when present")
        if not row.excerpt.strip():
            raise OrchestratorValidationError("evidence excerpt must be non-empty")

    for claim_row in output.answer_evidence_map:
        if not claim_row.claim.strip():
            raise OrchestratorValidationError("answer_evidence_map claim must be non-empty")
        missing_ids = [evidence_id for evidence_id in claim_row.evidence_ids if evidence_id not in evidence_by_id]
        if missing_ids:
            raise OrchestratorValidationError(f"answer_evidence_map references missing evidence ids: {missing_ids}")

        if require_stale_claim_gaps and _is_stale_only_claim(claim_row, evidence_by_id):  # noqa: SIM102 -- verbatim port
            if not output.gaps and not _contains_uncertainty_note(output.answer_markdown):
                raise OrchestratorValidationError(
                    "stale-only claim support requires a gap entry or uncertainty wording in answer_markdown"
                )


def _required_non_empty_str(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorValidationError(f"{field} must be a non-empty string")
    return value


def _str_list(value: Any, field: str) -> list[str]:  # noqa: ANN401 -- raw JSON value under validation
    if not isinstance(value, list | tuple):
        raise OrchestratorValidationError(f"{field} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise OrchestratorValidationError(f"{field} must be a list of strings")
    return list(value)


def _object_tuple(value: Any, field: str) -> tuple[Mapping[str, Any], ...]:  # noqa: ANN401
    if not isinstance(value, list | tuple):
        raise OrchestratorValidationError(f"{field} must be a list of objects")
    if not all(isinstance(item, dict) for item in value):
        raise OrchestratorValidationError(f"{field} must be a list of objects")
    return tuple(_freeze_mapping(item) for item in value)


def _freeze_mapping(value: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> FrozenValue:  # noqa: ANN401 -- raw JSON-ish value under freezing
    if isinstance(value, dict):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    return cast(FrozenValue, value)


def _parse_evidence_rows(value: Any) -> list[OrchestratorEvidence]:  # noqa: ANN401
    if not isinstance(value, list | tuple):
        raise OrchestratorValidationError("evidence must be a list")
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise OrchestratorValidationError("evidence rows must be objects")
        rows.append(
            OrchestratorEvidence(
                id=_required_non_empty_str(item, "id"),
                source_type=_required_non_empty_str(item, "source_type"),
                path=_required_non_empty_str(item, "path"),
                freshness=_required_non_empty_str(item, "freshness"),
                staleness_reason=_optional_non_empty_str(item, "staleness_reason"),
                excerpt=_required_non_empty_str(item, "excerpt"),
                line_refs=_str_list(item.get("line_refs"), "line_refs"),
            )
        )
    return rows


def _optional_non_empty_str(payload: dict[str, Any], field: str) -> str | None:
    if field not in payload:
        raise OrchestratorValidationError(f"{field} must be present")
    value = payload[field]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorValidationError(f"{field} must be a string or null")
    return value


def _parse_answer_evidence_map(value: Any) -> list[AnswerEvidenceMap]:  # noqa: ANN401
    if not isinstance(value, list | tuple):
        raise OrchestratorValidationError("answer_evidence_map must be a list")
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise OrchestratorValidationError("answer_evidence_map rows must be objects")
        if "evidence_ids" not in item:
            raise OrchestratorValidationError("answer_evidence_map rows must include evidence_ids")
        evidence_ids = _str_list(item["evidence_ids"], "evidence_ids")
        if not evidence_ids:
            raise OrchestratorValidationError("answer_evidence_map evidence_ids must be non-empty")
        rows.append(
            AnswerEvidenceMap(
                claim=_required_non_empty_str(item, "claim"),
                evidence_ids=evidence_ids,
            )
        )
    return rows


def _parse_gaps(value: Any) -> list[EvidenceGap]:  # noqa: ANN401
    if not isinstance(value, list | tuple):
        raise OrchestratorValidationError("gaps must be a list")
    gaps = []
    for item in value:
        if not isinstance(item, dict):
            raise OrchestratorValidationError("gaps rows must be objects")
        gaps.append(
            EvidenceGap(
                question=_required_non_empty_str(item, "question"),
                reason=_required_non_empty_str(item, "reason"),
            )
        )
    return gaps


def _is_stale_only_claim(row: AnswerEvidenceMap, evidence_by_id: dict[str, OrchestratorEvidence]) -> bool:
    if not row.evidence_ids:
        return False
    mapped_evidence = [evidence_by_id[evidence_id] for evidence_id in row.evidence_ids]
    return all(evidence.freshness == "stale" for evidence in mapped_evidence)


def _contains_uncertainty_note(answer_markdown: str) -> bool:
    answer_lower = answer_markdown.lower()
    return any(_contains_uncertainty_word(answer_lower, word) for word in UNCERTAINTY_WORDS)


def _contains_uncertainty_word(answer_lower: str, word: str) -> bool:
    if word == "may":
        return re.search(r"\bmay\b(?!\s+\d{4}\b)", answer_lower) is not None
    return re.search(rf"\b{re.escape(word)}\b", answer_lower) is not None


# ---------------------------------------------------------------------------
# The orchestrator loop: planning tools, worker dispatch, safe degradation.
# ---------------------------------------------------------------------------

#: The lanes the planning catalog covers. Bundle-declared lane names are
#: constraint 5's business, so this is the orchestrator's own scope, not a
#: model of the vault: it is what a planner is allowed to browse.
ORCHESTRATOR_CATALOG_LANES = ("concepts", "entities", "adrs", "sources", "work")

#: Bound for the worker-scoped file reader below, passed explicitly to
#: `commands.query._read_file_bounded` — the canonical implementation, which
#: this module no longer carries a private copy of (Task 11 reconciliation).
MAX_WORKER_FILE_BYTES = 200_000


def build_orchestrator_context(
    *,
    query: str,
    bundle: Bundle,
    repo_root: Path | None,
    initial_candidates: Sequence[InitialCandidate],
    graph_tools: list[BaseTool],
) -> OrchestratorContext:
    """The bounded context packet handed to the planner."""
    allowed = filter_graph_tools(graph_tools, ALLOWED_ORCHESTRATOR_GRAPH_TOOL_NAMES)
    return OrchestratorContext(
        query=query,
        bundle=bundle,
        repo_root=repo_root.resolve() if repo_root is not None else None,
        initial_candidates=tuple(initial_candidates),
        graph_tools_available=bool(allowed),
        graph_tool_names=tuple(graph_tool.name for graph_tool in allowed),
        worker_capabilities=_worker_capabilities(),
        answer_contract=_answer_contract(),
    )


def build_orchestrator_tools(*, bundle: Bundle, graph_tools: list[BaseTool]) -> list[BaseTool]:
    """Bounded planning tools: catalog search, one page read, capability discovery.

    Source-code reads stay behind `code_reader` worker tasks — the planner has
    no `read_file`, on purpose.
    """
    catalog = build_catalog(bundle, lanes=ORCHESTRATOR_CATALOG_LANES)

    @tool
    def read_wiki_page(path: str) -> str:
        """Read one bundle page by concept id, bounded to a safe size.

        Args:
            path: a concept id — the bundle-relative path minus `.md`.
        """
        return read_bounded_page(bundle, path, max_chars=MAX_ORCHESTRATOR_WIKI_PAGE_CHARS)

    @tool
    def search_wiki(query: str, kind: str | None = None, top_k: int = MAX_ORCHESTRATOR_SEARCH_ROWS) -> str:
        """Search the bundle catalog by title, summary or slug. Returns JSON rows.

        Args:
            query: substring to match.
            kind: optional lane or entry kind filter.
            top_k: row cap, itself capped at 20.
        """
        bounded = max(1, min(top_k, MAX_ORCHESTRATOR_SEARCH_ROWS))
        return json.dumps(search_catalog(catalog, query, kind=kind, limit=bounded), indent=2, sort_keys=True)

    @tool
    def list_worker_capabilities() -> str:
        """Return the valid worker task shapes and their limits, as JSON."""
        return json.dumps(_jsonable(_worker_capabilities()), indent=2, sort_keys=True)

    return [
        read_wiki_page,
        search_wiki,
        list_worker_capabilities,
        *filter_graph_tools(graph_tools, ALLOWED_ORCHESTRATOR_GRAPH_TOOL_NAMES),
    ]


async def run_query_orchestrator(
    *,
    query: str,
    bundle: Bundle,
    repo_root: Path | None,
    initial_candidates: Sequence[InitialCandidate],
    graph_tools: list[BaseTool],
    trace_dir: Path,
    workspace_root: Path | None = None,
    layout: WorkspaceLayout | None = None,
) -> QueryOrchestratorResult:
    """Run the bounded query-orchestration loop with safe degradation.

    `run_tool_loop`, `run_worker_batch` and `make_llm` are called unqualified
    (module globals, not a closure capture) so a caller — chiefly the test
    suite — can `monkeypatch.setattr(query_orchestrator, "run_tool_loop", …)`
    and have this function actually observe the replacement.

    `workspace_root` is threaded through to `code_reader` worker tasks as the
    `_read_file_bounded` `exclude` argument — the same exclusion
    `commands.query._run_code_fallback`'s `read_file` tool applies. Without
    it, an orchestrator worker plan whose hints happen to name the workspace
    directory (traces, the search index, config — all of which live under
    `repo_root` by default) could read the workspace's own derived state back
    into an answer's evidence.

    `layout` is what makes a `roles.<role>.<field>` override in `workspace.yaml`
    reach this path, and it is threaded to every role resolution below. `None`
    means packaged-only, which is `roles.py`'s own contract — passing a layout
    is how a caller opts into workspace overrides, and both real call sites
    already hold one. Without it this path was packaged-only while
    `commands.query`'s fixed path was not, so an override reached the fallback
    and not the default: the same role resolving to two different models
    depending on which path ran.
    """
    context = build_orchestrator_context(
        query=query,
        bundle=bundle,
        repo_root=repo_root,
        initial_candidates=initial_candidates,
        graph_tools=graph_tools,
    )
    tools = build_orchestrator_tools(bundle=bundle, graph_tools=graph_tools)
    llm = make_llm("query_orchestrator", layout=layout)
    messages: list[Any] = [
        SystemMessage(content=QUERY_ORCHESTRATOR_SYSTEM),
        HumanMessage(content=_orchestrator_context_prompt(context)),
    ]
    trace_metadata: dict[str, Any] = {
        "status": "ok",
        "worker_batches": 0,
        "graph_tools_available": context.graph_tools_available,
        "graph_tool_names": list(context.graph_tool_names),
    }
    accumulated_worker_results: list[Mapping[str, Any]] = []

    # Loop termination invariant: every path either returns or increments
    # trace_metadata["worker_batches"] exactly once, and the cap check enforces
    # that this counter cannot exceed MAX_ORCHESTRATOR_WORKER_BATCHES before
    # returning, guaranteeing termination.
    while True:
        try:
            loop_result = await run_tool_loop(
                llm=llm,
                tools=tools,
                messages=messages,
                max_iterations=MAX_ORCHESTRATOR_TOOL_ITERS,
                cap_label="query orchestrator",
            )
        except Exception as exc:
            return _degraded_result(
                query=query,
                status="tool_loop_error",
                error=f"{type(exc).__name__}: {exc}",
                worker_batches=trace_metadata["worker_batches"],
                graph_tools_available=context.graph_tools_available,
                graph_tool_names=context.graph_tool_names,
            )

        if loop_result.status != "ok":
            return _degraded_result(
                query=query,
                status="tool_loop_failed",
                error=loop_result.error or loop_result.status,
                worker_batches=trace_metadata["worker_batches"],
                graph_tools_available=context.graph_tools_available,
                graph_tool_names=context.graph_tool_names,
            )

        try:
            output = parse_orchestrator_output(loop_result.final_text, require_stale_claim_gaps=True)
        except OrchestratorValidationError as exc:
            return _degraded_result(
                query=query,
                status=_degradation_status_for_validation_error(exc),
                error=str(exc),
                worker_batches=trace_metadata["worker_batches"],
                graph_tools_available=context.graph_tools_available,
                graph_tool_names=context.graph_tool_names,
                worker_results=accumulated_worker_results,
            )

        if not output.worker_plan:
            trace_metadata["status"] = "ok"
            if loop_result.error:
                trace_metadata["tool_loop_error"] = loop_result.error
            return QueryOrchestratorResult(
                output=_output_with_authoritative_worker_results(output, accumulated_worker_results),
                trace_metadata=MappingProxyType(trace_metadata),
            )

        if trace_metadata["worker_batches"] >= MAX_ORCHESTRATOR_WORKER_BATCHES:
            trace_metadata["status"] = "capped"
            trace_metadata["error"] = f"worker batch cap reached ({MAX_ORCHESTRATOR_WORKER_BATCHES})"
            return QueryOrchestratorResult(
                output=_capped_output(
                    output,
                    query=query,
                    worker_results=accumulated_worker_results,
                    reason=trace_metadata["error"],
                ),
                trace_metadata=MappingProxyType(trace_metadata),
            )

        try:
            worker_tasks = parse_worker_tasks(output.worker_plan)
        except OrchestratorValidationError as exc:
            return _degraded_result(
                query=query,
                status="validation_error",
                error=str(exc),
                worker_batches=trace_metadata["worker_batches"],
                graph_tools_available=context.graph_tools_available,
                graph_tool_names=context.graph_tool_names,
                worker_results=accumulated_worker_results,
            )

        try:
            worker_results = await run_worker_batch(
                worker_tasks,
                query=query,
                bundle=bundle,
                repo_root=repo_root,
                trace_dir=trace_dir,
                workspace_root=workspace_root,
                layout=layout,
            )
        except Exception as exc:
            return _degraded_result(
                query=query,
                status="worker_batch_error",
                error=f"{type(exc).__name__}: {exc}",
                worker_batches=trace_metadata["worker_batches"],
                graph_tools_available=context.graph_tools_available,
                graph_tool_names=context.graph_tool_names,
                worker_results=accumulated_worker_results,
            )
        trace_metadata["worker_batches"] += 1
        accumulated_worker_results.extend(worker_results)
        messages.append(AIMessage(content=loop_result.final_text))
        messages.append(
            HumanMessage(
                content=(
                    f"Worker batch {trace_metadata['worker_batches']} results:\n"
                    f"{json.dumps(_jsonable(worker_results), indent=2, sort_keys=True)}\n\n"
                    "Use these results to continue. Return final JSON with an empty worker_plan when sufficient."
                )
            )
        )


def degraded_output(query: str, *, reason: str) -> OrchestratorOutput:
    """Build a valid low-confidence output for orchestrator failure paths."""
    return OrchestratorOutput(
        answer_markdown=f"Insufficient evidence to answer safely. {reason}",
        citations=[],
        evidence=[],
        answer_evidence_map=[],
        worker_plan=(),
        worker_results=(),
        gaps=[EvidenceGap(question=query, reason=reason)],
        confidence="low",
    )


def _degraded_result(
    *,
    query: str,
    status: str,
    error: str,
    worker_batches: int,
    graph_tools_available: bool,
    graph_tool_names: tuple[str, ...],
    worker_results: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
) -> QueryOrchestratorResult:
    return QueryOrchestratorResult(
        output=_output_with_authoritative_worker_results(degraded_output(query, reason=error), worker_results),
        trace_metadata=MappingProxyType(
            {
                "status": status,
                "error": error,
                "worker_batches": worker_batches,
                "graph_tools_available": graph_tools_available,
                "graph_tool_names": list(graph_tool_names),
            }
        ),
    )


def _degradation_status_for_validation_error(exc: OrchestratorValidationError) -> str:
    if str(exc).startswith("Invalid JSON"):
        return "invalid_json"
    return "validation_error"


def _output_with_authoritative_worker_results(
    output: OrchestratorOutput,
    worker_results: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> OrchestratorOutput:
    if not worker_results:
        return output
    return OrchestratorOutput(
        answer_markdown=output.answer_markdown,
        citations=list(output.citations),
        evidence=list(output.evidence),
        answer_evidence_map=list(output.answer_evidence_map),
        worker_plan=output.worker_plan,
        worker_results=_freeze_worker_result_rows(worker_results),
        gaps=list(output.gaps),
        confidence=output.confidence,
    )


def _capped_output(
    output: OrchestratorOutput,
    *,
    query: str,
    worker_results: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    reason: str,
) -> OrchestratorOutput:
    return OrchestratorOutput(
        answer_markdown=output.answer_markdown,
        citations=list(output.citations),
        evidence=list(output.evidence),
        answer_evidence_map=list(output.answer_evidence_map),
        worker_plan=(),
        worker_results=_freeze_worker_result_rows(worker_results),
        gaps=[
            *output.gaps,
            EvidenceGap(question=query, reason=reason),
        ],
        confidence="low",
    )


def _freeze_worker_result_rows(
    rows: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(_freeze_mapping(dict(_jsonable(row))) for row in rows)


def _orchestrator_context_prompt(context: OrchestratorContext) -> str:
    payload = {
        "query": context.query,
        "bundle_root": context.bundle.root.as_posix(),
        "repo_root": context.repo_root.as_posix() if context.repo_root is not None else None,
        "initial_candidates": [
            {
                "path": candidate.path,
                "score": candidate.score,
                "excerpt": candidate.excerpt,
                "freshness": candidate.freshness,
                "staleness_reason": candidate.staleness_reason,
            }
            for candidate in context.initial_candidates
        ],
        "graph_tools_available": context.graph_tools_available,
        "graph_tool_names": list(context.graph_tool_names),
        "worker_capabilities": _jsonable(context.worker_capabilities),
        "answer_contract": _jsonable(context.answer_contract),
        "worker_batch_cap": MAX_ORCHESTRATOR_WORKER_BATCHES,
    }
    return (
        "Answer the user query using the supplied context and tools. "
        "Return exactly one JSON object matching the contract.\n\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def parse_worker_tasks(rows: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> tuple[WorkerTask, ...]:
    """Parse and validate orchestrator worker-plan rows."""
    if not isinstance(rows, list | tuple):
        raise OrchestratorValidationError("worker_plan must be a list of objects")

    tasks = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise OrchestratorValidationError("worker_plan rows must be objects")

        worker = _required_non_empty_mapping_str(row, "worker")
        if worker not in ALLOWED_WORKERS:
            raise OrchestratorValidationError(f"worker must be one of {list(ALLOWED_WORKERS)}; got {worker!r}")

        task = WorkerTask(
            worker=worker,
            task_id=_required_non_empty_mapping_str(row, "task_id"),
            query_focus=_required_non_empty_mapping_str(row, "query_focus"),
            expected_evidence=_required_non_empty_mapping_str(row, "expected_evidence"),
            page_path=_parse_worker_page_path(row, worker),
            target_paths_or_hints=_parse_worker_target_hints(row, worker),
        )
        tasks.append(task)
    return tuple(tasks)


async def run_worker_batch(
    worker_tasks: list[WorkerTask] | tuple[WorkerTask, ...],
    *,
    query: str,
    bundle: Bundle | None,
    repo_root: Path | None,
    trace_dir: Path,
    workspace_root: Path | None = None,
    layout: WorkspaceLayout | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Dispatch orchestrated librarian/code-reader tasks and record partial failures."""
    if not worker_tasks:
        return ()

    pool = SubagentPool(trace_dir, price_lookup=cost_for_usage)
    results: list[Mapping[str, Any]] = []

    for role in ALLOWED_WORKERS:
        role_tasks = [task for task in worker_tasks if task.worker == role]
        if not role_tasks:
            continue

        binding = role_binding(role, layout=layout)
        if role == "librarian":
            if bundle is None:
                results.extend(
                    _worker_error_row(task, "bundle is required for librarian workers") for task in role_tasks
                )
                continue
            if layout is None:
                results.extend(
                    _worker_error_row(task, "layout is required for librarian workers") for task in role_tasks
                )
                continue
            config = load_workspace_config(layout)
            schema_set = load_schemas(config.declarations_dir / SCHEMA_DIRNAME)
            task_runner = _build_librarian_task_runner(
                binding.make_llm(), query=query, bundle=bundle, schema_set=schema_set
            )
        else:
            if repo_root is None:
                results.extend(
                    _worker_error_row(task, "repo_root is required for code_reader workers") for task in role_tasks
                )
                continue
            task_runner = _build_code_reader_task_runner(
                binding.make_llm(), query=query, repo_root=repo_root, workspace_root=workspace_root
            )

        fan_result: FanOutResult = await pool.run_all(
            items=list(role_tasks),
            task=task_runner,
            role=role,
            model_id=binding.spec.model_id,
            max_concurrency=binding.spec.max_concurrency,
        )
        results.extend(_worker_success_row(item, result) for item, result in fan_result.successes)
        results.extend(_worker_failure_from_error(error) for error in fan_result.errors)

    return tuple(results)


def _worker_capabilities() -> Mapping[str, Mapping[str, Any]]:
    return {
        "librarian": {
            "description": "Extract relevant evidence from one wiki page.",
            "required_fields": ("page_path", "query_focus", "expected_evidence"),
            "limits": {"page_path": "concept id — the bundle-relative path minus `.md`"},
        },
        "code_reader": {
            "description": "Verify source-backed claims through bounded source-reading worker tasks.",
            "required_fields": ("target_paths_or_hints", "query_focus", "expected_evidence"),
            "limits": {
                "target_paths_or_hints": "repo-relative paths or search hints; no direct repo files are read by "
                "orchestrator planning tools"
            },
        },
    }


def _required_non_empty_mapping_str(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorValidationError(f"{field} must be a non-empty string")
    return value


def _parse_worker_page_path(row: Mapping[str, Any], worker: str) -> str | None:
    if worker != "librarian":
        return None
    return _required_non_empty_mapping_str(row, "page_path")


def _parse_worker_target_hints(row: Mapping[str, Any], worker: str) -> tuple[str, ...]:
    if worker != "code_reader":
        return ()
    value = row.get("target_paths_or_hints")
    if not isinstance(value, list | tuple) or not value:
        raise OrchestratorValidationError("target_paths_or_hints must be a non-empty list of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise OrchestratorValidationError("target_paths_or_hints must be a non-empty list of strings")
    return tuple(value)


def _build_librarian_task_runner(
    llm: Any,  # noqa: ANN401 -- a bound BaseChatModel; langchain's runnable type does not narrow usefully
    *,
    query: str,
    bundle: Bundle,
    schema_set: SchemaSet,
) -> Callable[[WorkerTask], Awaitable[TaskResult]]:
    librarian_system = build_librarian_system(schema_set=schema_set)

    async def librarian_worker(task: WorkerTask) -> TaskResult:
        page_path = task.page_path or ""
        page_text = read_bounded_page(bundle, page_path, max_chars=MAX_WORKER_WIKI_PAGE_CHARS)
        response = await llm.ainvoke(
            [
                SystemMessage(content=librarian_system),
                HumanMessage(
                    content=(
                        f"Query: {query}\n\n"
                        f"Worker task: {task.task_id}\n"
                        f"Query focus: {task.query_focus}\n"
                        f"Expected evidence: {task.expected_evidence}\n\n"
                        f"Page ({page_path}):\n{page_text}"
                    )
                ),
            ]
        )
        return TaskResult(value=getattr(response, "content", "") or "", response=response)

    return librarian_worker


def _build_code_reader_task_runner(
    llm_raw: Any,  # noqa: ANN401 -- a bound BaseChatModel; see _build_librarian_task_runner
    *,
    query: str,
    repo_root: Path,
    workspace_root: Path | None = None,
) -> Callable[[WorkerTask], Awaitable[TaskResult]]:
    async def code_reader_worker(task: WorkerTask) -> TaskResult:
        @tool
        def read_file(path: str) -> str:
            """Read one source file allowed by this worker task's target hints."""
            return _read_worker_scoped_file(repo_root, path, task.target_paths_or_hints, exclude=workspace_root)

        llm = llm_raw.bind_tools([read_file])
        hints = "\n".join(f"- {hint}" for hint in task.target_paths_or_hints)
        messages: list[Any] = [
            SystemMessage(content=ORCHESTRATED_CODE_READER_SYSTEM),
            HumanMessage(
                content=(
                    f"Query: {query}\n\n"
                    f"Worker task: {task.task_id}\n"
                    f"Query focus: {task.query_focus}\n"
                    f"Expected evidence: {task.expected_evidence}\n\n"
                    "Target paths or hints:\n"
                    f"{hints}\n\n"
                    "Read only plausible repo-relative source paths through the read_file tool."
                )
            ),
        ]
        for _ in range(ORCHESTRATED_CODE_READER_MAX_ITERS):
            response = await llm.ainvoke(messages)
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                return TaskResult(value=getattr(response, "content", "") or "", response=response)
            messages.append(response)
            for call in tool_calls:
                call_args = call.get("args", {}) if isinstance(call, dict) else {}
                call_id = call.get("id", "") if isinstance(call, dict) else ""
                requested = call_args.get("path", "")
                tool_output = _read_worker_scoped_file(
                    repo_root, requested, task.target_paths_or_hints, exclude=workspace_root
                )
                messages.append(ToolMessage(content=tool_output, tool_call_id=call_id))
        return TaskResult(value="NO_RELEVANT_CONTENT", response=None)

    return code_reader_worker


def _read_worker_scoped_file(
    repo_root: Path, requested_path: str, hints: tuple[str, ...], *, exclude: Path | None = None
) -> str:
    if not _path_allowed_by_worker_hints(repo_root, requested_path, hints):
        return f"ERROR: refusing to read {requested_path!r}: outside this worker task's target_paths_or_hints"
    try:
        return _read_file_bounded(repo_root, requested_path, exclude=exclude, max_bytes=MAX_WORKER_FILE_BYTES)
    except PermissionError as exc:
        return f"ERROR: {exc}"
    except OSError as exc:
        return f"ERROR: {exc}"


def _path_allowed_by_worker_hints(repo_root: Path, requested_path: str, hints: tuple[str, ...]) -> bool:
    requested = _repo_relative_posix(repo_root, requested_path)
    if requested is None:
        return False
    for hint in hints:
        normalized_hint = _repo_relative_posix(repo_root, hint)
        if normalized_hint is None:
            continue
        if requested == normalized_hint:
            return True
        hint_is_directoryish = hint.endswith("/") or not Path(normalized_hint).suffix
        if hint_is_directoryish and requested.startswith(normalized_hint.rstrip("/") + "/"):
            return True
    return False


def _repo_relative_posix(repo_root: Path, path: str) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    root = repo_root.resolve(strict=False)
    candidate = (repo_root / path).resolve(strict=False)
    if not candidate.is_relative_to(root):
        return None
    return candidate.relative_to(root).as_posix()


def _worker_success_row(task: WorkerTask, result: Any) -> Mapping[str, Any]:  # noqa: ANN401 -- a TaskResult.value, itself Any
    return _freeze_mapping(
        {
            "task_id": task.task_id,
            "worker": task.worker,
            "status": "complete",
            "result": str(result or ""),
        }
    )


def _worker_failure_from_error(error: PerItemError) -> Mapping[str, Any]:
    task = error.item
    if isinstance(task, WorkerTask):
        return _worker_error_row(task, str(error.exception))
    return _freeze_mapping(
        {
            "task_id": "",
            "worker": "",
            "status": "error",
            "error": str(error.exception),
        }
    )


def _worker_error_row(task: WorkerTask, error: str) -> Mapping[str, Any]:
    return _freeze_mapping(
        {
            "task_id": task.task_id,
            "worker": task.worker,
            "status": "error",
            "error": error,
        }
    )


def _answer_contract() -> Mapping[str, Any]:
    return MappingProxyType(
        {
            "answer_markdown": "Markdown final answer.",
            "citations": "List of cited wiki/code paths.",
            "evidence": (
                "Rows with id, source_type, path, freshness, staleness_reason, excerpt, and line_refs. "
                "source_type must be wiki or code."
            ),
            "answer_evidence_map": "Claim-to-evidence id mapping.",
            "worker_plan": "Worker tasks requested by the orchestrator.",
            "worker_results": "Worker result summaries considered by the orchestrator.",
            "gaps": "Explicit unanswered questions or stale-only evidence gaps.",
            "confidence": "One of high, medium, or low.",
        }
    )
