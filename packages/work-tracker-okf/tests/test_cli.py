import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from okf_io import load
from typer.testing import CliRunner
from work_tracker_okf import cli
from work_tracker_okf.cli import app
from work_tracker_okf.mutation import MutationRefusal
from work_tracker_okf.resources import SEED_RELATIVE_PATHS

runner = CliRunner()
TODAY = "2026-08-22"


def _init(root: Path) -> None:
    result = runner.invoke(app, ["init", str(root), "--today", TODAY])
    assert result.exit_code == 0, result.output


def _file(root: Path, *, type_name: str = "Feature", title: str = "Child", affects: str | None = None) -> str:
    args = [
        "file",
        str(root),
        "--type",
        type_name,
        "--title",
        title,
        "--description",
        "d",
    ]
    if affects is not None:
        args.extend(("--affects", affects))
    args.extend(
        [
            "--today",
            TODAY,
        ]
    )
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    prefix = {
        "Bug": "bug",
        "Epic": "epic",
        "Feature": "feature",
        "Release": "release",
        "Spike": "spike",
        "TechDebt": "tech-debt",
        "TestGap": "test-gap",
    }[type_name]
    return f"work/{prefix}-{title.lower().replace(' ', '-')}"


def test_init_writes_every_seed_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _init(root)
    assert (root / "index.md").is_file()
    assert all((root / relative).is_file() for relative in SEED_RELATIVE_PATHS)


def test_init_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(root), "--today", TODAY, "--dry-run"])
    assert result.exit_code == 0 and not root.exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _init(root)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    second = runner.invoke(app, ["init", str(root), "--today", TODAY])
    assert second.exit_code == 0
    assert "skipped" in second.stdout
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_init_refuses_drifted_seed_on_stderr_without_overwriting(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    _init(root)
    seed = root / "schema" / "Feature.schema.json"
    seed.write_text("human edit\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root), "--today", TODAY])
    assert result.exit_code == 1
    assert "refused" in result.stderr
    assert "refused" not in result.stdout
    assert seed.read_text(encoding="utf-8") == "human edit\n"


def test_file_uses_a_date_free_canonical_path_and_owned_scaffolding(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path, type_name="Epic", title="Migration")
    assert (tmp_path / f"{path}.md").is_file()
    assert (tmp_path / path / "references" / ".gitkeep").is_file()
    assert (tmp_path / path / "children" / "index.md").is_file()


def test_file_accepts_a_canonical_parent_path(tmp_path: Path) -> None:
    _init(tmp_path)
    parent = _file(tmp_path, type_name="Epic", title="Migration")
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "Import",
            "--description",
            "d",
            "--parent-path",
            parent,
            "--today",
            TODAY,
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / parent / "children" / "feature-import.md").is_file()


def test_next_json_is_path_keyed(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path)
    result = runner.invoke(app, ["next", str(tmp_path), path, "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["path"] == path
    assert payload["work_status"] == "open"
    assert "slug" not in payload and "workflow" + "_status" not in payload
    assert "phase" not in payload
    assert "effort" not in payload
    assert "child_rollup" not in payload
    assert "work_status" not in payload["on_dispatch"]
    assert "document_status" not in payload["on_dispatch"]
    assert "stamp_source" not in payload["on_dispatch"]
    assert payload["on_dispatch"]["requires"] == []
    assert payload["on_dispatch"]["sync_plan_table"] is False
    assert "null" not in result.stdout


def test_next_unknown_path_is_nonzero(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(app, ["next", str(tmp_path), "work/missing"])
    assert result.exit_code == 1 and "unknown path" in result.stderr


def test_status_json_is_path_and_work_status_keyed(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path)
    result = runner.invoke(app, ["status", str(tmp_path), "--json"])
    payload = json.loads(result.stdout)
    assert payload["by_work_status"] == {"open": 1}
    assert payload["resume"]["primary"]["path"] == path


def test_empty_status_json_omits_the_absent_resume(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(app, ["status", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total"] == 0
    assert payload["children"] == {}
    assert "resume" not in payload


def test_lint_sends_warnings_to_stderr(tmp_path: Path) -> None:
    _init(tmp_path)
    _file(tmp_path)
    index = tmp_path / "work" / "index.md"
    index.write_text(
        "<!-- graph-works:work-items:start -->\n<!-- graph-works:work-items:end -->\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["lint", str(tmp_path), "--today", TODAY])
    assert result.exit_code == 0
    assert "warn" in result.stderr
    assert "warn" not in result.stdout


def test_lint_json_omits_line_when_the_finding_has_no_line(tmp_path: Path) -> None:
    _init(tmp_path)
    _file(tmp_path)
    index = tmp_path / "work" / "index.md"
    index.write_text(
        "<!-- graph-works:work-items:start -->\n<!-- graph-works:work-items:end -->\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["lint", str(tmp_path), "--today", TODAY, "--json"])
    assert result.exit_code == 0
    finding = next(
        item for item in json.loads(result.stdout)["findings"] if item["code"] == "structure.index-entry-missing"
    )
    assert "line" not in finding


def test_lint_failure_is_nonzero_and_diagnostic_only_on_stderr(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path)
    document = load(tmp_path / f"{path}.md")
    document.set_body("No required sections.\n")
    document.save()
    result = runner.invoke(app, ["lint", str(tmp_path), "--today", TODAY])
    assert result.exit_code == 1
    assert "error" in result.stderr
    assert "error" not in result.stdout


def test_file_requires_complete_dependency_mapping(tmp_path: Path) -> None:
    _init(tmp_path)
    dependency = _file(tmp_path, title="Dependency")
    path_only = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "Path only",
            "--description",
            "d",
            "--dep",
            dependency,
            "--today",
            TODAY,
        ],
    )
    assert path_only.exit_code == 1
    assert "expected path=...,blocks=...,needs=..." in path_only.stderr

    complete = f"path={dependency},blocks=execute,needs=resolved"
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "Mapped",
            "--description",
            "d",
            "--dep",
            complete,
            "--today",
            TODAY,
        ],
    )
    assert result.exit_code == 0, result.output
    assert load(tmp_path / "work" / "feature-mapped.md").fm_data()["depends_on"] == [
        {"path": dependency, "blocks": "execute", "needs": "resolved"}
    ]


def test_file_refuses_incomplete_dependency_mapping(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "Incomplete",
            "--description",
            "d",
            "--dep",
            "path=work/feature-dependency",
            "--today",
            TODAY,
        ],
    )
    assert result.exit_code == 1
    assert "missing blocks, needs" in result.stderr


def test_file_refuses_existing_page_and_unknown_type(tmp_path: Path) -> None:
    _init(tmp_path)
    _file(tmp_path, title="Duplicate")
    duplicate = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "Duplicate",
            "--description",
            "d",
            "--today",
            TODAY,
        ],
    )
    unknown = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Widget",
            "--title",
            "Unknown",
            "--description",
            "d",
            "--today",
            TODAY,
        ],
    )
    assert duplicate.exit_code == 1 and "page-exists" in duplicate.stderr
    assert unknown.exit_code == 1 and "unknown-type" in unknown.stderr


