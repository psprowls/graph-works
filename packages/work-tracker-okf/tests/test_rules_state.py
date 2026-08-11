from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from work_helpers import lane_report, write_item

TODAY = date(2026, 8, 3)


def codes_for(tmp_path: Path, frontmatter: str, *, slug: str = "2026-08-01-bug-x", body: str | None = None) -> set[str]:
    kwargs = {} if body is None else {"body": body}
    write_item(tmp_path, slug, frontmatter, **kwargs)  # type: ignore[arg-type]
    report = lane_report(tmp_path, today=TODAY)
    return {f.code for f in report.findings if f.code.startswith("state.")}


# --- state.phase-status-incoherent -----------------------------------------


@pytest.mark.parametrize(
    ("status", "phase"),
    [("accepted", "design"), ("in-progress", "plan"), ("resolved", "execute")],
)
def test_a_phase_the_status_does_not_admit_is_a_warn(tmp_path: Path, status: str, phase: str) -> None:
    codes = codes_for(tmp_path, f"type: Bug\nworkflow_status: {status}\nphase: {phase}\nupdated: 2026-08-01\n")
    assert "state.phase-status-incoherent" in codes


@pytest.mark.parametrize(
    ("status", "phase"),
    [("accepted", "execute"), ("in-progress", "finish"), ("resolved", "done"), ("open", "design")],
)
def test_a_coherent_pair_is_silent(tmp_path: Path, status: str, phase: str) -> None:
    codes = codes_for(tmp_path, f"type: Bug\nworkflow_status: {status}\nphase: {phase}\nupdated: 2026-08-01\n")
    assert "state.phase-status-incoherent" not in codes


