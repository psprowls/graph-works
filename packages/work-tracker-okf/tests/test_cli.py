import json
from pathlib import Path

from typer.testing import CliRunner
from work_helpers import CONFORMANT_TODAY, make_terminal
from work_tracker_okf.cli import app
from work_tracker_okf.resources import SEED_RELATIVE_PATHS

runner = CliRunner()

_FEATURE = "2026-03-02-epic-feature-filing-writer"
_SPIKE = "2026-03-03-spike-path-layout-questions"
_TODAY = CONFORMANT_TODAY.isoformat()


def test_init_command_writes_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0
    assert (root / "index.md").is_file()
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    # Refusals go to stderr, never stdout -- a clean run should carry none of
    # them, and the `wrote ...` lines belong on stdout so the command stays
    # pipeable.
    assert "wrote _schema/_base.schema.json" in result.stdout
    assert "refused" not in result.stderr


def test_init_command_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(root), "--dry-run"])
    assert result.exit_code == 0
    assert not root.exists()
    assert "would write index.md" in result.output


def test_init_command_is_idempotent(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path / "bundle")]).exit_code == 0
    second = runner.invoke(app, ["init", str(tmp_path / "bundle")])
    assert second.exit_code == 0
    assert "wrote " not in second.output
    assert "skipped index.md" in second.output


