"""The structured refusal envelope for `gw work --json` (D-004/D-007).

A `--json` refusal must print a single-key `{"error": {...}}` document on
stdout and still exit non-zero -- see the design at
work/epic-work-mutation-performance-hygiene/children/bug-json-refusal-envelope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import rendering
from okf_io import load
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    return root


def file_item(workspace: Path, title: str, *, kind: str = "Feature") -> str:
    args = ["work", "file", "--title", title, "--kind", kind, "--summary", "d", "--workspace", str(workspace), "--json"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return str(json.loads(result.stdout)["path"])


def test_post_payload_refusal_carries_the_computed_refusals_in_the_envelope(workspace: Path) -> None:
    """A post-payload class refusal: `archive` against a target whose referrer
    set contains an unparseable document."""
    path = file_item(workspace, "Done", kind="Bug")
    from graph_works_cli.workspace_resolution import resolve_workspace

    layout = resolve_workspace(str(workspace))
    page = layout.bundle_dir / f"{path}.md"
    document = load(page)
    document.set("work_status", "resolved")
    document.save()
    sources_dir = layout.bundle_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.joinpath("broken.md").write_text(
        f"---\ntitle: Broken\nbad: value: here\n---\n\nA link to [Done](../{path}.md).\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["work", "archive", path, "--workspace", str(workspace), "--json"])

    assert result.exit_code == exit_codes.GENERIC
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    error = doc["error"]
    assert error["reason"] == "refused"
    assert error["exit_code"] == exit_codes.GENERIC
    assert error["payload"] is not None
    assert error["payload"]["refusals"]
    assert any(r["path"] == "sources/broken.md" for r in error["payload"]["refusals"])


def test_pre_payload_failure_carries_a_null_payload(workspace: Path) -> None:
    """A pre-payload class failure: `next` against an unknown path."""
    result = runner.invoke(app, ["work", "next", "work/no-such-item", "--workspace", str(workspace), "--json"])

    assert result.exit_code == exit_codes.AMBIGUOUS
    doc = json.loads(result.stdout)
    assert set(doc) == {"error"}
    error = doc["error"]
    assert error["reason"] == "unresolved"
    assert error["payload"] is None
    assert error["exit_code"] == exit_codes.AMBIGUOUS


def test_success_and_refusal_are_structurally_discriminable(workspace: Path) -> None:
    """One success invocation and one refusal invocation of the same verb:
    `"error"` never appears in a success document, and a refusal document is
    exactly `{"error": ...}`."""
    success = runner.invoke(app, ["work", "next", "work/no-such-item-x", "--workspace", str(workspace)])
    assert success.exit_code != 0  # human mode, unaffected

    ok_path = file_item(workspace, "Alpha")
    success_doc = runner.invoke(app, ["work", "next", ok_path, "--workspace", str(workspace), "--json"])
    assert success_doc.exit_code == 0
    success_payload = json.loads(success_doc.stdout)
    assert "error" not in success_payload

    refusal = runner.invoke(app, ["work", "next", "work/no-such-item-x", "--workspace", str(workspace), "--json"])
    refusal_payload = json.loads(refusal.stdout)
    assert set(refusal_payload) == {"error"}


def test_every_work_json_command_declares_json_through_the_shared_option() -> None:
    """Every command registered under `work_app` (plus the root-level `gw
    next` alias) must declare `--json` through `rendering.json_option`, not a
    hand-rolled `typer.Option`, or its refusals silently degrade to `fail()`'s
    assert."""
    from graph_works_cli.work_cli.decision import decision_app
    from graph_works_cli.work_cli.main import work_app

    checked = 0
    for group in (work_app, decision_app):
        for command in group.registered_commands:
            # Typer command info -- build the click command to inspect params.
            import typer.main

            click_command = typer.main.get_command_from_info(
                command, pretty_exceptions_short=True, rich_markup_mode=None
            )
            json_params = [p for p in click_command.params if "--json" in getattr(p, "opts", [])]
            if not json_params:
                continue
            checked += 1
            (param,) = json_params
            # Typer wraps a declared callback in a fresh closure per `get_command_from_info()`
            # call (`update_wrapper` preserves `__wrapped__`), so identity is checked against
            # that, not the click-visible `param.callback` itself.
            assert getattr(param.callback, "__wrapped__", None) is rendering._set_json_mode, (
                f"{command.name}: --json must use rendering.json_option(), not a hand-rolled Option"
            )
    assert checked > 0


def test_root_callback_resets_json_mode_before_the_next_command_parses(workspace: Path) -> None:
    """The root callback's reset must run before the subcommand's own option
    callback -- a `--json` refusal must not leak `True` into a following
    non-`--json` refusal in the same process (`CliRunner` runs both in one
    process, so a leaked ContextVar would otherwise carry over)."""
    first = runner.invoke(app, ["work", "next", "work/no-such-item", "--workspace", str(workspace), "--json"])
    assert first.exit_code == exit_codes.AMBIGUOUS
    assert json.loads(first.stdout)["error"]["reason"] == "unresolved"

    second = runner.invoke(app, ["work", "next", "work/no-such-item", "--workspace", str(workspace)])
    assert second.exit_code == exit_codes.AMBIGUOUS
    assert second.stdout == ""
