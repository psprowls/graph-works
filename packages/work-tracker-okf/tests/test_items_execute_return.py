"""Execute-return metadata survives both read paths and lint/schema validation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal, get_args

import pytest
from okf_ext.schemas import load_schemas, schema_rule
from okf_io import load_bundle, validate
from work_helpers import lane_report, load_written_items, write_item
from work_tracker_okf.advance import COMMIT_GATE_REFUSALS, RefusalReason
from work_tracker_okf.init import install_bundle
from work_tracker_okf.items import IGNORE
from work_tracker_okf.returns import ExecuteReturn, ScopeRow
from work_tracker_okf.snapshot import WorkSnapshot

_BASE = "type: Bug\nstatus: draft\nwork_status: open\nopened: 2026-10-04\nupdated: 2026-10-04\n"
_TODAY = date(2026, 10, 4)
_CODE = "structure.execute-return-malformed"
_RETURN = {
    "id": "ret-20261004-3f9a1c2b",
    "recorded": "2026-10-04",
    "state": "active",
    "coverage": "/work/bug-a/references/03-execute-coverage.md",
    "scope": [{"id": "R1", "text": "Implement Task 4"}, {"id": "R2", "text": "Verify the return"}],
}


def _field(value: object) -> str:
    return f"execute_return: {json.dumps(value)}\n"


@pytest.mark.parametrize("with_plan", [False, True])
@pytest.mark.parametrize("state", ["active", "completed"])
def test_valid_return_projects_identically_from_bundle_and_rows(
    tmp_path: Path, with_plan: bool, state: Literal["active", "completed"]
) -> None:
    value = {**_RETURN, "state": state}
    if with_plan:
        value.update(plan="/work/bug-a/references/02-plan.md", plan_sha256="a" * 64)
    write_item(tmp_path, "work/bug-a", _BASE + _field(value))
    (item,) = load_written_items(tmp_path)
    expected = ExecuteReturn(
        "ret-20261004-3f9a1c2b",
        "2026-10-04",
        state,
        "/work/bug-a/references/03-execute-coverage.md",
        (ScopeRow("R1", "Implement Task 4"), ScopeRow("R2", "Verify the return")),
        "/work/bug-a/references/02-plan.md" if with_plan else None,
        "a" * 64 if with_plan else None,
    )
    assert item.execute_return == expected
    assert "execute_return" not in item.invalid_optional_fields
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    (row_item,) = WorkSnapshot.from_rows((path, doc.fm_data(dates="iso")) for path, doc in bundle.concepts.items())
    assert row_item.execute_return == expected
    assert row_item == item
    assert lane_report(tmp_path).by_code(_CODE) == ()


@pytest.mark.parametrize("value", ["oops", None, {}, {**_RETURN, "scope": []}, {**_RETURN, "recorded": "2026-02-30"}])
def test_malformed_return_stays_readable_and_warns_in_both_read_paths(tmp_path: Path, value: object) -> None:
    write_item(tmp_path, "work/bug-a", _BASE + _field(value))
    (item,) = load_written_items(tmp_path)
    assert item.execute_return is None
    assert "execute_return" in item.invalid_optional_fields
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    (row_item,) = WorkSnapshot.from_rows((path, doc.fm_data(dates="iso")) for path, doc in bundle.concepts.items())
    assert row_item.execute_return is None
    assert "execute_return" in row_item.invalid_optional_fields
    (finding,) = lane_report(tmp_path).by_code(_CODE)
    assert finding.severity == "warn"
    assert finding.path == "work/bug-a.md"


def test_absent_return_is_valid(tmp_path: Path) -> None:
    write_item(tmp_path, "work/bug-a", _BASE)
    (item,) = load_written_items(tmp_path)
    assert item.execute_return is None
    assert "execute_return" not in item.invalid_optional_fields
    assert lane_report(tmp_path).by_code(_CODE) == ()


def test_lane_lint_names_malformed_execute_return(tmp_path: Path) -> None:
    write_item(tmp_path, "work/bug-a", _BASE + "execute_return: oops\n")
    (finding,) = lane_report(tmp_path).by_code(_CODE)
    assert finding.severity == "warn"
    assert finding.path == "work/bug-a.md"


@pytest.fixture
def installed_root(tmp_path: Path) -> Path:
    result = install_bundle(tmp_path, today=_TODAY, declarations_dir=tmp_path / ".gw", dry_run=False)
    assert result.ok, result.diff()
    return tmp_path


def _schema_findings(root: Path, value: object) -> tuple:
    write_item(root, "work/bug-a", _BASE + _field(value))
    report = validate(
        load_bundle(root, ignore=IGNORE),
        today=_TODAY,
        extra_rules=(schema_rule(load_schemas(root / ".gw" / "schema")),),
    )
    return report.by_code("schemas.invalid")


@pytest.mark.parametrize("type_name", ["Bug", "Epic", "Feature", "Release", "Spike", "TechDebt", "TestGap"])
@pytest.mark.parametrize("with_plan", [False, True])
def test_installed_seed_schemas_accept_valid_returns(installed_root: Path, type_name: str, with_plan: bool) -> None:
    value = dict(_RETURN)
    if with_plan:
        value.update(plan="/work/bug-a/references/02-plan.md", plan_sha256="b" * 64)
    write_item(installed_root, "work/bug-a", _BASE.replace("type: Bug", f"type: {type_name}") + _field(value))
    report = validate(
        load_bundle(installed_root, ignore=IGNORE),
        today=_TODAY,
        extra_rules=(schema_rule(load_schemas(installed_root / ".gw" / "schema")),),
    )
    assert report.by_code("schemas.invalid") == ()


@pytest.mark.parametrize(
    "value",
    [
        "oops",
        None,
        {},
        {key: value for key, value in _RETURN.items() if key != "coverage"},
        {**_RETURN, "extra": True},
        {**_RETURN, "id": "ret-invalid"},
        {**_RETURN, "recorded": "04-10-2026"},
        {**_RETURN, "state": "prepared"},
        {**_RETURN, "coverage": "relative.md"},
        {**_RETURN, "plan": "/work/bug-a/references/02-plan.md"},
        {**_RETURN, "plan_sha256": "a" * 64},
        {**_RETURN, "plan": "relative.md", "plan_sha256": "a" * 64},
        {**_RETURN, "plan": "/work/bug-a/references/02-plan.md", "plan_sha256": "bad"},
        {**_RETURN, "scope": []},
        {**_RETURN, "scope": [{"id": "R0", "text": "Task"}]},
        {**_RETURN, "scope": [{"id": "R1"}]},
        {**_RETURN, "scope": [{"id": "R1", "text": "Task", "extra": True}]},
        {**_RETURN, "scope": [{"id": "R1", "text": "   "}]},
        {**_RETURN, "scope": [{"id": "R1", "text": "first\nsecond"}]},
        {**_RETURN, "scope": [{"id": "R1", "text": "first\rsecond"}]},
    ],
)
def test_installed_seed_schema_rejects_malformed_return(installed_root: Path, value: object) -> None:
    assert _schema_findings(installed_root, value)


def test_return_refusals_are_declared_and_cannot_bypass_the_commit_gate() -> None:
    codes = {
        "return-scope-invalid",
        "return-scope-required",
        "return-metadata-invalid",
        "return-pending",
        "return-destination-unverified",
        "return-evidence-missing",
        "return-evidence-stale",
        "return-evidence-incomplete",
        "return-plan-changed",
    }
    assert codes <= set(get_args(RefusalReason))
    assert codes.isdisjoint(COMMIT_GATE_REFUSALS)
