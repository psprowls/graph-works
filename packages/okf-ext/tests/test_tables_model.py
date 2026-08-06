"""The frozen values, and the claims that live on them rather than on a function."""

from __future__ import annotations

from pathlib import Path

import okf_ext.tables as tables_module
from ext_helpers import TABLED_STATES, tabled_bundle
from okf_ext.tables import Column, RowSplice, SplicePlan, Table, TableSpec, TextSplice


def test_the_documented_defaults_hold():
    assert Column("action").synonyms == ()
    assert TableSpec(columns=(Column("action"),)).min_matched == 2


def test_every_value_is_frozen():
    table = Table(headers=("a",), rows=(), start=1, stop=1, delimiter=0)
    try:
        table.start = 2  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Table must be frozen")


def test_changed_is_before_versus_after():
    assert not TextSplice(before="x", after="x", action=None, line=0).changed
    assert TextSplice(before="x", after="y", action="append", line=3).changed


def _splice(concept_id: str) -> RowSplice:
    return RowSplice(
        concept_id=concept_id,
        path=f"{concept_id}.md",
        heading="Plan",
        row={"action": "a"},
        action="append",
        line=3,
        digest="d",
        after="body",
    )


def test_an_empty_plan_reports_itself_as_empty():
    plan = SplicePlan(root=Path("/tmp"), splices=(), skipped=())
    assert plan.is_empty
    assert plan.concept_ids == ()


def test_concept_ids_are_sorted_and_deduplicated():
    plan = SplicePlan(root=Path("/tmp"), splices=(_splice("b"), _splice("a"), _splice("b")), skipped=())
    assert not plan.is_empty
    assert plan.concept_ids == ("a", "b")


def test_the_capability_exports_no_topic_and_no_codes():
    assert not hasattr(tables_module, "TOPIC")
    assert not hasattr(tables_module, "CODES")


def test_the_fixture_corpus_loads_without_a_parse_error():
    bundle = tabled_bundle()
    assert set(TABLED_STATES) <= set(bundle.concepts)
    assert [c for c, d in bundle.concepts.items() if d.parse_error is not None] == []
