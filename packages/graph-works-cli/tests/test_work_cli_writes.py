"""Path-native write commands and refusal stream contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import main as work_main
from graph_works_cli.workspace_resolution import resolve_workspace
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


def test_file_incomplete_apply_names_the_blocking_member_not_the_item(workspace: Path) -> None:
    """§1/D-095: a CRLF-flipped `work/index.md` makes filing refuse, and the
    top-level error must name `work/index.md` -- the file actually in the way
    -- not the item being filed.
    """
    file_item(workspace, "Baseline item")
    index = resolve_workspace(str(workspace)).bundle_dir / "work" / "index.md"
    index.write_bytes(index.read_bytes().replace(b"\n", b"\r\n"))

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
        ],
    )

    assert result.exit_code != 0
    last_line = [line for line in result.stderr.splitlines() if line.strip()][-1]
    assert "work/index.md" in last_line
    assert "feature-second-item" not in last_line


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


def test_advance_applies_by_default_and_accepts_released_at(workspace: Path) -> None:
    path = file_item(workspace, "Alpha")
    result = runner.invoke(
        app, ["work", "advance", path, "--released-at", "2026-09-01", "--workspace", str(workspace), "--json"]
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["path"] == path and payload["applied"] is True
    assert "work_status" in payload


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


def test_reparent_live_apply_updates_lane_indexes(workspace: Path) -> None:
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
    assert "work/index.md" in payload["indexes"]


def test_incomplete_path_apply_emits_the_envelope(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work_main.work, "run_reparent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        work_main.wire_work,
        "path_mutation_payload",
        lambda _result: {
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


def test_adopt_live_apply_updates_lane_indexes(workspace: Path) -> None:
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
    assert "work/index.md" in payload["indexes"]


def test_archive_live_apply_updates_lane_indexes(workspace: Path) -> None:
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
    assert "work/index.md" in payload["indexes"]


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
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
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


def _fm(workspace: Path, path: str) -> dict[str, object]:
    return load(workspace / "okf" / f"{path}.md").fm_data()


def _abs(*parts: str) -> str:
    """Host-absolute on every platform: `/wt/x` is not absolute on win32."""
    return str(Path(Path.cwd().anchor, *parts))


def test_record_placement_writes_the_pair_and_never_advances(workspace: Path) -> None:
    path = file_item(workspace, "Placed")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace), "--json"]).exit_code == 0
    phase = _fm(workspace, path)["phase"]
    placed = _abs("wt", "placed")

    result = runner.invoke(
        app,
        [
            "work",
            "record-placement",
            path,
            "--root",
            path,
            "--phase",
            str(phase),
            "--worktree",
            placed,
            "--branch",
            "psprowls/placed-1a2b3c4d",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["refusal"] is None and payload["written"] is True and payload["changed"] is True
    assert payload["after"] == {"worktree": placed, "branch": "psprowls/placed-1a2b3c4d"}
    fm = _fm(workspace, path)
    assert (fm["worktree"], fm["branch"], fm["phase"]) == (placed, "psprowls/placed-1a2b3c4d", phase)


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
def test_record_placement_refusal_is_an_envelope_and_writes_nothing(workspace: Path, json_output: bool) -> None:
    path = file_item(workspace, "Stale")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace), "--json"]).exit_code == 0
    page = workspace / "okf" / f"{path}.md"
    before = page.read_bytes()

    result = runner.invoke(
        app,
        [
            "work",
            "record-placement",
            path,
            "--root",
            path,
            "--phase",
            "finish",
            "--worktree",
            _abs("wt", "x"),
            "--branch",
            "b/x",
            "--workspace",
            str(workspace),
            *(["--json"] if json_output else []),
        ],
    )

    assert result.exit_code != 0
    assert "phase-mismatch" in result.stderr
    if json_output:
        doc = json.loads(result.stdout)
        assert doc["error"]["reason"] == "refused"
        assert doc["error"]["payload"]["refusal"]["reason"] == "phase-mismatch"
    else:
        assert result.stdout == ""
    assert page.read_bytes() == before


def test_record_placement_dry_run_writes_nothing(workspace: Path) -> None:
    path = file_item(workspace, "Dry")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace), "--json"]).exit_code == 0
    page = workspace / "okf" / f"{path}.md"
    before = page.read_bytes()
    phase = str(_fm(workspace, path)["phase"])

    result = runner.invoke(
        app,
        [
            "work",
            "record-placement",
            path,
            "--root",
            path,
            "--phase",
            phase,
            "--worktree",
            _abs("wt", "d"),
            "--branch",
            "b/d",
            "--dry-run",
            "--workspace",
            str(workspace),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "would record" in result.stdout
    assert page.read_bytes() == before


@pytest.mark.parametrize("no_infer", [False, True], ids=["default", "opt-out"])
def test_advance_forwards_the_inference_opt_out(workspace: Path, monkeypatch, no_infer: bool) -> None:
    seen: dict[str, object] = {}
    real = work_main.run_stage_advance

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(work_main, "run_stage_advance", spy)
    path = file_item(workspace, "Opt out")
    result = runner.invoke(
        app,
        [
            "work",
            "advance",
            path,
            *(["--no-infer-worktree"] if no_infer else []),
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["infer_worktree"] is (not no_infer)


def _placement_args(workspace: Path, path: str, *extra: str) -> list[str]:
    return [
        "work",
        "record-placement",
        path,
        "--root",
        path,
        "--phase",
        str(_fm(workspace, path)["phase"]),
        "--worktree",
        _abs("wt", "observed"),
        "--branch",
        "b/observed",
        "--workspace",
        str(workspace),
        *extra,
    ]


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("mode", ["recorded", "unchanged", "would record"])
def test_record_placement_distinguishes_write_replay_and_preview(workspace: Path, mode: str, json_output: bool) -> None:
    path = file_item(workspace, "Placement modes")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    page = workspace / "okf" / f"{path}.md"
    if mode == "unchanged":
        first = runner.invoke(app, _placement_args(workspace, path))
        assert first.exit_code == 0, first.output
    before = page.read_bytes()
    fm_before = _fm(workspace, path)
    extra = (["--dry-run"] if mode == "would record" else []) + (["--json"] if json_output else [])
    result = runner.invoke(app, _placement_args(workspace, path, *extra))
    assert result.exit_code == 0, result.output
    if json_output:
        payload = json.loads(result.stdout)
        assert set(payload) == {
            "path",
            "root",
            "expected_phase",
            "current_phase",
            "before",
            "after",
            "changed",
            "applied",
            "written",
            "rolled_back",
            "failures",
            "warnings",
            "refusal",
            "repo_note",
        }
        assert payload["path"] == payload["root"] == path
        assert payload["expected_phase"] == payload["current_phase"] == fm_before["phase"]
        assert payload["after"] == {"worktree": _abs("wt", "observed"), "branch": "b/observed"}
        assert payload["changed"] is (mode != "unchanged")
        assert payload["applied"] is (mode == "recorded")
        assert payload["written"] is (mode == "recorded")
        assert payload["rolled_back"] is False
        assert payload["refusal"] is None and payload["failures"] == [] and payload["warnings"] == []
        if mode == "unchanged":
            assert payload["before"] == payload["after"]
        else:
            assert payload["before"] == {"worktree": None, "branch": None}
        if payload["repo_note"]:
            assert payload["repo_note"] in result.stderr
    else:
        assert f": {mode} worktree={_abs('wt', 'observed')} branch=b/observed" in result.stdout
        assert f"phase={fm_before['phase']}, root={path}" in result.stdout
    if mode == "recorded":
        assert page.read_bytes() != before
        fm_after = _fm(workspace, path)
        for field in ("worktree", "branch", "updated"):
            fm_before.pop(field, None)
            fm_after.pop(field, None)
        assert fm_before == fm_after
    else:
        assert page.read_bytes() == before


def test_record_placement_text_replacement_names_the_previous_pair(workspace: Path) -> None:
    path = file_item(workspace, "Replace placement")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    first = runner.invoke(app, _placement_args(workspace, path))
    assert first.exit_code == 0, first.output
    args = _placement_args(workspace, path)
    args[args.index("--branch") + 1] = "b/replaced"
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert ": recorded " in result.stdout and "branch=b/replaced" in result.stdout
    assert f"was: worktree={_abs('wt', 'observed')} branch=b/observed" in result.stdout


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
def test_record_placement_stale_preimage_is_incomplete_apply(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    json_output: bool,
) -> None:
    from graph_works_core.orchestrate import placement

    path = file_item(workspace, "Stale preimage")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    page = workspace / "okf" / f"{path}.md"
    external = page.read_bytes() + b"\nExternal edit.\n"
    real_apply = placement.apply_mutation

    def race(*args, **kwargs):
        page.write_bytes(external)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(placement, "apply_mutation", race)
    result = runner.invoke(app, _placement_args(workspace, path, *(["--json"] if json_output else [])))
    assert result.exit_code == exit_codes.GENERIC
    assert "apply was incomplete" in result.stderr and "changed since planning" in result.stderr
    assert "[ok]" not in result.stdout and "would record" not in result.stdout
    if json_output:
        doc = json.loads(result.stdout)
        assert set(doc) == {"error"}
        assert doc["error"]["command"] == "work record-placement"
        assert doc["error"]["reason"] == "incomplete-apply"
        payload = doc["error"]["payload"]
        assert payload["refusal"] is None
        assert payload["applied"] is True and payload["changed"] is True and payload["written"] is False
        assert payload["rolled_back"] is False
        assert any("changed since planning" in failure for failure in payload["failures"])
    else:
        assert result.stdout == ""
    assert page.read_bytes() == external


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("rolled_back", [False, True], ids=["warning", "rollback"])
def test_record_placement_projects_application_warnings_and_rollback(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    json_output: bool,
    rolled_back: bool,
) -> None:
    from dataclasses import replace

    from graph_works_core.orchestrate.placement import run_record_placement
    from graph_works_core.workspace.transactions import MutationApplication

    path = file_item(workspace, "Application projection")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0

    def application_result(*args, **kwargs):
        # Keep the real plan; supply transaction outcomes that are rare at the CLI boundary.
        result = run_record_placement(*args, **{**kwargs, "dry_run": True})
        application = MutationApplication(
            transaction_id="projection",
            journal=workspace / "journal.json",
            moved=(),
            written=() if rolled_back else (f"{path}.md",),
            created_directories=(),
            warnings=("transaction warning",),
            failures=(),
            rolled_back=rolled_back,
        )
        return replace(result, application=application, repo_note="repository note")

    monkeypatch.setattr(work_main, "run_record_placement", application_result, raising=False)
    result = runner.invoke(app, _placement_args(workspace, path, *(["--json"] if json_output else [])))
    if rolled_back:
        assert result.exit_code == exit_codes.GENERIC
        assert "apply was incomplete" in result.stderr
        assert "[ok]" not in result.stdout and "would record" not in result.stdout
        if json_output:
            doc = json.loads(result.stdout)
            assert doc["error"]["reason"] == "incomplete-apply"
            payload = doc["error"]["payload"]
            assert payload["refusal"] is None and payload["failures"] == []
            assert payload["rolled_back"] is True and payload["written"] is False
    else:
        assert result.exit_code == 0, result.output
        assert "transaction warning" in result.stderr and "repository note" in result.stderr
        if json_output:
            payload = json.loads(result.stdout)
            assert payload["warnings"] == ["transaction warning"]
            assert payload["repo_note"] == "repository note"
        else:
            assert ": recorded " in result.stdout


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize(
    ("exception", "reason", "code"),
    [
        (work_main.WorkspaceError("bad workspace"), "workspace", exit_codes.SCHEMA_MISMATCH),
        (ValueError("unresolved path"), "unresolved", exit_codes.AMBIGUOUS),
        (OSError("disk failure"), "io", exit_codes.GENERIC),
    ],
)
def test_record_placement_maps_core_exceptions(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    json_output: bool,
    exception: Exception,
    reason: str,
    code: int,
) -> None:
    path = file_item(workspace, "Exception projection")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0

    def fail(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(work_main, "run_record_placement", fail, raising=False)
    result = runner.invoke(app, _placement_args(workspace, path, *(["--json"] if json_output else [])))
    assert result.exit_code == code
    assert str(exception) in result.stderr
    if json_output:
        doc = json.loads(result.stdout)
        assert doc["error"]["command"] == "work record-placement"
        assert doc["error"]["reason"] == reason and doc["error"]["exit_code"] == code
        assert doc["error"]["payload"] is None
    else:
        assert result.stdout == ""


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
    from graph_works_core.workspace import provenance

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
    from graph_works_core.workspace import provenance

    path = file_item(workspace, "Done", kind="Bug")
    assert runner.invoke(app, ["work", "advance", path, "--workspace", str(workspace)]).exit_code == 0
    monkeypatch.setattr(provenance, "write_active_work", lambda *a, **k: None)

    result = runner.invoke(app, ["work", "touch-active-work", path, "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "active-work pointer was not written" in result.stderr
    assert "->" not in result.stdout
