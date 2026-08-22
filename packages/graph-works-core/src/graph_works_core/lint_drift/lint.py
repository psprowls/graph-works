"""The lint aggregator: one mechanical `Report` per lane, plus a separate
semantic stream.

**Semantic findings are not `Finding`s.** Semantic lint is an async LLM
fan-out, so it can never be an `extra_rules=` entry — okf-io rules are pure and
synchronous. It gets its own frozen type alongside the mechanical `Report`:
a mechanical finding is reproducible and cites a spec section, and a judge's
verdict is neither, so `Finding.spec` would have nothing honest in it. Keeping
the types apart also removes the temptation to hand a semantic rule to
`extra_rules=`, which cannot work.

`run_mechanical` is the whole aggregation with an empty `semantic`. It is sync
and LLM-free on purpose: it is what a pre-commit gate or `just check` calls,
and what a CLI can expose with no model configured.

Three fields of the ported `LintResult` are absorbed rather than ported.
`obsidian_render_findings` is `okf_ext.render.render_rule()`; `code_drift` is
`code_wiki_okf.sync.sync_rule(snapshot)`; `guidance_lint_findings` drops
entirely until C8 lands, rather than stubbing a field nothing fills.
`open_proposals` survives, as a `ProposalBacklog` rather than a bare counter:
the trade C6 recorded when it dropped the content-hash suppression pass names
a revisit condition about the backlog's *age*, which an integer cannot answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import MappingProxyType

from code_graph_io import GraphReader
from code_wiki_okf.config import Config
from doc_wiki_okf.proposals import is_adr
from langchain_core.messages import HumanMessage, SystemMessage
from models_io.pricing import cost_for_usage
from okf_ext import proposals
from okf_io import Bundle, Document, Finding, Report, build_link_graph, load_bundle, validate
from subagents_io import SubagentPool, TaskResult
from subagents_io.roles import RoleBinding

from graph_works_core.agent_substrate.roles import role_binding
from graph_works_core.lint_drift.lanes import WIKI_LANE, Lane, compose_lanes
from graph_works_core.lint_drift.linter import (
    build_linter_adr_chain_system,
    build_linter_page_quality_system,
    build_linter_stale_claims_system,
)
from graph_works_core.prompts.project_context import render_project_context
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class LaneReport:
    """One lane's mechanical result, exactly as okf-io produced it."""

    name: str
    report: Report


@dataclass(frozen=True, slots=True)
class SemanticFinding:
    """One line a judge wrote. Not a `Finding`: no code, no spec citation.

    `page` is the concept id the judge named, when it named one — the prompt
    asks for a `<page id>: ` prefix and this is derived from it, never guessed.
    `model` is the model that said it, because a semantic verdict is only
    interpretable against the model that produced it.
    """

    group: str
    message: str
    page: str | None
    model: str


#: The four age buckets, in report order. Every open proposal lands in exactly
#: one, so `sum(ages.values()) == count` holds — `"undated"` is an explicit
#: bucket rather than a silent drop.
AGE_BUCKETS = ("<7d", "7-30d", ">30d", "undated")

_EMPTY_AGES: Mapping[str, int] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ProposalBacklog:
    """The undecided-proposal backlog, with enough age to be checkable.

    One value rather than a scalar plus siblings: count, age and malformed
    answer one question — has this backlog gone stale? — and a reader who
    consults only some of them draws the wrong conclusion.

    It exists because C6 dropped the ported content-hash suppression pass
    deliberately, accepting that a human who folds a change into a page without
    touching its proposal leaves that proposal standing open, and said to
    revisit "if the open-proposal backlog turns out to be dominated by
    already-folded findings". A bare integer cannot answer that.

    **`oldest` is "last filed against", not "first filed."** `plan_propose`
    re-stamps `generated.at` on merge and no first-filed date exists on disk
    anywhere. That is the right clock for the question rather than a
    compromise: a proposal a human quietly folded in stops being re-filed — its
    entity's anchor is already stamped, so it is no longer a candidate — and
    ages honestly, while one still accruing sources is actively drifting and
    should read as fresh.

    `malformed` counts proposals whose `page_status` is outside the
    vocabulary. `list_proposals`' filter matches the coerced value, so such a
    proposal is invisible to the `"proposed"` count at the same time it is
    unable to suppress anything on the propagation side. Counting it here is
    what makes it fixable.
    """

    count: int = 0
    oldest: date | None = None
    malformed: int = 0
    ages: Mapping[str, int] = _EMPTY_AGES


