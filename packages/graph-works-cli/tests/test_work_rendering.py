"""`gw work` projections — the CLI's public JSON contract, stated key by key."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import typer
from graph_works_cli import exit_codes
from graph_works_cli.work_cli import rendering
from graph_works_core.work.reconcile import CitedDecision, CommitRef, LandedSibling, ReconcileContext


def test_split_csv_trims_and_drops_empty_fragments() -> None:
    assert rendering.split_csv(" a , ,b ") == ["a", "b"]
    assert rendering.split_csv("") == []


def test_fail_writes_to_stderr_and_carries_the_code(capsys) -> None:
    with pytest.raises(typer.Exit) as caught:
        rendering.fail("bad slug", code=exit_codes.AMBIGUOUS)
    assert caught.value.exit_code == exit_codes.AMBIGUOUS
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Error: bad slug\n"


def test_echo_wrapped_hanging_indents_continuation_lines(capsys) -> None:
    rendering.echo_wrapped("  blocked: ", "first line\n  second line\nthird line")
    captured = capsys.readouterr()
    pad = " " * len("  blocked: ")
    assert captured.out == f"  blocked: first line\n{pad}second line\n{pad}third line\n"


def test_rollup_projects_totals_terminal_and_open_slugs() -> None:
    rollup = SimpleNamespace(total=3, terminal=1, open_slugs=("a", "b"))
    assert rendering._rollup(rollup) == {"total": 3, "terminal": 1, "open_slugs": ["a", "b"]}
    assert rendering._rollup(None) is None


def test_normalized_payload_skips_changes_that_did_not_persist() -> None:
    result = SimpleNamespace(
        application=SimpleNamespace(normalized=("child",)),
        normalizations=[
            SimpleNamespace(slug="parent", ref=SimpleNamespace(rel="parent/ref.md")),
            SimpleNamespace(slug="child", ref=SimpleNamespace(rel="child/ref.md")),
        ],
        selected_slug="child",
    )
    assert rendering.normalized_payload(result) == {"spec_doc": "child/ref.md"}


def test_next_blockers_appends_the_descend_reason_when_no_leaf_found() -> None:
    result = SimpleNamespace(
        route=SimpleNamespace(blockers=("dep block",)),
        descent=SimpleNamespace(leaf=None, reason="no dep-ready child"),
    )
    assert rendering.next_blockers(result) == ["dep block", "--descend: no dep-ready child"]


def test_render_next_prints_normalized_stamps_in_human_mode(capsys) -> None:
    payload = {
        "slug": "s",
        "kind": "Feature",
        "status": "open",
        "phase": "design",
        "normalized": {"spec_doc": "work/s/references/01-design-spec.md"},
        "descent": None,
        "action": None,
        "artifact": None,
        "blockers": [],
    }
    rendering.render_next(SimpleNamespace(warnings=()), payload)
    captured = capsys.readouterr()
    assert "[fix] stamped spec_doc: work/s/references/01-design-spec.md" in captured.out


def test_render_advance_prints_results_path_blockers_and_repo_note(capsys) -> None:
    payload = {
        "slug": "s",
        "phase": "execute",
        "status": "accepted",
        "applied": {},
        "stamped": {},
        "results_path": "work/s/references/results/01.md",
        "blockers": ["multi\nline\nblocker"],
        "repo_note": "no code repo declared",
    }
    rendering.render_advance(payload)
    captured = capsys.readouterr()
    assert "results: work/s/references/results/01.md" in captured.out
    assert "blocked: multi" in captured.out
    assert "no code repo declared" in captured.err


def test_render_advance_omits_the_repo_note_line_when_there_is_none(capsys) -> None:
    payload = {
        "slug": "s",
        "phase": "execute",
        "status": "accepted",
        "applied": {},
        "stamped": {},
        "results_path": None,
        "blockers": [],
        "repo_note": None,
    }
    rendering.render_advance(payload)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_render_status_prints_children_rollups_and_alternatives(capsys) -> None:
    payload = {
        "total": 5,
        "by_workflow_status": {"open": 5},
        "by_type": {"Feature": 5},
        "by_phase": {"plan": 5},
        "children": {"epic-a": {"total": 2, "terminal": 1, "open_slugs": ["b"]}},
        "resume": {
            "primary": {"slug": "s", "title": "T"},
            "alternatives": [{"slug": "alt", "title": "Alt"}],
        },
    }
    rendering.render_status(payload)
    captured = capsys.readouterr()
    assert "children epic-a: 1/2 terminal" in captured.out
    assert "alt: alt — Alt" in captured.out


def test_render_status_omits_the_resume_lines_when_there_is_nothing_to_resume(capsys) -> None:
    payload = {
        "total": 0,
        "by_workflow_status": {},
        "by_type": {},
        "by_phase": {},
        "children": {},
        "resume": None,
    }
    rendering.render_status(payload)
    captured = capsys.readouterr()
    assert "resume:" not in captured.out


def test_render_decision_write_prints_superseded_and_follow_up(capsys) -> None:
    payload = {
        "epic_slug": "e",
        "resolved_from": None,
        "entry": {"id": "D-002", "status": "answered"},
        "superseded": "D-001",
        "follow_up": {"slug": "2026-08-19-tech-debt-t", "page_path": "work/2026-08-19-tech-debt-t.md"},
        "warnings": ["heads up"],
    }
    rendering.render_decision_write(payload, "appended")
    captured = capsys.readouterr()
    assert "[ok] superseded D-001" in captured.out
    assert "[ok] follow-up 2026-08-19-tech-debt-t" in captured.out
    assert "heads up" in captured.err


def test_render_decision_list_prints_warnings(capsys) -> None:
    payload = {
        "epic_slug": "e",
        "resolved_from": None,
        "ledger_path": "work/e/references/00-decisions.md",
        "entries": [],
        "counts": {"open": 0},
        "warnings": ["stale ledger"],
    }
    rendering.render_decision_list(payload)
    captured = capsys.readouterr()
    assert "stale ledger" in captured.err


def test_render_lint_prints_findings_to_the_right_stream(capsys) -> None:
    from okf_io.validate import Finding

    report = SimpleNamespace(
        ok=False,
        findings=[
            Finding(code="w.one", severity="warn", message="a warning", spec="§1", path="p", line=1),
            Finding(code="e.one", severity="error", message="an error", spec="§1", path="p", line=2),
        ],
    )
    rendering.render_lint(report)
    captured = capsys.readouterr()
    assert "w.one: a warning" in captured.out
    assert "e.one: an error" in captured.err


def test_render_orchestrate_prints_advances_blocked_and_open_decisions(capsys) -> None:
    payload = {
        "slug": "root",
        "terminal": False,
        "slots_free": 1,
        "max_parallel": 2,
        "dispatches": [],
        "advances": [{"slug": "s", "reason": "stage complete", "worktree": None, "branch": None}],
        "blocked": [{"slug": "b", "kind": "Feature", "reason": "waiting on dependency"}],
        "decisions": {"open": [{"id": "D-001", "question": "q?"}]},
        "warnings": [],
    }
    rendering.render_orchestrate(payload)
    captured = capsys.readouterr()
    assert "advance s: stage complete" in captured.out
    assert "blocked b (Feature): waiting on dependency" in captured.out
    assert "open decision D-001: q?" in captured.out


def test_render_reconcile_prints_siblings_commits_decisions_and_holds(capsys) -> None:
    payload = {
        "slug": "s",
        "epic_slug": "e",
        "spec_path": "work/s/references/01-design-spec.md",
        "spec_anchor_commit": "abc1234",
        "anchor_source": "spec-git-history",
        "commit_range": "abc1234..HEAD",
        "touched_paths": ["packages/a"],
        "landed_siblings": [{"slug": "sib", "resolved_in": "deadbee", "affects": ["packages/a"]}],
        "commits_since": [{"sha": "deadbeefcafe", "subject": "feat: a"}],
        "cited_decisions": [{"id": "D-001", "status": "answered", "question": "q?"}],
        "contradictions": [{"id": "D-002", "status": "superseded", "question": "q2?"}],
        "has_open_decision": True,
        "diff_command": "git diff abc1234..HEAD -- packages/a",
        "warnings": [],
    }
    rendering.render_reconcile(payload)
    captured = capsys.readouterr()
    assert "landed: sib resolved_in deadbee" in captured.out
    assert "commit: deadbeef feat: a" in captured.out
    assert "cites: D-001 status=answered" in captured.out
    assert "CONTRADICTION: D-002 is superseded" in captured.out
    assert "held: an open decision already names this item" in captured.out
    assert "diff: git diff abc1234..HEAD -- packages/a" in captured.out


def test_reconcile_payload_keeps_every_donor_field() -> None:
    context = ReconcileContext(
        epic_slug="e",
        slug="s",
        spec_path="work/s/references/01-design-spec.md",
        spec_anchor_commit="abc1234",
        anchor_source="spec-git-history",
        commit_range="abc1234..HEAD",
        landed_siblings=(LandedSibling("sib", "deadbee", ("packages/a",)),),
        touched_paths=("packages/a",),
        commits_since=(CommitRef("deadbee", "feat: a"),),
        cited_decisions=(CitedDecision("D-001", "answered", "q?"),),
        contradictions=(CitedDecision("D-002", "superseded", "q2?"),),
        has_open_decision=True,
        diff_command="git diff abc1234..HEAD -- packages/a",
        warnings=("no repo resolved",),
    )
    payload = rendering.reconcile_payload(context)
    assert sorted(payload) == [
        "anchor_source",
        "cited_decisions",
        "commit_range",
        "commits_since",
        "contradictions",
        "diff_command",
        "epic_slug",
        "has_open_decision",
        "landed_siblings",
        "slug",
        "spec_anchor_commit",
        "spec_path",
        "touched_paths",
        "warnings",
    ]
    assert payload["landed_siblings"] == [{"slug": "sib", "resolved_in": "deadbee", "affects": ["packages/a"]}]
    assert payload["commits_since"] == [{"sha": "deadbee", "subject": "feat: a"}]
    assert json.loads(json.dumps(payload)) == payload
