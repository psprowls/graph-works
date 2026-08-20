"""`aggregate_trace`: the per-role and per-(role, model) rollups, including
the two awkward parts that are preserved on purpose.

`total_records` counts every record while the breakdowns skip non-groupable
ones -- the WR-02 fix, which keeps the Summary block's "Total records" line
backward-compatible while stopping `kind:`-bearing records from synthesizing a
phantom `unknown:` role bucket.
"""

from __future__ import annotations

import pytest
from subagents_io.trace import RoleModelTotals, RoleTotals, aggregate_trace, is_groupable


def _record(role="scanner", model="anthropic.claude-x", tokens_in=10, tokens_out=5, cost=0.5, **extra):
    return {
        "schema_version": 1,
        "role": role,
        "model_id": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        **extra,
    }


def test_an_empty_trace_aggregates_to_zeros():
    aggregate = aggregate_trace([])

    assert aggregate.by_role == {}
    assert aggregate.by_role_model == {}
    assert aggregate.total_records == 0
    assert aggregate.total_tokens_in == 0
    assert aggregate.total_tokens_out == 0


def test_records_roll_up_per_role_and_per_role_model():
    aggregate = aggregate_trace([_record(), _record(), _record(role="ingestor")])

    assert aggregate.by_role["scanner"] == RoleTotals(count=2, tokens_in=20, tokens_out=10)
    assert aggregate.by_role["ingestor"] == RoleTotals(count=1, tokens_in=10, tokens_out=5)
    assert aggregate.by_role_model[("scanner", "anthropic.claude-x")] == RoleModelTotals(
        role="scanner",
        model_id="anthropic.claude-x",
        count=2,
        tokens_in=20,
        tokens_out=10,
        cost_usd_sum=1.0,
        unknown_cost_count=0,
    )
    assert aggregate.total_records == 3


def test_none_token_values_count_as_zero_and_records_are_not_mutated():
    record = _record(tokens_in=None, tokens_out=None)
    original = dict(record)

    aggregate = aggregate_trace([record])

    assert aggregate.total_tokens_in == 0
    assert aggregate.total_tokens_out == 0
    assert record == original


def test_non_groupable_records_count_in_the_total_but_in_neither_breakdown():
    """WR-02: a `kind:`-bearing record has no `role`, and counting it in
    `by_role` synthesized a phantom `unknown:` bucket."""
    aggregate = aggregate_trace([_record(), {"kind": "query_summary", "tokens_in": 3}, {"event": "start"}])

    assert aggregate.total_records == 3
    assert aggregate.total_tokens_in == 13
    assert set(aggregate.by_role) == {"scanner"}
    assert aggregate.by_role["scanner"].count == 1


def test_a_missing_role_or_model_reads_as_unknown():
    aggregate = aggregate_trace([{"schema_version": 1, "tokens_in": 1, "tokens_out": 1}])

    assert set(aggregate.by_role_model) == {("unknown", "unknown")}


def test_a_null_cost_is_counted_rather_than_summed():
    aggregate = aggregate_trace([_record(cost=None), _record(cost=0.25)])

    totals = aggregate.by_role_model[("scanner", "anthropic.claude-x")]
    assert totals.cost_usd_sum == 0.25
    assert totals.unknown_cost_count == 1
    assert totals.fully_unknown is False


def test_a_group_with_no_known_cost_at_all_is_fully_unknown():
    aggregate = aggregate_trace([_record(cost=None), _record(cost=None)])

    assert aggregate.by_role_model[("scanner", "anthropic.claude-x")].fully_unknown is True


def test_a_non_numeric_cost_raises_rather_than_mis_summing():
    """T-09-06: production writers emit float or None; a string means a
    malformed producer, and silently coercing it would hide that."""
    with pytest.raises(ValueError):
        aggregate_trace([_record(cost="free")])


def test_the_cost_rollup_orders_known_groups_by_descending_cost():
    aggregate = aggregate_trace(
        [
            _record(role="a", model="m", cost=1.0),
            _record(role="b", model="m", cost=5.0),
            _record(role="c", model="m", cost=3.0),
        ]
    )

    assert [totals.role for totals in aggregate.cost_rollup()] == ["b", "c", "a"]


def test_the_cost_rollup_puts_fully_unknown_groups_last_and_breaks_ties_on_names():
    aggregate = aggregate_trace(
        [
            _record(role="z", model="m", cost=1.0),
            _record(role="a", model="m", cost=1.0),
            _record(role="b", model="n", cost=None),
            _record(role="a", model="n", cost=None),
        ]
    )

    assert [(totals.role, totals.model_id) for totals in aggregate.cost_rollup()] == [
        ("a", "m"),
        ("z", "m"),
        ("a", "n"),
        ("b", "n"),
    ]


def test_is_groupable_rejects_event_and_kind_records():
    assert is_groupable({"role": "scanner"}) is True
    assert is_groupable({"role": "scanner", "event": "start"}) is False
    assert is_groupable({"kind": "query_summary"}) is False
