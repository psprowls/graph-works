"""The four states, synonyms, and the defaulting row mapping."""

from __future__ import annotations

import pytest
from ext_helpers import TABLED_STATES, tabled_bundle
from okf_ext.tables import Column, TableSpec, read_section

PLAN = TableSpec(
    columns=(
        Column("action"),
        Column("done_when", synonyms=("done when", "done-when")),
        Column("rationale", synonyms=("why",)),
    )
)


@pytest.mark.parametrize(("concept_id", "state"), sorted(TABLED_STATES.items()))
def test_each_fixture_reads_as_its_documented_state(concept_id, state):
    document = tabled_bundle().concepts[concept_id]
    assert read_section(document.body, "Plan", PLAN).state == state


def test_ok_yields_canonical_rows():
    body = "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n| port | it works | because |\n"
    result = read_section(body, "Plan", PLAN)
    assert result.state == "ok"
    assert result.matched == ("action", "done_when", "rationale")
    assert dict(result.rows[0]) == {"action": "port", "done_when": "it works", "rationale": "because"}


def test_a_synonym_matches_and_reports_the_canonical_name():
    body = "## Plan\n\n| Action | Done-When | Why |\n| --- | --- | --- |\n| port | soon | reasons |\n"
    result = read_section(body, "Plan", PLAN)
    assert result.matched == ("action", "done_when", "rationale")
    assert result.rows[0]["rationale"] == "reasons"


def test_header_matching_ignores_case_and_surrounding_whitespace():
    body = "## Plan\n\n|   ACTION   | done when |\n| --- | --- |\n| port | soon |\n"
    assert read_section(body, "Plan", PLAN).matched == ("action", "done_when")


def test_a_missing_heading_is_missing():
    assert read_section("# T\n\nprose\n", "Plan", PLAN).state == "missing"


def test_a_heading_with_prose_and_no_table_is_malformed():
    result = read_section("## Plan\n\njust prose\n", "Plan", PLAN)
    assert result.state == "malformed"
    assert result.table is None
    assert result.rows == ()


def test_a_table_sharing_too_few_columns_is_malformed_but_still_reported():
    body = "## Plan\n\n| Action | Owner |\n| --- | --- |\n| port | me |\n"
    result = read_section(body, "Plan", PLAN)
    assert result.state == "malformed"
    assert result.matched == ("action",)
    assert result.table is not None  # a rule can still say what *was* there


def test_two_matched_columns_clear_the_default_threshold():
    body = "## Plan\n\n| Action | Rationale |\n| --- | --- |\n| port | because |\n"
    assert read_section(body, "Plan", PLAN).state == "ok"


def test_min_matched_is_clamped_to_the_number_of_columns():
    """A single-column spec must not be permanently malformed by a default of
    2 it could never meet."""
    spec = TableSpec(columns=(Column("path"),))
    body = "## Files\n\n| Path |\n| --- |\n| read.py |\n"
    assert read_section(body, "Files", spec).state == "ok"


def test_a_matched_header_with_no_data_rows_is_empty():
    body = "## Plan\n\n| Action | Done when |\n| --- | --- |\n"
    result = read_section(body, "Plan", PLAN)
    assert result.state == "empty"
    assert result.rows == ()
    assert result.table is not None


def test_an_absent_column_reads_as_an_empty_string_not_a_key_error():
    body = "## Plan\n\n| Action | Done when |\n| --- | --- |\n| port | soon |\n"
    row = read_section(body, "Plan", PLAN).rows[0]
    assert row["rationale"] == ""
    assert "rationale" not in row  # omitted from iteration, still readable
    assert list(row) == ["action", "done_when"]
    assert len(row) == 2


def test_a_column_in_neither_the_header_nor_the_spec_still_raises():
    body = "## Plan\n\n| Action | Done when |\n| --- | --- |\n| port | soon |\n"
    with pytest.raises(KeyError):
        read_section(body, "Plan", PLAN).rows[0]["not-a-column"]


def test_a_row_shorter_than_the_header_is_padded_before_reading():
    """Rows shorter than the header are padded by _build, so the spec layer
    reads empty strings for missing cells rather than indices out of bounds.
    """
    body = "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n| port |\n"
    row = read_section(body, "Plan", PLAN).rows[0]
    assert (row["action"], row["done_when"], row["rationale"]) == ("port", "", "")


def test_the_heading_is_matched_level_agnostically():
    body = "### Plan\n\n| Action | Done when |\n| --- | --- |\n| port | soon |\n"
    assert read_section(body, "Plan", PLAN).state == "ok"


def test_only_the_first_table_in_the_section_is_read():
    body = (
        "## Plan\n\n| Action | Done when |\n| --- | --- |\n| port | soon |\n\n"
        "| Other | Table |\n| --- | --- |\n| x | y |\n"
    )
    result = read_section(body, "Plan", PLAN)
    assert len(result.rows) == 1


def test_synonym_collision_first_wins():
    """When two spec columns resolve to the same header (shared synonym or
    synonym matching another's canonical name), only the first column claims it.
    The second is omitted from matched and reads as empty.
    """
    spec = TableSpec(
        columns=(
            Column("task"),
            Column("task_alt", synonyms=("task",)),  # task_alt's synonym == task's canonical
        ),
        min_matched=1,
    )
    body = "## Work\n\n| Task |\n| --- |\n| build |\n"
    result = read_section(body, "Work", spec)
    assert result.matched == ("task",)  # only task was matched
    assert result.rows[0]["task"] == "build"
    assert result.rows[0]["task_alt"] == ""  # omitted from iteration
    assert "task_alt" not in result.rows[0]
    assert list(result.rows[0]) == ["task"]


def test_spec_row_repr():
    """_SpecRow.__repr__ shows the underlying cell mapping."""
    body = "## Plan\n\n| Action | Done when |\n| --- | --- |\n| port | soon |\n"
    row = read_section(body, "Plan", PLAN).rows[0]
    assert repr(row) == "_SpecRow({'action': 'port', 'done_when': 'soon'})"
