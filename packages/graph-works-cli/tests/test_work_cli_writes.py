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
            "--version",
            "v2",
            "--target-date",
            "2026-09-01",
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
        work_main.rendering,
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


def test_archive_incomplete_apply_emits_the_envelope(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = SimpleNamespace(
        plan=SimpleNamespace(move_plan=None),
        wiki_plan=SimpleNamespace(moves=SimpleNamespace(stranded=())),
        ok=False,
    )
    monkeypatch.setattr(work_main, "run_archive", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(
        work_main.rendering,
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
