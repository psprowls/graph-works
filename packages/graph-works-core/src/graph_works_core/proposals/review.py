"""Mechanical review checks for one proposal: schema, citations, code drift, related ADRs.

Read-only and clock-free: the caller supplies `today`. The would-be page a
promotion produces is rendered through `_rendered_page`, the one place this
module turns a `PagePlan` into text, so the checks and any preview agree.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from typing import Literal

from doc_wiki_okf.actors import human_actor
from doc_wiki_okf.proposals import lane_set
from doc_wiki_okf.proposals.promote import plan_promotion
from okf_ext.bundle import SCHEMA_DIRNAME, SECTIONS_DIRNAME
from okf_ext.proposals import PagePlan, Proposal, render_write
from okf_ext.schemas import frontmatter_errors, load_schemas, schema_rule
from okf_ext.shape import load_sections
from okf_io import Bundle
from okf_io import parse as parse_document
from okf_io import validate as okf_validate

from graph_works_core.guidance.assembly import affects_overlap
from graph_works_core.guidance.claims import read_claims
from graph_works_core.proposals.commands import ProposalRefusal, find_proposal, lift_refusals, normalize_target
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.citations import resolve_citations
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout

CheckStatus = Literal["pass", "warn", "fail", "skipped"]
CheckId = Literal["schema", "citations", "code-drift", "related-adrs"]


@dataclass(frozen=True, slots=True)
class ProposalCheck:
    """One check's verdict and the findings behind it."""

    id: CheckId
    status: CheckStatus
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposalChecks:
    """Every check for one proposal, or a `no-proposal` refusal with none."""

    target: str
    proposal: str | None
    checks: tuple[ProposalCheck, ...]
    refusal: Literal["no-proposal"] | None


@dataclass(frozen=True, slots=True)
class ProposalPreview:
    """The page a proposal would produce, and for an update its diff against the target; or a `no-proposal` refusal."""

    target: str
    proposal: str | None
    mode: Literal["create", "update"] | None
    member: str | None
    rendered: str | None
    base: str | None
    diff: str | None
    refusals: tuple[ProposalRefusal, ...]
    refusal: Literal["no-proposal"] | None


def _status(fail: bool, warn: bool) -> CheckStatus:
    return "fail" if fail else "warn" if warn else "pass"


def _promotion(layout: WorkspaceLayout, bundle: Bundle, proposal: Proposal, *, today: date) -> PagePlan:
    """The plan promoting *proposal* today, as though it were approved. Nothing is written.

    A reviewer asks what approval would produce, so the status gate is satisfied
    on a copy; the proposal on disk is never touched.
    """
    config = load_workspace_config(layout)
    schemas = load_schemas(config.declarations_dir / SCHEMA_DIRNAME)
    return plan_promotion(
        bundle,
        lane_set(schemas),
        replace(proposal, page_status="approved"),
        section_set=load_sections(config.declarations_dir / SECTIONS_DIRNAME),
        by=human_actor(layout.root),
        at=datetime.combine(today, time(), tzinfo=UTC),
        on=today,
    )


def _rendered_page(bundle: Bundle, plan: PagePlan) -> str | None:
    """The text of the page *plan* would write (its first write), or `None` when it writes nothing."""
    return render_write(bundle, plan.writes[0]) if plan.writes else None


def _schema_check(layout: WorkspaceLayout, bundle: Bundle, proposal: Proposal, *, today: date) -> ProposalCheck:
    schema_dir = load_workspace_config(layout).declarations_dir / SCHEMA_DIRNAME
    if not schema_dir.is_dir():
        return ProposalCheck("schema", "skipped", ())
    schemas = load_schemas(schema_dir)
    report = okf_validate(bundle, today=today, extra_rules=[schema_rule(schemas)], scope=frozenset({proposal.member}))
    # `no-schema-for-type` is a coverage gap in the schema set, not a defect in this proposal.
    own = tuple(
        finding
        for finding in report.findings
        if finding.code.startswith("schemas.") and finding.code != "schemas.no-schema-for-type"
    )
    findings = [f"{finding.code}: {finding.message}" for finding in own]
    fail = any(finding.severity == "error" for finding in own)
    warn = bool(own) and not fail
    plan = _promotion(layout, bundle, proposal, today=today)
    if plan.refusals:
        findings.extend(f"promotion: {refusal.kind}: {refusal.detail}" for refusal in plan.refusals)
        fail = True
    elif (text := _rendered_page(bundle, plan)) is not None:
        page = parse_document(text)
        errors = frontmatter_errors(schemas, (page.fm.type or "").strip(), page.fm_data(dates="iso"))
        if errors:
            findings.extend(f"promoted page: {error}" for error in errors)
            fail = True
    return ProposalCheck("schema", _status(fail, warn), tuple(findings))


