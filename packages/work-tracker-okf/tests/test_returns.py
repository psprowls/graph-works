"""Execute-return records and pure selection of reopened execution scope."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
from okf_io import load
from work_tracker_okf.obligations import Obligation
from work_tracker_okf.returns import (
    ExecuteReturn,
    ReturnReport,
    ScopeRow,
    apply_execute_return,
    new_record,
    parse_execute_return,
    plan_scope,
    read_report,
    render_reopened,
    section_heading,
    verify_report,
)

COV = "/work/x/references/03-execute-coverage.md"
PLAN = "/work/x/references/02-plan.md"
RETURN_ID = "ret-20261004-0123abcd"


def test_explicit_scope_is_cleaned_and_deduplicated_in_order() -> None:
    plan = plan_scope(["  Task 4 ", "Task 5", "Task 4"], (), obligations_malformed=False)
    assert plan.refusal is None and plan.source == "explicit"
    assert plan.scope == ("Task 4", "Task 5")


@pytest.mark.parametrize("bad", ["", "   ", "two\nlines", "cr\rhere", "ends\n"])
def test_blank_or_multiline_explicit_scope_refuses(bad: str) -> None:
    plan = plan_scope(["ok", bad], (), obligations_malformed=False)
    assert plan.refusal == "return-scope-invalid" and plan.scope == ()
    assert plan.source is None and "--return-scope" in plan.detail


def test_fallback_uses_distinct_coverage_obligations_only_in_order() -> None:
    obligations = (
        Obligation("Tag the release", "deferred", "2026-10-01"),
        Obligation("second caveat", "coverage", "2026-10-01"),
        Obligation("first caveat", "coverage", "2026-10-02"),
        Obligation("second caveat", "coverage", "2026-10-03"),
    )
    plan = plan_scope((), obligations, obligations_malformed=False)
    assert plan.refusal is None and plan.source == "coverage-obligations"
    assert plan.scope == ("second caveat", "first caveat")


@pytest.mark.parametrize("obligations", [(), (Obligation("Tag", "deferred", "2026-10-01"),)])
def test_empty_or_deferred_only_fallback_refuses_with_instructions(obligations: tuple[Obligation, ...]) -> None:
    plan = plan_scope((), obligations, obligations_malformed=False)
    assert plan.refusal == "return-scope-required" and plan.scope == ()
    assert plan.source is None and "--return-scope" in plan.detail


def test_malformed_obligations_refuse_the_fallback_but_not_explicit_scope() -> None:
    obligations = (Obligation("caveat", "coverage", "2026-10-01"),)
    assert plan_scope((), obligations, obligations_malformed=True).refusal == "return-scope-required"
    explicit = plan_scope(["x"], obligations, obligations_malformed=True)
    assert explicit.refusal is None and explicit.scope == ("x",)


def test_new_record_numbers_rows_and_round_trips() -> None:
    record = new_record(RETURN_ID, ["a", "b"], on=date(2026, 10, 4), coverage=COV, plan=None, plan_sha256=None)
    assert record.scope == (ScopeRow("R1", "a"), ScopeRow("R2", "b")) and record.active
    assert record.to_data() == {
        "id": RETURN_ID,
        "recorded": "2026-10-04",
        "state": "active",
        "coverage": COV,
        "scope": [{"id": "R1", "text": "a"}, {"id": "R2", "text": "b"}],
    }
    assert parse_execute_return(record.to_data()) == (record, False)


def _raw() -> dict[str, object]:
    return {
        "id": RETURN_ID,
        "recorded": "2026-10-04",
        "state": "active",
        "coverage": COV,
        "scope": [{"id": "R1", "text": "a"}],
    }


@pytest.mark.parametrize("raw", ["text", [], {"id": "ret-x"}, {**_raw(), "unknown": "field"}])
def test_malformed_record_shapes_are_flagged_not_raised(raw: object) -> None:
    assert parse_execute_return(raw) == (None, True)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("id", "ret-x"),
        ("id", 4),
        ("id", "ret-20261004-0123ABCd"),
        ("recorded", "2026-02-30"),
        ("recorded", "20261004"),
        ("recorded", None),
        ("recorded", datetime(2026, 10, 4)),
        ("state", "weird"),
        ("state", ["active"]),
        ("coverage", "relative.md"),
        ("coverage", None),
        ("scope", []),
        ("scope", "a"),
        ("scope", ["a"]),
        ("scope", [{"id": "R1", "text": ""}]),
        ("scope", [{"id": "R1", "text": "two\nlines"}]),
        ("scope", [{"id": "R1", "text": "a", "extra": True}]),
        ("scope", [{"id": "R2", "text": "a"}]),
        ("scope", [{"id": "R1", "text": "a"}, {"id": "R1", "text": "b"}]),
        ("scope", [{"id": "R1", "text": "a"}, {"id": "R3", "text": "b"}]),
        ("plan", PLAN),
        ("plan_sha256", "0" * 64),
    ],
)
def test_malformed_record_fields_are_flagged_not_raised(field: str, bad: object) -> None:
    raw = _raw()
    raw[field] = bad
    assert parse_execute_return(raw) == (None, True)


@pytest.mark.parametrize(
    ("plan", "sha"),
    [("relative.md", "0" * 64), (4, "0" * 64), (PLAN, "short"), (PLAN, "A" * 64), (PLAN, 4)],
)
def test_invalid_plan_hash_pairs_refuse(plan: object, sha: object) -> None:
    assert parse_execute_return({**_raw(), "plan": plan, "plan_sha256": sha}) == (None, True)


def test_parse_accepts_yaml_date_completed_state_and_plan_pair() -> None:
    raw = {**_raw(), "recorded": date(2026, 10, 4), "state": "completed", "plan": PLAN, "plan_sha256": "0" * 64}
    record, malformed = parse_execute_return(raw)
    assert not malformed
    assert record == ExecuteReturn(RETURN_ID, "2026-10-04", "completed", COV, (ScopeRow("R1", "a"),), PLAN, "0" * 64)
    assert record is not None and not record.active
    assert record.to_data()["plan"] == PLAN and record.to_data()["plan_sha256"] == "0" * 64


def test_absent_record_is_neither() -> None:
    assert parse_execute_return(None) == (None, False)


def test_fingerprint_preserves_intent_across_completion() -> None:
    record = new_record(RETURN_ID, ["a", "b"], on=date(2026, 10, 4), coverage=COV, plan=PLAN, plan_sha256="0" * 64)
    fingerprint = record.fingerprint()
    assert len(fingerprint) == 64 and all(c in "0123456789abcdef" for c in fingerprint)
    assert replace(record, state="completed", recorded="2026-10-05").fingerprint() == fingerprint
    for changed in (
        replace(record, id="ret-20261004-1123abcd"),
        replace(record, scope=(ScopeRow("R1", "other"), ScopeRow("R2", "b"))),
        replace(record, scope=tuple(reversed(record.scope))),
        replace(record, scope=(ScopeRow("R3", "a"), ScopeRow("R2", "b"))),
        replace(record, coverage="/work/other.md"),
        replace(record, plan="/work/other-plan.md"),
        replace(record, plan_sha256="1" * 64),
    ):
        assert changed.fingerprint() != fingerprint


def test_apply_serializes_yaml_date_and_preserves_surrounding_document(tmp_path: Path) -> None:
    page = tmp_path / "item.md"
    before = "---\ntitle: 'Unchanged' # keep comment\n---\n\n# Body\n"
    page.write_text(before, encoding="utf-8", newline="")
    document = load(page)
    record = new_record(RETURN_ID, ["a"], on=date(2026, 10, 4), coverage=COV, plan=None, plan_sha256=None)
    apply_execute_return(document, record)
    assert type(document.fm_raw["execute_return"]["recorded"]) is date
    after = document.serialize()
    assert "title: 'Unchanged' # keep comment\n" in after and after.endswith("---\n\n# Body\n")
    assert "recorded: 2026-10-04\n" in after
    document.save()
    assert parse_execute_return(load(page).fm_raw["execute_return"]) == (record, False)


REC = new_record(
    RETURN_ID,
    ["Implement Task 4", "R9: - [x] # tricky"],
    on=date(2026, 10, 4),
    coverage=COV,
    plan=None,
    plan_sha256=None,
)


def test_reopen_appends_prepared_section_and_keeps_existing_bytes() -> None:
    before = "- [x] Original acceptance delivered -- done\n"
    after = render_reopened(before, REC)
    assert after == (
        "- [x] Original acceptance delivered -- done\n\n"
        "## Returned scope ret-20261004-0123abcd\n\n"
        "Report state: prepared\n\n"
        "- [ ] R1: Implement Task 4\n"
        "- [ ] R2: R9: - [x] # tricky\n"
    )
    assert render_reopened(after, REC) == after


@pytest.mark.parametrize("before", [None, ""])
def test_reopen_missing_or_empty_file_creates_section_only(before: str | None) -> None:
    assert render_reopened(before, REC) == (
        "## Returned scope ret-20261004-0123abcd\n\nReport state: prepared\n\n"
        "- [ ] R1: Implement Task 4\n- [ ] R2: R9: - [x] # tricky\n"
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("trailing", [False, True])
def test_reopen_preserves_newlines_and_missing_trailing_newline(newline: str, trailing: bool) -> None:
    before = f"- [x] a{newline}- [x] b" + (newline if trailing else "")
    after = render_reopened(before, REC)
    assert after.startswith(f"- [x] a{newline}- [x] b{newline}{newline}## Returned scope ")
    assert "\n" not in after.replace(newline, "")
    assert render_reopened(after, REC) == after


@pytest.mark.parametrize(
    ("before", "separator"),
    [("a\r\nb\r\nc\n", "\r\n"), ("a\nb\nc\r\n", "\n"), ("a\r\nb\n", "\n")],
)
def test_reopen_keeps_mixed_existing_bytes_and_uses_dominant_newline(before: str, separator: str) -> None:
    after = render_reopened(before, REC)
    assert after.startswith(before)
    appended = after[len(before) :]
    assert f"## Returned scope {RETURN_ID}{separator}{separator}Report state: prepared" in appended


def test_reopen_scope_that_looks_like_markdown_remains_row_text() -> None:
    record = replace(REC, scope=(ScopeRow("R1", "# heading"), ScopeRow("R2", "- [x] R9: injected")))
    text = render_reopened(None, record)
    assert "\n- [ ] R1: # heading\n- [ ] R2: - [x] R9: injected\n" in text
    assert read_report(text, RETURN_ID) == ReturnReport("prepared", (("R1", False), ("R2", False)), (), ())


def _reported(rows: str, state: str = "reported", rid: str = RETURN_ID) -> str:
    return f"- [x] acceptance\n\n## Returned scope {rid}\n\nReport state: {state}\n\n{rows}"


def test_second_return_gets_its_own_section_and_cannot_use_old_checked_rows() -> None:
    other = new_record(
        "ret-20261005-89abcdef", ["Task 5"], on=date(2026, 10, 5), coverage=COV, plan=None, plan_sha256=None
    )
    before = _reported("- [x] R1: done\n- [x] R2: done\n")
    text = render_reopened(before, other)
    assert text.startswith(before) and text.count("## Returned scope ") == 2
    assert verify_report(REC, text) == (None, "")
    assert verify_report(other, text)[0] == "return-evidence-stale"
    assert "- [ ] R1: Task 5\n" in text


def test_reopen_existing_report_is_byte_idempotent() -> None:
    text = _reported("- [X] R2: changed text -- assessed\r\n- [ ] R1: caveat -- explained")
    assert render_reopened(text, REC) == text
    assert section_heading(RETURN_ID) == "## Returned scope ret-20261004-0123abcd"


def test_read_report_exposes_marks_duplicate_ids_and_ignores_next_section() -> None:
    text = _reported("- [X] R2: R9: - [x] nested\n- [ ] R1: caveat\n- [x] R1: again\n")
    text += "## Next section\nReport state: prepared\n- [x] R3: unrelated\n"
    assert read_report(text.replace("\n", "\r\n"), RETURN_ID) == ReturnReport(
        "reported", (("R2", True), ("R1", True)), ("R1",), ()
    )
    assert read_report(text, "ret-20261005-89abcdef") is None


def test_verify_accepts_reported_rows_checked_or_not() -> None:
    text = _reported("- [x] R1: Implement Task 4 -- done\n- [ ] R2: tricky -- not done, see notes\n")
    assert verify_report(REC, text) == (None, "")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (None, "return-evidence-missing"),
        ("- [x] all old rows checked\n", "return-evidence-missing"),
        (_reported("- [x] R1: a\n- [x] R2: b\n", rid="ret-20261001-00000000"), "return-evidence-missing"),
        (_reported("- [ ] R1: a\n- [ ] R2: b\n", state="prepared"), "return-evidence-stale"),
        (_reported("- [x] R1: a\n- [x] R2: b\n", state="unknown"), "return-evidence-stale"),
        (_reported("- [x] R1: a\n- [x] R2: b\n").replace("Report state: reported\n", ""), "return-evidence-stale"),
        (_reported("- [x] R1: a\n"), "return-evidence-incomplete"),
        (_reported("- [x] R1: a\n- [x] R1: again\n- [x] R2: b\n"), "return-evidence-incomplete"),
        (_reported("- [x] R1: a\n- [x] R2: b\n- [x] R3: extra\n"), "return-evidence-incomplete"),
        (_reported("- [x] R1: a\n## Other\n- [x] R2: b\n"), "return-evidence-incomplete"),
    ],
)
def test_verify_refuses_stale_or_incomplete_evidence(text: str | None, code: str) -> None:
    refusal, detail = verify_report(REC, text)
    assert refusal == code and detail


def test_unchanged_prepared_render_is_not_a_report() -> None:
    assert verify_report(REC, render_reopened("", REC))[0] == "return-evidence-stale"


@pytest.mark.parametrize("second_state", ["reported", "prepared", ""])
def test_verify_refuses_duplicate_report_state_declarations(second_state: str) -> None:
    text = _reported("- [x] R1: a\n- [x] R2: b\n") + f"Report state: {second_state}\n"
    report = read_report(text, RETURN_ID)
    assert report is not None and report.state is None
    refusal, detail = verify_report(REC, text)
    assert refusal == "return-evidence-stale" and detail


def test_verify_refuses_duplicate_entire_return_sections() -> None:
    section = _reported("- [x] R1: a\n- [x] R2: b\n")
    text = section + "\n" + section
    assert render_reopened(text, REC) == text
    report = read_report(text, RETURN_ID)
    assert report is not None and report.state is None
    refusal, detail = verify_report(REC, text)
    assert refusal == "return-evidence-stale" and detail