def _finding_line(finding: Finding) -> str:
    """Render one mechanical finding as a single line.

    Columns align the common case: severity, code, path with line, and message
    all fit their fields. An over-long code or path pushes the rest of that
    line right rather than truncating — nothing is ever lost.

    Severity is data rather than structure (okf-io ADR-0008), so it renders as
    a word in its own column rather than sorting the finding into an errors or
    warnings bucket — the same shape `Report` itself keeps.
    """
    location = finding.path or ""
    if location and finding.line is not None:
        location = f"{location}:{finding.line}"
    message = " ".join(finding.message.split())
    return f"{finding.severity:<6} {finding.code:<36} {location:<28} {message}  ({finding.spec})"


def _semantic_line(finding: SemanticFinding) -> str:
    """One judge line, keeping the `<page id>: ` prefix when the judge named a page."""
    return finding.message if finding.page is None else f"{finding.page}: {finding.message}"


@dataclass(frozen=True, slots=True)
class LintReport:
    """Everything one lint run found."""

    mechanical: tuple[LaneReport, ...] = ()
    semantic: tuple[SemanticFinding, ...] = ()
    open_proposals: ProposalBacklog = ProposalBacklog()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """No lane reported an error-severity finding, and nothing failed.

        Semantic findings are deliberately not consulted: a judge's verdict is
        advisory, and a run that cannot be reproduced must not be able to fail
        a gate.
        """
        return not self.errors and all(lane.report.ok for lane in self.mechanical)

    def render(self) -> str:
        """Everything this report holds, as text, in reading order.

        A method rather than a free function because that is the vocabulary
        this workspace already speaks: `WorkspaceInit.diff()` in this very
        package, and okf-io's `IndexUpdate`, `LogAppend` and `Migration`, all
        render on demand from their own fields and write nothing. Anyone
        holding a `LintReport` holds the renderer, and no new name appears at
        the front door.

        Errors come first: an error means a lane did not walk or did not
        compose, so the findings below it are incomplete. Semantic findings sit
        in their own section and are never interleaved with mechanical ones —
        the whole reason `SemanticFinding` is not a `Finding` is the difference
        in trustworthiness, and interleaving them visually undoes the split.
        Every section is omitted entirely when it has nothing in it, and a
        report with nothing at all renders one line rather than the empty
        string, which would read as a broken command.

        Ordering is inherited, never recomputed: `validate()` already sorts
        each lane's findings, `mechanical` is already in lane order, and
        semantic groups follow `SEMANTIC_GROUPS`. Re-sorting here would be one
        more thing that can disagree with the report it renders.

        `render()` has no say in `ok`. The gate stays mechanical-only.
        """
        blocks: list[str] = []

        if self.errors:
            blocks.append("\n".join(["## Errors", *(f"! {line}" for line in self.errors)]))

        if any(lane.report.findings for lane in self.mechanical):
            lines = ["## Mechanical"]
            for lane in self.mechanical:
                lines.append(f"### {lane.name}")
                lines.extend(_finding_line(finding) for finding in lane.report.findings)
                if not lane.report.findings:
                    lines.append("(clean)")
            blocks.append("\n".join(lines))

        if self.semantic:
            lines = ["## Semantic"]
            for group in SEMANTIC_GROUPS:
                found = [finding for finding in self.semantic if finding.group == group]
                if not found:
                    continue
                models = ", ".join(sorted({finding.model for finding in found}))
                lines.append(f"### {group} — {models}")
                lines.extend(_semantic_line(finding) for finding in found)
            blocks.append("\n".join(lines))

        backlog = self.open_proposals
        if backlog.count or backlog.malformed:
            head = f"proposals: {backlog.count} open"
            if backlog.oldest is not None:
                head += f", oldest {backlog.oldest.isoformat()}"
            head += f", {backlog.malformed} malformed"
            spread = " | ".join(f"{bucket} {backlog.ages.get(bucket, 0)}" for bucket in AGE_BUCKETS)
            blocks.append(f"{head} | {spread}")

        return "\n\n".join(blocks) if blocks else "No findings."