def test_advance_dry_run_names_the_canonical_page_without_writing(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path)
    page = tmp_path / f"{path}.md"
    before = page.read_bytes()
    result = runner.invoke(app, ["advance", str(tmp_path), path, "--today", TODAY, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert path in result.stdout and page.read_bytes() == before


def test_advance_exits_nonzero_when_the_written_page_fails_post_write_lint(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path)
    assert runner.invoke(app, ["advance", str(tmp_path), path, "--today", TODAY]).exit_code == 0
    artifact = tmp_path / path / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["advance", str(tmp_path), path, "--today", TODAY, "--effort", "small"],
    )
    assert result.exit_code == 1
    assert "error" in result.stderr


def test_advance_enforces_effort_and_owner_gates(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path, type_name="TechDebt", affects="packages/work-tracker-okf")
    assert runner.invoke(app, ["advance", str(tmp_path), path, "--today", TODAY]).exit_code == 0
    artifact = tmp_path / path / "references" / "01-design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Design\n", encoding="utf-8")
    effort = runner.invoke(app, ["advance", str(tmp_path), path, "--today", TODAY])
    assert effort.exit_code == 1 and "effort-required" in effort.stderr
    assert runner.invoke(app, ["advance", str(tmp_path), path, "--today", TODAY, "--effort", "small"]).exit_code == 0
    owner = runner.invoke(app, ["advance", str(tmp_path), path, "--today", TODAY])
    assert owner.exit_code == 1 and "owner-required" in owner.stderr


