"""`gw work ask` / `gw work ask-answer`: the CLI owns the clock and the verbatim question read."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from graph_works_cli import exit_codes
from graph_works_cli.cli import app
from graph_works_cli.work_cli import ask as ask_module
from graph_works_core.workspace.errors import WorkspaceError
from typer.testing import CliRunner

runner = CliRunner()
NOW = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)


def test_ask_clock_returns_the_current_utc_instant() -> None:
    before = datetime.now(UTC)
    actual = ask_module._now()
    after = datetime.now(UTC)
    assert actual.tzinfo == UTC
    assert before <= actual <= after


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ask_module, "_now", lambda: NOW)
    root = tmp_path / "works"
    assert runner.invoke(app, ["bootstrap", "--topic", "Demo", "--workspace", str(root)]).exit_code == 0
    filed = runner.invoke(
        app,
        ["work", "file", "--title", "Alpha", "--kind", "Feature", "--summary", "d", "--workspace", str(root), "--json"],
    )
    assert filed.exit_code == 0, filed.output
    return root


def _gw(workspace: Path, *args: str):
    return runner.invoke(app, [*args, "--workspace", str(workspace)])


def _ask(workspace: Path, *extra: str):
    return _gw(
        workspace,
        "work",
        "ask",
        "work/feature-alpha",
        "--kind",
        "choice",
        "--summary",
        "Pick one.",
        "--option",
        "merge=Merge it",
        "--option",
        "hold=Hold",
        *extra,
    )


def test_ask_json_writes_the_payload_and_prints_the_orca_strings(workspace: Path) -> None:
    result = _ask(workspace, "--question", "Full question ü", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    resource = payload["payload"]["resource"]
    assert resource.startswith("/work/feature-alpha/references/asks/") and resource.endswith("-001-choice.json")
    assert payload["orca"] == {"question": f"Pick one.\n\ngw-ask: {resource}", "options": "merge,hold"}
    assert payload["ok"] is True and payload["applied"] is True and payload["refusals"] == []
    stored = json.loads(Path(payload["payload"]["path"]).read_bytes())
    assert stored["question"] == "Full question ü"
    assert stored["created"] == "2026-09-27T10:00:00Z"


def test_question_file_is_read_byte_identical_including_crlf(workspace: Path, tmp_path: Path) -> None:
    source = tmp_path / "q.md"
    source.write_bytes("## Detail\r\n\r\nLine ü\r\n".encode())
    result = _ask(workspace, "--question-file", str(source), "--json")
    assert result.exit_code == 0, result.output
    stored = json.loads(Path(json.loads(result.stdout)["payload"]["path"]).read_bytes())
    assert stored["question"] == "## Detail\r\n\r\nLine ü\r\n"


def test_free_prints_null_options(workspace: Path) -> None:
    result = _gw(
        workspace,
        "work",
        "ask",
        "work/feature-alpha",
        "--kind",
        "free",
        "--summary",
        "Date?",
        "--question",
        "When?",
        "--json",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["orca"]["options"] is None


@pytest.mark.parametrize("token", ["a b", " a", "a "])
def test_a_refusal_is_an_envelope_and_writes_nothing(workspace: Path, token: str) -> None:
    result = _gw(
        workspace,
        "work",
        "ask",
        "work/feature-alpha",
        "--kind",
        "choice",
        "--summary",
        "S",
        "--question",
        "Q",
        "--option",
        f"{token}=x",
        "--option",
        "c=y",
        "--json",
    )
    assert result.exit_code != 0
    envelope = json.loads(result.stdout)["error"]
    assert envelope["reason"] == "refused"
    assert envelope["payload"]["refusals"] == ["option-token-invalid"]
    assert not (workspace / "okf" / "work" / "feature-alpha" / "references" / "asks").exists()


@pytest.mark.parametrize("args", [(), ("--question", "q", "--question-file", "x.md")])
def test_exactly_one_question_source_is_required(workspace: Path, args: tuple[str, ...]) -> None:
    result = _ask(workspace, *args, "--json")
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["reason"] == "usage"


def test_an_unreadable_question_file_is_an_io_error(workspace: Path, tmp_path: Path) -> None:
    result = _ask(workspace, "--question-file", str(tmp_path / "missing.md"), "--json")
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["reason"] == "io"


def test_a_label_less_option_uses_its_token(workspace: Path) -> None:
    result = _gw(
        workspace,
        "work",
        "ask",
        "work/feature-alpha",
        "--kind",
        "choice",
        "--summary",
        "S",
        "--question",
        "Q",
        "--option",
        "merge",
        "--option",
        "hold",
        "--json",
    )
    assert result.exit_code == 0, result.output
    stored = json.loads(Path(json.loads(result.stdout)["payload"]["path"]).read_bytes())
    assert stored["options"] == [{"token": "merge", "label": "merge"}, {"token": "hold", "label": "hold"}]


def test_option_label_is_trimmed_without_changing_the_token(workspace: Path) -> None:
    result = _gw(
        workspace,
        "work",
        "ask",
        "work/feature-alpha",
        "--kind",
        "choice",
        "--summary",
        "S",
        "--question",
        "Q",
        "--option",
        "merge=  Merge now  ",
        "--option",
        "hold=Hold=for later",
        "--json",
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    stored = json.loads(Path(payload["payload"]["path"]).read_bytes())
    assert stored["options"] == [
        {"token": "merge", "label": "Merge now"},
        {"token": "hold", "label": "Hold=for later"},
    ]
    assert payload["orca"]["options"] == "merge,hold"


@pytest.mark.parametrize("verb", ["ask", "ask-answer"])
@pytest.mark.parametrize(
    ("error", "reason", "code"),
    [
        (WorkspaceError("bad ask workspace"), "workspace", exit_codes.SCHEMA_MISMATCH),
        (OSError("ask storage unavailable"), "io", exit_codes.GENERIC),
    ],
)
def test_ask_write_failures_have_json_envelopes(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    error: Exception,
    reason: str,
    code: int,
) -> None:
    if verb == "ask":
        target = "run_ask"
    else:
        asked = json.loads(_ask(workspace, "--question", "Q", "--json").stdout)
        target = "run_ask_answer"

    def raise_error(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(ask_module, target, raise_error)
    if verb == "ask":
        result = _ask(workspace, "--question", "Q", "--json")
    else:
        result = _gw(workspace, "work", "ask-answer", asked["payload"]["resource"], "--choice", "merge", "--json")
    assert result.exit_code == code
    assert json.loads(result.stdout) == {
        "error": {
            "command": f"work {verb}",
            "reason": reason,
            "message": str(error),
            "exit_code": code,
            "payload": None,
        }
    }
    assert result.stderr == f"Error: {error}\n"


def test_dry_run_human_output_writes_nothing(workspace: Path) -> None:
    result = _ask(workspace, "--question", "Q", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "[ok] would write /work/feature-alpha/references/asks/" in result.stdout
    assert "gw-ask: /work/feature-alpha/references/asks/" in result.stdout
    assert "options: merge,hold" in result.stdout
    assert not (workspace / "okf" / "work" / "feature-alpha" / "references" / "asks").exists()


def test_answer_records_by_resource_and_replays_idempotently(workspace: Path) -> None:
    asked = json.loads(_ask(workspace, "--question", "Q", "--json").stdout)
    resource = asked["payload"]["resource"]
    first = _gw(workspace, "work", "ask-answer", resource, "--choice", "merge", "--by", "policy:auto-merge", "--json")
    assert first.exit_code == 0, first.output
    body = json.loads(first.stdout)
    assert json.loads(body["reply_body"]) == {"ask": resource, "choice": "merge", "effort": None, "notes": None}
    stored = json.loads(Path(asked["payload"]["path"]).read_bytes())
    assert stored["answer"]["by"] == "policy:auto-merge"
    replay = _gw(workspace, "work", "ask-answer", resource, "--choice", "merge", "--json")
    assert replay.exit_code == 0 and json.loads(replay.stdout)["changed"] is False
    other = _gw(workspace, "work", "ask-answer", resource, "--choice", "hold", "--json")
    assert other.exit_code != 0
    assert json.loads(other.stdout)["error"]["payload"]["refusals"] == ["already-answered"]


def test_answer_human_output_prints_the_reply_body(workspace: Path) -> None:
    asked = json.loads(_ask(workspace, "--question", "Q", "--json").stdout)
    result = _gw(workspace, "work", "ask-answer", asked["payload"]["resource"], "--choice", "hold", "--notes", "later")
    assert result.exit_code == 0, result.output
    assert "[ok] recorded answer in /work/feature-alpha/references/asks/" in result.stdout
    assert 'reply: {"ask": ' in result.stdout
    stored = json.loads(Path(asked["payload"]["path"]).read_bytes())
    assert stored["answer"]["by"] == "human"
    replay = _gw(
        workspace, "work", "ask-answer", asked["payload"]["resource"], "--choice", "hold", "--notes", "later", "--json"
    )
    assert replay.exit_code == 0 and json.loads(replay.stdout)["changed"] is False


def test_answer_validation_refusal_is_an_envelope(workspace: Path) -> None:
    asked = json.loads(_ask(workspace, "--question", "Q", "--json").stdout)
    result = _gw(workspace, "work", "ask-answer", asked["payload"]["resource"], "--choice", "pr", "--json")
    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["payload"]["refusals"] == ["answer-choice-invalid"]


@pytest.mark.parametrize("by", ["", "   "])
@pytest.mark.parametrize("dry_run", [True, False])
def test_blank_answerer_is_a_structured_refusal_without_byte_changes(workspace: Path, by: str, dry_run: bool) -> None:
    asked = json.loads(_ask(workspace, "--question", "Q", "--json").stdout)
    path = Path(asked["payload"]["path"])
    before = path.read_bytes()
    args = ["work", "ask-answer", asked["payload"]["resource"], "--choice", "merge", "--by", by, "--json"]
    if dry_run:
        args.append("--dry-run")
    result = _gw(workspace, *args)
    assert result.exit_code != 0
    envelope = json.loads(result.stdout)["error"]
    assert envelope["reason"] == "refused"
    assert envelope["payload"]["refusals"] == ["answer-by-invalid"]
    assert path.read_bytes() == before