def _at_for(today: date) -> datetime:
    """The instant the sync snapshot is taken at, derived from *today*.

    This package never reads the clock, and `snapshot_bundle` requires an aware
    `datetime`. Deriving one is sound rather than a fudge: both `plan_entities`
    and `plan_mirror` strip `generated` / `last_updated_commit` / `tokens`
    before comparing renders, so `at` cannot change a staleness verdict.
    """
    return datetime.combine(today, time.min, tzinfo=UTC)


def _lane_reports(
    lanes: tuple[Lane, ...],
    *,
    today: date,
    strict: bool,
) -> tuple[tuple[LaneReport, ...], dict[str, Bundle], tuple[str, ...]]:
    """Walk and validate each lane, capturing a failed walk per lane.

    The bundles come back so the semantic pass and the proposal counter read
    the same walk rather than repeating it — okf-io's one-walk rule, kept
    across the two halves of this module.
    """
    reports: list[LaneReport] = []
    bundles: dict[str, Bundle] = {}
    errors: list[str] = []
    for lane in lanes:
        try:
            bundle = load_bundle(lane.root, ignore=lane.ignore)
        except OSError as exc:
            errors.append(f"{lane.name} lane: {exc}")
            continue
        report = validate(bundle, today=today, extra_rules=lane.rules, strict=strict)
        bundles[lane.name] = bundle
        reports.append(LaneReport(name=lane.name, report=report))
    return tuple(reports), bundles, tuple(errors)


def _bucket(filed: date | None, today: date) -> str:
    """Which of `AGE_BUCKETS` a proposal last filed on *filed* lands in."""
    if filed is None:
        return "undated"
    days = (today - filed).days
    if days < 7:
        return "<7d"
    return "7-30d" if days <= 30 else ">30d"


def _filed_on(bundle: Bundle, proposal: proposals.Proposal) -> date | None:
    """When *proposal* was last filed against, from its own `generated.at`.

    Read off the proposal document rather than the clock — this module never
    reads the clock, which is why `today` is a required keyword everywhere.
    """
    generated = bundle.concepts[proposal.concept_id].fm.generated
    at_dt = None if generated is None else generated.at_dt
    return None if at_dt is None else at_dt.date()


def _open_proposals(bundles: dict[str, Bundle], *, today: date) -> ProposalBacklog:
    """The wiki lane's undecided backlog, with its age spread.

    An empty backlog when the wiki lane did not compose or failed to load: a
    lane whose bundle is absent says nothing about its backlog, and reporting an
    empty one alongside that lane's error line is less misleading than omitting
    the field.
    """
    bundle = bundles.get(WIKI_LANE)
    if bundle is None:
        return ProposalBacklog()
    every = proposals.list_proposals(bundle)
    filed = [_filed_on(bundle, proposal) for proposal in every if proposal.page_status == "proposed"]
    ages = dict.fromkeys(AGE_BUCKETS, 0)
    for on in filed:
        ages[_bucket(on, today)] += 1
    dated = [on for on in filed if on is not None]
    return ProposalBacklog(
        count=len(filed),
        oldest=min(dated, default=None),
        malformed=sum(1 for proposal in every if proposal.malformed is not None),
        ages=MappingProxyType(ages),
    )


def run_mechanical(
    layout: WorkspaceLayout,
    config: Config,
    *,
    today: date,
    repo_root: Path | None,
    reader: GraphReader | None = None,
    strict: bool = False,
) -> LintReport:
    """The whole mechanical aggregation. Synchronous, and never calls a model."""
    report, _bundles = _run_mechanical(layout, config, today=today, repo_root=repo_root, reader=reader, strict=strict)
    return report