def test_an_absent_phase_is_unconstrained(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: accepted\nupdated: 2026-08-01\n")
    assert "state.phase-status-incoherent" not in codes


# --- the five companions ----------------------------------------------------


def test_in_progress_without_owner_is_an_error(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-x", "type: Bug\nworkflow_status: in-progress\nphase: execute\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("state.in-progress-without-owner")[0]
    assert finding.severity == "error"
    assert finding.path == "work/2026-08-01-bug-x.md"


def test_in_progress_with_an_owner_is_silent(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: in-progress\nphase: execute\nowner: pat\n")
    assert "state.in-progress-without-owner" not in codes


def test_related_prs_does_not_satisfy_the_owner_requirement(tmp_path: Path) -> None:
    """C5-C: rule 5 narrowed to `owner` alone. `sources[]` is the pointer surface."""
    codes = codes_for(
        tmp_path,
        "type: Bug\nworkflow_status: in-progress\nphase: execute\nrelated_prs:\n  - https://example.test/1\n",
    )
    assert "state.in-progress-without-owner" in codes


def test_resolved_without_ref_is_a_warn(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: resolved\nphase: done\n")
    assert "state.resolved-without-ref" in codes


def test_an_epic_is_exempt_from_resolved_without_ref(tmp_path: Path) -> None:
    """An epic resolves through the children-terminal gate, not a `resolved_in`."""
    codes = codes_for(
        tmp_path,
        "type: Epic\nworkflow_status: resolved\nphase: done\n",
        slug="2026-08-01-epic-x",
    )
    assert "state.resolved-without-ref" not in codes


def test_superseded_without_link_is_an_error(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-x", "type: Bug\nworkflow_status: superseded\n")
    assert lane_report(tmp_path, today=TODAY).by_code("state.superseded-without-link")[0].severity == "error"


def test_superseded_with_a_link_is_silent(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: superseded\nsuperseded_by: 2026-08-02-bug-y\n")
    assert "state.superseded-without-link" not in codes


def test_mitigated_without_mitigation_is_an_error(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-x", "type: Bug\nworkflow_status: mitigated\n")
    assert lane_report(tmp_path, today=TODAY).by_code("state.mitigated-without-mitigation")[0].severity == "error"


def test_mitigated_with_a_mitigation_is_silent(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: mitigated\nmitigation: feature-flagged off\n")
    assert "state.mitigated-without-mitigation" not in codes


def test_wontfix_without_rationale_is_a_warn(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: wontfix\n")
    assert "state.wontfix-without-rationale" in codes


def test_wontfix_with_a_rationale_is_silent(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: wontfix\nrationale: superseded by the port\n")
    assert "state.wontfix-without-rationale" not in codes


# --- staleness --------------------------------------------------------------


def test_stuck_open_fires_past_thirty_days(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: open\nupdated: 2026-05-01\n")
    assert "state.stuck-open" in codes


def test_stuck_open_is_silent_inside_the_threshold(tmp_path: Path) -> None:
    """The boundary, with no system clock anywhere: 30 days exactly is not >30."""
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: open\nupdated: 2026-07-04\n")
    assert "state.stuck-open" not in codes


def test_stuck_accepted_fires_past_sixty_days(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: accepted\nphase: execute\nupdated: 2026-04-01\n")
    assert "state.stuck-accepted" in codes


def test_stuck_accepted_is_silent_inside_the_threshold(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: accepted\nphase: execute\nupdated: 2026-06-04\n")
    assert "state.stuck-accepted" not in codes


def test_an_unparseable_updated_is_never_stale(tmp_path: Path) -> None:
    codes = codes_for(tmp_path, "type: Bug\nworkflow_status: open\nupdated: soonish\n")
    assert "state.stuck-open" not in codes


# --- state.archive-eligible -------------------------------------------------


@pytest.mark.parametrize("status", ["resolved", "wontfix", "superseded"])
def test_every_terminal_status_is_archive_eligible_as_a_warn(tmp_path: Path, status: str) -> None:
    write_item(tmp_path, "2026-08-01-bug-x", f"type: Bug\nworkflow_status: {status}\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("state.archive-eligible")[0]
    assert finding.severity == "warn"


@pytest.mark.parametrize("status", ["open", "accepted", "in-progress", "mitigated"])
def test_a_non_terminal_status_is_not_archive_eligible(tmp_path: Path, status: str) -> None:
    codes = codes_for(tmp_path, f"type: Bug\nworkflow_status: {status}\nowner: pat\nphase: execute\n")
    assert "state.archive-eligible" not in codes


def test_an_already_archived_terminal_item_is_silent(tmp_path: Path) -> None:
    page = tmp_path / "work" / "_archive" / "2026-08-01-bug-x.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntitle: T\ndescription: D\ntype: Bug\nworkflow_status: resolved\n---\n", encoding="utf-8")
    assert lane_report(tmp_path, today=TODAY).by_code("state.archive-eligible") == ()


# --- state.slug-type-mismatch -----------------------------------------------


@pytest.mark.parametrize(
    ("slug", "type_name"),
    [
        ("2026-08-01-bug-x", "Bug"),
        ("2026-08-01-tech-debt-x", "TechDebt"),
        ("2026-08-01-test-gap-x", "TestGap"),
        ("2026-08-01-spike-x", "Spike"),
        ("2026-08-01-epic-x", "Epic"),
        ("2026-08-01-epic-feature-x", "Feature"),
        ("2026-08-01-feature-x", "Feature"),
        ("2026-08-01-epic-feature-x", "Epic"),
    ],
)
def test_an_agreeing_slug_is_silent(tmp_path: Path, slug: str, type_name: str) -> None:
    """The last row is the accepted consequence named in the spec: an `Epic`
    slugged `<date>-epic-feature-x` passes on the bare-`epic` branch."""
    codes = codes_for(tmp_path, f"type: {type_name}\nworkflow_status: open\nupdated: 2026-08-01\n", slug=slug)
    assert "state.slug-type-mismatch" not in codes


@pytest.mark.parametrize(
    ("slug", "type_name"),
    [
        ("2026-08-01-bug-x", "Feature"),
        ("2026-08-01-feature-x", "Bug"),
        ("2026-08-01-test-gap-x", "TechDebt"),
        ("no-date-prefix-here", "Bug"),
    ],
)
def test_a_disagreeing_slug_is_a_warn(tmp_path: Path, slug: str, type_name: str) -> None:
    write_item(tmp_path, slug, f"type: {type_name}\nworkflow_status: open\nupdated: 2026-08-01\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("state.slug-type-mismatch")[0]
    assert finding.severity == "warn"


def test_a_type_outside_the_vocabulary_is_silent(tmp_path: Path) -> None:
    """The schema `enum` already reports it, and there is no prefix to expect."""
    codes = codes_for(tmp_path, "type: Playbook\nworkflow_status: open\nupdated: 2026-08-01\n")
    assert "state.slug-type-mismatch" not in codes


# --- the module's shape -----------------------------------------------------


def test_the_module_declares_ten_codes_all_prefixed_state() -> None:
    from work_tracker_okf._rules import state

    assert len(state.CODES) == 10
    assert all(code.startswith("state.") for code in state.CODES)


def test_the_module_reads_no_clock() -> None:
    from pathlib import Path as _Path

    source = _Path("packages/work-tracker-okf/src/work_tracker_okf/_rules/state.py").read_text(encoding="utf-8")
    assert "date.today()" not in source
