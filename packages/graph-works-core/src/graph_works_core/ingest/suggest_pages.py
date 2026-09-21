"""Propose which durable pages one source justifies, and file each as a proposal.

The output contract is spec §4.5's:

| Key | Where it comes from |
|---|---|
| `lane` | proposed by the extractor, one of the lanes |
| `slug` | derived: `lane_set.target_for(lane, title)` |
| `mode`, `existing_slug` | derived: `plan_file` reads `bundle.has_member` |
| `rank`, `confidence`, `evidence`, ... | ride through into `sources[]` |

**`classify` validates the decision; it never makes one.** The extractor
proposes a lane *and a rationale*, and `doc_wiki_okf.diataxis.classify` says
whether that decision stands. What it refuses is **dropped** with its closed
reason recorded in `status["unclassified"]` -- never defaulted into a lane.
Mapping every refusal to `explanation` is a two-line change that makes
`explanation` a dumping ground at the exact point where pages enter the vault.
That key carries `classify` refusals and nothing else: a `plan_file` refusal
and a same-run duplicate are different shapes and get their own keys, so
"closed vocabulary" is true of each key rather than of none.

`classify` validates a **type name**, and the lane's type comes from
`LaneSet[lane].type_name`. Its derived `concept_id` is deliberately unused: the
adr lane's type is `Explanation`, so that id would say `explanations/…`, while
the target is `lane_set.target_for`'s. Placement is the lane's; validation is
`classify`'s.

**Best-effort, always.** A reasoner failure, an extractor call failure, or a
parse miss yields zero proposals, records the failure in the status, and never
fails the ingest. The status is what `IngestResult.proposal_status` carries, and
the CLI surfaces its `error`, `failed` and `errored` entries as warnings, so a
degraded run is not silently indistinguishable from a run with nothing to
suggest. Best-effort is not the same as unaccounted for: a proposal
whose write does not land is recorded in `failed` and is **not** counted, so
the status never claims a proposal no file supports.

**Known consequence, inherited not introduced:** `okf_ext.proposals` keys
`sources[]` dedup on `resource` and skips an already-stored one, so a re-fired
source no longer updates its rationale in place the way `upsert_proposal` did.
Recorded in `docs/cutover-key-mapping.md`; not worked around here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from code_wiki_okf.entities.catalog import CONTENT_GROUPS
from code_wiki_okf.placement import GLOBAL_LANES, REPOSITORIES_LANE
from doc_wiki_okf.diataxis.classify import Unclassified, classify
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.lanes import LaneSet
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from okf_ext.proposals import ProposalPlan
from okf_ext.proposals import apply as apply_plan
from okf_ext.schemas import SchemaSet, declared_directories
from okf_io import Bundle

from graph_works_core.agent_substrate.agent_tools import strip_code_fence
from graph_works_core.agent_substrate.roles import make_llm
from graph_works_core.ingest.prompts.extractor import build_extractor_system
from graph_works_core.ingest.proposal_reasoner import ProposalReasonerResult, run_proposal_reasoner
from graph_works_core.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)

#: The extractor's own cap, enforced here rather than trusted from the model.
MAX_SUGGESTIONS = 5

#: The bundle lane the ingested material's own page lives in.
SOURCES_LANE = "sources"

#: Keys carried verbatim from a suggestion into the proposal's `sources[]`
#: entry, where `ReviewRenderer` reads them. Order is the rendered order.
RIDE_THROUGH: tuple[str, ...] = (
    "evidence",
    "existing_pages_considered",
    "reasoning_summary",
    "potential_conflicts",
    "implementation_notes",
)

_CONFIDENCES = frozenset({"high", "medium", "low"})
_UNRANKED = 999


def catalog_lanes(lane_set: LaneSet, schema_set: SchemaSet) -> tuple[str, ...]:
    """Every lane the reasoner's catalog covers, as directory ids.

    The proposal lanes come off the `LaneSet` (whose directories come off
    the loaded schemas). The four repository entity discovery indexes come
    from the code-wiki catalog's public grouping plus those same schema
    declarations; placement owns the repository and global lanes. `File` is
    intentionally absent: its `files/` directory is repository-local, never a
    top-level discovery index. `sources/` remains because the reasoner should
    see what has already been ingested.
    """
    proposal = tuple(lane.directory.rstrip("/") for lane in lane_set.lanes)
    declared = declared_directories(schema_set)
    repository_entities = tuple(
        directory.rstrip("/")
        for _heading, type_name in CONTENT_GROUPS
        if (directory := declared.get(type_name)) is not None
    )
    placement_catalogs = (REPOSITORIES_LANE, *GLOBAL_LANES)
    return tuple(dict.fromkeys((*proposal, *repository_entities, *placement_catalogs, SOURCES_LANE)))


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _validate_suggestion(raw: object, lane_names: Sequence[str]) -> dict[str, Any] | None:
    """Normalize one suggestion to the §4.5 shape, or `None` if unusable.

    Three requirements, checked here so `classify` is never called on input it
    would refuse for a reason the model could have avoided: a lane the set
    declares, a non-blank title, and a non-blank rationale.
    """
    if not isinstance(raw, dict):
        return None
    lane = str(raw.get("lane", "")).strip().lower()
    if lane not in lane_names:
        return None
    title = str(raw.get("title") or "").strip()
    rationale = str(raw.get("rationale") or "").strip()
    if not title or not rationale:
        return None
    try:
        rank = int(raw.get("rank", _UNRANKED))
    except (OverflowError, TypeError, ValueError):
        rank = _UNRANKED
    confidence = str(raw.get("confidence", "medium")).strip().lower()
    if confidence not in _CONFIDENCES:
        confidence = "medium"
    entry: dict[str, Any] = {
        "lane": lane,
        "title": title,
        "rationale": rationale,
        "description": str(raw.get("description") or "").strip() or rationale,
        "rank": rank,
        "confidence": confidence,
    }
    for key in RIDE_THROUGH:
        entry[key] = _string_list(raw.get(key)) if key != "reasoning_summary" else str(raw.get(key) or "").strip()
    return entry


def parse_extractor_response(text: str, *, lane_names: Sequence[str]) -> tuple[list[dict[str, Any]], bool]:
    """Parse the extractor output into `(suggestions, parsed)`.

    `parsed` is `True` whenever a well-formed list was recovered, **including
    an empty one** -- "nothing is warranted" is an answer, not a failure. It is
    `False` only for a JSON error or a top-level shape that is neither a list
    nor an object carrying `suggestions`.

    `json.loads` rather than a YAML reader: spec §6.9 declares three new
    dependencies and no YAML parser is among them, `json` is stdlib, and JSON
    is a closed grammar so a miss is unambiguous.
    """
    body = strip_code_fence(text or "")
    if not body:
        return [], False
    try:
        loaded = json.loads(body)
    except ValueError:
        return [], False

    if isinstance(loaded, list):
        items: list[Any] = loaded
    elif isinstance(loaded, dict) and "suggestions" in loaded:
        raw = loaded["suggestions"]
        if raw is None:
            items = []
        elif isinstance(raw, list):
            items = raw
        else:
            return [], False
    else:
        return [], False

    suggestions = [entry for entry in (_validate_suggestion(item, lane_names) for item in items) if entry is not None]
    suggestions.sort(key=lambda entry: int(entry["rank"]))
    return suggestions[:MAX_SUGGESTIONS], True


def build_curated_index(bundle: Bundle, lane_set: LaneSet) -> list[dict[str, str]]:
    """Existing pages in the proposal lanes: `{lane, id, title, summary}`.

    The dedup substrate the extractor proposes *against*. Built from the loaded
    bundle -- one walk, every member already typed -- rather than a directory
    walk per lane, which is C2 §4.2's move for the same reason.
    """
    by_directory = {lane.directory: lane.name for lane in lane_set.lanes}
    index: list[dict[str, str]] = []
    for concept_id in sorted(bundle.concepts):
        directory = f"{concept_id.rpartition('/')[0]}/"
        lane = by_directory.get(directory)
        if lane is None:
            continue
        document = bundle.concepts[concept_id]
        index.append(
            {
                "lane": lane,
                "id": concept_id,
                "title": str(document.fm.title or concept_id.rpartition("/")[2]),
                "summary": str(document.fm.description or ""),
            }
        )
    return index


def build_extract_prompt(analysis: str, curated: Sequence[Mapping[str, str]]) -> str:
    """The extractor's human message: what exists, then what the reasoner said."""
    if curated:
        listed = "\n".join(
            f"  - {entry['lane']} · {entry['id']} — {entry.get('title', '')}"
            + (f" — {entry['summary']}" if entry.get("summary") else "")
            for entry in curated
        )
    else:
        listed = "  (no curated pages yet)"

    return (
        "Existing curated pages. Argue for one of these when the source adds to it, "
        "and name it in existing_pages_considered:\n"
        f"{listed}\n\n"
        "--- Proposal reasoner analysis ---\n"
        f"{analysis}\n"
        "--- End proposal reasoner analysis ---\n\n"
        f"Normalize this analysis into at most {MAX_SUGGESTIONS} JSON suggestions. "
        'Return {"suggestions": []} if none are warranted.'
    )


