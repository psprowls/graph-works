"""Contract-test inputs for `graph_works_wire.wiki`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace as ns

from graph_works_core.guidance.claims import ClaimRow, ClaimsRefresh, SkippedEntry
from graph_works_core.guidance.closure import Closure, ClosureEntry, MatchedClaim
from graph_works_core.guidance.commands import ClaimsClosureRun, ClaimsShow
from graph_works_core.proposals import (
    ProposalCheck,
    ProposalChecks,
    ProposalDecideRun,
    ProposalFileRun,
    ProposalPreview,
    ProposalRefusal,
)
from graph_works_core.scan.commands import StructuralSummary
from graph_works_core.scan.repo_scan import RepoScanRun
from graph_works_core.wiki_page.citations import Citation, CitationCandidate, WikiCitations
from graph_works_core.wiki_page.commands import PageLink, PageRead, TreeNode, TreePage, WikiTree
from graph_works_core.wiki_page.section import SectionWriteRun
from graph_works_core.workspace.commits import CommitOutcome
from graph_works_wire import wiki
from okf_ext.proposals import ApplyResult, DecisionPlan, ProposalPlan, Write, WriteFailure

LAYOUT = ns(root=Path("/ws"), bundle_dir=Path("/ws/okf"), config_dir=Path("/ws/.gw"), cache_dir=Path("/ws/.gw/cache"))
ENTITIES = ns(written=("packages/a",), created=("packages/a",), updated=(), deleted=("packages/b",))
MIRROR = ns(
    created=1,
    regenerated=2,
    moved=0,
    deleted=1,
    declined=0,
    stranded=0,
    skipped_repos=("r2",),
    failed_repos=(("r3", "boom"),),
)
STRUCTURAL = ns(
    entities=ENTITIES,
    mirror=MIRROR,
    errors=("packages/c: declined",),
    warnings=("packages/old: retained entity: prose-edited",),
)
FINDING = ns(code="x", severity="warn", message="m", spec="s", path="a.md", line=3)


def ingest_result(*, degraded: bool) -> object:
    return ns(
        ok=True,
        page="sources/a.md",
        copy="sources/references/a.md",
        title="A",
        source_kind="reference",
        entity_uri=None,
        entity_page=None,
        frontmatter_parsed=not degraded,
        written=("sources/a.md",),
        indexes_updated=("index.md",),
        proposals=(
            {
                "type": "Explanation",
                "title": "T",
                "target": "concepts/t.md",
                "proposal": "p",
                "status": "filed",
            },
        ),
        proposal_status={"error": "timeout", "failed": ["x"], "errored": ["y"]} if degraded else {},
        refusals=(),
        notes=("ingestor drain ledger dropped: x",) if degraded else (),
    )


def lint_report(*, oldest: bool) -> object:
    return ns(
        ok=not oldest,
        mechanical=(ns(name="wiki", report=ns(findings=(FINDING,))),),
        semantic=(ns(group="contradiction", message="m", page="a.md", model="m1"),),
        open_proposals=ns(
            count=1,
            oldest=date(2026, 9, 1) if oldest else None,
            malformed=0,
            ages={"proposals/a.md": 3},
        ),
        source_drain=ns(sources=2, drained=1, items=5, landed=1, dropped=2, pending=2) if oldest else None,
        errors=("e",) if oldest else (),
    )


def proposal_decide(*, applied: bool, found: bool = True) -> ProposalDecideRun:
    write = Write(member="proposals/a.md", mode="update", frontmatter={"page_status": "approved"})
    if not found:
        return ProposalDecideRun(
            target="concepts/a.md",
            decision="approved",
            proposal=None,
            plan=None,
            refusals=(ProposalRefusal(path="concepts/a.md", kind="no-proposal", detail="not found"),),
        )
    return ProposalDecideRun(
        target="concepts/a.md",
        decision="approved",
        proposal="proposals/a.md",
        plan=DecisionPlan(Path("/ws/okf"), "proposals/a.md", "approved", (write,), ()),
        refusals=(),
        result=ApplyResult(written=("proposals/a.md",), failed=(), skipped=()) if applied else None,
        commit=CommitOutcome("committed", "abc123", "workspace: approve proposal a", ("okf/proposals/a.md",), None)
        if applied
        else None,
    )


def proposal_file(*, applied: bool) -> ProposalFileRun:
    write = Write(member="proposals/explanations-a.md", mode="create", text="---\nbody\n")
    plan = ProposalPlan(Path("/ws/okf"), "docs/explanations/a.md", write.member, (write,), ())
    return ProposalFileRun(
        type_name="Explanation",
        target=plan.target,
        proposal=plan.proposal,
        plan=plan,
        refusals=(),
        result=(
            ApplyResult(
                written=(),
                failed=(WriteFailure(path=write.member, kind="stale", error="changed"),),
                skipped=(),
            )
            if applied
            else None
        ),
    )


WIKI: dict[str, tuple[Callable[[], object], ...]] = {
    "wiki.proposal_checks_payload": (
        lambda: wiki.proposal_checks_payload(
            ProposalChecks(
                "docs/explanations/x.md",
                "proposals/x.md",
                (
                    ProposalCheck("schema", "pass", ()),
                    ProposalCheck("citations", "fail", ("missing source: /sources/one.md",)),
                    ProposalCheck("code-drift", "skipped", ()),
                    ProposalCheck("related-adrs", "warn", ("adrs/2026-01-01-x#D1: c",)),
                ),
                None,
            )
        ),
        lambda: wiki.proposal_checks_payload(ProposalChecks("docs/nope.md", None, (), "no-proposal")),
    ),
    "wiki.proposal_preview_payload": (
        lambda: wiki.proposal_preview_payload(
            ProposalPreview(
                "docs/explanations/x.md",
                "proposals/x.md",
                "update",
                "docs/explanations/x.md",
                "new\n",
                "old\n",
                "--- a/docs/explanations/x.md\n+++ b/docs/explanations/x.md\n@@ -1 +1 @@\n-old\n+new\n",
                (),
                None,
            )
        ),
        lambda: wiki.proposal_preview_payload(
            ProposalPreview(
                "docs/explanations/x.md",
                "proposals/x.md",
                "create",
                None,
                None,
                None,
                None,
                (ProposalRefusal("proposals/x.md", "bad", "detail"),),
                None,
            )
        ),
        lambda: wiki.proposal_preview_payload(
            ProposalPreview("docs/nope.md", None, None, None, None, None, None, (), "no-proposal")
        ),
    ),
    "wiki.citations_payload": (
        lambda: wiki.citations_payload(
            WikiCitations(
                "concepts/a",
                (
                    Citation("a.py:3", 2, "gw", "src/a.py", 3, 3, "resolved", ()),
                    Citation(
                        "b.py:1",
                        4,
                        None,
                        None,
                        1,
                        1,
                        "ambiguous",
                        (CitationCandidate("gw", "b.py"), CitationCandidate("other", "b.py")),
                    ),
                    Citation("c.py:9", 6, None, None, 9, 9, "missing", ()),
                ),
                None,
            )
        ),
        lambda: wiki.citations_payload(WikiCitations("concepts/nope", (), "unknown-page")),
    ),
    "wiki.page_payload": (
        lambda: wiki.page_payload(
            PageRead(
                "concepts/a",
                MappingProxyType({"title": "A"}),
                "body",
                (),
                (),
                (PageLink("concepts/a", "missing.md", "concepts/missing.md", False, 4),),
                None,
                None,
            )
        ),
        lambda: wiki.page_payload(PageRead("unknown", MappingProxyType({}), "", (), (), (), None, "unknown-page")),
    ),
    "wiki.bootstrap_payload": (
        lambda: wiki.bootstrap_payload(
            ns(
                ok=True,
                changed=True,
                layout=LAYOUT,
                created=(Path("/ws"),),
                diff=lambda: "+ a\n+ a\n- b\n= c\n",
            )
        ),
    ),
    "wiki.bootstrap_plan_payload": (
        lambda: wiki.bootstrap_plan_payload(ns(ok=True, is_empty=False, layout=LAYOUT, diff=lambda: "+ a\n! b\n")),
    ),
    "wiki.scan_normal_payload": (
        lambda: wiki.scan_normal_payload(
            ns(
                ok=True,
                worklist=ns(short_head="abc"),
                structural=STRUCTURAL,
                applied=ns(narrated=1, sections_filled=2, stamped=3),
                errors=("e",),
            )
        ),
    ),
    "wiki.scan_emit_payload": (
        lambda: wiki.scan_emit_payload(
            worklist_path=Path("/c/worklist.json"),
            briefs_dir=Path("/c/briefs"),
            results_dir=Path("/c/results"),
            short_head="abc",
            structural=STRUCTURAL,
        ),
    ),
    "wiki.repo_scan_payload": (
        lambda: wiki.repo_scan_payload(RepoScanRun("alpha", STRUCTURAL, None, None)),
        lambda: wiki.repo_scan_payload(
            RepoScanRun(
                "alpha",
                STRUCTURAL,
                None,
                None,
                applied=True,
                commit=CommitOutcome(
                    "committed", "abc123", "workspace: scan alpha", ("okf/code-graph/alpha.md",), None
                ),
            )
        ),
        lambda: wiki.repo_scan_payload(
            RepoScanRun("nope", StructuralSummary(), "unknown-repository", "'nope' is not a declared repository")
        ),
    ),
    "wiki.scan_apply_payload": (
        lambda: wiki.scan_apply_payload(ns(narrated=1, sections_filled=0, stamped=2, entity_errors=("x",))),
    ),
    "wiki.ingest_brief_payload": (lambda: wiki.ingest_brief_payload(ns(as_data=lambda: {"source": "a.md"})),),
    "wiki.ingest_payload": (
        lambda: wiki.ingest_payload(ingest_result(degraded=False)),
        lambda: wiki.ingest_payload(ingest_result(degraded=True)),
    ),
    "wiki.query_brief_payload": (
        lambda: wiki.query_brief_payload(
            ns(
                query="why",
                top_pages=(ns(path="a.md", excerpt="x", search_scores={"bm25": 1.0}),),
                retrieval="hybrid",
                page=None,
                refusal=None,
                warnings=(),
            )
        ),
        lambda: wiki.query_brief_payload(
            ns(
                query="why",
                top_pages=(),
                retrieval="lexical",
                page="concepts/nope",
                refusal="unknown-page",
                warnings=("embedding unavailable: RuntimeError: no credentials",),
            )
        ),
    ),
    "wiki.query_payload": (
        lambda: wiki.query_payload(
            ns(
                answer="a",
                citations=("a.md",),
                pages_drilled=1,
                search_scores={"a.md": {"rrf": 0.5}},
                path="orchestrated",
                fallback_error=None,
            )
        ),
    ),
    "wiki.lint_payload": (
        lambda: wiki.lint_payload(lint_report(oldest=True)),
        lambda: wiki.lint_payload(lint_report(oldest=False)),
    ),
    "wiki.wiki_lint_payload": (
        lambda: wiki.wiki_lint_payload(lint_report(oldest=True)),
        lambda: wiki.wiki_lint_payload(lint_report(oldest=False)),
    ),
    "wiki.drift_brief_payload": (
        lambda: wiki.drift_brief_payload(
            ns(
                targets=(
                    ns(
                        concept_id="concepts/b",
                        title="b",
                        kind="concept",
                        candidates=(
                            ns(
                                concept_id="packages/f",
                                resource="package:f",
                                title="f",
                                narrative="n",
                                last_updated_commit="abc",
                                changed_files=("a.py",),
                            ),
                        ),
                    ),
                )
            )
        ),
    ),
    "wiki.drift_payload": (
        lambda: wiki.drift_payload(
            ns(
                entities_considered=1,
                pages_judged=1,
                pages_stale=1,
                pages_skipped_settled=0,
                findings=(
                    ns(
                        target="concepts/b",
                        target_title="b",
                        entity_id="packages/f",
                        entity_title="f",
                        detected_commit="abc",
                        rationale="r",
                    ),
                ),
                plans=(
                    ns(
                        target="concepts/b",
                        proposal="proposals/b.md",
                        ok=False,
                        is_empty=False,
                        refusals=(ns(path="p", kind="k", detail="d"),),
                    ),
                ),
                errors=("e",),
                dry_run=True,
            )
        ),
    ),
    "wiki.stats_payload": (
        lambda: wiki.stats_payload(
            ns(
                total_pages=2,
                total_edges=1,
                component_count=1,
                top_outbound_hubs=(ns(page="a", degree=1),),
                top_inbound_hubs=(ns(page="b", degree=1),),
                orphans=("c",),
                sinks=("b",),
            )
        ),
    ),
    "wiki.proposal_payload": (
        lambda: wiki.proposal_payload(
            ns(
                member="proposals/a.md",
                target="concepts/a.md",
                target_type=None,
                title="A",
                description="d",
                page_status="proposed",
                sources=({"resource": "s.md"},),
                verified=({"by": "human"},),
                superseded_by=None,
                malformed=None,
            ),
            mode="create",
        ),
    ),
    "wiki.proposals_payload": (
        lambda: wiki.proposals_payload(
            [
                ns(
                    proposal=ns(
                        member="proposals/a.md",
                        target="concepts/a.md",
                        target_type=None,
                        title="A",
                        description="d",
                        page_status="proposed",
                        sources=(),
                        verified=(),
                        superseded_by="/concepts/b.md",
                        malformed="missing target",
                    ),
                    mode="update",
                    type_name=None,
                    type_refusal=ns(path="proposals/a.md", kind="malformed-proposal", detail="missing target"),
                )
            ]
        ),
        lambda: wiki.proposals_payload([]),
    ),
    "wiki.proposal_decide_payload": (
        lambda: wiki.proposal_decide_payload(proposal_decide(applied=False)),
        lambda: wiki.proposal_decide_payload(proposal_decide(applied=True)),
        lambda: wiki.proposal_decide_payload(proposal_decide(applied=False, found=False)),
    ),
    "wiki.section_write_payload": (
        lambda: wiki.section_write_payload(SectionWriteRun("docs/x", "Context", "\nold\n", "\nnew\n", None)),
        lambda: wiki.section_write_payload(
            SectionWriteRun(
                "docs/x",
                "Context",
                "\nold\n",
                "\nnew\n",
                None,
                applied=True,
                written=("docs/x.md",),
                commit=CommitOutcome(
                    "committed", "abc123", "workspace: write docs/x section Context", ("okf/docs/x.md",), None
                ),
            )
        ),
        lambda: wiki.section_write_payload(
            SectionWriteRun("docs/x", "Files", "\ngen\n", "\ngen\n", "generated-section")
        ),
        lambda: wiki.section_write_payload(
            SectionWriteRun("docs/x", "Context", "", "", None, applied=True, failures=("docs/x.md: io -- denied",))
        ),
    ),
    "wiki.proposal_file_payload": (
        lambda: wiki.proposal_file_payload(proposal_file(applied=False)),
        lambda: wiki.proposal_file_payload(proposal_file(applied=True)),
    ),
    "wiki.tag_inventory_payload": (
        lambda: wiki.tag_inventory_payload(
            ns(
                counts={"b": 1, "a": 2},
                concepts={"a": ("c1", "c2"), "b": ("c1",)},
                untagged=(),
                skipped=(ns(path="x.md", reason="parse", detail="bad"),),
            )
        ),
    ),
    "wiki.tags_undeclared_payload": (lambda: wiki.tags_undeclared_payload(("zeta",)),),
    "wiki.wiki_tree_payload": (
        lambda: wiki.wiki_tree_payload(
            WikiTree(
                (
                    TreeNode(
                        "Packages",
                        2,
                        True,
                        (TreePage("code-graph/r/entities/packages/a", "a", "Package"),),
                        (TreeNode("Extra", 3, True, (), ()),),
                    ),
                )
            )
        ),
        lambda: wiki.wiki_tree_payload(WikiTree(())),
    ),
    "wiki.claims_refresh_payload": (
        lambda: wiki.claims_refresh_payload(
            ClaimsRefresh(True, "corpus-changed", 4, 1, 0, (SkippedEntry("e/p", "claims", 2, "missing-id"),))
        ),
        lambda: wiki.claims_refresh_payload(ClaimsRefresh(False, "no-change", 0, 0, 0, ())),
    ),
    "wiki.claims_rows_payload": (
        lambda: wiki.claims_rows_payload(
            ClaimsShow(
                "repo:o/r",
                (
                    ClaimRow(
                        "adrs/a",
                        "D1",
                        "decision",
                        "A claim.",
                        ("repo:o/r",),
                        (),
                        ("plan",),
                        "authored",
                        "stable",
                        False,
                        None,
                        3,
                    ),
                ),
            )
        ),
        lambda: wiki.claims_rows_payload(ClaimsShow("repo:o/r", ())),
    ),
    "wiki.claims_closure_payload": (
        lambda: wiki.claims_closure_payload(
            ClaimsClosureRun(
                "work/x",
                "r",
                ("README.md",),
                Closure((ClosureEntry("repo:o/r", 3, "repository r"),), ("w",)),
                (
                    MatchedClaim(
                        ClaimRow(
                            "adrs/a",
                            "D1",
                            "decision",
                            "A claim.",
                            ("repo:o/r",),
                            (),
                            ("plan",),
                            "authored",
                            "stable",
                            False,
                            None,
                            3,
                        ),
                        3,
                        "repo:o/r",
                        "repository r",
                    ),
                ),
                3,
                None,
                None,
            )
        ),
        lambda: wiki.claims_closure_payload(
            ClaimsClosureRun("work/y", "s", (), Closure((), ()), (), 0, "refusal reason", "detail")
        ),
    ),
}