def test_advance_refuses_unknown_path_and_release_without_released_at(tmp_path: Path) -> None:
    _init(tmp_path)
    unknown = runner.invoke(app, ["advance", str(tmp_path), "work/missing", "--today", TODAY])
    assert unknown.exit_code == 1 and "unknown-path" in unknown.stderr

    release = _file(tmp_path, type_name="Release", title="Gate")
    page = tmp_path / f"{release}.md"
    document = load(page)
    document.set("work_status", "in-progress")
    document.set("phase", "finish")
    document.save()
    result = runner.invoke(app, ["advance", str(tmp_path), release, "--today", TODAY])
    assert result.exit_code == 1 and "released-at-required" in result.stderr


def test_release_resolution_accepts_released_at(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path, type_name="Release", title="R1")
    page = tmp_path / f"{path}.md"
    document = load(page)
    document.set("work_status", "in-progress")
    document.set("phase", "finish")
    document.save()
    result = runner.invoke(
        app,
        ["advance", str(tmp_path), path, "--today", TODAY, "--released-at", TODAY],
    )
    assert result.exit_code == 0, result.output
    assert "released_at: 2026-08-22" in page.read_text(encoding="utf-8")


def test_archive_dry_run_accepts_a_canonical_path_and_writes_nothing(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path, type_name="Bug", title="Done")
    page = tmp_path / f"{path}.md"
    document = load(page)
    document.set("work_status", "resolved")
    document.save()
    before = {member.relative_to(tmp_path): member.read_bytes() for member in tmp_path.rglob("*") if member.is_file()}

    result = runner.invoke(app, ["archive", str(tmp_path), path, "--today", TODAY, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"{path}.md -> work/_archive/bug-done.md" in result.stdout
    after = {member.relative_to(tmp_path): member.read_bytes() for member in tmp_path.rglob("*") if member.is_file()}
    assert after == before


def test_archive_applies_the_unified_effect_set_and_logs_the_path(tmp_path: Path) -> None:
    _init(tmp_path)
    path = _file(tmp_path, type_name="Bug", title="Done")
    page = tmp_path / f"{path}.md"
    document = load(page)
    document.set("work_status", "resolved")
    document.save()

    result = runner.invoke(app, ["archive", str(tmp_path), path, "--today", TODAY])

    assert result.exit_code == 0, result.output
    assert not page.exists()
    assert (tmp_path / "work/_archive/bug-done.md").is_file()
    assert "bug-done" not in (tmp_path / "work/index.md").read_text(encoding="utf-8")
    assert "bug-done" in (tmp_path / "work/_archive/index.md").read_text(encoding="utf-8")
    assert f"archived {path}" in (tmp_path / "log.md").read_text(encoding="utf-8")


def test_cli_helper_error_mapping_and_json_fragments(tmp_path: Path, monkeypatch, capsys) -> None:
    assert cli._today(None)
    with pytest.raises(typer.Exit):
        cli._today("not-a-date")
    with pytest.raises(typer.Exit):
        cli._bundle(tmp_path / "missing")

    transition = SimpleNamespace(
        phase="execute",
        work_status="in-progress",
        document_status="stable",
        requires=("owner",),
        sync_plan_table=True,
        stamp_source="plan",
    )
    assert cli._transition_json(transition)["requires"] == ["owner"]
    assert cli._transition_json(None) is None
    rollup = SimpleNamespace(total=2, terminal=1, open_paths=("work/a",))
    assert cli._rollup_json(rollup)["open_paths"] == ["work/a"]
    assert cli._rollup_json(None) is None
    finding = SimpleNamespace(code="x", severity="warn", message="m", spec="s", path=None, line=None)
    assert "line" not in cli._finding_json(finding)
    cli._echo_findings((finding,))
    assert "warn" in capsys.readouterr().err

    monkeypatch.setattr(cli, "rule_set", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad rules")))
    with pytest.raises(typer.Exit):
        cli._rules(tmp_path, repo_root=None, declarations_dir=None)
    monkeypatch.setattr(cli, "load_sections", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("bad sections")))
    with pytest.raises(typer.Exit):
        cli._sections(tmp_path, None)


@pytest.mark.parametrize(
    "value,message",
    [
        ("path", "expected"),
        ("other=x,path=a,blocks=plan,needs=resolved", "expected"),
        ("path=a,path=b,blocks=plan,needs=resolved", "expected"),
        ("path=,blocks=plan,needs=resolved", "expected"),
        ("path=a", "missing"),
    ],
)
def test_standalone_dependency_parser_rejects_incomplete_edges(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cli._dependency_edges([value])


def test_standalone_dependency_parser_reports_vocabulary_issues(monkeypatch) -> None:
    issue = SimpleNamespace(code="bad", detail="wrong")
    monkeypatch.setattr(cli, "parse_dependencies", lambda raw: SimpleNamespace(issues=(issue,), edges=()))
    with pytest.raises(ValueError, match="invalid --dep"):
        cli._dependency_edges(["path=a,blocks=plan,needs=resolved"])


def test_standalone_human_read_paths_and_argument_errors(tmp_path: Path) -> None:
    missing_declarations = runner.invoke(
        app, ["init", str(tmp_path / "bundle"), "--declarations-dir", str(tmp_path / "none")]
    )
    assert missing_declarations.exit_code == 1
    bad_today = runner.invoke(app, ["init", str(tmp_path / "bundle"), "--today", "bad"])
    assert bad_today.exit_code == 1

    _init(tmp_path)
    path = _file(tmp_path)
    routed = runner.invoke(app, ["next", str(tmp_path), path])
    assert routed.exit_code == 0 and path in routed.stdout
    human_status = runner.invoke(app, ["status", str(tmp_path)])
    assert human_status.exit_code == 0 and "resume:" in human_status.stdout
    empty = tmp_path / "empty"
    _init(empty)
    empty_status = runner.invoke(app, ["status", str(empty)])
    assert empty_status.exit_code == 0 and "item(s)" in empty_status.stdout
    missing_archive = runner.invoke(app, ["archive", str(tmp_path / "missing")])
    assert missing_archive.exit_code == 1


def test_file_dry_run_renders_every_planned_effect_without_writing(tmp_path: Path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "Preview",
            "--description",
            "d",
            "--today",
            TODAY,
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "would reconcile" in result.stdout and "filed work/feature-preview" in result.stdout
    assert not (tmp_path / "work/feature-preview.md").exists()


def test_next_human_output_reports_no_dispatch_and_each_blocker(tmp_path: Path, monkeypatch, capsys) -> None:
    state = SimpleNamespace(
        type="Feature",
        work_status="open",
        phase=None,
        effort=None,
        child_rollup=None,
    )
    result = SimpleNamespace(
        dispatch=None,
        reason="waiting",
        on_dispatch=None,
        on_complete=None,
        blockers=("dependency", "children"),
    )
    monkeypatch.setattr(cli, "_bundle", lambda root: object())
    monkeypatch.setattr(cli, "load_items", lambda bundle: ())
    monkeypatch.setattr(cli, "state_for", lambda items, path: state)
    monkeypatch.setattr(cli, "route", lambda current: result)

    cli.next_stage(tmp_path, "work/feature", False)

    output = capsys.readouterr().out
    assert "nothing to dispatch -- waiting" in output
    assert "blocked: dependency" in output and "blocked: children" in output


def test_init_maps_library_failure_and_archive_maps_planning_and_apply_failures(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "install_bundle", lambda *args, **kwargs: (_ for _ in ()).throw(cli.InitError("bad init")))
    with pytest.raises(typer.Exit):
        cli.init(tmp_path, None, TODAY, False)
    assert "bad init" in capsys.readouterr().err

    refused = cli.WorkMutationPlan(
        root=tmp_path,
        operation="archive",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=("opaque warning",),
        refusals=(MutationRefusal("work/a", "not-terminal", "open"),),
        validate_paths=(),
    )
    monkeypatch.setattr(cli, "_bundle", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "load_items", lambda bundle: ())
    monkeypatch.setattr(cli, "plan_archive", lambda *args, **kwargs: refused)
    with pytest.raises(typer.Exit):
        cli.archive(tmp_path, [], TODAY, False)
    captured = capsys.readouterr()
    assert "not-terminal" in captured.out and "opaque warning" in captured.err

    allowed = cli.WorkMutationPlan(
        root=tmp_path,
        operation="archive",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(),
    )
    monkeypatch.setattr(cli, "plan_archive", lambda *args, **kwargs: allowed)
    monkeypatch.setattr(cli, "_apply_mutation", lambda plan: (_ for _ in ()).throw(ValueError("stale")))
    with pytest.raises(typer.Exit):
        cli.archive(tmp_path, [], TODAY, False)
    assert "stale" in capsys.readouterr().err
