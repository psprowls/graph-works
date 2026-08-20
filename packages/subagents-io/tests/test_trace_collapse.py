"""`collapse_runs` and `render_collapsed_group`: the default view's maximal-run
detection, moved out of the legacy CLI body (D-042).

What is left in the CLI after this is
`render_collapsed_group(run) if len(run) >= 2 else render_trace_record(run[0])`
-- which is what the epic's thin-CLI rule asks for.
"""

from __future__ import annotations

from subagents_io.trace import collapse_runs, render_collapsed_group


def _record(role="scanner", model="anthropic.claude-x", **extra):
    return {"schema_version": 1, "role": role, "model_id": model, **extra}


def test_an_empty_trace_collapses_to_no_runs():
    assert collapse_runs([]) == ()


def test_consecutive_records_sharing_role_and_model_form_one_run():
    records = [_record(), _record(), _record()]

    assert collapse_runs(records) == (tuple(records),)


def test_a_model_change_ends_the_run():
    first, second = _record(), _record(model="anthropic.claude-y")

    assert collapse_runs([first, second]) == ((first,), (second,))


def test_a_role_change_ends_the_run():
    first, second = _record(), _record(role="ingestor")

    assert collapse_runs([first, second]) == ((first,), (second,))


def test_a_non_groupable_record_interrupts_a_run_and_stands_alone():
    first, marker, third = _record(), {"event": "start"}, _record()

    assert collapse_runs([first, marker, third]) == ((first,), (marker,), (third,))


def test_a_run_of_exactly_two_is_still_a_run():
    records = [_record(), _record()]

    assert collapse_runs(records) == (tuple(records),)


def test_a_run_that_ends_the_file_is_flushed():
    solo, first, second = _record(role="ingestor"), _record(), _record()

    assert collapse_runs([solo, first, second]) == ((solo,), (first, second))


def test_a_collapsed_group_renders_the_legacy_line():
    records = [
        _record(timestamp="2026-08-19T10:00:00Z", status="success", tokens_in=10, tokens_out=5, cost_usd=0.5),
        _record(timestamp="2026-08-19T10:00:09Z", status="error", tokens_in=1, tokens_out=2, cost_usd=0.25),
    ]

    line = render_collapsed_group(records)

    assert line == (
        "[2026-08-19T10:00:00Z .. 2026-08-19T10:00:09Z] scanner / anthropic.claude-x x2: "
        "1 success / 1 error, 11->7 tokens, $0.750000"
    )


def test_an_unrecognized_status_lands_in_the_other_bucket():
    """WR-03: a producer-added status surfaces rather than silently vanishing."""
    records = [_record(status="throttled", cost_usd=0.0), _record(status="success", cost_usd=0.0)]

    assert "1 success / 1 other" in render_collapsed_group(records)


def test_a_partially_unknown_cost_is_annotated():
    records = [_record(cost_usd=1.5), _record(cost_usd=None)]

    assert render_collapsed_group(records).endswith("$1.500000 (+1 unknown)")


def test_a_fully_unknown_cost_renders_as_not_available():
    records = [_record(cost_usd=None), _record(cost_usd=None)]

    assert render_collapsed_group(records).endswith("$n/a (2 unknown)")


def test_missing_fields_fall_back_rather_than_raising():
    line = render_collapsed_group([{"role": "scanner"}, {"role": "scanner"}])

    assert line.startswith("[- .. -] scanner / - x2: ")
    assert "2 other" in line


def test_the_read_half_is_reachable_from_the_front_door():
    import subagents_io

    for name in (
        "KNOWN_SCHEMA_VERSION",
        "RoleModelTotals",
        "RoleTotals",
        "TraceAggregate",
        "TraceFile",
        "TraceWarning",
        "aggregate_trace",
        "collapse_runs",
        "is_groupable",
        "read_trace_records",
        "render_collapsed_group",
    ):
        assert name in subagents_io.__all__, name
        assert hasattr(subagents_io, name), name
