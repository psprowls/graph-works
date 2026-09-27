"""Explicit `gw work` projections: exact keys, None-vs-empty distinctions, path keys."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace as ns

from graph_works_core.orchestrate.dispatch import DispatchFailure, DispatchResult, ObservedPlacement
from graph_works_core.orchestrate.dispatch_record import Overrides
from graph_works_core.orchestrate.reroute import RerouteResult
from graph_works_core.orchestrate.wait import Absorbed, WaitResult
from graph_works_core.work.commands import DispatchExplanation, OpenDecision
from graph_works_core.work.reconcile import CitedDecision, CommitRef, LandedSibling, ReconcileContext
from graph_works_core.workspace.dispatch import packaged_rule, resolve_dispatch
from graph_works_wire import config as wire_config
from graph_works_wire import work
from samples_work import BUNDLE, archive_run, next_result, status
from work_tracker_okf.decisions import Decision


def _wait_message(**over: object) -> dict:
    base = {
        "id": "m1",
        "type": "worker_done",
        "subject": "s",
        "body": "b",
        "from_": "term_1",
        "created_at": "2026-09-27T18:00:00Z",
        "payload": {"dispatchId": "ctx_1"},
        "payload_raw": None,
    }
    return {**base, **over}


def test_wait_payload_exact_event_shape() -> None:
    result = WaitResult(
        status="event",
        run_id="run_1",
        delivery_id="dlv_1",
        messages=(_wait_message(),),
        absorbed=(Absorbed("m0", "worker_done", "ctx_0", "duplicate-completion"),),
        self_acked=1,
        rebound=False,
        sleep_gap_s=None,
        waited_s=312,
    )
    assert work.wait_payload(result) == {
        "status": "event",
        "run_id": "run_1",
        "delivery_id": "dlv_1",
        "messages": [
            {
                "id": "m1",
                "type": "worker_done",
                "subject": "s",
                "body": "b",
                "from": "term_1",
                "created_at": "2026-09-27T18:00:00Z",
                "payload": {"dispatchId": "ctx_1"},
            }
        ],
        "absorbed": [
            {"message_id": "m0", "type": "worker_done", "dispatch_id": "ctx_0", "reason": "duplicate-completion"}
        ],
        "self_acked": 1,
        "rebound": False,
        "sleep_gap": None,
        "waited_s": 312,
        "pending_questions": None,
        "liveness": None,
    }


def test_wait_payload_timeout_with_sleep_gap_and_raw_payload() -> None:
    result = WaitResult(
        status="timeout",
        run_id="run_1",
        delivery_id=None,
        messages=(_wait_message(payload=None, payload_raw="{bad"),),
        absorbed=(),
        self_acked=0,
        rebound=True,
        sleep_gap_s=3000,
        waited_s=600,
    )
    payload = work.wait_payload(result)
    assert payload["sleep_gap"] == {"seconds": 3000}
    assert payload["messages"][0]["payload"] == "{bad"
    assert payload["rebound"] is True and payload["pending_questions"] is None and payload["liveness"] is None
    json.dumps(payload)


def test_worker_dispatch_payload_exact_success_shape() -> None:
    result = DispatchResult(
        status="dispatched",
        key="work/a#execute",
        run_id="run_1",
        task_id="task_1",
        task_title="Implement A",
        display_name="A",
        dispatch_id="ctx_1",
        terminal="term_1",
        placement=ObservedPlacement("create", "/wt", "feature/a", "A", "repo_1", "parent_1", True, ("created",)),
        recorded="written",
        probe="submitted-heartbeat",
        record_path="/record.json",
    )
    payload = work.dispatch_payload(result)
    assert set(payload) == {
        "ok",
        "status",
        "key",
        "task_id",
        "task_title",
        "display_name",
        "dispatch_id",
        "run_id",
        "terminal",
        "placement",
        "recorded",
        "probe",
        "record_path",
        "failure",
    }
    assert "error" not in payload
    assert payload == {
        "ok": True,
        "status": "dispatched",
        "key": "work/a#execute",
        "task_id": "task_1",
        "task_title": "Implement A",
        "display_name": "A",
        "dispatch_id": "ctx_1",
        "run_id": "run_1",
        "terminal": "term_1",
        "placement": {
            "action": "create",
            "path": "/wt",
            "branch": "feature/a",
            "display_name": "A",
            "repo_id": "repo_1",
            "parent_worktree_id": "parent_1",
            "lineage_set": True,
            "notes": ["created"],
            "start_sha": None,
        },
        "recorded": "written",
        "probe": "submitted-heartbeat",
        "record_path": "/record.json",
        "failure": None,
    }
    assert json.loads(json.dumps(payload)) == payload


def test_dispatch_resolution_keyword_remains_supported() -> None:
    resolution = resolve_dispatch({"variant": "single"}, rules=())
    assert work.dispatch_payload(resolution=resolution) == work.dispatch_payload(resolution)


def test_worker_dispatch_payload_failure_shape() -> None:
    result = DispatchResult(
        status=None,
        key="work/a#execute",
        run_id="run_1",
        failure=DispatchFailure("launch", "unsent", "worker did not launch", task_id="task_1", refusal="unavailable"),
    )
    payload = work.dispatch_payload(result)
    assert payload["ok"] is False
    assert payload["placement"] is None
    assert payload["failure"] == {
        "step": "launch",
        "reason": "unsent",
        "detail": "worker did not launch",
        "task_id": "task_1",
        "dispatch_id": None,
        "refusal": "unavailable",
    }
    assert "error" not in payload
    assert json.loads(json.dumps(payload)) == payload


def test_reroute_payload_success_and_failure_shapes() -> None:
    success = RerouteResult(
        status="rerouted",
        key="work/a#execute",
        run_id="run_2",
        reason="retry",
        superseded_task_id="task_1",
        superseded_dispatch_id="ctx_1",
        overrides=Overrides(agent="codex", model="gpt-6-sol", effort="high"),
        record_path="/record.json",
    )
    payload = work.reroute_payload(success)
    assert set(payload) == {
        "ok",
        "status",
        "key",
        "run_id",
        "reason",
        "superseded_task_id",
        "superseded_dispatch_id",
        "overrides",
        "record_path",
        "failure",
    }
    assert payload == {
        "ok": True,
        "status": "rerouted",
        "key": "work/a#execute",
        "run_id": "run_2",
        "reason": "retry",
        "superseded_task_id": "task_1",
        "superseded_dispatch_id": "ctx_1",
        "overrides": {"agent": "codex", "model": "gpt-6-sol", "effort": "high"},
        "record_path": "/record.json",
        "failure": None,
    }
    assert "error" not in payload
    assert json.loads(json.dumps(payload)) == payload

    failure = RerouteResult(
        status=None,
        key="work/a#execute",
        run_id="run_2",
        reason="retry",
        failure=DispatchFailure("reroute", "unsent", "task unavailable"),
    )
    failed = work.reroute_payload(failure)
    assert failed["ok"] is False
    assert failed["overrides"] is None
    assert failed["failure"] == {
        "step": "reroute",
        "reason": "unsent",
        "detail": "task unavailable",
        "task_id": None,
        "dispatch_id": None,
        "refusal": None,
    }
    assert "error" not in failed
    assert json.loads(json.dumps(failed)) == failed


def test_archive_payload_wiki_block_empty_planned_applied() -> None:
    empty = work.archive_payload(archive_run(applied=False), dry_run=True)["wiki"]
    assert empty == {
        "tokens": [],
        "path_mapping": {},
        "skipped": [],
        "refusals": [],
        "archived": [],
        "indexes": [],
        "failures": [],
    }

    planned = work.archive_payload(archive_run(applied=False, wiki=True), dry_run=True)["wiki"]
    assert planned["tokens"] == ["sources/one"]
    assert planned["path_mapping"] == {"sources/one.md": "sources/_archive/one.md"}
    assert planned["skipped"] == [{"token": "sources/two", "reason": "not-eligible", "detail": "d"}]
    assert planned["archived"] == [] and planned["indexes"] == [] and planned["failures"] == []

    applied = work.archive_payload(archive_run(applied=True, wiki=True), dry_run=False)["wiki"]
    assert applied["archived"] == ["sources/one"]
    assert applied["indexes"] == ["sources/_archive/index.md"]
    assert applied["failures"] == ["sources/x.md: io -- boom"]
    assert applied["refusals"] == [{"path": "sources/r.md", "kind": "k", "detail": "late"}]


def test_rollup_projects_open_paths() -> None:
    payload = work._rollup(SimpleNamespace(total=2, terminal=1, open_paths=("work/feature-a",)))
    assert payload == {"total": 2, "terminal": 1, "open_paths": ["work/feature-a"]}


def test_status_payload_carries_not_started() -> None:
    assert work.status_payload(status(resume=False))["not_started"] == 2


def test_normalized_payload_is_path_keyed_and_uses_canonical_source_fields() -> None:
    parent = SimpleNamespace(
        path="work/release-r",
        ref=SimpleNamespace(source_id="design", resource="/work/release-r/references/01-design.md"),
    )
    selected = SimpleNamespace(
        path="work/release-r/children/epic-e",
        ref=SimpleNamespace(
            source_id="design",
            resource="/work/release-r/children/epic-e/references/01-design.md",
        ),
    )
    result = SimpleNamespace(
        selected_path=selected.path,
        application=SimpleNamespace(normalized=(parent.path, selected.path)),
        normalizations=(parent, selected),
    )

    assert work.normalized_payload(result) == [
        {"path": parent.path, "source_id": "design", "resource": parent.ref.resource},
        {"path": selected.path, "source_id": "design", "resource": selected.ref.resource},
    ]


def test_reconcile_payload_freezes_owner_and_path_keys() -> None:
    context = ReconcileContext(
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
    payload = work.reconcile_payload(context)
    assert payload["owner_path"] == "work/epic-e"
    assert payload["path"] == "work/epic-e/children/feature-a"
    assert payload["landed_siblings"][0]["path"].endswith("feature-b")
    assert "slug" not in payload and "epic_slug" not in payload


def test_projection_helpers_cover_live_and_preview_shapes(tmp_path: Path) -> None:
    finding = SimpleNamespace(code="x", severity="warn", message="m", spec="s", path="work/a.md", line=7)
    transition = SimpleNamespace(
        phase="execute",
        work_status="in-progress",
        document_status="stable",
        requires=("owner",),
        sync_plan_table=True,
        stamp_source="plan",
    )
    assert work._transition(transition)["requires"] == ["owner"]
    assert work._finding(finding)["line"] == 7
    assert work._refusal(SimpleNamespace(path="work/a", kind="bad", detail="why"))["kind"] == "bad"
    assert work._application(None) == {"applied": False, "rolled_back": False, "failures": []}

    result = SimpleNamespace(
        requested_path="work/e",
        finish_targets=(),
        selected_path="work/a",
        descent=SimpleNamespace(path=("work/e", "work/a"), leaf=None, blocked_at="work/a", reason="blocked"),
        route=SimpleNamespace(blockers=("base",)),
        dispatch_preflight=None,
    )
    assert work.descent_payload(result)["from"] == "work/e"
    assert work.next_blockers(result)[-1] == "--descend: blocked"

    application = SimpleNamespace(rolled_back=True, failures=("failed",), warnings=("warning",), ok=False)
    update = SimpleNamespace(path=tmp_path / "index.md", changed=True)
    refusal = SimpleNamespace(path="work/a", kind="conflict", detail="changed")
    stripped = SimpleNamespace(path=tmp_path / "children" / "_archive" / "index.md", changed=True)
    regen = SimpleNamespace(
        application=application,
        plans=(update,),
        mutation=SimpleNamespace(warnings=("planned",), refusals=(refusal,)),
        marker_strips=(stripped,),
    )
    assert work.regen_index_payload(regen)["rolled_back"] is True
    assert work.regen_index_payload(regen)["indexes"] == [str(update.path), str(stripped.path)]

    archive_run = SimpleNamespace(
        ok=False,
        conflict=("work/a",),
        plan=SimpleNamespace(path_mapping={"work/a": "work/_archive/a"}, warnings=("p",), refusals=(refusal,)),
        result=SimpleNamespace(written=("work/index.md", "work/a.md"), warnings=("a",), rolled_back=False, failures=()),
        wiki_plan=SimpleNamespace(tokens=(), skipped=(), moves=SimpleNamespace(moves=(), refusals=())),
        wiki=None,
        pointer_cleared=True,
        logged="archived",
    )
    archived = work.archive_payload(archive_run, dry_run=False)
    assert archived["indexes"] == ["work/index.md"] and archived["conflict"] == ["work/a"]

    mutation = SimpleNamespace(
        plan=SimpleNamespace(
            path_mapping={"work/a": "work/e/children/a"},
            writes=(SimpleNamespace(member="work/index.md"),),
            warnings=("p",),
            refusals=(refusal,),
        ),
        application=application,
    )
    assert work.path_mutation_payload(mutation)["indexes"] == ["work/index.md"]


def test_complex_payloads_project_explicit_current_fields(tmp_path: Path) -> None:
    entry = SimpleNamespace(
        id="D-001",
        number=1,
        question="Why?",
        status="answered",
        affects=("work/a",),
        decided="user",
        supersedes=None,
        prose="Answer",
        hold=None,
        phase=None,
        checkpoint=None,
    )
    owner = SimpleNamespace(owner_path="work/e", redirected_from="work/a", ledger=tmp_path / "ledger.md")
    plan = SimpleNamespace(primary=entry, superseded=SimpleNamespace(id="D-000"), refusal=None)
    decision_result = SimpleNamespace(
        owner=owner,
        plan=plan,
        application=SimpleNamespace(rolled_back=False, failures=()),
        entries=(entry,),
        counts={"answered": 1},
        warnings=("w",),
    )
    assert work.decision_payload(decision_result)["requested_path"] == "work/a"

    overturn = SimpleNamespace(
        owner=owner,
        plan=SimpleNamespace(
            decision=plan,
            filing=SimpleNamespace(filing=SimpleNamespace(path="work/t", target=tmp_path / "t.md")),
            refusal=None,
        ),
        application=SimpleNamespace(mutation=SimpleNamespace(rolled_back=False, failures=(), ok=True)),
        warnings=("w",),
    )
    assert work.overturn_payload(overturn)["follow_up_filed"] is True

    worktree = SimpleNamespace(
        action="create",
        path="/tmp/w",
        branch="b",
        base_branch="main",
        exists=False,
        parent_path="/tmp/parent",
        start_sha=None,
    )
    dispatch = SimpleNamespace(
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
        auto_merge=True,
        prompt="go",
    )
    orchestration = SimpleNamespace(
        path="work/e",
        terminal=False,
        max_parallel=2,
        slots_free=1,
        max_attend=1,
        attend_slots_free=0,
        supervise_merges=False,
        live=("x",),
        dispatches=(dispatch,),
        dispatch_repos={dispatch.key: SimpleNamespace(name="code", path=Path("/code"), source="sole")},
        preparations=(),
        plan=SimpleNamespace(
            finish_targets={},
            dispatch_resolutions={dispatch.key: resolve_dispatch({"variant": "planned"}, rules=())},
            human_checkpoints={},
        ),
        advances=(SimpleNamespace(path="work/b", reason="done", worktree="w", branch="b", mode="return"),),
        blocked=(SimpleNamespace(path="work/c", kind="dependency", reason="wait"),),
        decisions_owner_path="work/e",
        decisions_ledger_path="ledger",
        open_decisions=(entry,),
        assumed_decisions=(entry,),
        decision_counts={"open": 1},
        holds=(),
        warnings=("w",),
        code_repo="/code",
        code_repo_name="code",
        code_repo_source="sole",
    )
    orchestrate_result = work.orchestrate_payload(orchestration)
    assert orchestrate_result["max_attend"] == 1
    assert orchestrate_result["attend_slots_free"] == 0
    assert list(orchestrate_result)[:6] == [
        "path",
        "terminal",
        "max_parallel",
        "slots_free",
        "max_attend",
        "attend_slots_free",
    ]
    assert orchestrate_result["dispatches"][0]["path"] == "work/a"
    assert orchestrate_result["dispatches"][0]["auto_merge"] is True
    assert orchestrate_result["dispatches"][0]["human_checkpoints"] is None
    assert orchestrate_result["dispatches"][0]["worktree"]["parent_path"] == "/tmp/parent"
    assert orchestrate_result["advances"][0]["mode"] == "return"
    assert orchestrate_result["supervise_merges"] is False
    assert orchestrate_result["holds"] == []
    assert orchestrate_result["repo"] == {"name": "code", "path": "/code", "source": "sole"}


def test_orchestrate_dispatches_project_human_checkpoints_or_null() -> None:
    from samples_work import WORK

    busy = WORK["work.orchestrate_payload"][0]()
    assert busy["dispatches"][0]["human_checkpoints"] == {"status": "declared", "items": ["Task 2: skim"]}


def test_orchestrate_payload_carries_holds() -> None:
    decision = SimpleNamespace(
        id="D-004",
        number=4,
        question="resume?",
        status="open",
        affects=("work/f",),
        decided=None,
        supersedes=None,
        prose="",
        hold="park",
        phase="execute",
        checkpoint="/work/f/references/03-execute-checkpoint-D-004.md",
    )
    hold = SimpleNamespace(
        path="work/f", owner_path="work/f", ledger_path="/ws/okf/work/f/references/00-decisions.md", decision=decision
    )
    result = SimpleNamespace(
        path="work/e",
        terminal=False,
        slots_free=0,
        max_parallel=1,
        max_attend=1,
        attend_slots_free=0,
        supervise_merges=False,
        live=(),
        dispatches=(),
        preparations=(),
        advances=(),
        blocked=(),
        decisions_owner_path="work/e",
        decisions_ledger_path="ledger",
        open_decisions=(),
        assumed_decisions=(),
        decision_counts={},
        holds=(hold,),
        warnings=(),
        code_repo=None,
    )
    payload = work.orchestrate_payload(result)
    assert payload["holds"] == [
        {
            "path": "work/f",
            "owner_path": "work/f",
            "ledger_path": "/ws/okf/work/f/references/00-decisions.md",
            "decision": {
                "id": "D-004",
                "number": 4,
                "question": "resume?",
                "status": "open",
                "affects": ["work/f"],
                "decided": None,
                "supersedes": None,
                "prose": "",
                "hold": "park",
                "phase": "execute",
                "checkpoint": "/work/f/references/03-execute-checkpoint-D-004.md",
            },
        }
    ]


def test_work_list_payload_maps_parent_path_to_parent() -> None:
    item = ns(
        path="work/e/children/a",
        type="Feature",
        title="A",
        work_status="open",
        phase="plan",
        effort="medium",
        owner=None,
        parent_path="work/e",
        updated="2026-09-19",
    )

    assert work.work_list_payload([item]) == {
        "items": [
            {
                "path": "work/e/children/a",
                "type": "Feature",
                "title": "A",
                "work_status": "open",
                "phase": "plan",
                "effort": "medium",
                "owner": None,
                "parent": "work/e",
                "updated": "2026-09-19",
            }
        ]
    }


def test_dispatch_explain_payload_with_a_dispatch() -> None:
    resolution = resolve_dispatch({"variant": "single"}, rules=())
    row = packaged_rule("single")
    explanation = DispatchExplanation(
        path="work/a",
        attributes={"stage": "plan", "variant": "single", "has_spec": True},
        packaged_rule=row,
        rules=((row, True),),
        resolution=resolution,
        next_result=ns(route=ns(blockers=()), descent=None, dispatch_preflight=None),
    )

    payload = work.dispatch_explain_payload(explanation)

    assert payload["path"] == "work/a"
    assert payload["attributes"] == {"stage": "plan", "variant": "single", "has_spec": True}
    assert payload["packaged_rule"] == wire_config.rule_payload(row)
    assert payload["rules"] == [{**wire_config.rule_payload(row), "matched": True}]
    assert {"profile": payload["profile"], "provenance": payload["provenance"]} == work.dispatch_payload(resolution)
    assert payload["blockers"] == []


def _queue_entry(title: str, path: str, *, resolution, preflight, requires=("owner",), blockers=()):
    return ns(
        item=ns(title=title),
        result=ns(
            selected_path=path,
            state=ns(type="Feature", phase="plan", work_status="open"),
            route=ns(reason="plan it", blockers=blockers, on_dispatch=ns(requires=requires)),
            descent=None,
            dispatch_resolution=resolution,
            dispatch_preflight=preflight,
        ),
    )


def test_work_queue_payload_follows_next_payloads_rules() -> None:
    resolution = resolve_dispatch({"variant": "single"}, rules=())
    ready = _queue_entry("A", "work/a", resolution=resolution, preflight=None)
    refused = _queue_entry("B", "work/b", resolution=None, preflight="dispatch.yaml: rule 0: bad")
    blocked = ns(
        item=ns(title="E"),
        result=ns(
            selected_path="work/e",
            state=ns(type="Epic", phase="execute", work_status="in-progress"),
            route=ns(reason="waiting", blockers=("waiting on children",), on_dispatch=None),
            descent=None,
            dispatch_resolution=None,
            dispatch_preflight=None,
        ),
    )

    rows = work.work_queue_payload([ready, refused, blocked])["items"]

    assert rows[0] == {
        "path": "work/a",
        "type": "Feature",
        "title": "A",
        "phase": "plan",
        "work_status": "open",
        "skill": resolution.profile.skill,
        "mode": resolution.profile.mode,
        "reason": "plan it",
        "blockers": [],
        "requires": ["owner"],
    }
    assert (rows[1]["skill"], rows[1]["mode"], rows[1]["reason"], rows[1]["requires"]) == (None, None, None, [])
    assert rows[1]["blockers"] == ["dispatch.yaml: rule 0: bad"]
    assert (rows[2]["skill"], rows[2]["requires"], rows[2]["blockers"]) == (None, [], ["waiting on children"])


def test_dispatch_explain_payload_without_a_dispatch() -> None:
    explanation = DispatchExplanation(
        path="work/e",
        attributes=None,
        packaged_rule=None,
        rules=(),
        resolution=None,
        next_result=ns(route=ns(blockers=("waiting on children",)), descent=None, dispatch_preflight="bad"),
    )

    payload = work.dispatch_explain_payload(explanation)

    assert payload["attributes"] is None and payload["packaged_rule"] is None
    assert payload["profile"] is None and payload["provenance"] is None
    assert payload["blockers"] == ["waiting on children", "bad"]


def test_open_decisions_payload_reuses_the_decision_projection() -> None:
    entry = Decision(id="D-001", number=1, question="q", status="open", affects=("work/a", "packages/a"))
    ledger = Path("/b/work/e/references/00-decisions.md")
    record = OpenDecision(owner_path="work/e", ledger=ledger, held=("work/a",), decision=entry)

    assert work.open_decisions_payload([record]) == {
        "decisions": [
            {
                "owner_path": "work/e",
                "ledger_path": str(ledger),
                "held": ["work/a"],
                "entry": work._decision(entry),
            }
        ]
    }
    assert work.open_decisions_payload([]) == {"decisions": []}


def test_finish_target_projection_is_explicit_and_ordered() -> None:
    from graph_works_core.workspace.finish import FinishTarget
    from graph_works_core.workspace.repos import ItemRepo
    from samples_work import BUNDLE, next_result

    result = next_result(full=True)
    result.finish_targets = (
        FinishTarget(ItemRepo("core", Path("/core"), "frontmatter"), "/core/epic", "epic/a", "main", "/core"),
        FinishTarget(ItemRepo(None, None, "sole"), "/ui/epic", "epic/a", "trunk", None),
    )
    result.state.phase = "finish"
    assert work.next_payload(result, bundle_root=BUNDLE)["finish_targets"] == [
        {
            "repo": {"name": "core", "path": "/core", "source": "frontmatter"},
            "worktree": "/core/epic",
            "source_branch": "epic/a",
            "target_branch": "main",
            "target_worktree": "/core",
        },
        {
            "repo": {"name": None, "path": None, "source": "sole"},
            "worktree": "/ui/epic",
            "source_branch": "epic/a",
            "target_branch": "trunk",
            "target_worktree": None,
        },
    ]


def test_next_payload_projects_assembled_guidance_without_text() -> None:
    payload = work.next_payload(next_result(full=True), bundle_root=BUNDLE)
    assert payload["guidance"] == [
        {"path": "/adrs/x.md", "id": "D1", "kind": "claim", "why": "package directory packages/a", "tokens": 5}
    ]
    assert payload["guidance_warnings"] == ["no graph"]
    assert payload["guidance_file"] == str(Path("/ws/okf/work/a/references/guidance-design.md"))
    json.dumps(payload)


def test_next_payload_always_carries_the_empty_guidance_form() -> None:
    payload = work.next_payload(next_result(full=False), bundle_root=BUNDLE)
    assert (payload["guidance"], payload["guidance_warnings"], payload["guidance_file"]) == ([], [], None)
    json.dumps(payload)


def test_ask_payload_shapes_success_and_refusal() -> None:
    from samples_work import WORK

    ok = WORK["work.ask_payload"][0]()
    assert ok == {
        "ok": True,
        "applied": True,
        "payload": {
            "path": str(Path("/ws/okf/work/a/references/asks/plan-001-choice.json")),
            "resource": "/work/a/references/asks/plan-001-choice.json",
        },
        "orca": {
            "question": "Pick.\n\ngw-ask: /work/a/references/asks/plan-001-choice.json",
            "options": "merge,hold",
        },
        "refusals": [],
    }
    refused = WORK["work.ask_payload"][1]()
    assert refused["ok"] is False and refused["payload"] is None and refused["orca"] is None
    assert refused["refusals"] == ["option-count"]


def test_ask_answer_payload_shapes_success_and_refusal() -> None:
    from samples_work import WORK

    ok = WORK["work.ask_answer_payload"][0]()
    assert ok["ok"] is True and ok["changed"] is True and ok["applied"] is True
    assert ok["payload"]["resource"] == "/work/a/references/asks/plan-001-choice.json"
    assert ok["reply_body"].startswith('{"ask": ')
    refused = WORK["work.ask_answer_payload"][1]()
    assert refused == {
        "ok": False,
        "applied": False,
        "changed": False,
        "payload": None,
        "reply_body": None,
        "refusals": ["payload-invalid"],
    }


def test_pin_detached_worktree_projection():
    action = SimpleNamespace(
        action="pin-detached",
        path=None,
        branch=None,
        base_branch="epic/x",
        exists=None,
        parent_path="/epic",
        start_sha="a" * 40,
    )
    assert work._worktree(action) == {
        "action": "pin-detached",
        "path": None,
        "branch": None,
        "base_branch": "epic/x",
        "exists": None,
        "parent_path": "/epic",
        "start_sha": "a" * 40,
    }


def test_reader_receipt_payload_freezes_attempt_and_outcome_fields() -> None:
    from graph_works_core.orchestrate.placement import ReaderRecord
    from work_tracker_okf.placement import ReaderObservation, ReaderReceiptPlan

    observation = ReaderObservation("task_1", "ctx_1", "key", "repo", "/reader", "a" * 40)
    plan = ReaderReceiptPlan("work/a", "work/a", "design", "design", observation, None, "")
    payload = work.reader_receipt_payload(ReaderRecord(plan, Path("/cache/ctx_1.json"), True, False))
    assert payload == {
        "path": "work/a",
        "root": "work/a",
        "expected_phase": "design",
        "current_phase": "design",
        "observation": {
            "task_id": "task_1",
            "dispatch_id": "ctx_1",
            "dispatch_key": "key",
            "repo": "repo",
            "worktree": "/reader",
            "start_sha": "a" * 40,
        },
        "receipt_path": str(Path("/cache/ctx_1.json")),
        "written": True,
        "replayed": False,
        "conflict": None,
        "refusal": None,
    }
    refused = ReaderReceiptPlan("work/a", "work/a", "design", None, observation, "unknown-path", "missing")
    payload = work.reader_receipt_payload(ReaderRecord(refused, None, False, False))
    assert payload["receipt_path"] is None and payload["current_phase"] is None
    assert payload["refusal"] == {"reason": "unknown-path", "detail": "missing"}
    payload = work.reader_receipt_payload(
        ReaderRecord(plan, Path("/cache/ctx_1.json"), False, False, "attempt-mismatch")
    )
    assert payload["conflict"] == "attempt-mismatch"