def _run_mechanical(
    layout: WorkspaceLayout,
    config: Config,
    *,
    today: date,
    repo_root: Path | None,
    reader: GraphReader | None,
    strict: bool,
) -> tuple[LintReport, dict[str, Bundle]]:
    """`run_mechanical`, plus the walked bundles the semantic pass needs.

    Private because the bundles are an implementation detail of running both
    halves off one walk; `run_mechanical` is the contract.
    """
    lane_set = compose_lanes(layout, config, repo_root=repo_root, at=_at_for(today), reader=reader)
    reports, bundles, walk_errors = _lane_reports(lane_set.lanes, today=today, strict=strict)
    return (
        LintReport(
            mechanical=reports,
            semantic=(),
            open_proposals=_open_proposals(bundles, today=today),
            errors=lane_set.errors + walk_errors,
        ),
        bundles,
    )


#: The role the semantic pass binds. One role for all three groups: they differ
#: by prompt, not by model, and splitting the role would make "swap the linter
#: model" a three-key edit.
LINTER_ROLE = "linter"

#: How many pages the page-quality group reads in one prompt. The cap is the
#: ported figure, kept: the group asks about the corpus rather than about one
#: page, and a prompt that grows with the bundle does not survive a real vault.
#: What changed is the *window* — a link neighbourhood around a seed that
#: rotates one page per day — not its size. The old name said "sample" and
#: promised coverage a fixed alphabetical prefix could not give.
PAGE_QUALITY_WINDOW = 20

#: The three groups, in report order.
SEMANTIC_GROUPS = ("page_quality", "adr_chain", "stale_claims")

#: The exact "nothing to report" sentences `prompts/linter.py` instructs each
#: group's system prompt to emit when a group is clean. A line that matches one
#: of these (after `.strip()`) is not a finding — it is the "empty means clean"
#: contract every other report type in this module already honors.
_NO_ISSUES_SENTINELS = frozenset(
    {
        "No page quality issues found.",
        "No ADR chain issues found.",
        "No stale claim issues found.",
    }
)

_BODY_PREVIEW_CHARS = 800


def _page_quality_window(bundle: Bundle, ids: tuple[str, ...], *, today: date) -> tuple[str, ...]:
    """A link neighbourhood around a seed that advances one page per day.

    Not `ids[:PAGE_QUALITY_WINDOW]`, and do not simplify it back to one.
    `Bundle.concepts` keys are bundle-relative posix paths, so `sorted()`
    orders by top-level directory and `adrs/` wins on any vault this tool
    generates — it beats `agent-plugins/` on the second character. The prefix
    this replaces was therefore the twenty lowest-numbered ADRs, a strict
    subset of the `adr_chain` group's own page set: the two expensive groups
    read the same corpus, and the page-quality checks that ask about orphans,
    dead links and decisions with no ADR were answered against twenty ADRs
    rather than against the vault.

    The seed is `today.toordinal() % len(ids)`, never `hash()`: Python
    salts string hashing per process, so a hash-derived seed picks a different
    window on every run and makes the window untestable. `toordinal()` advances
    the seed exactly one page per day and cycles through all of them, which is
    what makes "no page is permanently unreachable" a property rather than a
    hope. Deriving rotation from `today` also keeps this module's discipline:
    it never reads the clock.

    The neighbourhood is a breadth-first walk over undirected edges of the
    graph okf-io derives from the walk that already happened — no new IO. Each
    frontier is sorted before it extends, so the order is stable across runs.
    When the neighbourhood is thinner than the cap the window tops up forward
    through `ids`, wrapping, so the cap always fills.
    """
    cap = min(PAGE_QUALITY_WINDOW, len(ids))
    if not cap:
        return ()

    graph = build_link_graph(bundle)
    seed_index = today.toordinal() % len(ids)
    seed = ids[seed_index]

    window = [seed]
    seen = {seed}
    frontier = [seed]
    while frontier and len(window) < cap:
        neighbours: set[str] = set()
        for concept_id in frontier:
            for link in graph.out.get(concept_id, ()):
                target = link.target
                # `Link.target` keeps the `.md` that `Bundle.concepts` keys
                # drop, and is None for external destinations and for relative
                # paths escaping the root — so this drops assets and externals
                # for free. Images are excluded here for the same reason okf-io
                # excludes them from `backlinks`, so the walk is symmetric in
                # both directions.
                if target is None or link.image or not target.endswith(".md"):
                    continue
                candidate = target[: -len(".md")]
                if candidate in bundle.concepts:
                    neighbours.add(candidate)
            # Backlinks are already concept ids.
            neighbours.update(graph.backlinks.get(concept_id, ()))
        frontier = [candidate for candidate in sorted(neighbours) if candidate not in seen]
        seen.update(frontier)
        window.extend(frontier[: cap - len(window)])

    for offset in range(len(ids)):
        if len(window) == cap:
            break
        candidate = ids[(seed_index + offset) % len(ids)]
        if candidate in seen:
            continue
        seen.add(candidate)
        window.append(candidate)
    return tuple(window)


