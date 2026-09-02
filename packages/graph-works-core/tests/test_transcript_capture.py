"""In-process unit tests for the SessionEnd hook body.

Ported from `plugins/graph-works/hooks/examples/session-end-transcript-capture.sh`
(bug-windows-hook-command-unexecutable): the bash script's only exercise was
two slow wheel/sdist subprocess smoke tests. As a module, `main` is driven
directly here, and each branch -- guard, malformed input, missing transcript,
no pointer, invalid pointer, the happy path, and fail-open on an unexpected
exception -- gets its own scenario."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.transcript_capture import GUARD_ENV, TRACE_LOG_ENV, main


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    base = {TRACE_LOG_ENV: str(tmp_path / "trace.log")}
    base.update(overrides)
    return base


def _stdin(payload: object) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def _trace_text(tmp_path: Path) -> str:
    log = tmp_path / "trace.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_guard_zero_skips_and_traces(tmp_path: Path) -> None:
    rc = main(_stdin({"session_id": "abc", "transcript_path": "x"}), _env(tmp_path, **{GUARD_ENV: "0"}))
    assert rc == 0
    assert "skip" in _trace_text(tmp_path)
    assert "guard=0" in _trace_text(tmp_path)


def test_malformed_json_is_traced_as_error_and_returns_zero(tmp_path: Path) -> None:
    rc = main(io.StringIO("not json"), _env(tmp_path))
    assert rc == 0
    assert "error" in _trace_text(tmp_path)


def test_non_object_payload_is_traced_as_error_and_returns_zero(tmp_path: Path) -> None:
    rc = main(_stdin(["not", "an", "object"]), _env(tmp_path))
    assert rc == 0
    assert "error" in _trace_text(tmp_path)


def test_missing_transcript_path_skips(tmp_path: Path) -> None:
    rc = main(_stdin({"session_id": "abc"}), _env(tmp_path))
    assert rc == 0
    assert "no-transcript" in _trace_text(tmp_path)


def test_nonexistent_transcript_path_skips(tmp_path: Path) -> None:
    rc = main(_stdin({"session_id": "abc", "transcript_path": str(tmp_path / "missing.jsonl")}), _env(tmp_path))
    assert rc == 0
    assert "no-transcript" in _trace_text(tmp_path)


def test_no_active_work_pointer_skips(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    assert "no-pointer" in _trace_text(tmp_path)


def test_invalid_pointer_path_skips(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": "not/a/work/path", "phase": "execute"}) + "\n", encoding="utf-8"
    )

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    assert "invalid-path" in _trace_text(tmp_path)


def test_happy_path_copies_main_transcript_and_subagent_sidechains(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    work_path = "work/feature-x"
    page = layout.bundle_dir / f"{work_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8")
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": work_path, "phase": "execute"}) + "\n", encoding="utf-8"
    )

    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")
    sidechain_dir = tmp_path / "session" / "subagents"
    sidechain_dir.mkdir(parents=True)
    (sidechain_dir / "agent-42.jsonl").write_text('{"e":2}\n', encoding="utf-8")

    rc = main(
        _stdin({"session_id": "abcdef123456", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    references = layout.bundle_dir / work_path / "references"
    assert (references / "03-execute-transcript.jsonl").read_text(encoding="utf-8") == '{"e":1}\n'
    assert (references / "03-execute-transcript-subagent-42.jsonl").read_text(encoding="utf-8") == '{"e":2}\n'
    assert "copied" in _trace_text(tmp_path)


def test_exception_mid_copy_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    work_path = "work/feature-x"
    page = layout.bundle_dir / f"{work_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8")
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": work_path, "phase": "execute"}) + "\n", encoding="utf-8"
    )
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr("shutil.copy2", _boom)

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    assert "error" in _trace_text(tmp_path)
    assert "disk exploded" in _trace_text(tmp_path)


def test_default_trace_log_lives_under_tempfile_gettempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile as tempfile_module

    monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(tmp_path))
    rc = main(_stdin({"session_id": "abc"}), {})

    assert rc == 0
    assert (tmp_path / "claude-hooks" / "transcript-capture-trace.log").exists()


def test_trace_write_failure_is_swallowed(tmp_path: Path) -> None:
    # A directory where the trace log file would go: mkdir/open both fail with OSError.
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "trace.log").mkdir()

    rc = main(_stdin({"session_id": "abc"}), _env(tmp_path, **{TRACE_LOG_ENV: str(blocked / "trace.log")}))

    assert rc == 0
