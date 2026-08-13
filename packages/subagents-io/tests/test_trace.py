"""Unit tests for subagents_io.trace.write_trace_record (Phase 16 D-04).

Asserts the three invariants that pool.py previously embedded inline:
1. usage_metadata is captured into tokens_in/tokens_out on success responses.
2. usage_metadata is None-guarded (Bedrock error responses return None).
3. OSError on file open/write is caught and swallowed (never raises).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from subagents_io.trace import write_trace_record


def _read_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def test_write_trace_record_writes_valid_jsonl_with_tokens(tmp_path):
    """Success path: response.usage_metadata populates tokens_in/tokens_out."""
    response = MagicMock()
    response.usage_metadata = {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    trace_file = tmp_path / "trace.jsonl"

    write_trace_record(
        trace_file,
        role="scanner",
        model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        item="page-1",
        status="success",
        latency_ms=42,
        response=response,
    )

    records = _read_jsonl(trace_file)
    assert len(records) == 1
    rec = records[0]
    assert rec["schema_version"] == 1
    assert rec["role"] == "scanner"
    assert rec["status"] == "success"
    assert rec["latency_ms"] == 42
    assert rec["tokens_in"] == 100
    assert rec["tokens_out"] == 50
    assert rec["item_id"] == "page-1"
    assert "timestamp" in rec


def test_write_trace_record_handles_none_usage_metadata(tmp_path):
    """Bedrock error responses return usage_metadata = None (deepagents #1698)."""
    response = MagicMock()
    response.usage_metadata = None
    trace_file = tmp_path / "trace.jsonl"

    write_trace_record(
        trace_file,
        role="ingestor",
        model_id="test-model",
        item="page-2",
        status="success",  # status can still be "success" from caller's POV
        latency_ms=10,
        response=response,
    )

    records = _read_jsonl(trace_file)
    assert len(records) == 1
    rec = records[0]
    assert rec["tokens_in"] is None
    assert rec["tokens_out"] is None
    # Record was still written despite missing token counts
    assert rec["schema_version"] == 1
    assert rec["role"] == "ingestor"


def test_write_trace_record_swallows_oserror(tmp_path, monkeypatch, caplog):
    """OSError on file open is logged WARNING and swallowed — never raises."""
    # Path inside a non-existent directory triggers OSError on open
    bad_path = tmp_path / "does" / "not" / "exist" / "trace.jsonl"

    # Must not raise; WARNING log is emitted (D-10 IN-04)
    with caplog.at_level("WARNING", logger="subagents_io.trace"):
        write_trace_record(
            bad_path,
            role="scanner",
            model_id="test-model",
            item="page-3",
            status="error",
            latency_ms=5,
            response=None,
            error="boom",
        )

    # File was not written (parent missing)
    assert not bad_path.exists()

    # A WARNING-level record was emitted with the trace_io warning fragment.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("Trace write failed" in r.getMessage() for r in warnings), (
        f"expected WARNING log containing 'Trace write failed'; got: {[r.getMessage() for r in caplog.records]}"
    )


def test_write_trace_record_returns_record(tmp_path):
    """write_trace_record returns the dict it built AND writes the same record to disk."""
    import json

    from subagents_io.trace import write_trace_record

    path = tmp_path / "t.jsonl"
    returned = write_trace_record(
        path,
        "scanner",
        "model-x",
        "page-a",
        "success",
        100,
        None,
    )
    assert isinstance(returned, dict)
    assert returned["role"] == "scanner"
    assert returned["status"] == "success"
    assert returned["item_id"] == "page-a"
    # Backward compat: the on-disk record matches the returned dict exactly.
    written = json.loads(path.read_text().splitlines()[0])
    assert written == returned


def test_render_trace_record_format():
    """render_trace_record renders a single human-readable line for a record."""
    from subagents_io.trace import render_trace_record

    record = {
        "timestamp": "2026-05-13T10:00:00Z",
        "role": "scanner",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "item_id": "page-a",
        "status": "success",
        "latency_ms": 350,
        "tokens_in": 10,
        "tokens_out": 5,
    }
    out = render_trace_record(record)
    assert isinstance(out, str)
    assert "scanner" in out
    assert "page-a" in out
    assert "success" in out
    assert "350ms" in out
    assert "10->5" in out
    # Model id is rendered as its last 30 chars (matches gw trace convention).
    assert record["model_id"][-30:] in out


def test_render_trace_record_error_suffix():
    """Error records append an ERROR: <message> suffix."""
    from subagents_io.trace import render_trace_record

    record = {
        "timestamp": "2026-05-13T10:00:00Z",
        "role": "scanner",
        "model_id": "model-x",
        "item_id": "page-b",
        "status": "error",
        "latency_ms": 12,
        "tokens_in": None,
        "tokens_out": None,
        "error": "boom",
    }
    out = render_trace_record(record)
    assert "ERROR: boom" in out


def test_trace_logger_name_constant():
    """TRACE_LOGGER_NAME is published so consumers bind to a symbol, not a string literal."""
    from subagents_io.trace import TRACE_LOGGER_NAME

    assert TRACE_LOGGER_NAME == "subagents_io.pool.trace"


# ---------------------------------------------------------------------------
# The pricing seam. This is the only place in this package's suite where
# `models_io` is named — and it is the contract test for the injection, not
# test bookkeeping.
# ---------------------------------------------------------------------------


def test_cost_usd_is_none_without_a_price_lookup(tmp_path):
    """Omitting price_lookup leaves cost_usd null even for a priced model."""
    response = MagicMock()
    response.usage_metadata = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    path = tmp_path / "t.jsonl"

    record = write_trace_record(
        path,
        role="scanner",
        model_id="us.amazon.nova-pro-v1:0",
        item="page-1",
        status="success",
        latency_ms=1,
        response=response,
    )

    assert record["tokens_in"] == 1_000_000
    assert record["cost_usd"] is None


def test_cost_usd_is_populated_with_a_price_lookup(tmp_path):
    """price_lookup=cost_for_usage prices the record. Nova Pro: $0.80/M in + $3.20/M out."""
    from models_io.pricing import cost_for_usage

    response = MagicMock()
    response.usage_metadata = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    path = tmp_path / "t.jsonl"

    record = write_trace_record(
        path,
        role="scanner",
        model_id="us.amazon.nova-pro-v1:0",
        item="page-1",
        status="success",
        latency_ms=1,
        response=response,
        price_lookup=cost_for_usage,
    )

    assert record["cost_usd"] == pytest.approx(4.0)
    # And the on-disk record carries it too, not just the returned dict.
    assert _read_jsonl(path)[0]["cost_usd"] == pytest.approx(4.0)


def test_cost_usd_is_none_when_the_lookup_does_not_price_the_model(tmp_path):
    """UnknownModelError subclasses KeyError; the existing catch is the right width."""
    from models_io.pricing import cost_for_usage

    response = MagicMock()
    response.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
    path = tmp_path / "t.jsonl"

    record = write_trace_record(
        path,
        role="scanner",
        model_id="a-model-nobody-prices",
        item="page-1",
        status="success",
        latency_ms=1,
        response=response,
        price_lookup=cost_for_usage,
    )

    assert record["cost_usd"] is None
