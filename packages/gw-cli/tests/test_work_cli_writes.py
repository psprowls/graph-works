"""Path-native write commands and refusal stream contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from gw_cli import exit_codes
from gw_cli.cli import app
from gw_cli.work_cli import main as work_main
from gw_cli.workspace_resolution import resolve_workspace
from gw_orchestrate.gate_receipts import GateRun, render_receipt
from okf_io import load
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    return root


def file_item(workspace: Path, title: str, *, kind: str = "Feature", parent: str | None = None) -> str:
    args = ["work", "file", "--title", title, "--kind", kind, "--summary", "d", "--workspace", str(workspace), "--json"]
    if parent:
        args.extend(("--parent-path", parent))
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def test_file_human_reports_workspace_commit(workspace: Path) -> None:
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Committed item",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[ok] committed " in result.stdout
    assert "workspace: " in result.stdout


def test_file_human_notes_non_repo_workspace(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Uncommitted item",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[note] workspace not committed (not-a-repo)" in result.stderr


def test_file_writes_a_date_free_canonical_path_and_explicit_json(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Stable Name",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--name",
            "short name",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == "work/feature-short-name"
    assert payload["applied"] is True and payload["rolled_back"] is False
    assert resolve_workspace(str(workspace)).bundle_dir.joinpath(f"{payload['path']}.md").is_file()
    assert "slug" not in result.stdout


def test_file_parses_complete_dependency_specs(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def spy(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(work_main.work, "run_file", spy)
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--dep",
            "path=work/feature-a,blocks=execute,needs=resolved",
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0
    assert captured["depends_on"] == (work_main.work.DependencyEdge("work/feature-a", "execute", "resolved"),)


@pytest.mark.parametrize(
    "spec",
    [
        "path=work/feature-a",
        "blocks=execute,needs=resolved",
        "path=,blocks=execute,needs=resolved",
        "path=work/feature-a,blocks=execute,blocks=plan,needs=resolved",
        "path=work/feature-a,blocks=execute,needs=resolved,",
    ],
)
def test_incomplete_or_malformed_dep_is_refused_before_core(workspace: Path, spec: str) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--dep",
            spec,
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"} and doc["error"]["reason"] == "usage"
    assert spec in result.stderr


def test_file_refusal_emits_the_envelope(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--effort",
            "huge",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    assert doc["error"]["reason"] == "refused"
    assert doc["error"]["payload"]["refusal"] == "invalid-effort"
    assert "refused" in result.stderr


@pytest.mark.parametrize("json_mode", [False, True])
def test_file_incomplete_apply_names_the_blocking_member_not_the_item(workspace: Path, json_mode: bool) -> None:
    """§1/D-095: a `log.md` swapped for an escaping symlink makes the filing
    apply fail its preflight, and the top-level error must name `log.md` --
    the file actually in the way -- not the item being filed.
    """
    file_item(workspace, "Baseline item")
    log = resolve_workspace(str(workspace)).bundle_dir / "log.md"
    elsewhere = workspace / "elsewhere.md"
    elsewhere.write_bytes(log.read_bytes())
    log.unlink()
    log.symlink_to(elsewhere.resolve())

    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Second item",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--workspace",
            str(workspace),
            *(["--json"] if json_mode else []),
        ],
    )

    assert result.exit_code != 0
    last_line = [line for line in result.stderr.splitlines() if line.strip()][-1]
    assert "log.md" in last_line
    assert "feature-second-item" not in last_line

    assert "[ok] filed" not in result.stdout
    if json_mode:
        error = json.loads(result.stdout)["error"]
        assert error["reason"] == "incomplete-apply"
        assert error["payload"]["applied"] is False
        assert error["payload"]["failures"]


def test_file_json_keeps_warnings_on_stderr(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "one two three four five",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["warnings"]
    assert "words kept" in result.stderr


def test_filing_a_child_without_affects_warns_and_succeeds(workspace: Path) -> None:
    epic = file_item(workspace, "Parent", kind="Epic")
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Leaf",
            "--kind",
            "Bug",
            "--summary",
            "d",
            "--parent-path",
            epic,
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert any("no `affects` declared" in warning for warning in json.loads(result.stdout)["warnings"])
    assert "no `affects` declared" in result.stderr


def test_advance_applies_by_default_and_accepts_released_at(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(
        app, ["work", "advance", path, "--released-at", "2026-09-01", "--workspace", str(workspace), "--json"]
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == path and payload["applied"] is True
    assert "work_status" in payload


@pytest.mark.parametrize("dry_run", [False, True])
def test_advance_from_plan_reports_affects_drift_and_succeeds(workspace: Path, dry_run: bool) -> None:
    path = file_item(workspace, "Plan drift", kind="Feature")
    page = workspace / "okf" / f"{path}.md"
    document = load(page)
    document.set("phase", "plan")
    document.set("work_status", "in-progress")
    document.set("affects", ["packages/a"])
    document.save()
    artifact = workspace / "okf" / path / "references" / "02-plan.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("**Files:**\n- Modify: `packages/b/x.py`\n", encoding="utf-8", newline="")
    args = ["work", "advance", path, "--from", "plan", "--workspace", str(workspace), "--json"]
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    warning = (
        "affects drift: the plan names 1 path(s) outside `affects`: packages/b/x.py. Suggested widening: packages/b"
    )
    assert payload["warnings"] == [warning]
    assert warning in result.stderr
    assert _fm(workspace, path)["phase"] == ("plan" if dry_run else "execute")


def test_invalid_date_emits_a_usage_envelope(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--target-date",
            "not-a-date",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    assert doc["error"]["reason"] == "usage" and doc["error"]["payload"] is None
    assert "expected YYYY-MM-DD" in result.stderr


def test_reparent_live_apply_leaves_lane_indexes_untouched(workspace: Path) -> None:
    parent = file_item(workspace, "Parent", kind="Epic")
    source = file_item(workspace, "Child")
    result = runner.invoke(
        app,
        ["work", "reparent", source, "--parent", parent, "--workspace", str(workspace), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path_mapping"][source].startswith(f"{parent}/children/")
    assert payload["applied"] is True and payload["rolled_back"] is False
    layout = resolve_workspace(str(workspace))
    assert layout.bundle_dir.joinpath(f"{payload['path_mapping'][source]}.md").is_file()
    assert "work/index.md" not in payload["indexes"]
    assert layout.bundle_dir.joinpath("work/index.md").exists() is False


def _init_workspace_git(workspace: Path) -> str:
    for args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
        ("add", "."),
        ("commit", "-m", "Initial"),
    ):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_reparent_batch_moves_every_path_in_one_commit(workspace: Path) -> None:
    parent = file_item(workspace, "Parent", kind="Epic")
    sources = [file_item(workspace, "A"), file_item(workspace, "B")]
    before = _init_workspace_git(workspace)
    result = runner.invoke(
        app,
        ["work", "reparent", *sources, "--parent", parent, "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["sources"] == sources
    assert payload["path_mapping"] == {
        "work/feature-a": "work/epic-parent/children/feature-a",
        "work/feature-b": "work/epic-parent/children/feature-b",
    }
    assert payload["applied"] is True and payload["rolled_back"] is False
    assert payload["commit"]["status"] == "committed"
    assert payload["commit"]["subject"] == "workspace: reparent feature-a, feature-b under epic-parent"
    layout = resolve_workspace(str(workspace))
    for source, destination in payload["path_mapping"].items():
        assert not layout.bundle_dir.joinpath(f"{source}.md").exists()
        assert layout.bundle_dir.joinpath(f"{destination}.md").is_file()
    commits = subprocess.run(
        ["git", "-C", str(workspace), "rev-list", "--count", f"{before}..HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert commits.stdout.strip() == "1"
    actual_commit = subprocess.run(
        ["git", "-C", str(workspace), "log", "-1", "--format=%H%n%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert actual_commit == [payload["commit"]["sha"], payload["commit"]["subject"]]


def test_reparent_batch_refusal_moves_nothing(workspace: Path) -> None:
    parent = file_item(workspace, "Parent", kind="Epic")
    source = file_item(workspace, "A")
    before_commit = _init_workspace_git(workspace)
    bundle = resolve_workspace(str(workspace)).bundle_dir
    before = {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    result = runner.invoke(
        app,
        ["work", "reparent", source, "work/missing", "--parent", parent, "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.GENERIC
    envelope = json.loads(result.stdout)["error"]
    assert envelope["reason"] == "refused"
    assert envelope["payload"]["sources"] == [source, "work/missing"]
    assert envelope["payload"]["applied"] is False
    assert bundle.joinpath(f"{source}.md").is_file()
    assert {path.relative_to(bundle): path.read_bytes() for path in bundle.rglob("*") if path.is_file()} == before
    after_commit = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert after_commit == before_commit


def test_reparent_single_path_json_has_no_sources_key(workspace: Path) -> None:
    parent = file_item(workspace, "Parent", kind="Epic")
    source = file_item(workspace, "A")
    result = runner.invoke(
        app,
        ["work", "reparent", source, "--parent", parent, "--dry-run", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert "sources" not in json.loads(result.stdout)


@pytest.mark.parametrize("dry_run", [False, True])
def test_reparent_single_path_preserves_human_output_and_subject(workspace: Path, dry_run: bool) -> None:
    parent = file_item(workspace, "Parent", kind="Epic")
    source = file_item(workspace, "A")
    _init_workspace_git(workspace)
    args = ["work", "reparent", source, "--parent", parent, "--workspace", str(workspace)]
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    if dry_run:
        assert result.stdout == "work/feature-a -> work/epic-parent/children/feature-a\n"
        assert resolve_workspace(str(workspace)).bundle_dir.joinpath(f"{source}.md").is_file()
    else:
        commit = subprocess.run(
            ["git", "-C", str(workspace), "log", "-1", "--format=%H"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert result.stdout == (
            "[ok] work mutation applied\n"
            f"[ok] committed {commit[:12]} workspace: reparent feature-a under epic-parent\n"
        )


def test_incomplete_path_apply_emits_the_envelope(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work_main.work, "run_reparent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        work_main.wire_work,
        "path_mutation_payload",
        lambda _result, **_kwargs: {
            "path_mapping": {"work/feature-a": "work/epic-e/children/feature-a"},
            "indexes": [],
            "warnings": [],
            "refusals": [],
            "applied": True,
            "rolled_back": True,
            "failures": ["stale snapshot"],
        },
    )
    result = runner.invoke(
        app,
        ["work", "reparent", "work/feature-a", "--parent", "work/epic-e", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    assert doc["error"]["reason"] == "incomplete-apply"
    assert doc["error"]["payload"]["failures"] == ["stale snapshot"]
    assert "stale snapshot" in result.stderr


def test_adopt_live_apply_leaves_lane_indexes_untouched(workspace: Path) -> None:
    release = file_item(workspace, "R1", kind="Release")
    source = file_item(workspace, "Epic", kind="Epic")
    result = runner.invoke(
        app,
        ["work", "adopt", source, "--release", release, "--workspace", str(workspace), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path_mapping"][source].startswith(f"{release}/children/")
    assert payload["applied"] is True and payload["rolled_back"] is False
    layout = resolve_workspace(str(workspace))
    assert layout.bundle_dir.joinpath(f"{payload['path_mapping'][source]}.md").is_file()
    assert "work/index.md" not in payload["indexes"]
    assert layout.bundle_dir.joinpath("work/index.md").exists() is False


def test_archive_live_apply_leaves_lane_indexes_untouched(workspace: Path) -> None:
    path = file_item(workspace, "Done", kind="Bug")
    layout = resolve_workspace(str(workspace))
    page = layout.bundle_dir / f"{path}.md"
    document = load(page)
    document.set("work_status", "resolved")
    document.save()
    result = runner.invoke(app, ["work", "archive", path, "--workspace", str(workspace), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path_mapping"][path] == "work/_archive/bug-done"
    assert payload["applied"] is True and payload["rolled_back"] is False
    assert layout.bundle_dir.joinpath("work/_archive/bug-done.md").is_file()
    assert "work/index.md" not in payload["indexes"]
    assert layout.bundle_dir.joinpath("work/index.md").exists() is False


def test_archive_refusal_names_each_refusal_on_stderr(workspace: Path) -> None:
    path = file_item(workspace, "Done", kind="Bug")
    layout = resolve_workspace(str(workspace))
    page = layout.bundle_dir / f"{path}.md"
    document = load(page)
    document.set("work_status", "resolved")
    document.save()
    sources_dir = layout.bundle_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.joinpath("broken.md").write_text(
        f"---\ntitle: Broken\nbad: value: here\n---\n\n"
        f"A link to [Done](../{path}.md) in a document that will not parse.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["work", "archive", path, "--workspace", str(workspace)])

    assert result.exit_code == exit_codes.GENERIC
    assert "sources/broken.md" in result.stderr
    assert "parse-error" in result.stderr
    assert "cannot rewrite a document that failed to parse" in result.stderr
    assert "archive refused; nothing was applied" in result.stderr


def test_archive_of_a_child_is_refused_not_top_level_and_names_the_root(workspace: Path) -> None:
    epic = file_item(workspace, "Parent", kind="Epic")
    child = file_item(workspace, "Kid", kind="Bug", parent=epic)
    layout = resolve_workspace(str(workspace))
    for path in (epic, child):
        document = load(layout.bundle_dir / f"{path}.md")
        document.set("work_status", "resolved")
        document.save()

    result = runner.invoke(app, ["work", "archive", child, "--workspace", str(workspace), "--json"])

    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert doc["error"]["reason"] == "refused"
    refusals = doc["error"]["payload"]["refusals"]
    assert [refusal["kind"] for refusal in refusals] == ["not-top-level"]
    assert f"archive {epic}" in refusals[0]["detail"]
    assert layout.bundle_dir.joinpath(f"{child}.md").is_file()


def test_archive_incomplete_apply_emits_the_envelope(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(
        plan=SimpleNamespace(move_plan=None),
        wiki_commit=None,
        wiki_plan=SimpleNamespace(moves=SimpleNamespace(stranded=())),
        ok=False,
    )
    monkeypatch.setattr(work_main, "run_archive", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(
        work_main.wire_work,
        "archive_payload",
        lambda *_args, **_kwargs: {
            "warnings": [],
            "path_mapping": {"work/bug-done": "work/_archive/bug-done"},
            "indexes": [],
            "logged": None,
            "conflict": [],
            "refusals": [],
            "applied": True,
            "rolled_back": False,
            "failures": ["stale lane index"],
        },
    )
    result = runner.invoke(app, ["work", "archive", "work/bug-done", "--workspace", str(workspace), "--json"])
    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    assert doc["error"]["reason"] == "incomplete-apply"
    assert doc["error"]["payload"]["failures"] == ["stale lane index"]
    assert "stale lane index" in result.stderr


def test_every_advance_refusal_family_and_an_unknown_next_path_exit_nonzero(workspace: Path) -> None:
    """C4's exit-code half (D-081): every refusal is a refusal regardless of which check
    produced it. `unreadable-member` is covered on Windows by the locked-handle test below."""
    owner_required_item = file_item(workspace, "Owner Required")
    layout = resolve_workspace(str(workspace))
    owner_page = layout.bundle_dir / f"{owner_required_item}.md"
    document = load(owner_page)
    document.set("phase", "execute")
    document.set("work_status", "accepted")
    document.save()

    resolved_in_required_item = file_item(workspace, "Resolved In Required")
    resolved_in_page = layout.bundle_dir / f"{resolved_in_required_item}.md"
    document = load(resolved_in_page)
    document.set("phase", "finish")
    document.set("work_status", "in-progress")
    document.save()

    cases = [
        (["work", "advance", "work/no-such-item", "--workspace", str(workspace), "--json"], "unknown-path"),
        (["work", "advance", owner_required_item, "--workspace", str(workspace), "--json"], "owner-required"),
        (
            ["work", "advance", resolved_in_required_item, "--workspace", str(workspace), "--json"],
            "resolved-in-required",
        ),
        (["next", "work/no-such-item", "--workspace", str(workspace), "--json"], "unknown work item"),
    ]
    for args, expected_fragment in cases:
        result = runner.invoke(app, args)
        assert result.exit_code != 0, (args, result.output)
        assert expected_fragment in result.stderr, (args, result.stderr)


@pytest.mark.skipif(sys.platform != "win32", reason="exercises a Windows exclusive file handle (C4)")
def test_advance_names_a_locked_member_instead_of_collapsing_into_unknown_path(workspace: Path) -> None:
    path = file_item(workspace, "Locked")
    layout = resolve_workspace(str(workspace))
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()

    import ctypes

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    handle = ctypes.windll.kernel32.CreateFileW(
        str(page), GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
    )
    assert handle != INVALID_HANDLE_VALUE
    try:
        result = runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace), "--json"])
        assert result.exit_code != 0, result.output
        assert "unreadable-member" in result.stderr
        assert f"{path}.md" in result.stderr
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    assert page.read_bytes() == before


def test_split_topology_files_and_lints_clean(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    root = vault / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    code = tmp_path / "code"
    (code / "packages/foo").mkdir(parents=True)
    layout = resolve_workspace(str(root))
    layout.manifest_path.write_text(
        f'version: 1\nworkflow_config: workflow.yaml\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )

    file_result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Split-topology filing",
            "--kind",
            "Feature",
            "--summary",
            "One line",
            "--affects",
            "packages/foo",
            "--workspace",
            str(root),
            "--json",
        ],
    )
    assert file_result.exit_code == 0, file_result.output

    lint_result = runner.invoke(app, ["work", "lint", "--workspace", str(root), "--json"])
    payload = json.loads(lint_result.stdout)
    assert payload["ok"] is True, payload["findings"]
    assert not any(finding["code"] == "targets.affects-missing" for finding in payload["findings"])


def test_return_refuses_an_item_that_is_not_at_finish(workspace: Path) -> None:
    """The way home exists, and declines to invent one from an earlier phase."""
    path = file_item(workspace, "Beta")
    result = runner.invoke(app, ["work", "advance", path, "--return", "--workspace", str(workspace), "--json"])
    assert result.exit_code != 0
    assert "return-not-available" in result.stderr


@pytest.mark.parametrize(
    ("scope_args", "expected"),
    [([], ()), (["--return-scope", "A", "--return-scope", "B"], ("A", "B"))],
    ids=["omitted", "repeated"],
)
def test_advance_forwards_return_scope_as_a_tuple(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, scope_args: list[str], expected: tuple[str, ...]
) -> None:
    captured: dict[str, object] = {}

    def spy(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(work_main, "run_stage_advance", spy)
    result = runner.invoke(
        app,
        [
            "work",
            "advance",
            "work/feature-returned",
            "--from",
            "finish",
            "--return",
            *scope_args,
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["return_"] is True
    assert captured["return_scope"] == expected


def test_return_scope_without_return_uses_the_core_refusal(workspace: Path) -> None:
    path = file_item(workspace, "Scope without return")
    page = workspace / "okf" / f"{path}.md"
    before = page.read_bytes()
    result = runner.invoke(
        app,
        ["work", "advance", path, "--return-scope", "A", "--workspace", str(workspace), "--json"],
    )
    assert result.exit_code == exit_codes.GENERIC, result.output
    error = json.loads(result.stdout)["error"]
    assert error["reason"] == "refused"
    assert error["payload"]["refusal"]["reason"] == "return-scope-invalid"
    assert "return-scope-invalid" in result.stderr
    assert page.read_bytes() == before


def test_return_scope_help_explains_repeatability() -> None:
    result = runner.invoke(app, ["help", "work", "advance", "--json"])
    assert result.exit_code == 0, result.output
    options = json.loads(result.stdout)["options"]
    option = next((entry for entry in options if "--return-scope" in entry["opts"]), None)
    assert option is not None
    assert "repeatable" in option["help"]
    assert "--return" in option["help"]


def _fm(workspace: Path, path: str) -> dict[str, object]:
    return load(workspace / "okf" / f"{path}.md").fm_data()


def _abs(*parts: str) -> str:
    """Host-absolute on every platform: `/wt/x` is not absolute on win32."""
    return str(Path(Path.cwd().anchor, *parts))


def test_no_infer_worktree_is_accepted_and_warns(workspace: Path) -> None:
    path = file_item(workspace, "Opt out")
    result = runner.invoke(
        app, ["work", "advance", path, "--no-infer-worktree", "--workspace", str(workspace), "--json"]
    )
    assert result.exit_code == 0, result.output
    assert "--no-infer-worktree is retired and ignored" in result.output


def test_advance_rejects_worktree_flag(workspace: Path) -> None:
    path = file_item(workspace, "Flag gone")
    result = runner.invoke(app, ["work", "advance", path, "--worktree", "/x", "--workspace", str(workspace)])
    assert result.exit_code == 2


def test_no_infer_worktree_hidden_from_help() -> None:
    result = runner.invoke(app, ["work", "advance", "--help"])
    assert "--no-infer-worktree" not in result.output
    assert "--worktree" not in result.output


def test_a_release_only_field_on_another_kind_is_refused(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "T",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--version",
            "v2",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert doc["error"]["payload"]["refusal"] == "invalid-release-field"


def test_touch_active_work_stamps_the_current_phase(workspace: Path) -> None:
    path = file_item(workspace, "Done", kind="Bug")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    pointer = resolve_workspace(str(workspace)).cache_dir / "active-work.json"
    pointer.unlink()

    result = runner.invoke(app, ["work", "touch-active-work", path, "--json", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["phase"] == "design" and payload["refusal"] is None
    assert json.loads(Path(payload["pointer_path"]).read_text(encoding="utf-8"))["phase"] == "design"


def test_touch_active_work_refuses_an_unphased_item(workspace: Path) -> None:
    path = file_item(workspace, "Done", kind="Bug")

    result = runner.invoke(app, ["work", "touch-active-work", path, "--json", "--workspace", str(workspace)])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["payload"]["refusal"]["reason"] == "inactive-phase"


def test_touch_active_work_degraded_write_warns_and_exits_zero(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gw_kernel.workspace import provenance

    path = file_item(workspace, "Done", kind="Bug")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    monkeypatch.setattr(provenance, "write_active_work", lambda *a, **k: None)

    result = runner.invoke(app, ["work", "touch-active-work", path, "--json", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["pointer_path"] is None
    assert "active-work pointer was not written" in result.stderr


def test_touch_active_work_human_mode_reports_the_phase_on_success(workspace: Path) -> None:
    path = file_item(workspace, "Done", kind="Bug")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    pointer = resolve_workspace(str(workspace)).cache_dir / "active-work.json"
    pointer.unlink()

    result = runner.invoke(app, ["work", "touch-active-work", path, "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert f"{path}: active-work pointer -> design" in result.stdout


def test_touch_active_work_human_mode_degraded_write_omits_the_phase_line(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gw_kernel.workspace import provenance

    path = file_item(workspace, "Done", kind="Bug")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    monkeypatch.setattr(provenance, "write_active_work", lambda *a, **k: None)

    result = runner.invoke(app, ["work", "touch-active-work", path, "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "active-work pointer was not written" in result.stderr
    assert "->" not in result.stdout


def _execute_item_without_baseline(workspace: Path) -> str:
    """A filed item moved to `execute` with code `affects`, over a clean declared repo and no `start_sha`."""
    code = workspace.parent / "code-bypass"
    (code / "packages/a").mkdir(parents=True)
    (code / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
        ("add", "."),
        ("commit", "-m", "first"),
    ):
        subprocess.run(["git", *args], cwd=code, check=True, capture_output=True)
    layout = resolve_workspace(str(workspace))
    layout.manifest_path.write_text(
        "version: 1\nworkflow_config: workflow.yaml\n"
        f"repositories:\n  code:\n    path: {json.dumps(str(code))}\n    gate:\n      full: 'true'\n",
        encoding="utf-8",
        newline="\n",
    )
    result = runner.invoke(
        app,
        [
            "work",
            "file",
            "--title",
            "Bypassed",
            "--kind",
            "Feature",
            "--summary",
            "d",
            "--affects",
            "packages/a",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    path = str(json.loads(result.stdout)["path"])
    page = workspace / "okf" / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    assert "work_status: open\n" in text
    text = text.replace("work_status: open\n", "work_status: in-progress\nphase: execute\n", 1)
    text = text.replace("phase: design\n", "")
    page.write_text(text, encoding="utf-8", newline="")
    # a bypassed commit-gate code never exempts the receipt: mint one for the clean tree
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=code, check=True, capture_output=True, text=True
    ).stdout.strip()
    receipt = GateRun(
        run_id="20260928T120000Z-00000000",
        repo="code",
        worktree=str(code),
        head="b" * 40,
        tree=tree,
        clean=True,
        tree_changed=False,
        scope="full",
        command="true",
        names=(),
        exit=0,
        log_path="/l",
        log_tail="",
        started="2026-09-28T12:00:00Z",
        duration_s=1.0,
    )
    target = workspace / "okf" / path / "references" / "03-gate-receipts.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_receipt(path, [receipt], created="2026-09-28"), encoding="utf-8", newline="\n")
    return path


def test_advance_skip_gate_bypasses_one_refusal_and_reports_it(workspace: Path) -> None:
    path = _execute_item_without_baseline(workspace)
    base = ["work", "advance", path, "--workspace", str(workspace), "--json"]
    refused = runner.invoke(app, [*base, "--skip-gate", "worktree-missing"])
    assert refused.exit_code != 0
    assert "gate-bypass-invalid" in refused.stderr
    bypassed = runner.invoke(app, [*base, "--skip-gate", "worktree-missing", "--reason", "r", "--actor", "pat"])
    assert bypassed.exit_code == 0, bypassed.output
    payload = json.loads(bypassed.stdout)
    assert payload["gate_bypass"]["code"] == "worktree-missing"
    assert payload["gate_bypass"]["actor"] == "pat" and payload["gate_bypass"]["decision_id"]


def _executing_item_in_a_declared_repo(workspace: Path, tmp_path: Path) -> tuple[str, Path, str]:
    repo = tmp_path / "code"
    repo.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["commit", "--allow-empty", "-m", "first"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    manifest = workspace / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "repositories: {}\n", f"repositories:\n  code:\n    path: {repo.as_posix()}\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    path = file_item(workspace, "Baselined")
    page = workspace / "okf" / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    text = text.replace("work_status: open", "work_status: in-progress\nphase: execute")
    page.write_text(text, encoding="utf-8", newline="")
    return path, repo, head


def _with_frontmatter(workspace: Path, path: str, lines: str) -> None:
    page = workspace / "okf" / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(
        text.replace("phase: execute", "phase: execute\n" + lines.rstrip("\n")), encoding="utf-8", newline=""
    )