def test_init_command_refuses_a_hand_edited_seed_by_name(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "_sections").mkdir(parents=True)
    (root / "_sections/Bug.yaml").write_text("sections: []\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 1
    assert "_sections/Bug.yaml" in result.output
    assert "foreign-content" in result.output
    assert "refused" in result.stderr
    assert "refused" not in result.stdout


def test_init_command_refuses_a_root_that_is_a_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_init_command_rejects_a_declarations_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path / "bundle"), "--declarations-dir", str(not_a_dir)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_declarations_dir_relocates_the_declarations(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    elsewhere = tmp_path / "declarations"
    elsewhere.mkdir()
    result = runner.invoke(app, ["init", str(root), "--declarations-dir", str(elsewhere)])
    assert result.exit_code == 0
    assert (elsewhere / "_schema/Epic.schema.json").is_file()
    assert not (root / "_schema").exists()


def test_the_callback_keeps_init_an_explicit_subcommand(tmp_path: Path) -> None:
    """With one registered command Typer would otherwise collapse the app and
    swallow `init` as the positional argument. A bare path must be a usage
    error, not a silent install into a directory named `init`."""
    root = tmp_path / "bundle"
    result = runner.invoke(app, [str(root)])
    assert result.exit_code != 0
    assert not root.exists()


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "init" in result.output


def test_next_reports_the_dispatch(conformant_root) -> None:
    result = runner.invoke(app, ["next", str(conformant_root), _FEATURE])
    assert result.exit_code == 0
    assert "execute" in result.stdout


def test_next_json_carries_the_documented_shape(conformant_root) -> None:
    result = runner.invoke(app, ["next", str(conformant_root), _FEATURE, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "slug",
        "type",
        "workflow_status",
        "phase",
        "effort",
        "dispatch",
        "reason",
        "on_dispatch",
        "on_complete",
        "blockers",
        "child_rollup",
    }
    assert payload["dispatch"] == {"stage": "execute", "variant": "planned"}
    assert payload["type"] == "Feature"


def test_next_reports_blockers_as_data(conformant_root) -> None:
    """A terminal item never dispatches. That is an answer, not a failure."""
    result = runner.invoke(app, ["next", str(conformant_root), _SPIKE, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dispatch"] is None
    assert payload["blockers"]


def test_next_exits_one_on_an_unknown_slug(conformant_root) -> None:
    result = runner.invoke(app, ["next", str(conformant_root), "not-a-slug"])
    assert result.exit_code == 1
    assert "not-a-slug" in result.stderr


def test_status_json_carries_the_documented_shape(conformant_root) -> None:
    result = runner.invoke(app, ["status", str(conformant_root), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {"total", "by_workflow_status", "by_type", "by_phase", "children", "resume"}
    assert payload["total"] == 6
    assert payload["resume"]["primary"]["slug"]


def test_status_human_output_names_the_totals(conformant_root) -> None:
    result = runner.invoke(app, ["status", str(conformant_root)])
    assert result.exit_code == 0
    assert "6 item" in result.stdout


def test_lint_is_clean_on_the_conformant_vault(conformant_root) -> None:
    """The gate: zero *errors*, not zero warnings (C6-L)."""
    result = runner.invoke(app, ["lint", str(conformant_root), "--today", _TODAY])
    assert result.exit_code == 0
    assert "error " not in result.stderr


def test_lint_json_carries_the_documented_shape(conformant_root) -> None:
    result = runner.invoke(app, ["lint", str(conformant_root), "--today", _TODAY, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {"ok", "findings"}
    assert payload["ok"] is True
    assert set(payload["findings"][0]) == {"code", "severity", "message", "spec", "path", "line"}


def test_lint_strict_fails_on_the_conformant_vault(conformant_root) -> None:
    """C6-L, stated as a consequence rather than papered over: `--strict`
    promotes the seven standing warnings, so it must never be wired into the
    gate."""
    result = runner.invoke(app, ["lint", str(conformant_root), "--today", _TODAY, "--strict"])
    assert result.exit_code == 1


def test_lint_rejects_a_bad_today(conformant_root) -> None:
    result = runner.invoke(app, ["lint", str(conformant_root), "--today", "not-a-date"])
    assert result.exit_code == 1
    assert "ISO 8601" in result.stderr


def test_lint_rejects_a_declarations_dir_with_no_schema(conformant_root, tmp_path) -> None:
    elsewhere = tmp_path / "nothing"
    elsewhere.mkdir()
    result = runner.invoke(app, ["lint", str(conformant_root), "--today", _TODAY, "--declarations-dir", str(elsewhere)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_lint_rejects_a_root_that_is_not_a_directory(tmp_path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["lint", str(target)])
    assert result.exit_code == 1
    assert "not a directory" in result.stderr


def _init(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path), "--today", "2026-08-11"]).exit_code == 0


def test_file_writes_page_index_and_log(tmp_path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "The filing writer",
            "--description",
            "Owns where a work item lands.",
            "--affects",
            "packages/work-tracker-okf",
            "--today",
            "2026-08-11",
        ],
    )
    assert result.exit_code == 0
    slug = "2026-08-11-feature-the-filing-writer"
    assert (tmp_path / "work" / f"{slug}.md").is_file()
    assert f"({slug}.md)" in (tmp_path / "work" / "index.md").read_text(encoding="utf-8")
    assert f"filed {slug} (Feature)" in (tmp_path / "log.md").read_text(encoding="utf-8")
    assert f"wrote work/{slug}.md" in result.stdout


def test_file_dry_run_writes_nothing(tmp_path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Bug",
            "--title",
            "A dry bug",
            "--description",
            "D",
            "--today",
            "2026-08-11",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert not (tmp_path / "work").exists()
    assert "+ " in result.stdout


def test_file_carries_every_optional_field(tmp_path) -> None:
    _init(tmp_path)
    for type_, title in (("Epic", "Parent"), ("Feature", "A"), ("Feature", "B")):
        seeded = runner.invoke(
            app,
            [
                "file",
                str(tmp_path),
                "--type",
                type_,
                "--title",
                title,
                "--description",
                "D",
                "--today",
                "2026-08-11",
            ],
        )
        assert seeded.exit_code == 0, seeded.output
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Feature",
            "--title",
            "A child feature",
            "--description",
            "D",
            "--words",
            "child feature",
            "--parent",
            "2026-08-11-epic-parent",
            "--depends-on",
            "2026-08-11-feature-a",
            "--depends-on",
            "2026-08-11-feature-b",
            "--affects",
            "packages/work-tracker-okf",
            "--tags",
            "port",
            "--tags",
            "cli",
            "--today",
            "2026-08-11",
        ],
    )
    assert result.exit_code == 0
    text = (tmp_path / "work" / "2026-08-11-epic-feature-child-feature.md").read_text(encoding="utf-8")
    assert "parent: 2026-08-11-epic-parent" in text
    assert "- 2026-08-11-feature-a" in text and "- 2026-08-11-feature-b" in text
    assert "- port" in text and "- cli" in text


def test_file_echoes_slug_warnings_without_failing(tmp_path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "file",
            str(tmp_path),
            "--type",
            "Bug",
            "--title",
            "one two three four five six seven eight",
            "--description",
            "D",
            "--today",
            "2026-08-11",
        ],
    )
    assert result.exit_code == 0
    assert "slug words" in result.stderr


def test_file_refuses_an_existing_page(tmp_path) -> None:
    _init(tmp_path)
    args = [
        "file",
        str(tmp_path),
        "--type",
        "Bug",
        "--title",
        "Twice",
        "--description",
        "D",
        "--affects",
        "packages/work-tracker-okf",
        "--today",
        "2026-08-11",
    ]
    assert runner.invoke(app, args).exit_code == 0
    second = runner.invoke(app, args)
    assert second.exit_code == 1
    assert "page-exists" in second.stderr


def test_file_refuses_an_unknown_type(tmp_path) -> None:
    _init(tmp_path)
    result = runner.invoke(
        app,
        ["file", str(tmp_path), "--type", "Nonsense", "--title", "T", "--description", "D", "--today", "2026-08-11"],
    )
    assert result.exit_code == 1
    assert "unknown-type" in result.stderr


def test_init_today_stamps_the_log_entry(tmp_path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path), "--today", "2026-03-07"]).exit_code == 0
    assert "## 2026-03-07" in (tmp_path / "log.md").read_text(encoding="utf-8")


def _file_a_tech_debt(tmp_path) -> str:
    _init(tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "file",
                str(tmp_path),
                "--type",
                "TechDebt",
                "--title",
                "Compose the CLI",
                "--description",
                "D",
                "--affects",
                "packages/work-tracker-okf",
                "--today",
                "2026-08-11",
            ],
        ).exit_code
        == 0
    )
    return "2026-08-11-tech-debt-compose-the-cli"


def test_advance_writes_and_reports(tmp_path) -> None:
    slug = _file_a_tech_debt(tmp_path)
    result = runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11"])
    assert result.exit_code == 0
    assert f"wrote work/{slug}.md" in result.stdout
    assert "phase" in result.stdout
    assert "phase: design" in (tmp_path / "work" / f"{slug}.md").read_text(encoding="utf-8")


def test_advance_dry_run_writes_nothing_and_says_the_check_is_skipped(tmp_path) -> None:
    """C6-H's stated cost: a dry run cannot honestly show post-write findings,
    so it says so. The one place a dry run differs in kind."""
    slug = _file_a_tech_debt(tmp_path)
    before = (tmp_path / "work" / f"{slug}.md").read_bytes()
    result = runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11", "--dry-run"])
    assert result.exit_code == 0
    assert "lint check skipped (--dry-run)" in result.stdout
    assert (tmp_path / "work" / f"{slug}.md").read_bytes() == before


def test_advance_reports_the_items_own_findings_only(tmp_path) -> None:
    """The stamp is unconditional (C6-G), so the pointer at a not-yet-written
    spec reports itself immediately rather than waiting for a `lint` run."""
    slug = _file_a_tech_debt(tmp_path)
    runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11"])
    result = runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11", "--effort", "small"])
    assert result.exit_code == 0
    assert "targets.artifact-missing" in result.output
    assert all(slug in line or "targets" not in line for line in result.output.splitlines())


def test_advance_refuses_without_an_effort(tmp_path) -> None:
    slug = _file_a_tech_debt(tmp_path)
    runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11"])
    result = runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11"])
    assert result.exit_code == 1
    assert "effort-required" in result.stderr


def test_advance_refuses_without_an_owner(tmp_path) -> None:
    slug = _file_a_tech_debt(tmp_path)
    runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11"])
    runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11", "--effort", "small"])
    result = runner.invoke(app, ["advance", str(tmp_path), slug, "--today", "2026-08-11"])
    assert result.exit_code == 1
    assert "owner-required" in result.stderr


def test_advance_refuses_an_unknown_slug(tmp_path) -> None:
    _init(tmp_path)
    result = runner.invoke(app, ["advance", str(tmp_path), "nope", "--today", "2026-08-11"])
    assert result.exit_code == 1
    assert "unknown-slug" in result.stderr


def test_advance_refuses_a_feature_with_open_children(conformant_root) -> None:
    """The children gate rides `Transition.requires`; the CLI reports it as a
    refusal rather than pretending the advance happened."""
    result = runner.invoke(
        app, ["advance", str(conformant_root), "2026-03-01-epic-conformant-vault", "--today", _TODAY]
    )
    assert result.exit_code == 1
    assert "refused" in result.stderr


def test_archive_sweeps_terminal_items(conformant_root) -> None:
    result = runner.invoke(app, ["archive", str(conformant_root), "--today", _TODAY])
    assert result.exit_code == 0
    assert (conformant_root / "work" / "_archive" / f"{_SPIKE}.md").is_file()
    assert not (conformant_root / "work" / f"{_SPIKE}.md").exists()
    assert f"archived {_SPIKE}" in (conformant_root / "log.md").read_text(encoding="utf-8")


def test_archive_dry_run_writes_nothing(conformant_root) -> None:
    before = (conformant_root / "work" / f"{_SPIKE}.md").read_bytes()
    result = runner.invoke(app, ["archive", str(conformant_root), "--today", _TODAY, "--dry-run"])
    assert result.exit_code == 0
    assert (conformant_root / "work" / f"{_SPIKE}.md").read_bytes() == before


def test_archive_targets_a_named_slug(conformant_root) -> None:
    make_terminal(conformant_root, _FEATURE, status="resolved")
    result = runner.invoke(app, ["archive", str(conformant_root), _FEATURE, "--today", _TODAY])
    assert result.exit_code == 0
    assert (conformant_root / "work" / "_archive" / _FEATURE / "references" / "01-design-spec.md").is_file()
    assert not (conformant_root / "work" / _FEATURE).exists()


def test_archive_reports_a_named_skip_and_exits_one(conformant_root) -> None:
    result = runner.invoke(app, ["archive", str(conformant_root), _FEATURE, "--today", _TODAY])
    assert result.exit_code == 1
    assert "not-terminal" in result.stderr


def test_archive_warns_about_stranded_wikilinks_on_stderr_and_still_exits_zero(conformant_root) -> None:
    make_terminal(conformant_root, _FEATURE, status="resolved")
    page = conformant_root / "work" / f"{_SPIKE}.md"
    page.write_text(
        page.read_text(encoding="utf-8") + f"\nSee [[work/{_FEATURE}]] for the writer.\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["archive", str(conformant_root), _FEATURE, "--today", _TODAY])
    assert result.exit_code == 0
    assert "1 inbound [[wikilink]] reference(s)" in result.stderr
    assert "okf_ext.moves" in result.stderr


def test_an_empty_sweep_logs_nothing_and_exits_zero(tmp_path) -> None:
    _init(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    before = (tmp_path / "log.md").read_text(encoding="utf-8")
    result = runner.invoke(app, ["archive", str(tmp_path), "--today", "2026-08-11"])
    assert result.exit_code == 0
    assert (tmp_path / "log.md").read_text(encoding="utf-8") == before


def test_sync_children_reports_no_drift_on_a_synced_vault(tmp_path) -> None:
    _init(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    result = runner.invoke(app, ["sync-children", str(tmp_path)])
    assert result.exit_code == 0
    assert "no drift" in result.stdout


def test_sync_children_writes_the_derived_key(conformant_root) -> None:
    """The conformant vault's epic authors no `children:` while three items name
    it as `parent` -- `graph.children-stale` is exactly that, and this repairs
    it."""
    epic = conformant_root / "work" / "2026-03-01-epic-conformant-vault.md"
    assert "children:" not in epic.read_text(encoding="utf-8")
    result = runner.invoke(app, ["sync-children", str(conformant_root)])
    assert result.exit_code == 0
    assert "children:" in epic.read_text(encoding="utf-8")


def test_sync_children_dry_run_writes_nothing(conformant_root) -> None:
    epic = conformant_root / "work" / "2026-03-01-epic-conformant-vault.md"
    before = epic.read_bytes()
    result = runner.invoke(app, ["sync-children", str(conformant_root), "--dry-run"])
    assert result.exit_code == 0
    assert epic.read_bytes() == before