def _group_pages(bundle: Bundle, *, today: date) -> dict[str, tuple[tuple[str, Document], ...]]:
    """The three groups' page sets, re-derived from the rebuild's own dialect.

    Not ported: the old selectors read `key.startswith("adrs/")` and
    `fm.get("source_path") or fm.get("package_path")`, and the rebuild has
    neither key. `stale_claims` now means "documents carrying `sources[]`",
    which is where provenance lives in OKF v0.2.

    The wiki lane already excludes the work lane, so "non-work documents" needs
    no filter here — the ignore recipe is the filter.

    `page_quality` is the one group whose page set is a window rather than a
    predicate; `_page_quality_window` says why, and why it is not a slice.
    `adr_chain` and `stale_claims` see every qualifying page.
    """
    ids = tuple(sorted(bundle.concepts))
    ordered = tuple((concept_id, bundle.concepts[concept_id]) for concept_id in ids)
    window = _page_quality_window(bundle, ids, today=today)
    return {
        "page_quality": tuple((concept_id, bundle.concepts[concept_id]) for concept_id in window),
        "adr_chain": tuple((cid, doc) for cid, doc in ordered if is_adr(cid, doc.fm.type or "")),
        "stale_claims": tuple((cid, doc) for cid, doc in ordered if doc.fm.sources),
    }


def _linter_input(pages: Sequence[tuple[str, Document]]) -> str:
    """One human message describing a group's pages."""
    lines: list[str] = []
    for concept_id, document in pages:
        lines.append(f"--- Page: {concept_id} ---")
        lines.append(f"type: {document.fm.type or '(none)'}")
        lines.append(f"title: {document.fm.title or '(none)'}")
        lines.append(f"description: {document.fm.description or '(none)'}")
        if document.fm.sources:
            declared = ", ".join(source.resource or "(no resource)" for source in document.fm.sources)
            lines.append(f"sources: {declared}")
        preview = document.body.strip()[:_BODY_PREVIEW_CHARS]
        if preview:
            lines.append(preview)
        lines.append("")
    return "\n".join(lines)


def _systems(project_context: str) -> dict[str, str]:
    return {
        "page_quality": build_linter_page_quality_system(project_context=project_context),
        "adr_chain": build_linter_adr_chain_system(project_context=project_context),
        "stale_claims": build_linter_stale_claims_system(project_context=project_context),
    }


def _split_page(line: str, bundle: Bundle) -> tuple[str | None, str]:
    """`("concepts/x", "the rest")` when the line names a page, else `(None, line)`.

    Derived from the `<page id>: ` prefix the output-format block asks for, and
    only when that prefix actually names a concept in the bundle — a message
    that merely happens to contain a colon keeps its text whole.
    """
    head, separator, tail = line.partition(": ")
    if separator and head in bundle.concepts:
        return head, tail.strip()
    return None, line


def _findings_from(reply: str, *, group: str, model: str, bundle: Bundle) -> tuple[SemanticFinding, ...]:
    """One finding per non-empty line, the ported parse.

    A line that is exactly one of the "no issues found" sentinels is skipped
    like an empty line: it is the model reporting a clean group, not a finding.
    """
    found: list[SemanticFinding] = []
    for raw in reply.splitlines():
        line = raw.strip()
        if not line or line in _NO_ISSUES_SENTINELS:
            continue
        page, message = _split_page(line, bundle)
        found.append(SemanticFinding(group=group, message=message, page=page, model=model))
    return tuple(found)


