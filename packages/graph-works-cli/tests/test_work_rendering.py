"""Explicit path-native JSON projections and stream helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_core.work.reconcile import CitedDecision, CommitRef, LandedSibling, ReconcileContext


@pytest.fixture(autouse=True)
def _json_mode_default() -> object:
    """`fail()` reads `_JSON_MODE`/`_COMMAND_NAME`, which Click's own
    `--json` parsing sets -- these unit tests call `fail()` directly, so set
    a default the way a non-`--json` invocation would, and reset both after
    so a test run alongside CLI-level tests (which do set `_COMMAND_NAME`
    through a real invocation) cannot leak into this file's assertions."""
    mode_token = rendering._JSON_MODE.set(False)
    name_token = rendering._COMMAND_NAME.set("")
    yield
    rendering._JSON_MODE.reset(mode_token)
    rendering._COMMAND_NAME.reset(name_token)


def test_split_csv_trims_and_drops_empty_fragments() -> None:
    assert rendering.split_csv(" a, ,b,, ") == ["a", "b"]


def test_fail_writes_only_to_stderr_and_carries_the_code(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as caught:
        rendering.fail("nope", reason="unresolved", code=exit_codes.AMBIGUOUS)
    captured = capsys.readouterr()
    assert caught.value.exit_code == exit_codes.AMBIGUOUS
    assert captured.out == "" and "nope" in captured.err


def test_fail_emits_the_envelope_on_stdout_in_json_mode(capsys: pytest.CaptureFixture[str]) -> None:
    rendering._JSON_MODE.set(True)
    with pytest.raises(typer.Exit) as caught:
        rendering.fail("nope", reason="unresolved", code=exit_codes.AMBIGUOUS, payload={"a": 1})
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert set(doc) == {"error"}
    assert doc["error"] == {
        "command": "",
        "reason": "unresolved",
        "message": "nope",
        "exit_code": exit_codes.AMBIGUOUS,
        "payload": {"a": 1},
    }
    assert caught.value.exit_code == exit_codes.AMBIGUOUS
    assert "nope" in captured.err


def test_fail_rejects_a_reason_outside_the_closed_vocabulary() -> None:
    rendering._JSON_MODE.set(True)
    with pytest.raises(AssertionError):
        rendering.fail("nope", reason="not-a-real-reason")  # type: ignore[arg-type]


def test_fail_asserts_when_json_mode_was_never_declared() -> None:
    rendering._JSON_MODE.set(None)
    with pytest.raises(AssertionError, match="json_option"):
        rendering.fail("nope", reason="usage")


def test_rollup_projects_open_paths() -> None:
    payload = rendering._rollup(SimpleNamespace(total=2, terminal=1, open_paths=("work/feature-a",)))
    assert payload == {"total": 2, "terminal": 1, "open_paths": ["work/feature-a"]}


def test_render_next_uses_path_and_work_status(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "selected_path": "work/feature-a",
        "kind": "Feature",
        "work_status": "open",
        "phase": "design",
        "normalized": None,
        "descent": None,
        "action": None,
        "artifact": None,
        "blockers": [],
    }
    rendering.render_next(SimpleNamespace(warnings=()), payload)
    assert "work/feature-a: kind=Feature work_status=open" in capsys.readouterr().out


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

    assert rendering.normalized_payload(result) == [
        {"path": parent.path, "source_id": "design", "resource": parent.ref.resource},
        {"path": selected.path, "source_id": "design", "resource": selected.ref.resource},
    ]


def test_render_status_uses_path_keyed_resume(capsys: pytest.CaptureFixture[str]) -> None:
    rendering.render_status(
        {
            "total": 1,
            "by_work_status": {"open": 1},
            "by_type": {"Feature": 1},
            "by_phase": {},
            "children": {},
            "resume": {"primary": {"path": "work/feature-a", "title": "A"}, "alternatives": []},
        }
    )
    assert "resume: work/feature-a" in capsys.readouterr().out


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
    payload = rendering.reconcile_payload(context)
    assert payload["owner_path"] == "work/epic-e"
    assert payload["path"] == "work/epic-e/children/feature-a"
    assert payload["landed_siblings"][0]["path"].endswith("feature-b")
    assert "slug" not in payload and "epic_slug" not in payload


def test_render_decision_names_owner_and_request(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "owner_path": "work/epic-e",
        "requested_path": "work/epic-e/children/feature-a",
        "entry": {"id": "D-001", "status": "open"},
        "superseded": None,
        "warnings": [],
    }
    rendering.render_decision_write(payload, "appended")
    assert "work/epic-e (requested work/epic-e/children/feature-a)" in capsys.readouterr().out


def test_dense_human_renderers_cover_every_optional_group(capsys: pytest.CaptureFixture[str]) -> None:
    rendering.echo_wrapped("  blocked: ", "first\n second")
    rendering.render_next(
        SimpleNamespace(warnings=("careful",)),
        {
            "normalized": [{"path": "work/a", "source_id": "design", "resource": "/work/a/references/01-design.md"}],
            "selected_path": "work/a",
            "kind": "Feature",
            "work_status": "open",
            "phase": "design",
            "descent": {"path": ["work/e", "work/a"]},
            "action": {"skill": "superpowers:brainstorming", "reason": "design"},
            "artifact": {"path": "/tmp/01-design.md"},
            "blockers": ["one\ntwo"],
        },
    )
    rendering.render_advance(
        {
            "path": "work/a",
            "phase": "execute",
            "work_status": "in-progress",
            "changes": {"phase": ["plan", "execute"]},
            "stamped": {"plan": "/work/a/references/02-plan.md"},
            "results_path": "/tmp/results.md",
            "blockers": ["wait"],
            "repo_note": "partial",
        }
    )
    rendering.render_status(
        {
            "total": 2,
            "by_work_status": {"open": 2},
            "by_type": {"Feature": 2},
            "by_phase": {"design": 2},
            "children": {"work/e": {"terminal": 1, "total": 2}},
            "resume": {
                "primary": {"path": "work/a", "title": "A"},
                "alternatives": [{"path": "work/b", "title": "B"}],
            },
        }
    )
    rendering.render_lint(
        SimpleNamespace(
            findings=(
                SimpleNamespace(severity="warn", code="w", message="warning"),
                SimpleNamespace(severity="error", code="e", message="error"),
            )
        )
    )
    decision = {"id": "D-001", "status": "answered", "question": "Why?"}
    rendering.render_decision_write(
        {
            "owner_path": "work/e",
            "requested_path": "work/c",
            "entry": decision,
            "superseded": "D-000",
            "follow_up": {"path": "work/t", "page_path": "/tmp/t.md"},
        },
        "appended",
    )
    rendering.render_decision_list(
        {
            "owner_path": "work/e",
            "requested_path": "work/c",
            "ledger_path": "/tmp/ledger.md",
            "entries": [decision, {"id": "D-002", "status": None, "question": ""}],
            "counts": {"answered": 1},
        }
    )
    rendering.render_orchestrate(
        {
            "path": "work/e",
            "terminal": False,
            "slots_free": 1,
            "max_parallel": 2,
            "supervise_merges": False,
            "dispatches": [
                {
                    "key": "work/a#execute",
                    "skill": "tdd",
                    "mode": "worktree",
                    "model": "m",
                    "worktree": {"action": "create"},
                }
            ],
            "advances": [{"path": "work/a", "reason": "ready", "mode": "advance"}],
            "blocked": [{"path": "work/b", "kind": "dependency", "reason": "one\ntwo"}],
            "decisions": {"open": [decision]},
            "warnings": ["partial"],
        }
    )
    rendering.render_reconcile(
        {
            "path": "work/a",
            "owner_path": "work/e",
            "spec_path": "/tmp/spec.md",
            "spec_anchor_commit": "abc",
            "anchor_source": "history",
            "commit_range": "abc..HEAD",
            "touched_paths": ["packages/a"],
            "landed_siblings": [{"path": "work/b", "resolved_in": "def"}],
            "commits_since": [{"sha": "123456789", "subject": "change"}],
            "cited_decisions": [decision],
            "contradictions": [decision],
            "has_open_decision": True,
            "diff_command": "git diff",
            "warnings": ["partial"],
        }
    )
    captured = capsys.readouterr()
    assert "CONTRADICTION" in captured.out
    assert "partial" in captured.err


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
    assert rendering._transition(transition)["requires"] == ["owner"]
    assert rendering._finding(finding)["line"] == 7
    assert rendering._refusal(SimpleNamespace(path="work/a", kind="bad", detail="why"))["kind"] == "bad"
    assert rendering._application(None) == {"applied": False, "rolled_back": False, "failures": []}

    result = SimpleNamespace(
        requested_path="work/e",
        selected_path="work/a",
        descent=SimpleNamespace(path=("work/e", "work/a"), leaf=None, blocked_at="work/a", reason="blocked"),
        route=SimpleNamespace(blockers=("base",)),
    )
    assert rendering.descent_payload(result)["from"] == "work/e"
    assert rendering.next_blockers(result)[-1] == "--descend: blocked"

    application = SimpleNamespace(rolled_back=True, failures=("failed",), warnings=("warning",), ok=False)
    update = SimpleNamespace(path=tmp_path / "index.md", changed=True)
    refusal = SimpleNamespace(path="work/a", kind="conflict", detail="changed")
    regen = SimpleNamespace(
        application=application,
        plans=(update,),
        mutation=SimpleNamespace(warnings=("planned",), refusals=(refusal,)),
    )
    assert rendering.regen_index_payload(regen)["rolled_back"] is True

    archive_run = SimpleNamespace(
        ok=False,
        conflict=("work/a",),
        plan=SimpleNamespace(path_mapping={"work/a": "work/_archive/a"}, warnings=("p",), refusals=(refusal,)),
        result=SimpleNamespace(written=("work/index.md", "work/a.md"), warnings=("a",), rolled_back=False, failures=()),
        pointer_cleared=True,
        logged="archived",
    )
    archived = rendering.archive_payload(archive_run, dry_run=False)
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
    assert rendering.path_mutation_payload(mutation)["indexes"] == ["work/index.md"]


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
    assert rendering.decision_payload(decision_result)["requested_path"] == "work/a"

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
    assert rendering.overturn_payload(overturn)["follow_up_filed"] is True

    worktree = SimpleNamespace(action="create", path="/tmp/w", branch="b", base_branch="main", exists=False)
    dispatch = SimpleNamespace(
        key="work/a#execute",
        slug="work/a",
        phase="execute",
        kind="Feature",
        effort="medium",
        skill="tdd",
        mode="worktree",
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
        permission_mode="full",
        supervise_merges=False,
        live=("x",),
        dispatches=(dispatch,),
        advances=(SimpleNamespace(path="work/b", reason="done", worktree="w", branch="b", mode="return"),),
        blocked=(SimpleNamespace(path="work/c", kind="dependency", reason="wait"),),
        decisions_owner_path="work/e",
        decisions_ledger_path="ledger",
        open_decisions=(entry,),
        assumed_decisions=(entry,),
        decision_counts={"open": 1},
        warnings=("w",),
    )
    orchestrate_result = rendering.orchestrate_payload(orchestration)
    assert orchestrate_result["dispatches"][0]["path"] == "work/a"
    assert orchestrate_result["advances"][0]["mode"] == "return"
    assert orchestrate_result["supervise_merges"] is False


def test_fail_preserves_an_explicit_cause(capsys: pytest.CaptureFixture[str]) -> None:
    cause = ValueError("root")
    with pytest.raises(typer.Exit) as caught:
        rendering.fail("bad", reason="io", cause=cause)
    assert caught.value.__cause__ is cause
    assert "bad" in capsys.readouterr().err


def test_render_orchestrate_prints_supervise_merges_only_when_true(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "path": "work/e",
        "terminal": False,
        "slots_free": 1,
        "max_parallel": 2,
        "supervise_merges": False,
        "dispatches": [],
        "advances": [],
        "blocked": [],
        "decisions": {"open": []},
        "warnings": [],
    }
    rendering.render_orchestrate(payload)
    assert "supervise_merges" not in capsys.readouterr().out

    rendering.render_orchestrate({**payload, "supervise_merges": True})
    assert "supervise_merges=True" in capsys.readouterr().out
