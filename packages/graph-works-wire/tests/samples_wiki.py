"""Contract-test inputs for `graph_works_wire.wiki`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace as ns

from graph_works_core.proposals import ProposalDecideRun, ProposalFileRun, ProposalRefusal
from graph_works_core.wiki_page.commands import PageLink, PageRead
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
STRUCTURAL = ns(entities=ENTITIES, mirror=MIRROR, errors=("packages/c: declined",))
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
                "lane": "concepts",
                "title": "T",
                "target": "concepts/t.md",
                "proposal": "p",
                "status": "filed",
            },
        ),
        proposal_status={"error": "timeout", "failed": ["x"], "errored": ["y"]} if degraded else {},
        refusals=(),
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
    )


def proposal_file(*, applied: bool) -> ProposalFileRun:
    write = Write(member="proposals/explanations-a.md", mode="create", text="---\nbody\n")
    plan = ProposalPlan(Path("/ws/okf"), "explanations/a.md", write.member, (write,), ())
    return ProposalFileRun(
        lane="explanation",
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
            ns(query="why", top_pages=(ns(path="a.md", excerpt="x", search_scores={"bm25": 1.0}),))
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
                title="A",
                description="d",
                page_status="proposed",
                sources=({"resource": "s.md"},),
                verified=({"by": "human"},),
                malformed=None,
            )
        ),
    ),
    "wiki.proposal_decide_payload": (
        lambda: wiki.proposal_decide_payload(proposal_decide(applied=False)),
        lambda: wiki.proposal_decide_payload(proposal_decide(applied=True)),
        lambda: wiki.proposal_decide_payload(proposal_decide(applied=False, found=False)),
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
}
