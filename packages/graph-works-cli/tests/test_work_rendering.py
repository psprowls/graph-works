"""Explicit path-native JSON projections and stream helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import typer
from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_wire import work as wire_work


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


def test_render_next_uses_path_and_work_status(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "selected_path": "work/feature-a",
        "kind": "Feature",
        "work_status": "open",
        "phase": "design",
        "normalized": None,
        "descent": None,
        "dispatch": None,
        "action": None,
        "artifact": None,
        "blockers": [],
    }
    rendering.render_next(SimpleNamespace(warnings=()), payload)
    assert "work/feature-a: kind=Feature work_status=open" in capsys.readouterr().out


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
            "dispatch": None,
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
                    "agent": "codex",
                    "reasoning_effort": None,
                    "provenance": {},
                    "model": "m",
                    "worktree": {"action": "create"},
                }
            ],
            "advances": [{"path": "work/a", "reason": "ready", "mode": "advance"}],
            "blocked": [{"path": "work/b", "kind": "dependency", "reason": "one\ntwo"}],
            "decisions": {"open": [decision]},
            "holds": [],
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
        "holds": [],
        "warnings": [],
    }
    rendering.render_orchestrate(payload)
    assert "supervise_merges" not in capsys.readouterr().out

    rendering.render_orchestrate({**payload, "supervise_merges": True})
    assert "supervise_merges=True" in capsys.readouterr().out


def test_render_orchestrate_prints_holds(capsys: pytest.CaptureFixture[str]) -> None:
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
    payload = wire_work.orchestrate_payload(result)
    rendering.render_orchestrate(payload)
    assert "  hold work/f D-004 (park at execute): resume?" in capsys.readouterr().out


def test_decision_list_shows_the_hold_shape(capsys: pytest.CaptureFixture[str]) -> None:
    rendering.render_decision_list(
        {
            "owner_path": "work/f",
            "requested_path": "work/f",
            "ledger_path": "l",
            "counts": {"open": 2},
            "entries": [
                {"id": "D-001", "status": "open", "question": "q", "hold": "skip", "phase": "plan"},
                {"id": "D-002", "status": "open", "question": "r", "hold": None, "phase": None},
            ],
        }
    )
    out = capsys.readouterr().out
    assert "  D-001  open  [skip at plan] — q" in out
    assert "  D-002  open — r" in out
