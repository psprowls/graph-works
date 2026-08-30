"""Error mapping and output-policy branches for the thin work CLI."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from graph_works_cli import exit_codes
from graph_works_cli.work_cli import decision, main, reconcile, rendering
from graph_works_core.workspace.errors import WorkspaceConfigError, WorkspaceError

LAYOUT = SimpleNamespace(bundle_dir=Path("/tmp/bundle"), repo_root=Path("/tmp/repo"))


def _exit_code(call) -> int:
    with pytest.raises(typer.Exit) as caught:
        call()
    return caught.value.exit_code


@pytest.mark.parametrize("error,code", [(WorkspaceConfigError("bad"), exit_codes.SCHEMA_MISMATCH), (OSError("io"), 1)])
def test_config_errors_are_mapped(monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(main, "load_workspace_config", lambda layout: (_ for _ in ()).throw(error))
    assert _exit_code(lambda: main._config(LAYOUT)) == code
    monkeypatch.setattr(decision, "load_workspace_config", lambda layout: (_ for _ in ()).throw(error))
    assert _exit_code(lambda: decision._config(LAYOUT)) == code


@pytest.mark.parametrize(
    "raw,fragment",
    [
        ("path", "key=value"),
        ("other=x,path=a,blocks=plan,needs=resolved", "unknown key"),
        ("path=a,path=b,blocks=plan,needs=resolved", "duplicate key"),
        ("path=,blocks=plan,needs=resolved", "must not be empty"),
        ("path=a", "missing blocks, needs"),
    ],
)
def test_dependency_syntax_failures(raw: str, fragment: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(lambda: main._parse_dep_spec(raw)) == 1
    assert fragment in capsys.readouterr().err


def test_dependency_vocabulary_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    issue = SimpleNamespace(code="bad-phase", detail="wrong", index=0)
    monkeypatch.setattr(main.work, "parse_dependencies", lambda raw: SimpleNamespace(issues=(issue,), edges=()))
    assert _exit_code(lambda: main._dependency_edges(["path=a,blocks=plan,needs=resolved"])) == 1
    assert "bad-phase" in capsys.readouterr().err


@pytest.mark.parametrize("error,code", [(WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH), (OSError("io"), 1)])
def test_file_maps_core_failures(monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "_config", lambda layout: object())
    monkeypatch.setattr(main, "_dependency_edges", lambda dep: ())
    monkeypatch.setattr(main.work, "run_file", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    assert (
        _exit_code(lambda: main.file("T", "Feature", "S", "", "", "", "", [], "", "", "", "", "", False, "", False))
        == code
    )


def test_file_output_and_incomplete_paths(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "_config", lambda layout: object())
    monkeypatch.setattr(main, "_dependency_edges", lambda dep: ())
    filing = SimpleNamespace(diff=lambda: "preview")
    outcome = SimpleNamespace(plan=SimpleNamespace(refusal=None, filing=filing))
    monkeypatch.setattr(main.work, "run_file", lambda *args, **kwargs: outcome)

    monkeypatch.setattr(
        rendering,
        "file_payload",
        lambda value: {
            "path": "work/a",
            "page_path": "work/a.md",
            "refusal": None,
            "detail": "",
            "indexes": ["work/index.md"],
            "logged": "- filed",
            "warnings": ["warn"],
            "applied": False,
            "rolled_back": False,
            "failures": [],
        },
    )
    main.file("T", "Feature", "S", "", "", "", "", [], "", "", "", "", "", True, "", False)
    assert "preview" in capsys.readouterr().out
    main.file("T", "Feature", "S", "", "", "", "", [], "", "", "", "", "", False, "", False)
    assert "reconciled" in capsys.readouterr().out

    monkeypatch.setattr(
        rendering,
        "file_payload",
        lambda value: {
            "path": "work/a",
            "page_path": "work/a.md",
            "refusal": None,
            "detail": "",
            "indexes": [],
            "logged": None,
            "warnings": [],
            "applied": True,
            "rolled_back": True,
            "failures": ["failed"],
        },
    )
    assert (
        _exit_code(lambda: main.file("T", "Feature", "S", "", "", "", "", [], "", "", "", "", "", False, "", False))
        == 1
    )


@pytest.mark.parametrize("name", ["status", "lint"])
def test_read_commands_map_io_failures(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "_config", lambda layout: object())
    monkeypatch.setattr(main, "resolve_repo", lambda layout: (None, None))
    monkeypatch.setattr(main.work, f"run_{name}", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("io")))
    call = (lambda: main.status("", False)) if name == "status" else (lambda: main.lint(False, "", False))
    assert _exit_code(call) == 1


def test_lint_non_ok_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "_config", lambda layout: object())
    monkeypatch.setattr(main, "resolve_repo", lambda layout: (None, None))
    report = SimpleNamespace(ok=False, findings=())
    monkeypatch.setattr(main.work, "run_lint", lambda *args, **kwargs: report)
    assert _exit_code(lambda: main.lint(False, "", True)) == exit_codes.GENERIC


@pytest.mark.parametrize(
    "error,code",
    [
        (WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH),
        (ValueError("ambiguous"), exit_codes.AMBIGUOUS),
        (OSError("io"), 1),
    ],
)
def test_next_advance_and_orchestrate_map_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: int
) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "warn_if_stale_routing", lambda: None)
    for target, call in (
        ("run_next", lambda: main.next_stage("work/a", False, "", False)),
        ("run_stage_advance", lambda: main.advance("work/a", "", "", "", "", "", "", "", False, False, "", False)),
        ("run_orchestrate", lambda: main.orchestrate("work/a", "", "", False)),
    ):
        owner = main.work if target == "run_next" else main
        monkeypatch.setattr(owner, target, lambda *args, _error=error, **kwargs: (_ for _ in ()).throw(_error))
        assert _exit_code(call) == code


def test_next_json_warns_and_exits_on_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "warn_if_stale_routing", lambda: None)
    result = SimpleNamespace(route=SimpleNamespace(dispatch=None), warnings=("warn",))
    monkeypatch.setattr(main.work, "run_next", lambda *args, **kwargs: result)
    monkeypatch.setattr(rendering, "next_payload", lambda *args, **kwargs: {"blockers": ["blocked"]})
    assert _exit_code(lambda: main.next_stage("work/a", False, "", True)) == exit_codes.GENERIC


def test_advance_output_policy_branches(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "warn_if_stale_routing", lambda: None)
    result = SimpleNamespace(outcome=SimpleNamespace(plan=SimpleNamespace(diff=lambda: "preview")))
    monkeypatch.setattr(main, "run_stage_advance", lambda *args, **kwargs: result)
    payload = {
        "refusal": None,
        "applied": False,
        "rolled_back": False,
        "failures": [],
        "warnings": ["warn"],
        "repo_note": "note",
    }
    monkeypatch.setattr(rendering, "advance_payload", lambda *args: payload)
    main.advance("work/a", "", "", "", "", "", "", "", False, False, "", True)
    assert "note" in capsys.readouterr().err
    main.advance("work/a", "", "", "", "", "", "", "", False, True, "", False)
    assert "preview" in capsys.readouterr().out
    payload["refusal"] = {"reason": "bad", "detail": "why"}
    assert _exit_code(lambda: main.advance("work/a", "", "", "", "", "", "", "", False, False, "", False)) == 1


def test_regen_index_all_output_policies(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main.work, "run_regen_indexes", lambda *args, **kwargs: object())
    payload = {
        "warnings": ["w"],
        "refusals": [],
        "applied": False,
        "rolled_back": False,
        "failures": [],
        "indexes": ["i"],
    }
    monkeypatch.setattr(rendering, "regen_index_payload", lambda value: payload)
    main.regen_index(True, "", False)
    assert "would reconcile" in capsys.readouterr().out
    payload["indexes"] = []
    main.regen_index(False, "", False)
    assert "nothing to do" in capsys.readouterr().out
    payload["refusals"] = ["bad"]
    assert _exit_code(lambda: main.regen_index(False, "", False)) == 1


def test_finish_path_mutation_all_policies(capsys: pytest.CaptureFixture[str]) -> None:
    base = {
        "warnings": ["warn"],
        "refusals": [],
        "failures": [],
        "applied": False,
        "rolled_back": False,
        "path_mapping": {"work/a": "work/e/children/a"},
    }
    main._finish_path_mutation(base, dry_run=True, json_output=False)
    assert "work/a ->" in capsys.readouterr().out
    main._finish_path_mutation(base, dry_run=False, json_output=False)
    assert "applied" in capsys.readouterr().out
    main._finish_path_mutation(base, dry_run=False, json_output=True)
    base["refusals"] = [{"path": "work/a", "kind": "bad", "detail": "why"}]
    assert _exit_code(lambda: main._finish_path_mutation(base, dry_run=False, json_output=False)) == 1


@pytest.mark.parametrize("name", ["reparent", "adopt"])
@pytest.mark.parametrize("error,code", [(WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH), (OSError("io"), 1)])
def test_mutation_commands_map_workspace_and_io_failures(
    monkeypatch: pytest.MonkeyPatch, name: str, error: Exception, code: int
) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    target = {"reparent": "run_reparent", "adopt": "run_release_adoption"}[name]
    monkeypatch.setattr(main.work, target, lambda *args, **kwargs: (_ for _ in ()).throw(error))
    calls = {
        "reparent": lambda: main.reparent("work/a", "work/e", False, "", False),
        "adopt": lambda: main.adopt("work/e", "work/r", False, "", False),
    }
    assert _exit_code(calls[name]) == code


@pytest.mark.parametrize("error,code", [(WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH), (OSError("io"), 1)])
def test_regen_index_maps_workspace_and_io_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: int
) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main.work, "run_regen_indexes", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    assert _exit_code(lambda: main.regen_index(False, "", False)) == code


def test_decision_emit_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rendering, "render_decision_write", lambda payload, verb: None)
    base: dict[str, object] = {
        "warnings": ["warn"],
        "refusal": None,
        "applied": False,
        "rolled_back": False,
        "failures": [],
    }
    decision._emit(base, verb="appended", json_output=False)
    decision._emit(base, verb="appended", json_output=True)
    base["refusal"] = "bad"
    assert _exit_code(lambda: decision._emit(base, verb="x", json_output=False)) == 1
    base["refusal"] = None
    base["applied"] = True
    base["failures"] = ["failed"]
    assert _exit_code(lambda: decision._emit(base, verb="x", json_output=False)) == 1


@pytest.mark.parametrize("error,code", [(ValueError("unknown"), exit_codes.AMBIGUOUS), (OSError("io"), 1)])
def test_decision_verbs_map_target_and_io_errors(monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(decision, "resolve_workspace", lambda workspace: LAYOUT)
    calls = (
        ("run_decision_add", lambda: decision.add("work/a", "q", "open", "", "", "", "", "user", False, "", False)),
        ("run_decision_list", lambda: decision.list_cmd("work/a", "", "", "", "", False)),
        ("run_decision_answer", lambda: decision.answer("work/a", "D-001", "a", "", "user", False, "", False)),
        (
            "run_decision_supersede",
            lambda: decision.supersede("work/a", "D-001", "q", "a", "", "", "user", False, "", False),
        ),
    )
    for target, call in calls:
        monkeypatch.setattr(decision.work, target, lambda *args, _error=error, **kwargs: (_ for _ in ()).throw(_error))
        assert _exit_code(call) == code


def test_decision_write_verbs_map_workspace_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decision, "resolve_workspace", lambda workspace: LAYOUT)
    error = WorkspaceError("schema")
    calls = (
        ("run_decision_add", lambda: decision.add("work/a", "q", "open", "", "", "", "", "user", False, "", False)),
        ("run_decision_answer", lambda: decision.answer("work/a", "D-001", "a", "", "user", False, "", False)),
        (
            "run_decision_supersede",
            lambda: decision.supersede("work/a", "D-001", "q", "a", "", "", "user", False, "", False),
        ),
    )
    for target, call in calls:
        monkeypatch.setattr(decision.work, target, lambda *args, **kwargs: (_ for _ in ()).throw(error))
        assert _exit_code(call) == exit_codes.SCHEMA_MISMATCH


@pytest.mark.parametrize(
    "error,code",
    [
        (WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH),
        (ValueError("unknown"), exit_codes.AMBIGUOUS),
        (OSError("io"), 1),
    ],
)
def test_reconcile_maps_failures(monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(reconcile, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(reconcile, "run_reconcile_context", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    assert _exit_code(lambda: reconcile.reconcile_context("work/a", "", "", "", False)) == code


def test_repo_override_and_reconcile_render_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert reconcile._repo_override("") is None
    assert _exit_code(lambda: reconcile._repo_override(str(tmp_path))) == exit_codes.NOT_IN_GIT_REPO
    (tmp_path / ".git").mkdir()
    assert reconcile._repo_override(str(tmp_path)) == tmp_path
    monkeypatch.setattr(reconcile, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(reconcile, "run_reconcile_context", lambda *args, **kwargs: object())
    monkeypatch.setattr(rendering, "reconcile_payload", lambda value: {"warnings": []})
    monkeypatch.setattr(rendering, "render_reconcile", lambda payload: None)
    reconcile.reconcile_context("work/a", "", "", "", False)


@pytest.mark.parametrize("error,code", [(WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH), (OSError("io"), 1)])
def test_archive_maps_workspace_and_io_failures(monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "run_archive", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    assert _exit_code(lambda: main.archive([], False, "", False)) == code


@pytest.mark.parametrize("error,code", [(WorkspaceError("schema"), exit_codes.SCHEMA_MISMATCH), (OSError("io"), 1)])
def test_lint_maps_workspace_and_io_failures(monkeypatch: pytest.MonkeyPatch, error: Exception, code: int) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "_config", lambda layout: object())
    monkeypatch.setattr(main, "resolve_repo", lambda layout: (_ for _ in ()).throw(error))
    assert _exit_code(lambda: main.lint(False, "", False)) == code


def test_advance_forwards_start_sha_to_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "warn_if_stale_routing", lambda: None)
    seen: dict[str, object] = {}

    def _capture(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return SimpleNamespace(outcome=SimpleNamespace(plan=SimpleNamespace(diff=lambda: "preview")))

    monkeypatch.setattr(main, "run_stage_advance", _capture)
    monkeypatch.setattr(
        rendering,
        "advance_payload",
        lambda *args: {
            "refusal": None,
            "applied": False,
            "rolled_back": False,
            "failures": [],
            "warnings": [],
            "repo_note": None,
        },
    )
    main.advance("work/a", "", "", "", "", "", "", "abc1234", False, True, "", False)
    assert seen["start_sha"] == "abc1234"


def test_advance_forwards_no_start_sha_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty option string is absence, not a sha — core's own derivation
    depends on getting `None` here."""
    monkeypatch.setattr(main, "resolve_workspace", lambda workspace: LAYOUT)
    monkeypatch.setattr(main, "warn_if_stale_routing", lambda: None)
    seen: dict[str, object] = {}

    def _capture(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return SimpleNamespace(outcome=SimpleNamespace(plan=SimpleNamespace(diff=lambda: "preview")))

    monkeypatch.setattr(main, "run_stage_advance", _capture)
    monkeypatch.setattr(
        rendering,
        "advance_payload",
        lambda *args: {
            "refusal": None,
            "applied": False,
            "rolled_back": False,
            "failures": [],
            "warnings": [],
            "repo_note": None,
        },
    )
    main.advance("work/a", "", "", "", "", "", "", "", False, True, "", False)
    assert seen["start_sha"] is None