def _source_entry(suggestion: Mapping[str, Any], *, source_page: str, source_title: str) -> dict[str, Any]:
    """One `sources[]` entry: identity, rationale, and the ride-through keys.

    `resource` is the identity `okf_ext.proposals` dedups on, and it is the
    source page's own bundle path -- known before the page is written, so it is
    stable whether this is the first or the fifth proposal this source files.
    """
    entry: dict[str, Any] = {
        "id": Path(source_page).stem,
        "resource": f"/{source_page}",
        "title": source_title,
        "rationale": suggestion["rationale"],
        "rank": suggestion["rank"],
        "confidence": suggestion["confidence"],
    }
    for key in RIDE_THROUGH:
        if suggestion.get(key):
            entry[key] = suggestion[key]
    return entry


@dataclass(frozen=True, slots=True)
class PlannedProposal:
    """One classified, `plan_file`'d suggestion, not yet applied."""

    title: str
    lane: str
    target: str
    rank: int
    confidence: str
    plan: ProposalPlan


async def plan_suggestions(
    *,
    bundle: Bundle,
    schema_set: SchemaSet,
    lane_set: LaneSet,
    material: Path,
    source_text: str,
    source_page: str,
    source_title: str,
    source_kind: str,
    origin: str,
    page_text: str,
    entity_uri: str | None,
    entity_page: str | None,
    by: str,
    at: datetime,
    graph_tools: Sequence[BaseTool] = (),
    layout: WorkspaceLayout | None = None,
    model_override: str | None = None,
) -> tuple[list[PlannedProposal], dict[str, Any]]:
    """Reason, extract, validate, `plan_file`. Writes nothing. Returns `(planned, status)`.

    Never raises. Every failure path returns `([], status)` with the reason
    recorded in `status`, same contract `run_suggest_phase` used to carry alone.

    A blank *source_text* returns `([], status)` before either model call, with
    `reasoner` and `extractor` left at `"skipped"`. Binary material extracts to
    `""` and an empty text file is the same case; `error` stays unset, because a
    deliberate skip is not a degradation and `log.md` should not say it was.

    `status` carries the plan-time-only keys: `reasoner`, `extractor`,
    `unclassified`, `refused`, `duplicates`, `errored` -- a suggestion's
    classify/`plan_file` pipeline raising is still caught per suggestion, same
    as before the split. `proposals` is an **optimistic** count equal to
    `len(planned)`: nothing has been applied yet. `failed` is always `[]`
    here -- a planned write not landing is `apply_suggestions`' own finding.
    """
    lane_names = tuple(lane.name for lane in lane_set.lanes)
    status: dict[str, Any] = {
        "reasoner": "skipped",
        "extractor": "skipped",
        "proposals": 0,
        "unclassified": [],
        "refused": [],
        "duplicates": [],
        "failed": [],
        "errored": [],
        "error": None,
    }

    if not source_text.strip():
        # Nothing to reason over. Binary material extracts to `""` (B-G), and
        # two model calls against an empty source produce suggestions the
        # material does not support. `reasoner` and `extractor` stay
        # `"skipped"`, which is the record: every other path overwrites them.
        return [], status

    try:
        reasoned = await run_proposal_reasoner(
            bundle=bundle,
            lanes=catalog_lanes(lane_set, schema_set),
            lane_set=lane_set,
            material=material,
            source_text=source_text,
            source_page=source_page,
            source_kind=source_kind,
            origin=origin,
            page_text=page_text,
            entity_uri=entity_uri,
            entity_page=entity_page,
            graph_tools=graph_tools,
            layout=layout,
            model_override=model_override,
        )
    except Exception:  # best-effort by contract; the ingest still writes
        logger.warning("proposal reasoner raised; skipping suggestions", exc_info=True)
        reasoned = ProposalReasonerResult(status="failed", analysis="", error="proposal_reasoner failed")

    status["reasoner"] = reasoned.status
    if reasoned.status != "ok":
        status["error"] = reasoned.error or "proposal_reasoner failed"
        return [], status

    prompt = build_extract_prompt(reasoned.analysis, build_curated_index(bundle, lane_set))
    try:
        response = await make_llm("extractor", layout=layout, model_override=model_override).ainvoke(
            [SystemMessage(build_extractor_system(lane_set=lane_set)), HumanMessage(prompt)]
        )
    except Exception:  # best-effort by contract
        logger.warning("extractor call failed; skipping suggestions", exc_info=True)
        status["extractor"] = "failed"
        status["error"] = "extractor failed"
        return [], status

    if not isinstance(response.content, str):
        status["extractor"] = "failed"
        status["error"] = "extractor output did not parse"
        return [], status

    suggestions, parsed = parse_extractor_response(response.content, lane_names=lane_names)
    if not parsed:
        status["extractor"] = "failed"
        status["error"] = "extractor output did not parse"
        return [], status

    planned: list[PlannedProposal] = []
    unclassified: list[str] = []
    refused: list[str] = []
    duplicates: list[str] = []
    errored: list[str] = []
    filed: set[str] = set()
    for suggestion in suggestions:
        try:
            lane = lane_set[suggestion["lane"]]
            decision = classify(
                schema_set,
                type_name=lane.type_name,
                title=suggestion["title"],
                rationale=suggestion["rationale"],
                decided_by="agent:extractor",
            )
            if isinstance(decision, Unclassified):
                # Dropped, never defaulted. `Unclassified.reason` is closed
                # vocabulary and this key holds nothing else, so the status
                # stays machine-readable on the page that records it.
                unclassified.append(f"{suggestion['title']}: {decision.reason}")
                logger.info("dropping %r: %s (%s)", suggestion["title"], decision.reason, decision.detail)
                continue

            target = lane_set.target_for(suggestion["lane"], suggestion["title"])
            if target in filed:
                # *bundle* is a snapshot taken before this loop ran, so it does
                # not carry the loop's own writes and `plan_file` would derive
                # `mode="create"` a second time -- a write `apply` then refuses
                # as `stale`. Nothing is lost by dropping it instead:
                # `plan_propose` dedups `sources[]` on `resource`, and every
                # suggestion in this run carries the same source page, so the
                # second entry would be skipped even by a correct merge. Its own
                # key: the entry is a target path, not an `Unclassified.reason`.
                duplicates.append(f"{suggestion['title']}: {target}")
                logger.info("dropping %r: already filed against %s", suggestion["title"], target)
                continue

            plan = plan_file(
                bundle,
                lane_set,
                lane=suggestion["lane"],
                title=suggestion["title"],
                description=suggestion["description"],
                source=_source_entry(suggestion, source_page=source_page, source_title=source_title),
                by=by,
                at=at,
            )
            if not plan.ok:
                # `Refusal.kind`, bare: the key name is what the `refused (…)`
                # wrapper used to say, and one shape per key is the point.
                refused.append(f"{suggestion['title']}: {plan.refusals[0].kind}")
                logger.info("proposal for %r refused: %s", suggestion["title"], plan.refusals[0].detail)
                continue
            filed.add(target)
            planned.append(
                PlannedProposal(
                    title=suggestion["title"],
                    lane=suggestion["lane"],
                    target=target,
                    rank=suggestion["rank"],
                    confidence=suggestion["confidence"],
                    plan=plan,
                )
            )
        except Exception as exc:
            logger.warning("suggestion %r raised; dropping it", suggestion["title"], exc_info=True)
            errored.append(f"{suggestion['title']}: {type(exc).__name__}")
            continue

    status["extractor"] = "ok"
    status["proposals"] = len(planned)
    status["unclassified"] = unclassified
    status["refused"] = refused
    status["duplicates"] = duplicates
    status["errored"] = errored
    return planned, status


