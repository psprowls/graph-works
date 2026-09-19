"""Contract-test inputs for `graph_works_wire.work`: one thunk per shape, both
sides of every None/non-None branch the projections take."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace as ns

from graph_works_core.work.commands import ItemRead, ItemSource
from graph_works_core.work.reconcile import CitedDecision, CommitRef, LandedSibling, ReconcileContext
from graph_works_core.workspace.dispatch import resolve_dispatch
from graph_works_wire import work

BUNDLE = Path("/ws/okf")
RESOLUTION = resolve_dispatch({"variant": "planned"}, rules=())
TRANSITION = ns(
    phase="execute",
    work_status="in-progress",
    document_status="stable",
    requires=("owner",),
    sync_plan_table=True,
    stamp_source="plan",
)
ROLLUP = ns(total=2, terminal=1, open_paths=("work/e/children/a",))
APPLICATION = ns(rolled_back=True, failures=("f",), warnings=("w",), ok=False)
REFUSAL = ns(path="work/a", kind="conflict", detail="changed")
ENTRY = ns(
    id="D-001",
    number=1,
    question="Why?",
    status="answered",
    affects=("work/a",),
    decided="user",
    supersedes=None,
    prose="Answer",
    hold="park",
    phase="execute",
    checkpoint="/work/a/references/c.md",
)
FINDING = ns(code="x", severity="warn", message="m", spec="s", path="work/a.md", line=7)


def next_result(*, full: bool) -> object:
    kept = ns(path="work/a", ref=ns(source_id="design", resource="/work/a/references/01-design.md"))
    dropped = ns(path="work/b", ref=ns(source_id="design", resource="/work/b/references/01-design.md"))
    return ns(
        requested_path="work/e",
        selected_path="work/a",
        state=ns(work_status="open", type="Feature", phase="design", effort="medium"),
        route=ns(
            reason="r",
            on_dispatch=TRANSITION if full else None,
            on_complete=TRANSITION if full else None,
            blockers=("b",),
        ),
        dispatch_resolution=RESOLUTION,
        dispatch_preflight=None if full else "malformed skill",
        artifact=ns(path=lambda root: root / "work/a/references/01-design.md") if full else None,
        child_rollup=ROLLUP if full else None,
        descent=(
            ns(path=("work/e", "work/a"), leaf="work/a", blocked_at=None, reason="")
            if full
            else ns(path=("work/e",), leaf=None, blocked_at="work/e", reason="blocked")
        ),
        application=ns(normalized=("work/a",) if full else ()),
        normalizations=(kept, dropped),
    )


def bare_next() -> object:
    """No resolution, no descent: the `None` arms of `next_payload`."""
    result = next_result(full=True)
    result.dispatch_resolution = None  # type: ignore[attr-defined]
    result.descent = None  # type: ignore[attr-defined]
    return result


def advance(*, applied: bool) -> object:
    plan = ns(
        changes=(
            ns(key="updated", before=None, after=date(2026, 9, 1)),
            ns(key="phase", before="design", after="plan"),
        ),
        transition=TRANSITION if applied else None,
        route=ns(blockers=() if applied else ("blocked",), on_complete=TRANSITION),
        refusal=None if applied else "bad",
        detail="why",
    )
    outcome = ns(
        plan=plan,
        stamped=ns(source_id="plan", resource="/work/a/references/02-plan.md") if applied else None,
        plan_row=True,
        changed=applied,
    )
    return ns(
        outcome=outcome,
        application=APPLICATION if applied else None,
        warnings=("w",),
        results_path=Path("/r/results.json") if applied else None,
        pointer_path=Path("/r/pointer") if applied else None,
        repo_note=None if applied else "note",
    )


def placement(*, applied: bool) -> object:
    plan = ns(
        path="work/a",
        root="work/a",
        expected_phase="execute",
        current_phase="execute",
        before=(None, None),
        after=("/wt", "b"),
        changed=True,
        refusal=None if applied else "stale",
        detail="d",
    )
    return ns(plan=plan, application=APPLICATION if applied else None, written=applied, repo_note="note")


def filing(*, applied: bool) -> object:
    plan = ns(
        filing=ns(path="work/a", target=Path("/ws/okf/work/a.md"), detail=""),
        refusal=None,
        indexes=(ns(path=Path("/ws/okf/work/index.md"), changed=True), ns(path=Path("/ws/x.md"), changed=False)),
        log=ns(entry="- filed") if applied else None,
        warnings=("p",),
    )
    return ns(plan=plan, application=APPLICATION if applied else None)


def status(*, resume: bool) -> object:
    rollup = ns(
        total=3,
        by_work_status={"open": 3},
        by_type={"Feature": 3},
        by_phase={"design": 1},
        children={"work/e": ROLLUP},
    )
    primary = ns(path="work/a", title="A")
    return ns(
        rollup=rollup,
        resume=ns(primary=primary, alternatives=(ns(path="work/b", title="B"),)) if resume else None,
    )


def regen(*, applied: bool) -> object:
    return ns(
        application=APPLICATION if applied else None,
        plans=(ns(path=Path("/ws/okf/work/index.md"), changed=True), ns(path=Path("/ws/y.md"), changed=False)),
        mutation=ns(warnings=("planned",), refusals=(REFUSAL,)),
    )


def archive_run(*, applied: bool, wiki: bool = False) -> object:
    wiki_plan = ns(
        tokens=("sources/one",) if wiki else (),
        skipped=(ns(token="sources/two", reason="not-eligible", detail="d"),) if wiki else (),
        moves=ns(
            moves=(ns(source="sources/one.md", dest="sources/_archive/one.md"),) if wiki else (),
            refusals=(),
        ),
    )
    wiki_result = (
        ns(
            archived=("sources/one",),
            refusals=(ns(path="sources/r.md", kind="k", detail="late"),),
            move=ns(failed=(ns(path="sources/x.md", kind="io", error="boom"),)),
            indexes=(ns(path="sources/_archive/index.md", changed=True), ns(path="sources/index.md", changed=False)),
        )
        if applied and wiki
        else None
    )
    return ns(
        ok=applied,
        conflict=("work/a",),
        plan=ns(path_mapping={"work/a": "work/_archive/a"}, warnings=("p",), refusals=(REFUSAL,)),
        result=(
            ns(written=("work/index.md", "work/a.md"), warnings=("a",), rolled_back=False, failures=())
            if applied
            else None
        ),
        wiki_plan=wiki_plan,
        wiki=wiki_result,
        pointer_cleared=applied,
        logged="archived" if applied else None,
    )


def path_mutation(*, applied: bool) -> object:
    return ns(
        plan=ns(
            path_mapping={"work/a": "work/e/children/a"},
            writes=(ns(member="work/index.md"), ns(member="work/a.md")),
            warnings=("p",),
            refusals=(REFUSAL,),
        ),
        application=APPLICATION if applied else None,
    )


OWNER = ns(owner_path="work/e", redirected_from="work/a", ledger=Path("/ws/okf/work/e/references/00-decisions.md"))


def decision(*, planned: bool) -> object:
    plan = ns(primary=ENTRY, superseded=ns(id="D-000"), refusal=None) if planned else None
    return ns(
        owner=OWNER,
        plan=plan,
        application=ns(rolled_back=False, failures=()) if planned else None,
        entries=(ENTRY,),
        counts={"answered": 1},
        warnings=("w",),
    )


def overturn(*, applied: bool) -> object:
    return ns(
        owner=ns(owner_path="work/e", redirected_from=None, ledger=Path("/ledger.md")),
        plan=ns(
            decision=ns(primary=None if not applied else ENTRY, superseded=None if not applied else ns(id="D-000")),
            filing=ns(filing=ns(path="work/t", target=Path("/ws/okf/work/t.md"))),
            refusal=None if applied else "refused",
        ),
        application=ns(mutation=ns(rolled_back=False, failures=(), ok=True)) if applied else None,
        warnings=(),
    )


def orchestrate(*, busy: bool) -> object:
    worktree = ns(action="create", path="/wt", branch="b", base_branch="main", exists=False, parent_path="/p")
    dispatch = ns(
        key="work/a#execute",
        slug="work/a",
        phase="execute",
        kind="Feature",
        effort="medium",
        skill="tdd",
        mode="worktree",
        agent="codex",
        model="m",
        reasoning_effort="high",
        worktree=worktree,
        merge_target="main",
        prompt="go",
    )
    hold = ns(path="work/f", owner_path="work/f", ledger_path="/ledger.md", decision=ENTRY)
    return ns(
        path="work/e",
        terminal=not busy,
        max_parallel=2,
        slots_free=1,
        supervise_merges=busy,
        live=("x",) if busy else (),
        dispatches=(dispatch,) if busy else (),
        plan=ns(dispatch_resolutions={dispatch.key: RESOLUTION}),
        advances=(ns(path="work/b", reason="done", worktree="w", branch="b", mode="return"),) if busy else (),
        blocked=(ns(path="work/c", kind="dependency", reason="wait"),) if busy else (),
        decisions_owner_path="work/e",
        decisions_ledger_path="ledger",
        open_decisions=(ENTRY,) if busy else (),
        assumed_decisions=(ENTRY,) if busy else (),
        decision_counts={"open": 1},
        holds=(hold,) if busy else (),
        warnings=("w",),
        code_repo="/code" if busy else None,
    )


RECONCILE = ReconcileContext(
    owner_path="work/epic-e",
    path="work/epic-e/children/feature-a",
    spec_path="work/x/references/01-design.md",
    spec_anchor_commit="abc",
    anchor_source="spec-git-history",
    commit_range="abc..HEAD",
    landed_siblings=(LandedSibling("work/epic-e/children/feature-b", "deadbee", ("packages/a",)),),
    touched_paths=("packages/a",),
    commits_since=(CommitRef("deadbee", "feat: a"),),
    cited_decisions=(CitedDecision("D-001", "answered", "q?"),),
    warnings=("partial",),
)

WORK: dict[str, tuple[Callable[[], object], ...]] = {
    "work.item_payload": (
        lambda: work.item_payload(
            ItemRead(
                "work/a",
                MappingProxyType({"title": "A"}),
                "body",
                (ItemSource("design", "/work/a/references/01-design.md", None),),
                ("work/a/references/01-design.md",),
                None,
                (),
                None,
                None,
            )
        ),
        lambda: work.item_payload(
            ItemRead("work/missing", MappingProxyType({}), "", (), (), None, (), "unreadable", "x")
        ),
    ),
    "work.next_payload": (
        lambda: work.next_payload(next_result(full=True), bundle_root=BUNDLE),
        lambda: work.next_payload(next_result(full=False), bundle_root=BUNDLE),
        lambda: work.next_payload(bare_next(), bundle_root=BUNDLE),
    ),
    "work.normalized_payload": (
        lambda: work.normalized_payload(next_result(full=True)),
        lambda: work.normalized_payload(next_result(full=False)),
    ),
    "work.descent_payload": (
        lambda: work.descent_payload(next_result(full=True)),
        lambda: work.descent_payload(bare_next()),
    ),
    "work.dispatch_payload": (lambda: work.dispatch_payload(RESOLUTION),),
    "work.advance_payload": (
        lambda: work.advance_payload(advance(applied=True), "work/a"),
        lambda: work.advance_payload(advance(applied=False), "work/a"),
    ),
    "work.placement_payload": (
        lambda: work.placement_payload(placement(applied=True)),
        lambda: work.placement_payload(placement(applied=False)),
    ),
    "work.file_payload": (
        lambda: work.file_payload(filing(applied=True)),
        lambda: work.file_payload(filing(applied=False)),
    ),
    "work.status_payload": (
        lambda: work.status_payload(status(resume=True)),
        lambda: work.status_payload(status(resume=False)),
    ),
    "work.ingest_queue_payload": (
        lambda: work.ingest_queue_payload(
            ns(pending=(ns(path="work/a", work_status="resolved", resource="/work/a/r.md", origin="design"),))
        ),
    ),
    "work.lint_payload": (lambda: work.lint_payload(ns(ok=False, findings=(FINDING,))),),
    "work.regen_index_payload": (
        lambda: work.regen_index_payload(regen(applied=True)),
        lambda: work.regen_index_payload(regen(applied=False)),
    ),
    "work.archive_payload": (
        lambda: work.archive_payload(archive_run(applied=True, wiki=True), dry_run=False),
        lambda: work.archive_payload(archive_run(applied=False), dry_run=True),
    ),
    "work.path_mutation_payload": (
        lambda: work.path_mutation_payload(path_mutation(applied=True)),
        lambda: work.path_mutation_payload(path_mutation(applied=False)),
    ),
    "work.decision_payload": (
        lambda: work.decision_payload(decision(planned=True)),
        lambda: work.decision_payload(decision(planned=False)),
    ),
    "work.overturn_payload": (
        lambda: work.overturn_payload(overturn(applied=True)),
        lambda: work.overturn_payload(overturn(applied=False)),
    ),
    "work.orchestrate_payload": (
        lambda: work.orchestrate_payload(orchestrate(busy=True)),
        lambda: work.orchestrate_payload(orchestrate(busy=False)),
    ),
    "work.reconcile_payload": (lambda: work.reconcile_payload(RECONCILE),),
}
