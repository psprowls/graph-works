"""`gw wiki` proposal workflow boundary tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.wiki_cli import proposals as proposals_module
from graph_works_core.proposals import commands as proposals_commands
from okf_ext.proposals import Proposal
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def initialized_workspace(tmp_path: Path) -> Path:
    """Create the smallest real initialized workspace for proposal commands."""
    root = tmp_path / "works"
    result = runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)])
    assert result.exit_code == 0
    return root


def _plan(*, ok: bool = True, writes: tuple[object, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(ok=ok, is_empty=not writes, writes=writes)


def _result(*, ok: bool = True, written: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(ok=ok, written=written, failed=())


def _decide_run(*, plan_ok: bool = True, result_ok: bool = True, written: tuple[str, ...] = ()) -> SimpleNamespace:
    refusals = () if plan_ok else (SimpleNamespace(path="proposals/a.md", kind="not-proposed", detail="closed"),)
    return SimpleNamespace(
        target="concepts/a.md",
        decision="approved",
        proposal="proposals/a.md",
        ok=plan_ok and result_ok,
        plan=_plan(ok=plan_ok),
        refusals=refusals,
        result=None if not plan_ok else _result(ok=result_ok, written=written),
    )


def _file_run(*, plan_ok: bool = True, result_ok: bool = True, written: tuple[str, ...] = ()) -> SimpleNamespace:
    refusals = () if plan_ok else (SimpleNamespace(path="proposals/a.md", kind="refused", detail="no"),)
    return SimpleNamespace(
        lane="explanation",
        target="explanations/typed-cli.md",
        proposal="proposals/explanations-typed-cli.md",
        ok=plan_ok and result_ok,
        plan=_plan(ok=plan_ok),
        refusals=refusals,
        result=None if not plan_ok else _result(ok=result_ok, written=written),
    )


def _file_args(workspace: Path) -> list[str]:
    return [
        "wiki",
        "proposal",
        "file",
        "--lane",
        "explanation",
        "--title",
        "Typed CLI",
        "--id",
        "source-1",
        "--resource",
        "sources/one.md",
        "--workspace",
        str(workspace),
    ]


def test_proposals_lists_only_open_proposals_as_the_frozen_json_shape(initialized_workspace: Path) -> None:
    result = runner.invoke(app, ["wiki", "proposals", "--json", "--workspace", str(initialized_workspace)])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_proposals_uses_the_open_filter_and_renders_target_status_and_malformed_state(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    proposal = Proposal(
        "proposals/a.md", "proposals/a", "concepts/a.md", "A", "", "proposed", "proposed", (), (), "missing metadata"
    )
    called: list[object] = []
    monkeypatch.setattr(
        proposals_commands, "load_bundle", lambda _root: SimpleNamespace(has_member=lambda _member: False)
    )
    monkeypatch.setattr(
        proposals_commands,
        "list_proposals",
        lambda bundle, *, page_status=None: called.append((bundle, page_status)) or (proposal,),
    )
    json_result = runner.invoke(app, ["wiki", "proposals", "--json", "--workspace", str(initialized_workspace)])
    human_result = runner.invoke(app, ["wiki", "proposals", "--workspace", str(initialized_workspace)])
    assert json_result.exit_code == human_result.exit_code == 0
    assert json.loads(json_result.stdout)[0]["malformed"] == "missing metadata"
    assert json.loads(json_result.stdout)[0]["mode"] == "create"
    assert human_result.stdout == "concepts/a.md: proposed (malformed: missing metadata)\n"
    assert [entry[1] for entry in called] == ["proposed", "proposed"]


def test_proposals_reports_an_unreadable_bundle_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    def unreadable(_root: object) -> object:
        raise OSError("proposals/ is unreadable")

    monkeypatch.setattr(proposals_commands, "load_bundle", unreadable)
    result = runner.invoke(app, ["wiki", "proposals", "--workspace", str(initialized_workspace)])
    assert result.exit_code == exit_codes.GENERIC
    assert result.stdout == "" and result.stderr == "Error: proposals/ is unreadable\n"


def test_proposals_says_so_when_no_proposal_is_open(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    monkeypatch.setattr(
        proposals_commands, "load_bundle", lambda _root: SimpleNamespace(has_member=lambda _member: False)
    )
    monkeypatch.setattr(proposals_commands, "list_proposals", lambda *_args, **_kwargs: ())
    result = runner.invoke(app, ["wiki", "proposals", "--workspace", str(initialized_workspace)])
    assert result.exit_code == 0 and result.stdout == "no open proposals\n"


@pytest.mark.parametrize(("command", "decision"), (("approve", "approved"), ("reject", "rejected")))
@pytest.mark.parametrize("target_spelling", (r"\concepts\a.md", "./concepts/a.md", "dir/../concepts/a.md"))
def test_proposal_decisions_pass_the_raw_target_to_core(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, command: str, decision: str, target_spelling: str
) -> None:
    """Normalizing in the CLI again would make the core the wrong owner of identity rules."""
    captured: list[tuple[object, ...]] = []

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        captured.append((*args, kwargs))
        return _decide_run()

    monkeypatch.setattr(proposals_module, "run_proposal_decide", fake_run)
    result = runner.invoke(
        app, ["wiki", "proposal", command, target_spelling, "--workspace", str(initialized_workspace)]
    )
    assert result.exit_code == 0
    layout, raw, actual_decision = captured[0][:3]
    kwargs = captured[0][3]
    assert raw == target_spelling and actual_decision == decision
    assert kwargs["by"] == "human" and kwargs["dry_run"] is False
    assert isinstance(kwargs["at"], datetime) and kwargs["at"].tzinfo is UTC
    assert layout.bundle_dir == proposals_module.resolve_workspace(str(initialized_workspace)).bundle_dir


def test_proposal_decision_unknown_target_is_a_real_workspace_refusal(initialized_workspace: Path) -> None:
    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "explanations/nope.md", "--workspace", str(initialized_workspace)]
    )
    bundle = proposals_module.resolve_workspace(str(initialized_workspace)).bundle_dir
    assert result.exit_code == exit_codes.GENERIC
    assert result.stderr == f"Error: no proposal targets 'explanations/nope.md' in {bundle}\n"


@pytest.mark.parametrize(
    ("plan_ok", "apply_ok", "message"),
    ((False, True, "proposal decision plan was refused"), (True, False, "proposal decision was incomplete")),
)
def test_proposal_decision_refusal_or_incomplete_apply_exits_one(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, plan_ok: bool, apply_ok: bool, message: str
) -> None:
    monkeypatch.setattr(
        proposals_module,
        "run_proposal_decide",
        lambda *_args, **_kwargs: _decide_run(plan_ok=plan_ok, result_ok=apply_ok),
    )
    result = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )
    assert result.exit_code == exit_codes.GENERIC
    assert result.stderr == f"Error: {message}\n"


def test_proposal_decision_reports_core_io_and_echoes_written_members(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    def unreadable(*_args: object, **_kwargs: object) -> object:
        raise OSError("proposals/a.md is read-only")

    monkeypatch.setattr(proposals_module, "run_proposal_decide", unreadable)
    failed = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )
    assert failed.exit_code == exit_codes.GENERIC and failed.stderr == "Error: proposals/a.md is read-only\n"
    monkeypatch.setattr(
        proposals_module,
        "run_proposal_decide",
        lambda *_args, **_kwargs: _decide_run(written=("proposals/a.md", "proposals/index.md")),
    )
    written = runner.invoke(
        app, ["wiki", "proposal", "approve", "concepts/a.md", "--workspace", str(initialized_workspace)]
    )
    assert written.exit_code == 0 and written.stdout == "proposals/a.md\nproposals/index.md\n"


def test_proposal_file_builds_the_ordered_source_mapping_and_timestamps_it(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    captured: list[dict[str, object]] = []

    def fake_run(_layout: object, **kwargs: object) -> SimpleNamespace:
        captured.append(kwargs)
        return _file_run()

    monkeypatch.setattr(proposals_module, "run_proposal_file", fake_run)
    result = runner.invoke(
        app,
        [
            *_file_args(initialized_workspace),
            "--description",
            "Make proposal filing explicit.",
            "--rationale",
            "This removes ambiguity.",
            "--evidence",
            "first",
            "--evidence",
            "second",
        ],
    )
    assert result.exit_code == 0 and result.stdout == "nothing to do\n"
    assert captured[0]["source"] == {
        "id": "source-1",
        "resource": "sources/one.md",
        "rationale": "This removes ambiguity.",
        "evidence": ["first", "second"],
    }
    assert captured[0]["by"] == "agent:graph-works-cli" and captured[0]["dry_run"] is False
    assert isinstance(captured[0]["at"], datetime) and captured[0]["at"].tzinfo is UTC


def test_proposal_file_omits_unprovided_optional_source_fields(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        proposals_module, "run_proposal_file", lambda _layout, **kwargs: captured.append(kwargs) or _file_run()
    )
    assert runner.invoke(app, _file_args(initialized_workspace)).exit_code == 0
    assert captured[0]["source"] == {"id": "source-1", "resource": "sources/one.md"}


@pytest.mark.parametrize(
    ("plan_ok", "apply_ok", "message"),
    ((False, True, "proposal filing plan was refused"), (True, False, "proposal filing was incomplete")),
)
def test_proposal_file_refusal_or_incomplete_apply_exits_one(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path, plan_ok: bool, apply_ok: bool, message: str
) -> None:
    monkeypatch.setattr(
        proposals_module, "run_proposal_file", lambda *_args, **_kwargs: _file_run(plan_ok=plan_ok, result_ok=apply_ok)
    )
    result = runner.invoke(app, _file_args(initialized_workspace))
    assert result.exit_code == exit_codes.GENERIC
    assert result.stderr == f"Error: {message}\n"


def test_proposal_file_reports_an_unknown_lane(monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path) -> None:
    monkeypatch.setattr(
        proposals_module, "run_proposal_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyError("nope"))
    )
    result = runner.invoke(app, _file_args(initialized_workspace))
    assert result.exit_code == exit_codes.GENERIC and "Error:" in result.stderr


def test_proposal_file_reports_an_unloadable_lane_schema(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    monkeypatch.setattr(
        proposals_module,
        "run_proposal_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("schema unreadable")),
    )
    result = runner.invoke(app, _file_args(initialized_workspace))
    assert result.exit_code == exit_codes.GENERIC and "Error: schema unreadable" in result.stderr


def test_proposal_file_reports_core_io_and_echoes_written_members(
    monkeypatch: pytest.MonkeyPatch, initialized_workspace: Path
) -> None:
    def unwritable(*_args: object, **_kwargs: object) -> object:
        raise OSError("proposals/ is read-only")

    monkeypatch.setattr(proposals_module, "run_proposal_file", unwritable)
    failed = runner.invoke(app, _file_args(initialized_workspace))
    assert failed.exit_code == exit_codes.GENERIC and failed.stderr == "Error: proposals/ is read-only\n"
    monkeypatch.setattr(
        proposals_module, "run_proposal_file", lambda *_args, **_kwargs: _file_run(written=("proposals/typed-cli.md",))
    )
    written = runner.invoke(app, _file_args(initialized_workspace))
    assert written.exit_code == 0 and written.stdout == "proposals/typed-cli.md\n"


@pytest.mark.parametrize(
    ("missing", "value"), (("--lane", "explanation"), ("--title", "T"), ("--id", "s1"), ("--resource", "r"))
)
def test_proposal_file_requires_each_filing_identity_field(missing: str, value: str) -> None:
    options = {"--lane": "explanation", "--title": "T", "--id": "s1", "--resource": "r"}
    options.pop(missing)
    result = runner.invoke(app, ["wiki", "proposal", "file", *[part for pair in options.items() for part in pair]])
    assert result.exit_code == 2 and missing in result.stderr


@pytest.mark.parametrize("flag", ("--kind", "--target-slug", "--origin"))
def test_proposal_file_rejects_removed_flags(flag: str) -> None:
    result = runner.invoke(app, [*_file_args(Path()), flag, "x"])
    assert result.exit_code == 2 and f"No such option: {flag}" in result.stderr


def test_file_json_dry_run_writes_nothing_and_projects(initialized_workspace: Path) -> None:
    bundle = proposals_module.resolve_workspace(str(initialized_workspace)).bundle_dir
    before = sorted(path.as_posix() for path in bundle.rglob("*"))
    result = runner.invoke(app, [*_file_args(initialized_workspace), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["lane"] == "explanation" and doc["target"] == "explanations/typed-cli.md"
    assert doc["applied"] is False and doc["writes"][0]["mode"] == "create"
    assert sorted(path.as_posix() for path in bundle.rglob("*")) == before


@pytest.mark.parametrize(("command", "status"), (("approve", "approved"), ("reject", "rejected")))
def test_decide_json_dry_run_then_apply(initialized_workspace: Path, command: str, status: str) -> None:
    assert runner.invoke(app, _file_args(initialized_workspace)).exit_code == 0
    base = [
        "wiki",
        "proposal",
        command,
        "explanations/typed-cli.md",
        "--workspace",
        str(initialized_workspace),
        "--json",
    ]
    planned = runner.invoke(app, [*base, "--dry-run"])
    assert planned.exit_code == 0, planned.output
    planned_doc = json.loads(planned.stdout)
    assert planned_doc["applied"] is False and planned_doc["writes"][0]["frontmatter"]["page_status"] == status
    applied = runner.invoke(app, base)
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["written"] == ["proposals/explanations-typed-cli.md"]


def test_decide_dry_run_without_json_prints_planned_members(initialized_workspace: Path) -> None:
    assert runner.invoke(app, _file_args(initialized_workspace)).exit_code == 0
    result = runner.invoke(
        app,
        [
            "wiki",
            "proposal",
            "approve",
            "explanations/typed-cli.md",
            "--dry-run",
            "--workspace",
            str(initialized_workspace),
        ],
    )
    assert result.exit_code == 0 and result.stdout.strip() == "proposals/explanations-typed-cli.md"
