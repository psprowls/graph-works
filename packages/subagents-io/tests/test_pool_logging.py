"""Fan-out lifecycle logging tests for SubagentPool (gw --verbose).

The pool logs unconditionally; these tests use caplog (which captures via the
root logger by propagation) to assert what is emitted at INFO vs DEBUG.
"""

from __future__ import annotations

import logging


async def test_fanout_emits_start_completions_and_summary(tmp_path, make_task, caplog):
    from subagents_io.pool import SubagentPool

    pool = SubagentPool(trace_dir=tmp_path / "traces")
    task = make_task()

    with caplog.at_level(logging.INFO):
        result = await pool.run_all(
            items=["a", "b"],
            task=task,
            role="librarian",
            model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            max_concurrency=5,
        )

    assert len(result.successes) == 2

    starts = [
        r for r in caplog.records if r.name == "subagents_io.pool" and r.getMessage().startswith("-> fan-out start:")
    ]
    summaries = [
        r for r in caplog.records if r.name == "subagents_io.pool" and r.getMessage().startswith("ok fan-out done:")
    ]
    completions = [r for r in caplog.records if r.name == "subagents_io.pool.trace"]

    assert len(starts) == 1
    assert "role=librarian" in starts[0].getMessage()
    assert "items=2" in starts[0].getMessage()
    assert "concurrency=5" in starts[0].getMessage()

    assert len(completions) == 2
    assert all("success" in r.getMessage() for r in completions)

    assert len(summaries) == 1
    assert "2 ok / 0 err" in summaries[0].getMessage()


async def test_per_item_start_only_at_debug(tmp_path, make_task, caplog):
    from subagents_io.pool import SubagentPool

    pool = SubagentPool(trace_dir=tmp_path / "traces")
    task = make_task()

    # At INFO: per-item start lines (DEBUG) are NOT emitted.
    with caplog.at_level(logging.INFO):
        await pool.run_all(items=["a"], task=task, role="librarian", model_id="m", max_concurrency=1)
    info_item_starts = [r for r in caplog.records if r.getMessage().startswith("-> item start:")]
    assert info_item_starts == []

    caplog.clear()

    # At DEBUG: one per-item start line per item.
    with caplog.at_level(logging.DEBUG):
        await pool.run_all(items=["a", "b"], task=task, role="librarian", model_id="m", max_concurrency=1)
    debug_item_starts = [r for r in caplog.records if r.getMessage().startswith("-> item start:")]
    assert len(debug_item_starts) == 2


async def test_error_completion_line_is_logged(tmp_path, make_task, caplog):
    from subagents_io.pool import SubagentPool

    pool = SubagentPool(trace_dir=tmp_path / "traces")
    task = make_task(raise_for={"b"})

    with caplog.at_level(logging.INFO):
        result = await pool.run_all(items=["a", "b"], task=task, role="librarian", model_id="m", max_concurrency=2)

    assert len(result.successes) == 1
    assert len(result.errors) == 1

    completions = [r for r in caplog.records if r.name == "subagents_io.pool.trace"]
    assert len(completions) == 2
    assert any("error" in r.getMessage() for r in completions)

    summaries = [
        r for r in caplog.records if r.name == "subagents_io.pool" and r.getMessage().startswith("ok fan-out done:")
    ]
    assert "1 ok / 1 err" in summaries[0].getMessage()


async def test_trace_logger_name_matches_the_logger_the_pool_writes_to(tmp_path, make_task, caplog):
    """TRACE_LOGGER_NAME names the logger pool.py actually emits under.

    Asserted by capturing real records, NOT by comparing the constant to a
    string literal — `pool.py` derives its logger from `__name__` and never
    reads the constant, so a package rename that updates only one half fails
    here instead of silently detaching a downstream handler.
    """
    from subagents_io import TRACE_LOGGER_NAME
    from subagents_io.pool import SubagentPool

    pool = SubagentPool(trace_dir=tmp_path / "traces")
    task = make_task()

    with caplog.at_level(logging.INFO):
        await pool.run_all(items=["a", "b"], task=task, role="librarian", model_id="m", max_concurrency=2)

    emitted = {r.name for r in caplog.records}
    assert TRACE_LOGGER_NAME in emitted, f"nothing logged under {TRACE_LOGGER_NAME}; saw {sorted(emitted)}"
    assert len([r for r in caplog.records if r.name == TRACE_LOGGER_NAME]) == 2
