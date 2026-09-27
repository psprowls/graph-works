"""The shared error helper, promoted to the package root by C6 (§4.6)."""

from __future__ import annotations

import json

import pytest
import typer
from graph_works_cli import errors, exit_codes
from graph_works_cli.errors import fail
from graph_works_cli.wiki_cli import errors as wiki_errors


def test_wiki_errors_reexports_the_promoted_helper() -> None:
    """A second copy under wiki_cli would let the two drift apart silently."""
    assert wiki_errors.exit_error is errors.exit_error


def test_exit_error_writes_one_prefixed_line_and_exits_generic(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as caught:
        errors.exit_error("boom")

    assert caught.value.exit_code == exit_codes.GENERIC
    captured = capsys.readouterr()
    assert captured.err == "Error: boom\n"
    assert captured.out == ""


def test_exit_error_honours_a_code_and_chains_a_cause() -> None:
    cause = ValueError("underlying")

    with pytest.raises(typer.Exit) as caught:
        errors.exit_error("boom", code=exit_codes.NOT_INITIALIZED, cause=cause)

    assert caught.value.exit_code == exit_codes.NOT_INITIALIZED
    assert caught.value.__cause__ is cause


def test_fail_in_json_mode_emits_one_envelope_then_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as raised:
        fail("nope", reason="refused", json_mode=True, command="wiki archive", payload={"ok": False})
    out, err = capsys.readouterr()
    assert raised.value.exit_code == exit_codes.GENERIC
    assert json.loads(out) == {
        "error": {
            "command": "wiki archive",
            "reason": "refused",
            "message": "nope",
            "exit_code": exit_codes.GENERIC,
            "payload": {"ok": False},
        }
    }
    assert err == "Error: nope\n"


def test_fail_in_human_mode_writes_only_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    cause = ValueError("x")
    with pytest.raises(typer.Exit) as raised:
        fail("nope", reason="io", json_mode=False, command="archive", code=exit_codes.AMBIGUOUS, cause=cause)
    out, err = capsys.readouterr()
    assert out == "" and err == "Error: nope\n"
    assert raised.value.exit_code == exit_codes.AMBIGUOUS and raised.value.__cause__ is cause


def test_fail_rejects_a_reason_outside_the_vocabulary() -> None:
    with pytest.raises(ValueError):
        fail("nope", reason="made-up", json_mode=True, command="archive")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("error: no graph", "Error: no graph\n"),
        ("no graph", "Error: no graph\n"),
        ("error: parse error: line 3", "Error: parse error: line 3\n"),
    ],
)
def test_echo_error_normalizes_only_the_leading_core_prefix(message, expected, capsys) -> None:
    errors.echo_error(message)
    captured = capsys.readouterr()
    assert captured.err == expected
    assert captured.out == ""


def test_exit_error_restyles_a_core_prefixed_message(capsys) -> None:
    with pytest.raises(typer.Exit) as caught:
        errors.exit_error("error: no graph")
    assert caught.value.exit_code == exit_codes.GENERIC
    captured = capsys.readouterr()
    assert captured.err == "Error: no graph\n"
    assert captured.out == ""


@pytest.mark.parametrize("json_mode", [False, True])
def test_work_fail_restyles_stderr_preserving_envelope_code_and_cause(json_mode, capsys) -> None:
    from graph_works_cli.work_cli import rendering

    mode_token = rendering._JSON_MODE.set(json_mode)
    command_token = rendering._COMMAND_NAME.set("work archive")
    cause = ValueError("underlying")
    try:
        with pytest.raises(typer.Exit) as caught:
            rendering.fail(
                "error: no graph",
                reason="refused",
                code=exit_codes.NOT_INITIALIZED,
                cause=cause,
                payload={"ok": False},
            )
    finally:
        rendering._JSON_MODE.reset(mode_token)
        rendering._COMMAND_NAME.reset(command_token)
    assert caught.value.exit_code == exit_codes.NOT_INITIALIZED
    assert caught.value.__cause__ is cause
    captured = capsys.readouterr()
    assert captured.err == "Error: no graph\n"
    if json_mode:
        assert json.loads(captured.out) == {
            "error": {
                "command": "work archive",
                "reason": "refused",
                "message": "error: no graph",
                "exit_code": exit_codes.NOT_INITIALIZED,
                "payload": {"ok": False},
            }
        }
    else:
        assert captured.out == ""