async def _semantic_pass(
    bundle: Bundle,
    binding: RoleBinding,
    *,
    today: date,
    trace_dir: Path,
    project_context: str,
) -> tuple[tuple[SemanticFinding, ...], tuple[str, ...]]:
    """Fan the three groups out, capturing each one's failure on its own.

    An empty group is dropped **before** the fan-out rather than short-circuited
    inside the task: a group with no pages has no question to ask, and not
    dispatching it is the honest way to say so.
    """
    systems = _systems(project_context)
    grouped = _group_pages(bundle, today=today)
    items = [(name, systems[name], grouped[name]) for name in SEMANTIC_GROUPS if grouped[name]]
    if not items:
        return (), ()

    async def judge(item: tuple[str, str, tuple[tuple[str, Document], ...]]) -> TaskResult:
        _name, system, pages = item
        response = await binding.make_llm().ainvoke(
            [SystemMessage(content=system), HumanMessage(content=_linter_input(pages))]
        )
        if not isinstance(response.content, str):
            raise RuntimeError("linter returned non-text content")
        return TaskResult(value=response.content, response=response)

    pool = SubagentPool(trace_dir=trace_dir, price_lookup=cost_for_usage)
    fan = await pool.run_all(
        items,
        judge,
        LINTER_ROLE,
        model_id=binding.spec.model_id,
        max_concurrency=binding.spec.max_concurrency,
    )

    findings: list[SemanticFinding] = []
    for item, reply in fan.successes:
        findings.extend(_findings_from(reply, group=item[0], model=binding.spec.model_id, bundle=bundle))
    errors = tuple(f"{failure.item[0]}: {failure.exception}" for failure in fan.errors)
    return tuple(findings), errors


async def run_lint(
    layout: WorkspaceLayout,
    config: Config,
    *,
    today: date,
    repo_root: Path | None,
    reader: GraphReader | None = None,
    model_override: str | None = None,
    strict: bool = False,
) -> LintReport:
    """The mechanical aggregation plus the three-group semantic fan-out.

    A missing credential is a **typed refusal** from the role resolver, not an
    `ImportError` guard: C2 binds roles to a model factory unconditionally, so
    "no model configured" is one `errors` line and an empty `semantic`, with
    the mechanical half unaffected.
    """
    mechanical, bundles = _run_mechanical(
        layout, config, today=today, repo_root=repo_root, reader=reader, strict=strict
    )
    bundle = bundles.get(WIKI_LANE)
    if bundle is None:
        # The wiki lane failed to walk, or failed to compose. Either way the
        # semantic pass has no corpus — and saying so is what every other
        # refusal in this module already does.
        return _with(
            mechanical,
            errors=(
                *mechanical.errors,
                f"{LINTER_ROLE}: the wiki lane produced no bundle, so the semantic pass did not run",
            ),
        )

    try:
        binding = role_binding(LINTER_ROLE, layout=layout, model_override=model_override)
    except (KeyError, ValueError) as exc:
        return _with(mechanical, errors=(*mechanical.errors, f"{LINTER_ROLE}: {exc}"))

    findings, errors = await _semantic_pass(
        bundle,
        binding,
        today=today,
        trace_dir=layout.cache_dir / "traces",
        project_context=render_project_context(layout),
    )
    return _with(mechanical, semantic=findings, errors=mechanical.errors + errors)


def _with(
    report: LintReport,
    *,
    semantic: tuple[SemanticFinding, ...] = (),
    errors: tuple[str, ...] | None = None,
) -> LintReport:
    """A copy of *report* with the semantic half filled in. `LintReport` is
    frozen, and `dataclasses.replace` on a slotted frozen dataclass is the
    idiom every other value type in this workspace uses."""
    return LintReport(
        mechanical=report.mechanical,
        semantic=semantic,
        open_proposals=report.open_proposals,
        errors=report.errors if errors is None else errors,
    )


__all__ = [
    "AGE_BUCKETS",
    "LINTER_ROLE",
    "PAGE_QUALITY_WINDOW",
    "SEMANTIC_GROUPS",
    "LaneReport",
    "LintReport",
    "ProposalBacklog",
    "SemanticFinding",
    "run_lint",
    "run_mechanical",
]
