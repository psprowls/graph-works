"""Drift propagation: propose curated-page updates for entities whose code
moved.

candidates -> curated backlink targets -> kind-aware judge -> one proposal
source per finding -> stamp the anchor. The orchestration shape is the ported
one; three of its collaborators are not.

- Backlinks are native: `okf_io.build_link_graph(bundle).backlinks` comes off
  the same single walk that produced the bundle, so there is no backlink index
  to build.
- Provenance is native `sources[]` through `okf_ext.proposals`, not a
  capability-owned `origins[]` dialect. Identity is the target path, so two
  entities drifting one page produce one proposal with two sources.
- The anchor is `<cache_dir>/drift/propagated.json`, not a frontmatter stamp
  (see `graph_works_core.lint_drift.drift_anchor`).

**One suppression pass is dropped.** The ported code hashed each curated target
and suppressed any candidate whose commit was an ancestor of the target's
stamped anchor. That needs `page_body_hash`, `section_hash` and `is_ancestor`,
none of which exist in the rebuild, plus a second frontmatter stamp with the
same owned-key problem the anchor already answers. Repeat runs are instead
covered by `okf_ext.proposals` contributing no write when every incoming source
is already on the document. The accepted delta: a human who folds a change into
a page *without* touching the proposal will see that proposal stand open.
Surfacing undisposed proposals is the ledger's job.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from code_wiki_okf.config import Config
from code_wiki_okf.git_state import changed_files_since
from doc_wiki_okf.proposals import is_adr
from langchain_core.messages import HumanMessage, SystemMessage
from models_io.pricing import cost_for_usage
from okf_ext.body import find_section
from okf_ext.proposals import PROPOSAL_TYPE, ProposalPlan, apply, list_proposals, plan_propose
from okf_io import Bundle, Document, LinkGraph, build_link_graph, load_bundle
from subagents_io import SubagentPool, TaskResult
from subagents_io.roles import RoleBinding

from graph_works_core.agent_substrate.roles import role_binding
from graph_works_core.lint_drift.drift_anchor import read_anchors, write_anchors
from graph_works_core.lint_drift.drift_propagator import (
    build_drift_propagator_prompt,
    parse_drift_propagator_verdict,
)
from graph_works_core.workspace.layout import WorkspaceLayout

#: The section an entity page's human-or-LLM-authored ground truth lives in.
#: A page without one is not judged: there is nothing to judge the curated
#: page against, and the generated sections are the code's own words.
NARRATIVE_HEADING = "Narrative"

#: The provenance key `code_wiki_okf.entities.sync` stamps and owns.
LAST_UPDATED_COMMIT_KEY = "last_updated_commit"


class _NodeLister(Protocol):
    """The four questions this module asks a graph reader.

    A structural type rather than `GraphReader`: it names exactly what is
    read, and it is what lets the tests hand in a four-method stub instead of
    standing up a sqlite graph to assert an orchestration property.
    """

    def list_packages(self) -> list[Any]: ...
    def list_apps(self) -> list[Any]: ...
    def list_test_suites(self) -> list[Any]: ...
    def list_agent_plugins(self) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class Candidate:
    """One entity page whose code moved since the anchor last recorded it."""

    concept_id: str
    resource: str
    title: str
    narrative: str
    last_updated_commit: str
    anchor: str | None
    changed_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Target:
    """One curated page a candidate backlinks, and how to judge it."""

    concept_id: str
    title: str
    body: str
    kind: str  # "concept" | "adr" -- the judge rubric's key
    candidates: tuple[Candidate, ...]


def _node_paths(reader: _NodeLister) -> dict[str, str]:
    """Graph uri -> repo-relative node path, for the kinds that have one.

    The four listers are named rather than derived: a repository or a
    dependency carries no `node.path`, so neither has a change signal and
    neither is ever a candidate. Naming them is also what lets `_NodeLister`
    check those four method names statically.
    """
    listers = (reader.list_packages, reader.list_apps, reader.list_test_suites, reader.list_agent_plugins)
    paths: dict[str, str] = {}
    for lister in listers:
        for node in lister():
            attrs = node.attrs if isinstance(node.attrs, dict) else {}
            uri = attrs.get("uri")
            if uri and node.path:
                paths[str(uri)] = str(node.path)
    return paths


def _narrative(document: Document) -> str:
    """The `## Narrative` body, stripped. `""` when absent or blank."""
    section = find_section(document.body, NARRATIVE_HEADING)
    return "" if section is None else section.slice(document.body).strip()


def _owning_repo(node_path: str, config: Config, repo_root: Path | None) -> Path | None:
    """The repo whose checkout contains *node_path*, or *repo_root*.

    Membership is "the path exists under it", the same question the scan front
    half asks. A single-repo workspace resolves every entity to `repo_root`,
    byte-identically to the ported behaviour.
    """
    for repo in config.repos:
        if (repo.path / node_path).exists():
            return repo.path
    return repo_root


def _commit(document: Document) -> str:
    value = document.fm.extra.get(LAST_UPDATED_COMMIT_KEY)
    return value.strip() if isinstance(value, str) else ""


def propagation_candidates(
    bundle: Bundle,
    reader: _NodeLister,
    anchors: Mapping[str, str],
    *,
    config: Config,
    repo_root: Path | None,
) -> tuple[Candidate, ...]:
    """Entity pages where `last_updated_commit` differs from the anchor.

    Four ways to not be a candidate, each a fact rather than a failure: no
    `resource` (not a graph-derived page at all), an anchor already at this
    commit (nothing new to judge), a kind with no `node.path` (no change
    signal), and no narrative (no ground truth). None of them raises.
    """
    node_paths = _node_paths(reader)
    found: list[Candidate] = []
    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        resource = document.fm.resource
        commit = _commit(document)
        if not resource or not commit:
            continue
        anchor = anchors.get(concept_id)
        if anchor == commit:
            continue
        node_path = node_paths.get(resource)
        if not node_path:
            continue
        narrative = _narrative(document)
        if not narrative:
            continue
        repo = _owning_repo(node_path, config, repo_root)
        changed = () if repo is None else tuple(changed_files_since(repo, anchor or "", [node_path]) or ())
        found.append(
            Candidate(
                concept_id=concept_id,
                resource=resource,
                title=(document.fm.title or concept_id),
                narrative=narrative,
                last_updated_commit=commit,
                anchor=anchor,
                changed_files=changed,
            )
        )
    return tuple(found)


def _is_drift_target(concept_id: str, document: Document) -> bool:
    """A curated page: not graph-derived, not a proposal.

    Derived rather than a folder list. The ported code enumerated
    `{"concepts", "adrs"}` and excluded `sources`/`work`; here the wiki lane's
    `ignore=` has already removed the work lane, a graph-derived page is the
    one carrying a `resource`, and a proposal announces itself by `type`. A new
    curated directory therefore needs no edit here — constraint 5.
    """
    return document.fm.resource is None and (document.fm.type or "").strip() != PROPOSAL_TYPE


def _target_kind(concept_id: str, document: Document) -> str:
    """`"adr"` for the ADR lane's type in the ADR lane's directory, else
    `"concept"` — the judge rubric's key.

    The two-part test itself lives in `doc_wiki_okf.proposals.is_adr`: the
    package that owns the vocabulary owns the test of it.
    """
    return "adr" if is_adr(concept_id, document.fm.type or "") else "concept"


def drift_targets(
    candidates: Sequence[Candidate],
    bundle: Bundle,
    links: LinkGraph,
) -> tuple[Target, ...]:
    """The curated pages backlinked by *candidates*, one entry per page.

    Grouped by target, because identity in the proposal ledger is the target
    path: two entities drifting one page must reach the judge as one question
    and land as one proposal with two sources.
    """
    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        for source_id in links.backlinks.get(candidate.concept_id, ()):
            document = bundle.concepts.get(source_id)
            if document is None or not _is_drift_target(source_id, document):
                continue
            grouped.setdefault(source_id, []).append(candidate)
    return tuple(
        Target(
            concept_id=concept_id,
            title=(bundle.concepts[concept_id].fm.title or concept_id),
            body=bundle.concepts[concept_id].body,
            kind=_target_kind(concept_id, bundle.concepts[concept_id]),
            candidates=tuple(found),
        )
        for concept_id, found in sorted(grouped.items())
    )


#: The role the judge binds.
PROPAGATOR_ROLE = "drift_propagator"

#: Who the proposal ledger records as the author of a drift-filed source.
PROPAGATOR_ACTOR = "agent:drift-propagator"

#: Page statuses a human has already acted on. The one suppression the ledger
#: cannot infer, so it is kept: `plan_propose` would refuse a decided proposal
#: anyway, and pre-filtering saves the judging call that produced the refusal.
#: `_settled_targets` folds in a second reason — a malformed proposal — for the
#: same purpose; see its docstring.
HUMAN_DECIDED: frozenset[str] = frozenset({"approved", "rejected", "created"})


@dataclass(frozen=True, slots=True)
class DriftFinding:
    """One entity's contribution to one target's staleness."""

    target: str  # bundle-relative posix, the proposal's identity
    target_title: str
    entity_id: str
    entity_title: str
    detected_commit: str
    rationale: str


@dataclass(frozen=True, slots=True)
class PropagateResult:
    """What one propagation run considered, judged, and filed."""

    entities_considered: int = 0
    pages_judged: int = 0
    pages_stale: int = 0
    pages_skipped_settled: int = 0
    findings: tuple[DriftFinding, ...] = ()
    plans: tuple[ProposalPlan, ...] = ()
    errors: tuple[str, ...] = ()
    dry_run: bool = True


def _source(finding: DriftFinding) -> dict[str, str]:
    """One `sources[]` entry, in the shape the design spec fixes.

    `at_commit` is what makes the entry drift-checkable later: it is the same
    declared-provenance shape §7's custom-type model uses, so a page filed by
    this propagator and a page written by a future extractor answer the same
    staleness question with the same machinery.
    """
    return {
        "id": f"drift-{finding.entity_id}-{finding.detected_commit[:7]}",
        "resource": f"/{finding.entity_id}.md",
        "title": finding.entity_title,
        "at_commit": finding.detected_commit,
        "rationale": finding.rationale,
    }


def write_propagation_findings(
    bundle: Bundle,
    findings: Sequence[DriftFinding],
    *,
    by: str = PROPAGATOR_ACTOR,
    at: datetime,
) -> tuple[ProposalPlan, ...]:
    """Plan one proposal per distinct target, carrying one source per finding.

    It **plans** rather than writes — the caller decides whether to `apply`,
    which is what makes `dry_run` honest.

    A function rather than an inline loop because `run_propagate_drift` calls
    it twice: once for the whole finding set under `dry_run`, and once per
    target on the live path, where `apply` refuses a plan built against a
    bundle a prior `apply` already mutated. Both branches must emit
    byte-identical proposals, which is what makes `dry_run` a preview of the
    live path rather than a second code path.

    Public because it is part of this vertical's declared surface — exported
    from `graph_works_core.__init__` alongside `DriftFinding` and
    `PropagateResult` — so a caller can plan proposals from findings it already
    holds without running the judged pass. Deliberately *not* because the scan
    vertical is a second caller: it is not one, and C3's §3.1 removed that
    coupling by name.
    """
    grouped: dict[str, list[DriftFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.target, []).append(finding)
    return tuple(
        plan_propose(
            bundle,
            target,
            [_source(finding) for finding in group],
            title=group[0].target_title,
            description=(
                f"{len(group)} entity change(s) may have overtaken this page's claims. "
                "Filed by drift propagation; nothing was edited."
            ),
            by=by,
            at=at,
        )
        for target, group in sorted(grouped.items())
    )


def _settled_targets(bundle: Bundle) -> frozenset[str]:
    """Targets the ledger has already answered for — or cannot answer for.

    Two reasons, one rule, because the argument is the same one `HUMAN_DECIDED`
    already makes: `plan_propose` would refuse both anyway, and pre-filtering is
    what saves the judging call that produced the refusal. A human disposed of
    the first; the second carries `Proposal.malformed`, meaning a `page_status`
    outside the vocabulary (coerced to `None`, so it satisfies neither
    `HUMAN_DECIDED` here nor the `"proposed"` filter the backlog counts with).

    `pages_skipped_settled` absorbs both: the field counts targets skipped
    because the ledger already answered for them, and a malformed proposal is
    an answer nobody can read — a reason to skip, not a different kind of skip.
    The distinction a human needs is on the lint side, where the malformed
    proposal is named and counted.
    """
    return frozenset(
        proposal.target
        for proposal in list_proposals(bundle)
        if proposal.target and (proposal.malformed is not None or proposal.page_status in HUMAN_DECIDED)
    )


def _refusal_errors(plans: Sequence[ProposalPlan]) -> tuple[str, ...]:
    """One line per refusal across *plans*.

    A refused plan lands in `PropagateResult.plans` and nothing downstream
    inspects `plan.refusals`, so without this a run over-reports: it names a
    stale page it did not and could not file.
    """
    return tuple(f"{plan.target}: {refusal.kind} — {refusal.detail}" for plan in plans for refusal in plan.refusals)


@dataclass(frozen=True, slots=True)
class _Narrowed:
    """What `--only` left — or the one line saying it named nothing.

    A small value rather than a fourth tuple element: three positional
    booleans-and-tuples at a call site is where a reader stops being able to
    tell which is which.
    """

    candidates: tuple[Candidate, ...] = ()
    targets: tuple[Target, ...] = ()
    page_scoped: bool = False
    error: str | None = None


def _in_bundle(bundle: Bundle, only: str) -> bool:
    """Whether *only* names any member of *bundle* — a concept id, or a
    concept's `resource`.

    Tested against the bundle rather than the candidate set on purpose: naming
    a real entity that simply has not drifted is a legitimate zero, and
    refusing it would make `--only` unusable for its main job.
    """
    if only in bundle.concepts:
        return True
    return any(document.fm.resource == only for document in bundle.concepts.values())


def _narrow(
    bundle: Bundle,
    candidates: tuple[Candidate, ...],
    targets: tuple[Target, ...],
    only: str | None,
) -> _Narrowed:
    """Apply `--only`.

    An entity match narrows the candidate set; anything else is read as a page
    and narrows the target set. `page_scoped` is what the stamping rule keys
    off: a page-scoped run judges one of an entity's targets, so stamping would
    falsely mark the entity fully propagated and starve its other targets.

    A value matching neither an entity nor any page **in the bundle** is a
    refusal, not an empty result — without it the run returns
    `entities_considered=0, pages_judged=0, errors=()`, byte-identical to
    "nothing drifted".

    The page arm normalizes a trailing `.md` because the proposal identity
    written for a page is `<concept_id>.md`, which is the form that appears in
    every filed proposal and therefore the form a human types.
    """
    if only is None:
        return _Narrowed(candidates=candidates, targets=targets)
    matched = tuple(c for c in candidates if only in (c.concept_id, c.resource))
    if matched:
        kept = {c.concept_id for c in matched}
        narrowed = tuple(
            Target(
                concept_id=t.concept_id,
                title=t.title,
                body=t.body,
                kind=t.kind,
                candidates=tuple(c for c in t.candidates if c.concept_id in kept),
            )
            for t in targets
        )
        return _Narrowed(candidates=matched, targets=tuple(t for t in narrowed if t.candidates))
    page_id = only[: -len(".md")] if only.endswith(".md") else only
    if not _in_bundle(bundle, only) and not _in_bundle(bundle, page_id):
        return _Narrowed(error=f"--only {only!r} names no entity and no page in this bundle")
    return _Narrowed(
        candidates=candidates,
        targets=tuple(t for t in targets if t.concept_id == page_id),
        page_scoped=True,
    )


#: Stop-reason values that mean "the model ran out of room", across providers.
_TOKEN_CAP_REASONS = frozenset({"max_tokens", "max_token", "length"})


def _stopped_on_token_cap(response: Any) -> bool:  # noqa: ANN401 -- whatever the provider SDK returned
    """Whether *response* was cut off by the role's `max_tokens` ceiling.

    A truncated verdict is not a garbled one: it is a real judgement that ran
    out of room. `parse_drift_propagator_verdict` cannot tell them apart and
    fails safe to not-stale for both, which turns a stale page into a clean
    one with nothing logged. The provider reports the difference, so this reads
    it rather than guessing from unbalanced JSON.
    """
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, Mapping):
        return False
    for key in ("stop_reason", "finish_reason", "stopReason"):
        value = metadata.get(key)
        if isinstance(value, str) and value.lower() in _TOKEN_CAP_REASONS:
            return True
    return False


async def _judge_all(
    targets: Sequence[Target],
    binding: RoleBinding,
    *,
    trace_dir: Path,
) -> tuple[list[tuple[Target, dict[str, Any]]], tuple[str, ...]]:
    """One judging call per target, fanned out, failures captured per item."""

    async def judge(target: Target) -> TaskResult:
        entities = [(c.concept_id, c.narrative, list(c.changed_files)) for c in target.candidates]
        system, human = build_drift_propagator_prompt(target.kind, target.title, target.body, entities)
        response = await binding.make_llm().ainvoke([SystemMessage(content=system), HumanMessage(content=human)])
        if _stopped_on_token_cap(response):
            raise RuntimeError(
                f"drift propagator hit its token cap ({binding.spec.max_tokens}) with "
                f"{len(entities)} entities to judge; the verdict was truncated. Raise "
                f"`roles.{PROPAGATOR_ROLE}.max_tokens` in workspace.yaml."
            )
        if not isinstance(response.content, str):
            raise RuntimeError("drift propagator returned non-text content")
        return TaskResult(value=parse_drift_propagator_verdict(response.content), response=response)

    pool = SubagentPool(trace_dir=trace_dir, price_lookup=cost_for_usage)
    fan = await pool.run_all(
        list(targets),
        judge,
        PROPAGATOR_ROLE,
        model_id=binding.spec.model_id,
        max_concurrency=binding.spec.max_concurrency,
    )
    errors = tuple(f"{failure.item.concept_id}: {failure.exception}" for failure in fan.errors)
    return list(fan.successes), errors


def _findings_for(target: Target, verdict: Mapping[str, Any]) -> tuple[DriftFinding, ...]:
    """The verdict's findings, restricted to the entities actually judged.

    A hallucinated `entity_stem` contributes nothing: a source needs a real
    entity behind it, and the batch the judge saw is the only honest lookup.
    """
    if not verdict.get("stale"):
        return ()
    by_id = {candidate.concept_id: candidate for candidate in target.candidates}
    found: list[DriftFinding] = []
    for entry in verdict.get("findings") or []:
        candidate = by_id.get(str(entry.get("entity_stem", "")))
        if candidate is None:
            continue
        found.append(
            DriftFinding(
                target=f"{target.concept_id}.md",
                target_title=target.title,
                entity_id=candidate.concept_id,
                entity_title=candidate.title,
                detected_commit=candidate.last_updated_commit,
                rationale=str(entry.get("rationale", "")),
            )
        )
    return tuple(found)


def _stamp(cache_dir: Path, processed: Iterable[Candidate]) -> None:
    """Merge this run's anchors onto whatever was already recorded.

    A merge rather than a replace at this level: an entity absent from this
    run's candidate set was not re-judged, so dropping its anchor would force a
    needless pass next time.

    Who reaches this is the caller's decision and it is not "everything
    processed": a candidate whose target failed to judge, or whose finding
    reached a refused plan, was not recorded anywhere — see `_blocked`.
    """
    anchors = read_anchors(cache_dir)
    for candidate in processed:
        anchors[candidate.concept_id] = candidate.last_updated_commit
    write_anchors(cache_dir, anchors)


def _blocked(
    judge_targets: Sequence[Target],
    verdicts: Sequence[tuple[Target, Mapping[str, Any]]],
    findings: Sequence[DriftFinding],
    plans: Sequence[ProposalPlan],
) -> frozenset[str]:
    """Candidates this run must not stamp.

    The anchor means "these targets were judged at this commit", so a candidate
    is withheld when any target listing it did not come back with a verdict.
    Refused plans extend the same rule rather than adding a second one: a
    finding that reached no proposal is as unrecorded as one that reached no
    verdict, and leaving it out would reintroduce the defect through the
    malformed-proposal path.
    """
    judged = {target.concept_id for target, _verdict in verdicts}
    refused = {plan.target for plan in plans if not plan.ok}
    unjudged = {
        candidate.concept_id
        for target in judge_targets
        if target.concept_id not in judged
        for candidate in target.candidates
    }
    return frozenset(unjudged | {finding.entity_id for finding in findings if finding.target in refused})


async def run_propagate_drift(
    layout: WorkspaceLayout,
    config: Config,
    reader: _NodeLister,
    *,
    at: datetime,
    repo_root: Path | None,
    dry_run: bool = True,
    only: str | None = None,
    model_override: str | None = None,
) -> PropagateResult:
    """Propose curated-page updates for entities whose code moved.

    `dry_run=True` is the default, matching every other writer in this
    workspace: the plans come back for inspection either way, and only a
    deliberate `dry_run=False` writes.
    """
    bundle = load_bundle(layout.bundle_dir)
    anchors = read_anchors(layout.cache_dir)
    candidates = propagation_candidates(bundle, reader, anchors, config=config, repo_root=repo_root)
    targets = drift_targets(candidates, bundle, build_link_graph(bundle))
    narrowed = _narrow(bundle, candidates, targets, only)
    if narrowed.error is not None:
        return PropagateResult(errors=(narrowed.error,), dry_run=dry_run)
    candidates, targets, page_scoped = narrowed.candidates, narrowed.targets, narrowed.page_scoped

    settled = _settled_targets(bundle)
    judge_targets = tuple(t for t in targets if f"{t.concept_id}.md" not in settled)
    skipped = len(targets) - len(judge_targets)

    # Processed = every candidate whose backlinkers were considered, INCLUDING
    # those whose targets were all pre-filtered out. A settled target is a
    # question already answered, not one left open.
    processed = (
        candidates if not page_scoped else tuple({c.concept_id: c for t in targets for c in t.candidates}.values())
    )

    if not judge_targets:
        if not dry_run and not page_scoped:
            _stamp(layout.cache_dir, processed)
        return PropagateResult(entities_considered=len(processed), pages_skipped_settled=skipped, dry_run=dry_run)

    try:
        binding = role_binding(PROPAGATOR_ROLE, layout=layout, model_override=model_override)
    except (KeyError, ValueError) as exc:
        return PropagateResult(
            entities_considered=len(processed),
            pages_skipped_settled=skipped,
            errors=(f"{PROPAGATOR_ROLE}: {exc}",),
            dry_run=dry_run,
        )

    verdicts, errors = await _judge_all(judge_targets, binding, trace_dir=layout.cache_dir / "traces")

    findings: list[DriftFinding] = []
    stale_pages = 0
    for target, verdict in verdicts:
        target_findings = _findings_for(target, verdict)
        if target_findings:
            stale_pages += 1
            findings.extend(target_findings)

    # `apply` refuses a plan built against a bundle a prior `apply` already
    # mutated on disk, so the live branch re-walks between targets: each plan is
    # built and applied against the current walk, one target at a time. Planning
    # everything against the pre-apply bundle and then applying in a loop raises
    # on the second target.
    if dry_run:
        plans = write_propagation_findings(bundle, findings, at=at)
    else:
        applied: list[ProposalPlan] = []
        for target_path in sorted({finding.target for finding in findings}):
            group = [finding for finding in findings if finding.target == target_path]
            plan = write_propagation_findings(bundle, group, at=at)[0]
            if plan.ok and not plan.is_empty:
                apply(bundle, plan)
                bundle = load_bundle(layout.bundle_dir)
            applied.append(plan)
        plans = tuple(applied)
        if not page_scoped:
            blocked = _blocked(judge_targets, verdicts, findings, plans)
            _stamp(layout.cache_dir, tuple(c for c in processed if c.concept_id not in blocked))

    return PropagateResult(
        entities_considered=len(processed),
        pages_judged=len(judge_targets),
        pages_stale=stale_pages,
        pages_skipped_settled=skipped,
        findings=tuple(findings),
        plans=plans,
        errors=errors + _refusal_errors(plans),
        dry_run=dry_run,
    )


__all__ = [
    "HUMAN_DECIDED",
    "LAST_UPDATED_COMMIT_KEY",
    "NARRATIVE_HEADING",
    "PROPAGATOR_ACTOR",
    "PROPAGATOR_ROLE",
    "Candidate",
    "DriftFinding",
    "PropagateResult",
    "Target",
    "drift_targets",
    "propagation_candidates",
    "run_propagate_drift",
    "write_propagation_findings",
]
