"""Contract-test inputs for `gw_wire.work`: one thunk per shape, both
sides of every None/non-None branch the projections take."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace as ns

from gw_kernel.workspace.commits import CommitOutcome
from gw_kernel.workspace.dispatch import packaged_rules_matching, resolve_dispatch
from gw_orchestrate.accept_integration import AcceptIntegrationResult
from gw_orchestrate.asks import AskAnswerResult, AskResult
from gw_orchestrate.dispatch import DispatchFailure, DispatchResult, ObservedPlacement
from gw_orchestrate.dispatch_record import Overrides
from gw_orchestrate.gate import GateCheckResult, GateRunResult, GateWaitResult, NotifyResult, PlannedUnit
from gw_orchestrate.gate_receipts import GateEvidence, GateRun, UnitEvidence
from gw_orchestrate.integrate import IntegrateResult
from gw_orchestrate.reroute import RerouteResult
from gw_orchestrate.wait import Absorbed, WaitResult
from gw_pipeline.place import PlaceRefusal, PlaceResult, PlaceStep, PruneKept, PruneReport, Refresh
from gw_pipeline.placement import EffectivePlacement, Placement
from gw_pipeline.transactions import MutationApplication
from gw_wire import work
from gw_work.affecting import AffectingRow, WorkAffecting
from gw_work.carried import CarriedContext, FilledSlot, SlotFill
from gw_work.commands import BoardRow, DispatchExplanation, ItemRead, ItemSource
from gw_work.migrate_placement import (
    MigrationBlocker,
    MigrationOrphan,
    MigrationPlan,
    MigrationResult,
    MigrationRow,
    WorkspaceStamp,
)
from gw_work.obligations import ObligationRecord
from gw_work.reconcile import CitedDecision, CommitRef, LandedSibling, ReconcileContext
from work_tracker_okf.obligations import Obligation, ObligationPlan
from work_tracker_okf.workflow import Blocker

BUNDLE = Path("/ws/okf")
RESOLUTION = resolve_dispatch({"stage": "execute", "has_plan": True}, rules=())
TRANSITION = ns(
    phase="execute",
    work_status="in-progress",
    document_status="stable",
    requires=("owner",),
    sync_plan_table=True,
    stamp_source="plan",
    stamp_baseline=False,
)
ROLLUP = ns(total=2, terminal=1, open_paths=("work/e/children/a",))
APPLICATION = ns(rolled_back=True, failures=("f",), warnings=("w",), ok=False, commit=None)
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
        path=None,
        artifacts=(),
        requested_path="work/e",
        finish_targets=(),
        selected_path="work/a",
        state=ns(work_status="open", type="Feature", phase="design", effort="medium"),
        route=ns(
            reason="r",
            on_dispatch=TRANSITION if full else None,
            on_complete=TRANSITION if full else None,
            path_candidates=(),
            blockers=(Blocker("invalid", "b"),),
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
        application=ns(normalized=("work/a",) if full else (), commits=()),
        normalizations=(kept, dropped),
        guidance=(
            ns(
                entries=(
                    ns(
                        kind="claim",
                        path="/adrs/x.md",
                        id="D1",
                        why="package directory packages/a",
                        text="- **D1** x",
                        tokens=5,
                    ),
                ),
                warnings=("no graph",),
            )
            if full
            else None
        ),
        carried=(
            CarriedContext(
                (
                    FilledSlot(
                        "landed_since", "Landed since your design", SlotFill(("- a landed",), {"shas": ["abc"]})
                    ),
                    FilledSlot(
                        "finish_obligations",
                        "Finish obligations",
                        SlotFill(warnings=("finish_obligations: unavailable: x",)),
                    ),
                ),
                ("frame note",),
            )
            if full
            else CarriedContext()
        ),
        guidance_file=Path("/ws/okf/work/a/references/guidance-design.md") if full else None,
        repository_notes=("repository demo: advance pin",) if full else (),
    )


def bare_next() -> object:
    """No resolution, no descent: the `None` arms of `next_payload`."""
    result = next_result(full=True)
    result.dispatch_resolution = None  # type: ignore[attr-defined]
    result.descent = None  # type: ignore[attr-defined]
    result.repository_notes = ()  # type: ignore[attr-defined]
    return result


GATE_RUN = GateRun(
    "20260928T120000Z-0a1b2c3d",
    "code",
    "/wt",
    "a" * 40,
    "b" * 40,
    True,
    False,
    "full",
    "just check",
    (),
    0,
    "/l",
    "t",
    "2026-09-28T12:00:00Z",
    1.0,
)
GATE_MATCH = GateEvidence("work/other", GATE_RUN.run_id, (UnitEvidence("a", "1" * 64, "work/x", "r0"),))
GATE_RUN_STARTED = GateRunResult(
    "started",
    None,
    "",
    GATE_RUN.run_id,
    None,
    "just check",
    ("a",),
    "/l",
    ("w",),
    units=(
        PlannedUnit("a", "1" * 64, True, None),
        PlannedUnit("b", "2" * 64, False, UnitEvidence("b", "2" * 64, "work/x", "r0")),
    ),
    stale=("a",),
)
GATE_RUN_NOTIFIED = replace(GATE_RUN_STARTED, notify=NotifyResult(True, "term_a", None))
GATE_RUN_UNNOTIFIED = replace(GATE_RUN_STARTED, notify=NotifyResult(False, None, "no-terminal"))
GATE_RUN_REFUSED = GateRunResult(None, "dirty-tree", "d", None, None, None, (), None, ())
GATE_WAIT_FINISHED = GateWaitResult(
    "finished", None, "", GATE_RUN.run_id, 0, True, "/l", "tail", "/r.md", units=(("a", 0),), repo_wide_exit=0
)
GATE_RUN_QUEUED = GateRunResult("queued", None, "", GATE_RUN.run_id, None, "just check", ("a",), "/l", (), position=2)
GATE_WAIT_QUEUED = GateWaitResult("queued", None, "", GATE_RUN.run_id, None, False, "/l", None, None, position=2)
GATE_WAIT_REFUSED = GateWaitResult(None, "no-run", "d", None, None, False, None, None, None)
GATE_CHECK_SATISFIED = GateCheckResult("satisfied", None, "", GATE_MATCH, "b" * 40, ())
GATE_CHECK_UNSATISFIED = GateCheckResult("unsatisfied", None, "no receipt", None, "b" * 40, ("w",), stale=("a",))


def advance(*, applied: bool, bypass: bool = False) -> object:
    plan = ns(
        changes=(
            ns(key="updated", before=None, after=date(2026, 9, 1)),
            ns(key="phase", before="design", after="plan"),
        ),
        transition=TRANSITION if applied else None,
        route=ns(blockers=() if applied else (Blocker("invalid", "blocked"),), on_complete=TRANSITION),
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
        gate_bypass=ns(code="no-start-sha", reason="r", actor="pat", detail="d", decision_id="D-001")
        if bypass
        else None,
        gate_receipt=GATE_MATCH if bypass else None,
    )


def filing(*, applied: bool) -> object:
    plan = ns(
        filing=ns(path="work/a", target=Path("/ws/okf/work/a.md"), detail=""),
        refusal=None,
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
        not_started=2,
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
        marker_strips=(),
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
            ns(written=("work/index.md", "work/a.md"), warnings=("a",), rolled_back=False, failures=(), commit=None)
            if applied
            else None
        ),
        wiki_plan=wiki_plan,
        wiki=wiki_result,
        wiki_commit=None,
        pointer_cleared=applied,
        logged="archived" if applied else None,
        prune_warnings=("prune work/a: git-unavailable: x",) if applied else (),
        pruned=(
            ns(
                removed_worktrees=("/wt/a",),
                deleted_branches=("feature-a",),
                kept=(ns(path="work/a", repo="code", branch="feature-a", why="dirty"),),
            )
            if applied
            else None
        ),
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
        application=ns(rolled_back=False, failures=(), commit=None) if planned else None,
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
        application=ns(mutation=ns(rolled_back=False, failures=(), ok=True, commit=None)) if applied else None,
        warnings=(),
    )


def orchestrate(*, busy: bool) -> object:
    placement = ns(
        repo_name="ui",
        repo_root=Path("/ui"),
        branch="feature/a-1a2b3c4d",
        worktree=Path("/wt"),
        parent_branch="main",
        parent_worktree=Path("/p"),
        owner_path="work/e",
        slug="a",
    )
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
        merge_target="main",
        auto_merge=True,
        prompt="go",
    )
    hold = ns(path="work/f", owner_path="work/f", ledger_path="/ledger.md", decision=ENTRY)
    return ns(
        path="work/e",
        terminal=not busy,
        max_parallel=2,
        slots_free=1,
        max_attend=1,
        attend_slots_free=1,
        supervise_merges=busy,
        live=("x",) if busy else (),
        dispatches=(dispatch,) if busy else (),
        plan=ns(
            finish_targets={},
            dispatch_resolutions={dispatch.key: RESOLUTION},
            human_checkpoints={dispatch.key: ns(status="declared", items=("Task 2: skim",))},
            placements={dispatch.key: placement} if busy else {},
        ),
        dispatch_repos={dispatch.key: ns(name="ui", path=Path("/ui"), source="item")},
        advances=(ns(path="work/b", reason="done", mode="return"),) if busy else (),
        blocked=(ns(path="work/c", kind="dependency", reason="wait"),) if busy else (),
        decisions_owner_path="work/e",
        decisions_ledger_path="ledger",
        open_decisions=(ENTRY,) if busy else (),
        assumed_decisions=(ENTRY,) if busy else (),
        decision_counts={"open": 1},
        holds=(hold,) if busy else (),
        warnings=("w",),
        code_repo="/code" if busy else None,
        code_repo_name="code" if busy else None,
        code_repo_source="sole" if busy else None,
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

PLACE_PARENT = Placement(
    "work/e",
    "work/e",
    "Epic",
    "e",
    "code",
    Path("/code"),
    "epic/e",
    Path("/wt/epic/e"),
    "main",
    Path("/code"),
    None,
)
PLACE_CHILD = Placement(
    "work/e/children/a",
    "work/e/children/a",
    "Feature",
    "a",
    "code",
    Path("/code"),
    "feature/a",
    Path("/wt/feature/a"),
    "epic/e",
    Path("/wt/epic/e"),
    "work/e",
)
PLACE_ENSURE = PlaceResult(
    PLACE_CHILD.path,
    "ensure",
    False,
    PLACE_CHILD,
    (
        PlaceStep(PLACE_PARENT, "verified", None, "already registered"),
        PlaceStep(PLACE_CHILD, "created", Refresh("ff", "old", "new"), "registered", "internal detail"),
    ),
    (None, "new"),
    CommitOutcome("committed", "ws-sha", "internal subject", ("okf/work/a.md",), None),
    ("notes.md",),
    None,
    None,
    ("note",),
    MutationApplication("tx", Path("/journal"), (), (), (), ("internal warning",), ("write failed",), True),
)
PLACE_START_REFUSED = replace(
    PLACE_ENSURE,
    mode="start",
    chain=(),
    start_sha=("old", "old"),
    workspace_commit=None,
    application=None,
    refusal=PlaceRefusal("already-started", "baseline exists"),
)
PLACE_CONFLICT = replace(
    PLACE_ENSURE,
    dry_run=True,
    chain=(PlaceStep(PLACE_CHILD, "verified", Refresh("conflict", None, None, ("a.py",)), None),),
    start_sha=None,
    workspace_commit=None,
    application=None,
    refusal=PlaceRefusal("refresh-conflict", "conflicts: a.py"),
)
PLACE_REMOVE = replace(
    PLACE_CONFLICT,
    mode="remove",
    dry_run=False,
    refusal=None,
    chain=(PlaceStep(PLACE_CHILD, "removed", None, None),),
)
PLACE_PRUNE = replace(
    PLACE_REMOVE,
    path=None,
    mode="prune",
    placement=None,
    chain=(),
    pruned=PruneReport(
        ("/wt/old",),
        ("feature/old",),
        (
            PruneKept("work/e", "code", "epic/e", "open"),
            PruneKept("work/occupied", "code", "feature/occupied", "occupied"),
        ),
    ),
)
PLACE_NO_REPO = PlaceResult("work/note", "ensure", False, None, (), None, None, (), None, None, ("no repo",))

MIGRATION_PLAN = MigrationPlan(
    rows=(
        MigrationRow("work/a", "code", "/wt/a", "feature/a", "remove-worktree", None),
        MigrationRow("work/a", "code", "/wt/a", "feature/a", "delete-branch", "receipt"),
        MigrationRow("work/b", "code", "/wt/b", "feature/b", "keep", "dirty"),
    ),
    blockers=(
        MigrationBlocker(
            "work/c",
            "code",
            "/wt/c",
            "orca/c",
            "feature/c",
            ("git -C /wt/c branch -m orca/c feature/c", "gw work place work/c"),
        ),
    ),
    orphans=(MigrationOrphan("code", "psprowls/gw-reader-1"),),
    strips=("work/a", "work/b", "work/c"),
    refusal=None,
    workspace_stamps=(WorkspaceStamp("work/c", "/ws/c", "ws/c", 3),),
)
MIGRATION_RESULT = MigrationResult(
    ("/wt/a",),
    ("feature/a",),
    (MigrationRow("work/b", "code", "/wt/b", "feature/b", "keep", "dirty"),),
    ("work/a", "work/b"),
    CommitOutcome(
        "committed", "1" * 40, "workspace: migrate-placement strip retired placement fields", ("okf/work/a.md",), None
    ),
)

WORK: dict[str, tuple[Callable[[], object], ...]] = {
    "work.migrate_placement_payload": (
        lambda: work.migrate_placement_payload(MIGRATION_PLAN),
        lambda: work.migrate_placement_payload(MIGRATION_PLAN, MIGRATION_RESULT),
        lambda: work.migrate_placement_payload(MigrationPlan((), (), (), (), None)),
    ),
    "work.place_payload": (
        lambda: work.place_payload(PLACE_ENSURE),
        lambda: work.place_payload(PLACE_START_REFUSED),
        lambda: work.place_payload(PLACE_CONFLICT),
        lambda: work.place_payload(PLACE_REMOVE),
        lambda: work.place_payload(PLACE_PRUNE),
        lambda: work.place_payload(PLACE_NO_REPO),
    ),
    "work.obligation_payload": (
        lambda: work.obligation_payload(
            ObligationRecord(ObligationPlan("work/a", (), (Obligation("Tag it", "deferred", "2026-09-29"),)))
        ),
        lambda: work.obligation_payload(ObligationRecord(ObligationPlan("work/a", (), (), "empty-text", "one line"))),
    ),
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
    "work.dispatch_payload": (
        lambda: work.dispatch_payload(RESOLUTION),
        lambda: work.dispatch_payload(
            DispatchResult(
                status="dispatched",
                key="work/a#execute",
                run_id="run_1",
                task_id="task_1",
                placement=ObservedPlacement("/wt", "b", "A", ("created",), "a" * 40),
                delivery="verified",
            )
        ),
        lambda: work.dispatch_payload(
            DispatchResult(
                status=None,
                key="work/a#execute",
                run_id="run_1",
                failure=DispatchFailure("launch", "unsent", "worker unavailable"),
            )
        ),
    ),
    "work.reroute_payload": (
        lambda: work.reroute_payload(
            RerouteResult(
                status="rerouted",
                key="work/a#execute",
                run_id="run_2",
                reason="retry",
                superseded_task_id="task_1",
                overrides=Overrides(agent="codex"),
            )
        ),
        lambda: work.reroute_payload(
            RerouteResult(
                status=None,
                key="work/a#execute",
                run_id="run_2",
                reason="retry",
                failure=DispatchFailure("reroute", "unsent", "worker unavailable"),
            )
        ),
    ),
    "work.wait_payload": (
        lambda: work.wait_payload(
            WaitResult(
                status="event",
                run_id="run_1",
                delivery_id="dlv_1",
                messages=(
                    {
                        "id": "m1",
                        "type": "question",
                        "subject": "q",
                        "body": "q",
                        "from_": "term_1",
                        "created_at": "t",
                        "payload": {"question": "q"},
                        "payload_raw": None,
                    },
                ),
                absorbed=(Absorbed("m0", "worker_done", "ctx_0", "duplicate-completion"),),
                self_acked=1,
                rebound=False,
                sleep_gap_s=None,
                waited_s=12,
                pending_questions=(
                    {
                        "message_id": "msg_0000000000b2",
                        "label": "q-00b2",
                        "dispatch_id": "ctx_b",
                        "task_id": "task_b",
                        "question": "Merge?",
                        "options": ["merge", "hold"],
                        "ask_resource": None,
                        "asked_at": "2026-09-27T15:30:00Z",
                    },
                ),
                warnings=("partial read",),
            )
        ),
        lambda: work.wait_payload(
            WaitResult(
                status="timeout",
                run_id="run_1",
                delivery_id=None,
                messages=(),
                absorbed=(),
                self_acked=0,
                rebound=True,
                sleep_gap_s=3000,
                waited_s=600,
                liveness=[],
                pending_questions=(),
            )
        ),
        lambda: work.wait_payload(
            WaitResult(
                status="timeout",
                run_id="run_1",
                delivery_id=None,
                messages=(),
                absorbed=(),
                self_acked=0,
                rebound=False,
                sleep_gap_s=None,
                waited_s=600,
                liveness=[
                    {
                        "key": "work/a#execute",
                        "handle": "ctx_1",
                        "terminal": "term_a",
                        "gate_wait": {
                            "run_id": "r",
                            "path": "work/a",
                            "terminal": "term_a",
                            "state": "running",
                            "resume_line": None,
                        },
                        "state": "running",
                        "heartbeat_at": "2026-09-27T12:00:00Z",
                        "heartbeat_age_s": 90,
                        "transcript_at": None,
                        "transcript_age_s": None,
                        "output_at": None,
                        "output_age_s": None,
                        "worktree_path": "/worktree",
                        "progress": {
                            "ledger": "/worktree/.superpowers/sdd/plan/progress.md",
                            "plan": "/worktree/plan.md",
                            "completed": 1,
                            "total": 2,
                        },
                        "notes": [],
                    }
                ],
            )
        ),
    ),
    "work.dispatch_explain_payload": (
        lambda: work.dispatch_explain_payload(
            DispatchExplanation(
                path="work/a",
                attributes=MappingProxyType({"stage": "execute", "has_plan": True}),
                packaged_rules=packaged_rules_matching({"stage": "execute", "has_plan": True}),
                rules=((packaged_rules_matching({"stage": "execute", "has_plan": True})[0], False),),
                resolution=RESOLUTION,
                next_result=ns(route=ns(blockers=()), descent=None, dispatch_preflight=None),
            )
        ),
        lambda: work.dispatch_explain_payload(
            DispatchExplanation(
                path="work/e",
                attributes=None,
                packaged_rules=None,
                rules=(),
                resolution=None,
                next_result=ns(route=ns(blockers=(Blocker("invalid", "b"),)), descent=None, dispatch_preflight=None),
            )
        ),
    ),
    "work.gate_run_payload": (
        lambda: work.gate_run_payload(GATE_RUN_STARTED, "work/a"),
        lambda: work.gate_run_payload(GATE_RUN_NOTIFIED, "work/a"),
        lambda: work.gate_run_payload(GATE_RUN_UNNOTIFIED, "work/a"),
        lambda: work.gate_run_payload(GATE_RUN_REFUSED, "work/a"),
        lambda: work.gate_run_payload(GATE_RUN_QUEUED, "work/a"),
    ),
    "work.gate_wait_payload": (
        lambda: work.gate_wait_payload(GATE_WAIT_FINISHED, "work/a"),
        lambda: work.gate_wait_payload(GATE_WAIT_REFUSED, "work/a"),
        lambda: work.gate_wait_payload(GATE_WAIT_QUEUED, "work/a"),
    ),
    "work.gate_check_payload": (
        lambda: work.gate_check_payload(GATE_CHECK_SATISFIED, "work/a"),
        lambda: work.gate_check_payload(GATE_CHECK_UNSATISFIED, "work/a"),
    ),
    "work.advance_payload": (
        lambda: work.advance_payload(advance(applied=True), "work/a"),
        lambda: work.advance_payload(advance(applied=False), "work/a"),
    ),
    "work.accept_integration_payload": (
        lambda: work.accept_integration_payload(
            AcceptIntegrationResult(
                "work/a",
                "code",
                "feature",
                "a" * 40,
                "main",
                "b" * 40,
                "pat",
                "rebased",
                None,
                None,
                None,
                None,
                "",
                False,
            )
        ),
        lambda: work.accept_integration_payload(
            AcceptIntegrationResult(
                "work/a",
                "code",
                "feature",
                "a" * 40,
                "main",
                "b" * 40,
                "pat",
                "rebased",
                "D-001",
                "receipt.md",
                "work/a/references/00-decisions.md",
                None,
                "",
                True,
            )
        ),
        lambda: work.accept_integration_payload(
            AcceptIntegrationResult(
                "work/a",
                "code",
                None,
                None,
                None,
                None,
                "",
                "",
                None,
                None,
                None,
                "missing-attribution",
                "--reason and --by are required",
                False,
            )
        ),
    ),
    "work.integrate_payload": (
        lambda: work.integrate_payload(
            IntegrateResult(
                "work/a",
                "code",
                "squash",
                "default",
                "feature",
                "a" * 40,
                "main",
                "/code",
                "b" * 40,
                None,
                "planned",
                None,
                "",
                (),
                None,
                False,
            )
        ),
        lambda: work.integrate_payload(
            IntegrateResult(
                "work/a",
                "code",
                "merge",
                "flag",
                "feature",
                "a" * 40,
                "main",
                "/code",
                "b" * 40,
                "c" * 40,
                "integrated",
                None,
                "",
                (),
                "receipt.md",
                True,
            )
        ),
        lambda: work.integrate_payload(
            IntegrateResult(
                "work/a",
                "code",
                "squash",
                "config",
                "feature",
                "a" * 40,
                "main",
                "/code",
                "b" * 40,
                None,
                None,
                "conflict",
                "CONFLICT",
                ("a.txt",),
                None,
                False,
            )
        ),
    ),
    "work.file_payload": (
        lambda: work.file_payload(filing(applied=True)),
        lambda: work.file_payload(filing(applied=False)),
    ),
    "work.status_payload": (
        lambda: work.status_payload(status(resume=True)),
        lambda: work.status_payload(status(resume=False)),
    ),
    "work.touch_active_work_payload": (
        lambda: work.touch_active_work_payload(
            ns(path="work/a", phase="plan", pointer_path=Path("/r/active-work.json"), refusal=None, detail=None)
        ),
        lambda: work.touch_active_work_payload(
            ns(path="work/a", phase=None, pointer_path=None, refusal="inactive-phase", detail="why")
        ),
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
    "work.ask_payload": (
        lambda: work.ask_payload(
            AskResult(
                (),
                Path("/ws/okf/work/a/references/asks/plan-001-choice.json"),
                "/work/a/references/asks/plan-001-choice.json",
                "Pick.\n\ngw-ask: /work/a/references/asks/plan-001-choice.json",
                "merge,hold",
                True,
            )
        ),
        lambda: work.ask_payload(AskResult(("option-count",), None, None, None, None, False)),
    ),
    "work.ask_answer_payload": (
        lambda: work.ask_answer_payload(
            AskAnswerResult(
                (),
                Path("/ws/okf/work/a/references/asks/plan-001-choice.json"),
                "/work/a/references/asks/plan-001-choice.json",
                (
                    '{"ask": "/work/a/references/asks/plan-001-choice.json", '
                    '"choice": "merge", "effort": null, "notes": null}'
                ),
                True,
                True,
            )
        ),
        lambda: work.ask_answer_payload(AskAnswerResult(("payload-invalid",), None, None, None, False, False)),
    ),
    "work.path_mutation_payload": (
        lambda: work.path_mutation_payload(path_mutation(applied=True)),
        lambda: work.path_mutation_payload(path_mutation(applied=False)),
        lambda: work.path_mutation_payload(path_mutation(applied=False), sources=()),
        lambda: work.path_mutation_payload(path_mutation(applied=False), sources=("work/a",)),
        lambda: work.path_mutation_payload(path_mutation(applied=True), sources=("work/a", "work/b")),
    ),
    "work.decision_payload": (
        lambda: work.decision_payload(decision(planned=True)),
        lambda: work.decision_payload(decision(planned=False)),
    ),
    "work.open_decisions_payload": (
        lambda: work.open_decisions_payload(
            [
                ns(
                    owner_path="work/e",
                    ledger=Path("work/e/references/00-decisions.md"),
                    held=("work/a",),
                    decision=ns(
                        id="D-001",
                        number=1,
                        question="q",
                        status="open",
                        affects=("work/a",),
                        decided=None,
                        supersedes=None,
                        hold="park",
                        phase="plan",
                        checkpoint=None,
                        prose="",
                    ),
                )
            ]
        ),
        lambda: work.open_decisions_payload([]),
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
    "work.work_list_payload": (
        lambda: work.work_list_payload(
            [
                BoardRow(
                    ns(
                        path="work/a",
                        type="Bug",
                        title="A",
                        work_status="accepted",
                        phase=None,
                        effort=None,
                        owner="pat",
                        parent_path=None,
                        updated="2026-09-01",
                    ),
                    EffectivePlacement("code", "/w/a", "bug/a", None, "legacy"),
                )
            ]
        ),
        lambda: work.work_list_payload([]),
    ),
    "work.work_affecting_payload": (
        lambda: work.work_affecting_payload(
            WorkAffecting(
                "code",
                "packages/a/src",
                (
                    AffectingRow(
                        ns(
                            path="work/a",
                            type="Bug",
                            title="A",
                            work_status="accepted",
                            phase=None,
                            effort=None,
                            owner="pat",
                            parent_path=None,
                            updated="2026-09-01",
                        ),
                        ("packages/a",),
                    ),
                ),
                None,
            )
        ),
        lambda: work.work_affecting_payload(WorkAffecting("nope", "x", (), "unknown-repository")),
    ),
    "work.work_queue_payload": (
        lambda: work.work_queue_payload(
            [
                ns(
                    item=ns(title="A"),
                    result=ns(
                        selected_path="work/a",
                        state=ns(type="Feature", phase="plan", work_status="open"),
                        route=ns(reason="r", blockers=(), on_dispatch=ns(requires=("owner",))),
                        descent=None,
                        dispatch_resolution=RESOLUTION,
                        dispatch_preflight=None,
                    ),
                )
            ]
        ),
        lambda: work.work_queue_payload([]),
    ),
}
