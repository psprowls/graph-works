"""`finish_obligations`: tolerant parse, coverage derivation, plan/apply."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from okf_io import load
from work_helpers import load_written_items, write_item
from work_tracker_okf.obligations import (
    Obligation,
    apply_obligations,
    derive,
    parse_obligations,
    plan_add,
    plan_derive,
    unchecked_lines,
)

TODAY = date(2026, 9, 29)
DEFERRED = Obligation("Cherry-pick the reseed branch", "deferred", "2026-09-20")
CAVEAT = Obligation("Live Orca test not run", "coverage", "2026-09-21")


def test_parse_accepts_well_formed_entries_in_order() -> None:
    raw = [
        {"text": "Cherry-pick the reseed branch", "origin": "deferred", "recorded": "2026-09-20"},
        {"text": "Live Orca test not run", "origin": "coverage", "recorded": "2026-09-21"},
    ]
    assert parse_obligations(raw) == ((DEFERRED, CAVEAT), False)


def test_parse_treats_absent_and_empty_as_nothing() -> None:
    assert parse_obligations(None) == ((), False)
    assert parse_obligations([]) == ((), False)


@pytest.mark.parametrize("raw", [{"text": "x"}, "x"])
def test_parse_flags_a_wrong_container_type(raw: object) -> None:
    assert parse_obligations(raw) == ((), True)


@pytest.mark.parametrize(
    "bad",
    [
        {"text": "", "origin": "deferred", "recorded": "2026-09-20"},
        {"text": "two\nlines", "origin": "deferred", "recorded": "2026-09-20"},
        {"text": "x", "origin": "later", "recorded": "2026-09-20"},
        {"text": "x", "origin": "deferred", "recorded": "20-09-2026"},
        {"text": "x", "origin": "deferred", "recorded": "2026-02-30"},
        {"text": "x", "origin": "deferred"},
        {"text": "x", "origin": "deferred", "recorded": "2026-09-20", "status": "done"},
        "just a string",
        {"text": "x", "origin": ["deferred"], "recorded": "2026-09-20"},
    ],
)
def test_parse_keeps_good_entries_and_flags_a_bad_one(bad: object) -> None:
    good = {"text": "Cherry-pick the reseed branch", "origin": "deferred", "recorded": "2026-09-20"}
    assert parse_obligations([good, bad]) == ((DEFERRED,), True)


def test_parse_accepts_a_yaml_date_object_for_recorded() -> None:
    raw = [{"text": "x", "origin": "coverage", "recorded": date(2026, 9, 20)}]
    assert parse_obligations(raw) == ((Obligation("x", "coverage", "2026-09-20"),), False)


def test_unchecked_lines_reads_only_dash_bracket_space_lines() -> None:
    text = (
        "# Coverage\n"
        "- [x] delivered one\n"
        "- [X] delivered two\n"
        "- [ ] live Orca test not run\n"
        "   - [ ]   indented and padded  \n"
        "* [ ] a star bullet is not a coverage line\n"
        "- [ ]\n"
        "- [ ]    \n"
        "prose mentioning - [ ] mid-line\n"
    )
    assert unchecked_lines(text) == ("live Orca test not run", "indented and padded")


def test_derive_keeps_deferred_in_order_then_appends_coverage() -> None:
    other = Obligation("Tag the release", "deferred", "2026-09-22")
    result = derive((DEFERRED, CAVEAT, other), "- [ ] new caveat\n", on=TODAY)
    assert result == (DEFERRED, other, Obligation("new caveat", "coverage", "2026-09-29"))


def test_derive_with_no_file_clears_coverage_only() -> None:
    assert derive((DEFERRED, CAVEAT), None, on=TODAY) == (DEFERRED,)


def test_derive_keeps_recorded_for_an_unchanged_caveat() -> None:
    assert derive((CAVEAT,), "- [ ] Live Orca test not run\n", on=TODAY) == (CAVEAT,)


def test_derive_drops_duplicate_unchecked_lines() -> None:
    assert derive((), "- [ ] same\n- [ ] same\n", on=TODAY) == (Obligation("same", "coverage", "2026-09-29"),)


def _item(tmp_path: Path, extra: str = "", *, status: str = "in-progress", phase: str = "execute"):
    write_item(tmp_path, "work/feature-a", f"type: Feature\nwork_status: {status}\nphase: {phase}\n{extra}")
    (tmp_path / "work" / "feature-a").mkdir(exist_ok=True)
    return load_written_items(tmp_path)


def test_plan_add_appends_one_deferred_entry(tmp_path: Path) -> None:
    items = _item(tmp_path)
    plan = plan_add(items, "work/feature-a", "  Tag the release  ", on=TODAY)
    assert plan.refusal is None and plan.changed
    assert plan.after == (Obligation("Tag the release", "deferred", "2026-09-29"),)


@pytest.mark.parametrize("value", ["", "   ", "two\nlines", "cr\rline", "one line\n", "\none line"])
def test_plan_add_refuses_empty_or_multiline_text(tmp_path: Path, value: str) -> None:
    plan = plan_add(_item(tmp_path), "work/feature-a", value, on=TODAY)
    assert plan.refusal == "empty-text" and not plan.changed


@pytest.mark.parametrize(("status", "phase"), [("resolved", "done"), ("wontfix", "execute"), ("in-progress", "done")])
def test_plan_add_refuses_a_terminal_item(tmp_path: Path, status: str, phase: str) -> None:
    plan = plan_add(_item(tmp_path, status=status, phase=phase), "work/feature-a", "x", on=TODAY)
    assert plan.refusal == "terminal-item"


def test_plan_add_refuses_an_unknown_path(tmp_path: Path) -> None:
    assert plan_add(_item(tmp_path), "work/feature-missing", "x", on=TODAY).refusal == "unknown-path"


def test_plan_derive_is_a_no_op_for_an_unchanged_file(tmp_path: Path) -> None:
    (item,) = _item(tmp_path)
    item = replace(item, finish_obligations=(CAVEAT,))
    assert not plan_derive(item, "- [ ] Live Orca test not run\n", on=TODAY).changed


def test_apply_touches_only_the_finish_obligations_block(tmp_path: Path) -> None:
    items = _item(tmp_path)
    page = tmp_path / "work" / "feature-a.md"
    before = page.read_text(encoding="utf-8")
    document = load(page)
    apply_obligations(document, plan_add(items, "work/feature-a", "Tag the release", on=TODAY))
    after = document.serialize()
    added = [line.strip() for line in after.splitlines() if line not in before.splitlines()]
    assert added == ["finish_obligations:", "- text: Tag the release", "origin: deferred", "recorded: 2026-09-29"]
    assert [line for line in before.splitlines() if line not in after.splitlines()] == []


def test_apply_an_empty_result_removes_the_key(tmp_path: Path) -> None:
    extra = "finish_obligations:\n  - text: Live Orca test not run\n    origin: coverage\n    recorded: 2026-09-21\n"
    (item,) = _item(tmp_path, extra)
    item = replace(item, finish_obligations=(CAVEAT,))
    document = load(tmp_path / "work" / "feature-a.md")
    apply_obligations(document, plan_derive(item, None, on=TODAY))
    assert "finish_obligations" not in document.serialize()


def test_apply_refuses_a_refused_plan(tmp_path: Path) -> None:
    items = _item(tmp_path)
    document = load(tmp_path / "work" / "feature-a.md")
    before = document.serialize()
    # A stale/refused plan may still carry an after value; refusal must win.
    plan = replace(plan_add(items, "work/feature-a", "Tag the release", on=TODAY), refusal="empty-text")
    with pytest.raises(ValueError, match="refused obligation plan"):
        apply_obligations(document, plan)
    assert document.serialize() == before


def test_projection_round_trips_what_apply_wrote(tmp_path: Path) -> None:
    items = _item(tmp_path)
    page = tmp_path / "work" / "feature-a.md"
    document = load(page)
    apply_obligations(document, plan_add(items, "work/feature-a", "Tag the release", on=TODAY))
    document.save()
    (reloaded,) = load_written_items(tmp_path)
    assert reloaded.finish_obligations == (Obligation("Tag the release", "deferred", "2026-09-29"),)