def _citations_check(bundle: Bundle, proposal: Proposal) -> ProposalCheck:
    resources = [str(source.get("resource") or "") for source in proposal.sources]
    resources = [resource for resource in resources if resource]
    if not resources:
        return ProposalCheck("citations", "skipped", ())
    missing = tuple(
        f"missing source: {resource}" for resource in resources if not bundle.has_member(resource.removeprefix("/"))
    )
    return ProposalCheck("citations", _status(bool(missing), False), missing)


def _code_checks(layout: WorkspaceLayout, bundle: Bundle, body: str) -> tuple[ProposalCheck, ProposalCheck]:
    citations = resolve_citations(layout, body)
    if not citations:
        return ProposalCheck("code-drift", "skipped", ()), ProposalCheck("related-adrs", "skipped", ())
    missing = [f"missing: {c.raw} (line {c.line})" for c in citations if c.status == "missing"]
    ambiguous = [f"ambiguous: {c.raw} (line {c.line})" for c in citations if c.status == "ambiguous"]
    drift = ProposalCheck("code-drift", _status(bool(missing), bool(ambiguous)), (*missing, *ambiguous))
    cited = tuple(sorted({c.path for c in citations if c.path is not None}))
    if not cited:
        return drift, ProposalCheck("related-adrs", "skipped", ())
    # Matches on `constrains` only: an `about:` URI would need the code graph to map to paths.
    related = tuple(
        f"{row.page}#{row.id}: {row.claim}"
        for row in read_claims(bundle, layout.cache_dir)
        if row.kind == "decision"
        and row.page.startswith("adrs/")
        and not row.superseded
        and affects_overlap(row.constrains, cited)
    )
    return drift, ProposalCheck("related-adrs", _status(False, bool(related)), related)


def run_proposal_checks(layout: WorkspaceLayout, target: str, *, today: date) -> ProposalChecks:
    """Run the four mechanical checks over the proposal for *target*. Never writes."""
    bundle = load_workspace_bundle(layout)
    proposal = find_proposal(bundle, target)
    if proposal is None:
        return ProposalChecks(normalize_target(target), None, (), "no-proposal")
    document = bundle.concept(proposal.concept_id)
    assert document is not None
    checks = (
        _schema_check(layout, bundle, proposal, today=today),
        _citations_check(bundle, proposal),
        *_code_checks(layout, bundle, document.body),
    )
    return ProposalChecks(proposal.target, proposal.member, checks, None)


def run_proposal_preview(layout: WorkspaceLayout, target: str, *, today: date) -> ProposalPreview:
    """The page promoting the proposal for *target* would write, with a unified diff for an update. Never writes."""
    bundle = load_workspace_bundle(layout)
    proposal = find_proposal(bundle, target)
    if proposal is None:
        return ProposalPreview(normalize_target(target), None, None, None, None, None, None, (), "no-proposal")
    plan = _promotion(layout, bundle, proposal, today=today)
    rendered = None if plan.refusals else _rendered_page(bundle, plan)
    if rendered is None:
        return ProposalPreview(
            proposal.target, proposal.member, plan.mode, None, None, None, None, lift_refusals(plan.refusals), None
        )
    # Promotion writes to a path built from the title (dated for ADRs), not `proposal.target`, so the
    # page write's own mode and member are the truth; the target's existence is not.
    write = plan.writes[0]
    mode, member = write.mode, write.member
    if mode == "create":
        return ProposalPreview(proposal.target, proposal.member, mode, member, rendered, None, None, (), None)
    document = bundle.concept(member.removesuffix(".md"))
    base = "" if document is None else document.raw_text
    diff = "".join(difflib.unified_diff(base.splitlines(True), rendered.splitlines(True), f"a/{member}", f"b/{member}"))
    return ProposalPreview(proposal.target, proposal.member, mode, member, rendered, base, diff, (), None)


__all__ = [
    "CheckId",
    "CheckStatus",
    "ProposalCheck",
    "ProposalChecks",
    "ProposalPreview",
    "run_proposal_checks",
    "run_proposal_preview",
]
