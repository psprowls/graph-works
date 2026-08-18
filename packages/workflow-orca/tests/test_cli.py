"""The transport: two streams, one envelope, one error class."""

from __future__ import annotations

import json
import sys

import pytest
from subagents_io.backend import BackendError
from workflow_orca._cli import OrcaCliError, OrcaResult, _subprocess_run, unwrap

ARGV = ("orca", "orchestration", "run-list", "--json")


def _ok(payload: dict) -> OrcaResult:
    return OrcaResult(returncode=0, stdout=json.dumps({"id": "x", "ok": True, "result": payload}), stderr="")


def test_unwrap_returns_the_result_block():
    assert unwrap(ARGV, _ok({"runs": []})) == {"runs": []}


def test_a_non_zero_exit_raises_carrying_argv_and_stderr():
    result = OrcaResult(returncode=1, stdout="", stderr="boom\n")
    with pytest.raises(OrcaCliError) as excinfo:
        unwrap(ARGV, result)
    assert excinfo.value.argv == ARGV
    assert excinfo.value.returncode == 1
    assert "boom" in str(excinfo.value)


def test_ok_false_raises_carrying_orcas_own_error_code():
    body = {"id": "x", "ok": False, "error": {"code": "consumer_fenced", "message": "not bound"}}
    result = OrcaResult(returncode=0, stdout=json.dumps(body), stderr="")
    with pytest.raises(OrcaCliError) as excinfo:
        unwrap(ARGV, result)
    assert excinfo.value.code == "consumer_fenced"
    assert "not bound" in str(excinfo.value)


def test_unparseable_stdout_raises_orca_cli_error():
    # Not JSONDecodeError. A coordinator catching BackendError must catch
    # every way this transport can fail, including the one where `orca` is
    # not on PATH and the shell printed a message instead of JSON.
    result = OrcaResult(returncode=0, stdout="orca: command not found", stderr="")
    with pytest.raises(OrcaCliError):
        unwrap(ARGV, result)


def test_keepalive_on_stderr_does_not_disturb_stdout():
    # `check --wait` writes a JSON keepalive line to stderr every 15s while
    # the real response goes to stdout. A transport that merges the two
    # cannot parse its own output; this test is the whole reason OrcaResult
    # carries two fields instead of one.
    keepalive = '{"_keepalive": true}\n{"_keepalive": true}\n'
    result = OrcaResult(
        returncode=0,
        stdout=json.dumps({"id": "x", "ok": True, "result": {"messages": []}}),
        stderr=keepalive,
    )
    assert unwrap(ARGV, result) == {"messages": []}


def test_orca_cli_error_is_a_backend_error():
    # So a coordinator catching the protocol's base class catches this
    # without knowing what Orca is.
    assert issubclass(OrcaCliError, BackendError)


def test_a_missing_result_block_raises():
    result = OrcaResult(returncode=0, stdout=json.dumps({"id": "x", "ok": True}), stderr="")
    with pytest.raises(OrcaCliError):
        unwrap(ARGV, result)


def test_a_json_array_envelope_raises():
    # Valid JSON, just not the object the envelope is always shaped as —
    # a different failure mode than the JSONDecodeError case above.
    result = OrcaResult(returncode=0, stdout=json.dumps([1, 2, 3]), stderr="")
    with pytest.raises(OrcaCliError, match="not an object"):
        unwrap(ARGV, result)


def test_subprocess_run_executes_and_captures_both_streams():
    # The one test that exercises the real default runner rather than a
    # `FakeRunner` — everything else in this suite injects `run=`.
    argv = [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]
    result = _subprocess_run(argv)
    assert result.returncode == 0
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