def apply_suggestions(
    bundle: Bundle, planned: Sequence[PlannedProposal]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply each planned proposal. Returns `(reports, status)`.

    Call only once the source page's own write has landed -- planning happens
    before the page exists, applying happens after, so a proposal never cites
    a `resource` that was never written. An `is_empty` planned item (a
    same-target merge with no metadata change) is skipped the same way the
    un-split `run_suggest_phase` skipped it.

    `status` carries `proposals` (the confirmed-landed count, `len(reports)`),
    `failed` (`f"{title}: {failure.kind}"` for a write that did not commit --
    not counted, so the page never claims a proposal no file supports), and
    `errored` (belt-and-suspenders: `apply_plan`'s own failure modes are
    returned as `WriteFailure`, not raised, so this is not expected to fire
    in practice).
    """
    reports: list[dict[str, Any]] = []
    failed: list[str] = []
    errored: list[str] = []
    for item in planned:
        try:
            if not item.plan.is_empty:
                applied = apply_plan(bundle, item.plan)
                if not applied.ok:
                    # A planned write that did not land is not a filed
                    # proposal. Reporting it as one puts a count in
                    # `IngestResult.proposal_status` and in `log.md` that no
                    # file on disk supports.
                    failed.append(f"{item.title}: {applied.failed[0].kind}")
                    logger.warning("proposal for %r did not land: %s", item.title, applied.failed[0].error)
                    continue
            reports.append(
                {
                    "lane": item.lane,
                    "title": item.title,
                    "target": item.target,
                    "proposal": item.plan.proposal,
                    "rank": item.rank,
                    "confidence": item.confidence,
                    "status": "proposed",
                }
            )
        except Exception as exc:
            logger.warning("suggestion %r raised; dropping it", item.title, exc_info=True)
            errored.append(f"{item.title}: {type(exc).__name__}")
            continue
    return reports, {"proposals": len(reports), "failed": failed, "errored": errored}


def merge_apply_status(status: dict[str, Any], apply_status: dict[str, Any]) -> dict[str, Any]:
    """Fold `apply_suggestions`' findings into `plan_suggestions`' plan-time `status`, in place.

    `apply_status["proposals"]` and `["failed"]` replace the plan-time
    values outright -- `apply_suggestions` is the authority on what actually
    landed. `errored` extends rather than replaces: `plan_suggestions` can
    already have recorded a per-suggestion exception, and `apply_suggestions`'
    own is a different failure mode on a different (surviving) subset of
    suggestions, so both belong in the list. Guarded so a callsite that never
    sees an apply-time error doesn't manufacture an empty-list `errored` key
    where none was needed.

    Shared by `run_suggest_phase` (plan then apply, same call) and
    `run_ingest_source` (apply deferred until the main page write commits) --
    one merge, so a future edit to the fold can't drift between the two.
    """
    status["proposals"] = apply_status["proposals"]
    status["failed"] = apply_status["failed"]
    if apply_status["errored"]:
        status["errored"] = [*status["errored"], *apply_status["errored"]]
    return status


async def run_suggest_phase(**kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # noqa: ANN401
    """Unchanged signature and behavior: plan then immediately apply.

    The seam `plan_suggestions`/`apply_suggestions` exist for:
    `run_ingest_source` defers the `apply_suggestions` call until its own
    page write commits. Every other caller -- and the existing test suite --
    sees the same plan-then-apply behavior as before the split.
    """
    planned, status = await plan_suggestions(**kwargs)
    reports, apply_status = apply_suggestions(kwargs["bundle"], planned)
    return reports, merge_apply_status(status, apply_status)


__all__ = [
    "MAX_SUGGESTIONS",
    "RIDE_THROUGH",
    "SOURCES_LANE",
    "PlannedProposal",
    "apply_suggestions",
    "build_curated_index",
    "build_extract_prompt",
    "catalog_lanes",
    "merge_apply_status",
    "parse_extractor_response",
    "plan_suggestions",
    "run_suggest_phase",
]
