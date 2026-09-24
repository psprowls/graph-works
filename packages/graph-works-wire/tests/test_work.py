"""Explicit `gw work` projections: exact keys, None-vs-empty distinctions, path keys."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace as ns

from graph_works_core.work.commands import DispatchExplanation, OpenDecision
from graph_works_core.work.reconcile import CitedDecision, CommitRef, LandedSibling, ReconcileContext
from graph_works_core.workspace.dispatch import packaged_rule, resolve_dispatch
from graph_works_wire import config as wire_config
from graph_works_wire import work
from samples_work import archive_run
from work_tracker_okf.decisions import Decision


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
        action="create", path="/tmp/w", branch="b", base_branch="main", exists=False, parent_path="/tmp/parent"
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
        prompt="go",
    )
    orchestration = SimpleNamespace(
        path="work/e",
        terminal=False,
        max_parallel=2,
        slots_free=1,
        supervise_merges=False,
        live=("x",),
        dispatches=(dispatch,),
        dispatch_repos={dispatch.key: SimpleNamespace(name="code", path=Path("/code"), source="sole")},
        preparations=(),
        plan=SimpleNamespace(dispatch_resolutions={dispatch.key: resolve_dispatch({"variant": "planned"}, rules=())}),
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
    assert orchestrate_result["dispatches"][0]["path"] == "work/a"
    assert orchestrate_result["dispatches"][0]["worktree"]["parent_path"] == "/tmp/parent"
    assert orchestrate_result["advances"][0]["mode"] == "return"
    assert orchestrate_result["supervise_merges"] is False
    assert orchestrate_result["holds"] == []
    assert orchestrate_result["repo"] == {"name": "code", "path": "/code", "source": "sole"}


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
