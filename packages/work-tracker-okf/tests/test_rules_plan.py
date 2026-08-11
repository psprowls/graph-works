from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from work_helpers import EMPTY_PLAN_BODY, lane_report, write_item

TODAY = date(2026, 8, 3)

OK_TABLE = (
    "\n## Plan\n\n"
    "| Action | Done when | Rationale |\n"
    "| --- | --- | --- |\n"
    "| Ship it | The suite is green | The fixture is the contract |\n"
)

MISSING_DONE_WHEN = (
    "\n## Plan\n\n"
    "| Action | Done when | Rationale |\n"
    "| --- | --- | --- |\n"
    "| Ship it |  | The fixture is the contract |\n"
)

MALFORMED = "\n## Plan\n\nNo table here, just prose.\n"

NO_SECTION = "\n## Notes / log\n\nNothing.\n"


def codes_for(tmp_path: Path, frontmatter: str, body: str, *, slug: str = "2026-08-01-feature-x") -> set[str]:
    write_item(tmp_path, slug, frontmatter, body=body)
    return {f.code for f in lane_report(tmp_path, today=TODAY).findings if f.code.startswith("plan.")}


# --- plan.accepted-without-plan ---------------------------------------------


@pytest.mark.parametrize("body", [OK_TABLE, EMPTY_PLAN_BODY])
def test_accepted_with_an_ok_or_empty_table_is_silent(tmp_path: Path, body: str) -> None:
    """work-io's behaviour exactly: a seeded-but-unfilled table is filed correctly."""
    codes = codes_for(tmp_path, "type: Feature\nworkflow_status: accepted\nphase: execute\n", body)
    assert "plan.accepted-without-plan" not in codes


@pytest.mark.parametrize("body", [MALFORMED, NO_SECTION])
def test_accepted_with_a_malformed_or_absent_table_is_an_error(tmp_path: Path, body: str) -> None:
    write_item(
        tmp_path, "2026-08-01-feature-x", "type: Feature\nworkflow_status: accepted\nphase: execute\n", body=body
    )
    assert lane_report(tmp_path, today=TODAY).by_code("plan.accepted-without-plan")[0].severity == "error"


def test_a_non_accepted_item_with_no_table_is_silent(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Feature\nworkflow_status: open\n", NO_SECTION)
    assert "plan.accepted-without-plan" not in codes


# --- plan.table-malformed ---------------------------------------------------


def test_a_heading_with_no_table_is_a_warn(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\n", body=MALFORMED)
    assert lane_report(tmp_path, today=TODAY).by_code("plan.table-malformed")[0].severity == "warn"


def test_an_absent_section_gets_no_lane_code(tmp_path: Path) -> None:
    """`sections.missing` from tier 2 owns it; a second code is drift."""
    codes = codes_for(tmp_path, "type: Feature\nworkflow_status: open\n", NO_SECTION)
    assert codes == set()


# --- plan.done-when-missing -------------------------------------------------


@pytest.mark.parametrize("type_name", ["Epic", "Feature"])
def test_an_empty_done_when_cell_is_a_warn_for_a_parent_type(tmp_path: Path, type_name: str) -> None:
    slug = "2026-08-01-epic-x" if type_name == "Epic" else "2026-08-01-feature-x"
    write_item(tmp_path, slug, f"type: {type_name}\nworkflow_status: open\n", body=MISSING_DONE_WHEN)
    assert lane_report(tmp_path, today=TODAY).by_code("plan.done-when-missing")[0].severity == "warn"


@pytest.mark.parametrize("type_name", ["Bug", "TechDebt", "TestGap", "Spike"])
def test_a_non_parent_type_is_exempt(tmp_path: Path, type_name: str) -> None:
    prefix = {"Bug": "bug", "TechDebt": "tech-debt", "TestGap": "test-gap", "Spike": "spike"}[type_name]
    codes = codes_for(
        tmp_path,
        f"type: {type_name}\nworkflow_status: open\n",
        MISSING_DONE_WHEN,
        slug=f"2026-08-01-{prefix}-x",
    )
    assert "plan.done-when-missing" not in codes


def test_a_filled_table_is_silent(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Feature\nworkflow_status: open\n", OK_TABLE)
    assert "plan.done-when-missing" not in codes


def test_an_empty_table_has_no_rows_to_complain_about(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Feature\nworkflow_status: open\n", EMPTY_PLAN_BODY)
    assert "plan.done-when-missing" not in codes


# --- plan.action-target-missing ---------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "packages" / "example").mkdir(parents=True)
    (repo / "packages" / "example" / "module.py").write_text("x = 1\n", encoding="utf-8")
    return repo


def test_an_action_naming_a_missing_path_is_an_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    body = (
        "\n## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "| Edit packages/example/gone.py | It compiles | Because |\n"
    )
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\n", body=body)
    report = lane_report(vault, today=TODAY, repo_root=_repo(tmp_path))
    finding = report.by_code("plan.action-target-missing")[0]
    assert finding.severity == "error"
    assert "packages/example/gone.py" in finding.message


def test_an_action_naming_a_present_path_is_silent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    body = (
        "\n## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "| Edit packages/example/module.py | It compiles | Because |\n"
    )
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\n", body=body)
    assert lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("plan.action-target-missing") == ()


def test_an_http_token_is_skipped(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    body = (
        "\n## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "| See https://example.test/a/b | Read | Because |\n"
    )
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\n", body=body)
    assert lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("plan.action-target-missing") == ()


def test_no_repo_root_skips_the_code_entirely(tmp_path: Path) -> None:
    """Not knowing where the repo is says nothing about whether the paths are good."""
    vault = tmp_path / "vault"
    body = (
        "\n## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "| Edit packages/example/gone.py | It compiles | Because |\n"
    )
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\n", body=body)
    assert lane_report(vault, today=TODAY).by_code("plan.action-target-missing") == ()


def test_a_malformed_table_is_never_scanned_for_actions(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\n", body=MALFORMED)
    assert lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("plan.action-target-missing") == ()


# --- the module's shape -----------------------------------------------------


def test_the_spec_carries_the_three_declared_columns() -> None:
    from work_tracker_okf._rules.plan import PLAN_TABLE_SPEC

    assert [column.name for column in PLAN_TABLE_SPEC.columns] == ["action", "done when", "rationale"]


def test_the_module_declares_four_codes_all_prefixed_plan() -> None:
    from work_tracker_okf._rules import plan

    assert len(plan.CODES) == 4
    assert all(code.startswith("plan.") for code in plan.CODES)


def test_the_factory_adds_exactly_one_rule_when_a_repo_root_is_injected(tmp_path: Path) -> None:
    from work_tracker_okf._rules import plan
    from work_tracker_okf._rules._common import LaneConfig

    assert len(plan.rules(LaneConfig())) == 1
    assert len(plan.rules(LaneConfig(repo_root=tmp_path))) == 2
