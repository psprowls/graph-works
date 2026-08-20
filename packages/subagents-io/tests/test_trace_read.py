"""`read_trace_records`: the leniency policy, stated once, in the package that
writes the format.

Every branch here is a *warning*, never a refusal: a trace is diagnostics, and
a reader that rejected a file would take away the one view of a run that
already happened.
"""

from __future__ import annotations

import json
from pathlib import Path

from subagents_io.trace import KNOWN_SCHEMA_VERSION, TraceWarning, read_trace_records


def _jsonl(tmp_path: Path, *records: object) -> Path:
    path = tmp_path / "trace.jsonl"
    path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")
    return path


def test_the_known_schema_version_is_the_one_the_writer_stamps():
    assert KNOWN_SCHEMA_VERSION == 1


def test_records_come_back_in_file_order(tmp_path):
    path = _jsonl(
        tmp_path,
        {"schema_version": 1, "role": "scanner"},
        {"schema_version": 1, "role": "ingestor"},
    )

    result = read_trace_records(path)

    assert [record["role"] for record in result.records] == ["scanner", "ingestor"]
    assert result.warnings == ()
    assert result.path == path


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('\n{"schema_version": 1}\n\n   \n', encoding="utf-8")

    result = read_trace_records(path)

    assert len(result.records) == 1
    assert result.warnings == ()


def test_unversioned_records_warn_once_per_file(tmp_path):
    path = _jsonl(tmp_path, {"role": "a"}, {"role": "b"}, {"role": "c"})

    result = read_trace_records(path)

    assert len(result.records) == 3
    assert result.warnings == (TraceWarning(kind="unversioned", observed=0),)


def test_future_versions_warn_once_per_file_and_still_render(tmp_path):
    path = _jsonl(
        tmp_path,
        {"schema_version": 7, "role": "a"},
        {"schema_version": 9, "role": "b"},
    )

    result = read_trace_records(path)

    assert len(result.records) == 2
    assert result.warnings == (TraceWarning(kind="future-version", observed=7),)


def test_a_non_integer_schema_version_warns_nothing(tmp_path):
    """T-09-15: lenient consumer. A string version is rendered best-effort."""
    path = _jsonl(tmp_path, {"schema_version": "1.0", "role": "a"})

    result = read_trace_records(path)

    assert len(result.records) == 1
    assert result.warnings == ()


def test_a_malformed_line_warns_per_line_and_is_dropped(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"schema_version": 1}\nnot json\nalso not json\n', encoding="utf-8")

    result = read_trace_records(path)

    assert len(result.records) == 1
    assert [warning.kind for warning in result.warnings] == ["malformed-line", "malformed-line"]
    assert [warning.line for warning in result.warnings] == [2, 3]
    assert all(warning.detail for warning in result.warnings)


def test_a_line_that_is_not_an_object_is_warned_and_dropped(tmp_path):
    path = _jsonl(tmp_path, [1, 2], {"schema_version": 1, "role": "a"})

    result = read_trace_records(path)

    assert len(result.records) == 1
    assert result.warnings == (TraceWarning(kind="malformed-line", line=1, detail="line is not a JSON object"),)


def test_both_version_warnings_can_appear_in_one_file(tmp_path):
    path = _jsonl(tmp_path, {"role": "a"}, {"schema_version": 4, "role": "b"})

    result = read_trace_records(path)

    assert [warning.kind for warning in result.warnings] == ["unversioned", "future-version"]
