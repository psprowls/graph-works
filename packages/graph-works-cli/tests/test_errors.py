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
