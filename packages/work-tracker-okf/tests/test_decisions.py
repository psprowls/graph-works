from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from work_tracker_okf import decisions
from work_tracker_okf.decisions import (
    UNSET,
    VALID_STATUSES,
    Decision,
    append,
    apply_plan,
    compose_prose,
    counts,
    decided_stamp,
    id_number,
    load,
    merge_prose,
    parse,
    plan_append,
    plan_supersede,
    plan_update,
    prose_block,
    query,
    render,
    set_fields,
    supersede,
)

CANONICAL = """\
# Decisions — epic-foo

## D-001 — Does A depend on B?
status: answered
affects: [child-one, child-two]
decided: 2026-08-11 by user
supersedes: —

**Answer:** Yes.

**Rationale:** Cheaper to reverse than the alternative.

## D-002 — Should C ship first?
status: assumed
affects: []
decided: —
supersedes: —

**If wrong:** C3 and C5 both need re-planning.
"""


def _pr(result):
    return result.preamble, result.entries


def test_parse_reads_the_canonical_entries():
    result = parse(CANONICAL)
    assert result.warnings == []
    assert result.preamble == "# Decisions — epic-foo\n"
    assert [d.id for d in result.entries] == ["D-001", "D-002"]
    first = result.entries[0]
    assert first.number == 1
    assert first.question == "Does A depend on B?"
    assert first.status == "answered"
    assert first.affects == ("child-one", "child-two")
    assert first.decided == "2026-08-11 by user"
    assert first.supersedes is None
    assert first.prose.startswith("**Answer:** Yes.")


def test_render_round_trips_byte_identically():
    result = parse(CANONICAL)
    assert render(result.preamble, result.entries) == CANONICAL


def test_the_round_trip_is_idempotent_on_a_second_pass():
    once = render(*_pr(parse(CANONICAL)))
    assert render(*_pr(parse(once))) == once


def test_parse_of_empty_text_is_empty():
    result = parse("")
    assert result.entries == [] and result.preamble == "" and result.warnings == []


def test_text_with_no_heading_is_all_preamble():
    result = parse("# Decisions\n\nnothing here yet\n")
    assert result.entries == []
    assert result.preamble == "# Decisions\n\nnothing here yet\n"


def test_unknown_keys_round_trip_rather_than_being_dropped():
    text = "## D-003 — q\nstatus: open\nowner: pat\nticket: ABC-1\n\nbody\n"
    entry = parse(text).entries[0]
    assert entry.extra_keys == (("owner", "pat"), ("ticket", "ABC-1"))
    assert render("", [entry]) == text


def test_a_bare_url_shaped_prose_line_is_not_swallowed_as_a_key():
    """A prose line immediately below the key block that happens to look like
    `key: value` (a bare URL, colon with no following whitespace) must not be
    absorbed into `extra_keys` — the key scan stops at the first line that
    doesn't match `key: value` or bare `key:`.

    The source text here has no blank line before the URL, so byte-identical
    round-tripping against it isn't in play — `render` always normalizes a
    blank line ahead of a non-empty prose block, same as it would for any
    hand-written text lacking that separator (see `CANONICAL`, which already
    carries one). What this guards is that the URL lands in `.prose`, not
    `.extra_keys`, and that the render is stable once normalized.
    """
    text = "## D-022 — q\nstatus: open\nhttps://example.com/path\n\nmore prose\n"
    entry = parse(text).entries[0]
    assert entry.extra_keys == ()
    assert entry.prose == "https://example.com/path\n\nmore prose"
    once = render(*_pr(parse(text)))
    assert render(*_pr(parse(once))) == once


def test_a_compact_key_value_with_no_space_after_the_colon_still_parses():
    """Agents and hand-edits sometimes skip the space after the colon —
    `status:open` must still be recognized as a key line, not fall through to
    prose and take every following key:value line down with it."""
    text = "## D-023 — q\nstatus:open\naffects:[a, b]\n\nbody\n"
    entry = parse(text).entries[0]
    assert entry.status == "open"
    assert entry.affects == ("a", "b")
    assert entry.prose == "body"


def test_a_repeated_recognized_key_falls_through_to_extra_keys():
    text = "## D-003 — q\nstatus: open\nstatus: answered\n\nbody\n"
    entry = parse(text).entries[0]
    assert entry.status == "open"
    assert entry.extra_keys == (("status", "answered"),)


def test_affects_accepts_the_bare_spelling():
    text = "## D-004 — q\nstatus: open\naffects: a, b\n\nbody\n"
    assert parse(text).entries[0].affects == ("a", "b")


def test_a_missing_status_warns_and_blanks():
    result = parse("## D-005 — q\naffects: []\n\nbody\n")
    assert result.entries[0].status == ""
    assert any("D-005" in w and "status" in w for w in result.warnings)


def test_an_invalid_status_warns_and_blanks():
    result = parse("## D-006 — q\nstatus: maybe\n\nbody\n")
    assert result.entries[0].status == ""
    assert any("maybe" in w for w in result.warnings)


def test_a_trailing_comment_on_status_is_warned_about_and_dropped():
    result = parse("## D-007 — q\nstatus: answered   # answered | assumed | open\n\nbody\n")
    assert result.entries[0].status == "answered"
    assert any("comment" in w for w in result.warnings)


def test_a_heading_with_no_question_warns():
    result = parse("## D-008\nstatus: open\n\nbody\n")
    assert result.entries[0].question == ""
    assert any("D-008" in w for w in result.warnings)


def test_a_heading_with_no_question_round_trips_without_a_dangling_dash():
    text = "## D-008\nstatus: open\n\nbody\n"
    entry = parse(text).entries[0]
    assert render("", [entry]) == text


def test_parse_never_raises_on_garbage():
    result = parse("## D-00X not a heading\n:::\n## D-009\n???\n")
    assert isinstance(result.warnings, list)


def test_all_three_dash_spellings_are_accepted():
    """Agents type an em dash, an en dash and a hyphen, so the parser takes all
    three rather than warning on two of them."""
    for dash in ("—", "–", "-", "--"):  # noqa: RUF001 -- en/em dash intentional
        entry = parse(f"## D-001 {dash} q\nstatus: open\n").entries[0]
        assert entry.question == "q", dash


def test_a_duplicate_id_warns():
    result = parse("## D-010 — a\nstatus: open\n\n## D-010 — b\nstatus: open\n")
    assert any("duplicate" in w for w in result.warnings)


def test_a_duplicate_id_warns_even_across_the_padded_and_unpadded_spelling():
    """Duplicate detection compares by number, like every other identity check
    in this module — `D-014` and `D-14` are the same entry differently typed."""
    result = parse("## D-014 — a\nstatus: open\n\n## D-14 — b\nstatus: open\n")
    assert any("duplicate" in w for w in result.warnings)


def test_an_entry_without_prose_round_trips():
    text = "## D-011 — q\nstatus: open\naffects: []\ndecided: —\nsupersedes: —\n"
    entry = parse(text).entries[0]
    assert entry.prose == ""
    assert render("", [entry]) == text


def test_render_orders_by_number_and_normalizes_key_order():
    text = "## D-002 — b\nsupersedes: D-001\nstatus: answered\n\nx\n"
    lines = render("", parse(text).entries).splitlines()
    assert lines[1] == "status: answered"
    assert lines[2] == "supersedes: D-001"
    assert lines[3] == ""
    assert lines[4] == "x"


def test_render_sorts_entries_by_number_not_by_file_order():
    text = "## D-002 — b\nstatus: open\n\n## D-001 — a\nstatus: open\n"
    out = render("", parse(text).entries)
    assert out.index("## D-001") < out.index("## D-002")


def test_prose_block_extracts_bold_labelled_text():
    entry = parse(CANONICAL).entries[0]
    assert prose_block(entry, "Answer") == "Yes."
    assert prose_block(entry, "**Rationale:**") == "Cheaper to reverse than the alternative."
    assert prose_block(entry, "If wrong") is None
    assumed = parse(CANONICAL).entries[1]
    assert prose_block(assumed, "If wrong") == "C3 and C5 both need re-planning."


def test_prose_block_collects_the_continuation_lines():
    entry = parse("## D-001 — q\nstatus: open\n\n**If wrong:** one\ntwo\n\nthree\n").entries[0]
    assert prose_block(entry, "If wrong") == "one\ntwo"


def test_compose_and_merge_replace_known_labels_and_keep_unrelated_prose() -> None:
    existing = "**Answer:** guess\n\nFree-form note\n\n**If wrong:** re-plan"
    merged = merge_prose(existing, compose_prose(answer="settled", rationale="evidence"))
    assert merged == ("**Answer:** settled\n\n**Rationale:** evidence\n\nFree-form note\n\n**If wrong:** re-plan")


def test_merge_collapses_duplicate_blocks_for_each_supplied_label() -> None:
    existing = (
        "**Answer:** first guess\n\nFree-form note\n\n**Answer:** second guess\n\n"
        "**Rationale:** first reason\n\n**If wrong:** re-plan\n\n**Rationale:** second reason"
    )
    replacement = compose_prose(answer="settled", rationale="evidence")

    merged = merge_prose(existing, replacement)

    assert merged == ("**Answer:** settled\n\nFree-form note\n\n**Rationale:** evidence\n\n**If wrong:** re-plan")


def test_decided_stamp_uses_only_supplied_values() -> None:
    assert decided_stamp(date(2026, 8, 18), "pat") == "2026-08-18 by pat"


@pytest.mark.parametrize(
    ("status", "answer", "if_wrong", "refusal"),
    [
        ("open", None, None, None),
        ("assumed", "guess", None, "if-wrong-required"),
        ("assumed", "guess", "re-plan", None),
        ("answered", None, None, "answer-required"),
        ("superseded", "x", None, "status-disallowed"),
    ],
)
def test_append_status_rules(tmp_path: Path, status, answer, if_wrong, refusal) -> None:
    plan = plan_append(
        tmp_path / "ledger.md",
        question="q",
        status=status,
        answer=answer,
        rationale=None,
        if_wrong=if_wrong,
        affects=(),
        on=date(2026, 8, 18),
        decided_by="pat",
    )
    assert plan.refusal == refusal


def test_the_status_vocabulary_is_the_closed_four():
    assert frozenset({"answered", "assumed", "open", "superseded"}) == VALID_STATUSES


def test_a_decision_is_frozen():
    decision = Decision(id="D-001", number=1)
    with pytest.raises(Exception, match="cannot assign"):
        decision.status = "open"


@pytest.mark.parametrize(
    "text",
    [
        "## D-020 — q\nstatus: open\naffects: [a, b]\n\nbody\n",
        "## D-021 — q\nstatus: answered\ndecided: 2026-08-11\n\nbody\n",
        "## D-022 — q\nstatus: open\naffects: [x]\nsupersedes: D-020\n\nbody\n",
    ],
)
def test_render_never_fabricates_a_key_the_source_did_not_carry(text):
    """The regression `_present_keys` exists for: a partial subset must not grow
    an `affects: []` or a `decided: —` line it never had."""
    assert render("", parse(text).entries) == text


def test_id_number_unifies_the_padded_and_unpadded_spellings():
    assert id_number("D-014") == id_number("D-14") == id_number("14") == id_number("014") == 14


def test_id_number_rejects_anything_else():
    with pytest.raises(ValueError, match="fourteen"):
        id_number("fourteen")


def _query_entries():
    return parse(
        "## D-001 — q1\nstatus: superseded\naffects: [child-a]\n\nx\n\n"
        "## D-002 — q2\nstatus: assumed\naffects: [child-a, child-b]\n\nx\n\n"
        "## D-003 — q3\nstatus: answered\n\nFollows from D-001.\n\n"
        "## D-004 — q4\nstatus: answered\naffects: [child-a]\nsupersedes: D-001\n\nx\n"
    ).entries


def test_query_by_status():
    entries = _query_entries()
    assert [d.id for d in query(entries, status="assumed")] == ["D-002"]
    assert [d.id for d in query(entries, status="superseded")] == ["D-001"]


def test_query_by_affects():
    assert [d.id for d in query(_query_entries(), affects="child-b")] == ["D-002"]
    assert [d.id for d in query(_query_entries(), affects="child-a")] == ["D-001", "D-002", "D-004"]


def test_query_by_cites_matches_supersedes_and_prose():
    """`cites` selects entries that REFERENCE an id, not the entry carrying it."""
    assert [d.id for d in query(_query_entries(), cites="D-001")] == ["D-003", "D-004"]


def test_query_by_cites_normalizes_the_id_width():
    assert [d.id for d in query(_query_entries(), cites="D-1")] == ["D-003", "D-004"]


def test_query_combines_filters_with_and():
    assert [d.id for d in query(_query_entries(), status="answered", cites="D-001")] == ["D-003", "D-004"]


def test_query_with_no_filters_returns_everything_in_the_order_given():
    entries = _query_entries()
    assert [d.id for d in query(entries)] == [d.id for d in entries]


def test_query_rejects_a_malformed_cites_value():
    with pytest.raises(ValueError, match="cites"):
        query(_query_entries(), cites="fourteen")


def test_counts_rolls_up_per_status_plus_total():
    assert counts(_query_entries()) == {
        "answered": 2,
        "assumed": 1,
        "open": 0,
        "superseded": 1,
        "invalid": 0,
        "total": 4,
    }


def test_counts_flags_an_invalid_status():
    entries = parse("## D-001 — q\nstatus: nonsense\n\nx\n").entries
    assert counts(entries) == {
        "answered": 0,
        "assumed": 0,
        "open": 0,
        "superseded": 0,
        "invalid": 1,
        "total": 1,
    }


# --- extract_cited_decisions: what a spec cites ------------------------------


def test_cited_ids_come_back_in_first_seen_order_deduped():
    text = "cites D-014 then D-002 then D-014 again"
    assert decisions.extract_cited_decisions(text) == ("D-014", "D-002")


def test_cited_ids_are_verbatim_so_padded_and_unpadded_stay_distinct():
    # The caller unifies by number via `id_number`, exactly as `query`'s
    # `cites` filter already does. Unifying here would lose the source spelling.
    assert decisions.extract_cited_decisions("D-14 and D-014") == ("D-14", "D-014")


def test_text_with_no_ids_cites_nothing():
    assert decisions.extract_cited_decisions("no decisions here, D-x is not one") == ()


def test_extract_cited_decisions_is_exported():
    assert "extract_cited_decisions" in decisions.__all__


def _ledger(tmp_path: Path) -> Path:
    from work_tracker_okf.paths import decisions_ledger

    path = decisions_ledger("2026-08-11-epic-foo").path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


DAY = date(2026, 8, 18)


def seeded_ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "references/00-decisions.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "# Decisions\n\n## D-001 — q\nstatus: open\n\n**Rationale:** pending\n",
        encoding="utf-8",
    )
    return ledger


def recording_lock(paths: list[Path]):
    @contextmanager
    def locked(path: Path):
        paths.append(path)
        yield

    return locked


def test_planning_is_byte_for_byte_no_write(tmp_path: Path) -> None:
    ledger = seeded_ledger(tmp_path)
    before = ledger.read_bytes()
    plan = plan_update(
        ledger,
        "D-001",
        answer="settled",
        rationale="evidence",
        on=date(2026, 8, 18),
        decided_by="pat",
    )
    assert plan.refusal is None
    assert ledger.read_bytes() == before


def test_update_replaces_answer_and_rationale_while_preserving_other_prose(tmp_path: Path) -> None:
    ledger = seeded_ledger(tmp_path)
    ledger.write_text(
        "# Decisions\n\n## D-001 — q\nstatus: assumed\n\n"
        "**Answer:** guess\n\n**Rationale:** stale\n\nFree-form note\n\n**If wrong:** re-plan\n",
        encoding="utf-8",
    )
    plan = plan_update(
        ledger,
        "D-001",
        answer="settled",
        rationale=None,
        on=DAY,
        decided_by="pat",
    )
    assert plan.primary is not None
    assert plan.primary.prose == "**Answer:** settled\n\nFree-form note\n\n**If wrong:** re-plan"


def test_apply_rejects_a_stale_snapshot_without_writing(tmp_path: Path) -> None:
    ledger = seeded_ledger(tmp_path)
    plan = plan_update(
        ledger,
        "D-001",
        answer="settled",
        rationale=None,
        on=date(2026, 8, 18),
        decided_by="pat",
    )
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\nexternal edit\n", encoding="utf-8")
    before = ledger.read_bytes()
    applied = apply_plan(plan)
    assert applied.stale is True
    assert applied.written is False
    assert ledger.read_bytes() == before


def test_apply_treats_a_crlf_to_lf_external_edit_as_stale(tmp_path: Path) -> None:
    ledger = tmp_path / "references/00-decisions.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"# Decisions\r\n\r\n## D-001 - q\r\nstatus: open\r\n")
    plan = plan_update(
        ledger,
        "D-001",
        answer="settled",
        rationale=None,
        on=DAY,
        decided_by="pat",
    )
    ledger.write_bytes(ledger.read_bytes().replace(b"\r\n", b"\n"))
    externally_edited = ledger.read_bytes()

    applied = apply_plan(plan)

    assert applied.stale is True
    assert applied.written is False
    assert ledger.read_bytes() == externally_edited


def test_supersession_apply_writes_both_entries_under_one_lock(tmp_path: Path, monkeypatch) -> None:
    ledger = seeded_ledger(tmp_path)
    locks: list[Path] = []
    monkeypatch.setattr(decisions, "_locked", recording_lock(locks))
    applied = apply_plan(
        plan_supersede(
            ledger,
            "D-001",
            question="q2",
            answer="a",
            rationale=None,
            on=DAY,
            decided_by="pat",
        )
    )
    assert applied.written is True
    assert len(locks) == 1


@pytest.mark.parametrize("status", ["answered", "superseded"])
def test_answer_refuses_entries_outside_open_or_assumed(tmp_path: Path, status: str) -> None:
    ledger = seeded_ledger(tmp_path)
    text = ledger.read_text(encoding="utf-8").replace("status: open", f"status: {status}")
    ledger.write_text(text, encoding="utf-8")
    plan = plan_update(
        ledger,
        "D-001",
        answer="settled",
        rationale=None,
        on=DAY,
        decided_by="pat",
    )
    assert plan.refusal == ("superseded-decision" if status == "superseded" else "transition-disallowed")


def test_the_caller_composes_the_path_the_module_never_discovers_it(tmp_path: Path):
    """The package's standing rule: every file function takes a resolved `Path`."""
    from work_tracker_okf.paths import decisions_ledger

    assert _ledger(tmp_path) == tmp_path / "work" / "2026-08-11-epic-foo" / "references" / "00-decisions.md"
    assert decisions_ledger("x").path(tmp_path).name == "00-decisions.md"


def test_an_absent_ledger_reads_as_empty(tmp_path: Path):
    """A pre-design epic legitimately has no ledger, so absence is not an error."""
    result = load(tmp_path / "nope" / "00-decisions.md")
    assert result.entries == [] and result.warnings == [] and result.preamble == ""


def test_append_allocates_from_001(tmp_path: Path):
    ledger = _ledger(tmp_path)
    first = append(ledger, question="q1", status="open")
    second = append(ledger, question="q2", status="assumed", affects=("a",), prose="**If wrong:** boom")
    assert (first.id, second.id) == ("D-001", "D-002")
    entries = load(ledger).entries
    assert [d.id for d in entries] == ["D-001", "D-002"]
    assert entries[1].affects == ("a",)


def test_append_creates_the_parent_directory(tmp_path: Path):
    from work_tracker_okf.paths import decisions_ledger

    ledger = decisions_ledger("2026-08-11-epic-fresh").path(tmp_path)
    append(ledger, question="q", status="open")
    assert ledger.exists()


def test_append_rejects_an_invalid_status(tmp_path: Path):
    """Caller error, not content: `parse` tolerates the same value on the read."""
    with pytest.raises(ValueError, match="maybe"):
        append(_ledger(tmp_path), question="q", status="maybe")


def test_append_never_reuses_a_deleted_id(tmp_path: Path):
    """Max-plus-one, never gap-filling — which is what makes a gap a genuine
    lost-entry signal, and what `decisions.entry-invalid` reports."""
    ledger = _ledger(tmp_path)
    for question in ("q1", "q2", "q3"):
        append(ledger, question=question, status="open")
    parsed = load(ledger)
    kept = [d for d in parsed.entries if d.id != "D-002"]
    ledger.write_text(render(parsed.preamble, kept), encoding="utf-8")
    assert append(ledger, question="q4", status="open").id == "D-004"


def test_append_preserves_the_preamble(tmp_path: Path):
    ledger = _ledger(tmp_path)
    ledger.write_text("# Decisions\n", encoding="utf-8")
    append(ledger, question="q", status="open")
    assert load(ledger).preamble == "# Decisions\n"


def test_set_fields_updates_and_validates(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    updated = set_fields(ledger, "D-001", status="answered", decided="2026-08-11 by user", prose="**Answer:** yes")
    assert updated.status == "answered" and updated.decided == "2026-08-11 by user"
    assert load(ledger).entries[0].prose == "**Answer:** yes"


def test_set_fields_leaves_untouched_fields_alone(tmp_path: Path):
    """The whole point of the UNSET sentinel: "not given" is not "set to None"."""
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="assumed", affects=("child-a",), decided="2026-08-11")
    updated = set_fields(ledger, "D-001", status="answered")
    assert updated.question == "q"
    assert updated.affects == ("child-a",)
    assert updated.decided == "2026-08-11"


def test_set_fields_can_clear_an_optional_field_explicitly(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="answered", decided="2026-08-11")
    assert set_fields(ledger, "D-001", decided=None).decided is None


def test_set_fields_adds_a_previously_absent_field_and_it_survives_reload(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="assumed")  # decided/affects absent
    set_fields(ledger, "D-001", decided="2026-08-11 by user", affects=("child-a",))
    reloaded = load(ledger).entries[0]
    assert reloaded.decided == "2026-08-11 by user"
    assert reloaded.affects == ("child-a",)


def test_set_fields_rejects_an_invalid_status(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    with pytest.raises(ValueError, match="maybe"):
        set_fields(ledger, "D-001", status="maybe")


def test_set_fields_on_an_unknown_id_lists_the_known_ones(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    with pytest.raises(ValueError, match="D-001"):
        set_fields(ledger, "D-099", status="answered")


def test_set_fields_on_an_empty_ledger_says_so(tmp_path: Path):
    with pytest.raises(ValueError, match="ledger is empty"):
        set_fields(_ledger(tmp_path), "D-001", status="answered")


def test_supersede_applies_both_halves(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="old q", status="assumed", affects=("child-a",))
    old, new = supersede(ledger, "D-001", question="new q", prose="**Answer:** other way")
    assert old.status == "superseded"
    assert new.id == "D-002" and new.supersedes == "D-001" and new.status == "answered"
    assert new.affects == ("child-a",)  # inherited when affects is not overridden
    entries = load(ledger).entries
    assert entries[0].status == "superseded" and entries[1].supersedes == "D-001"


def test_supersede_takes_an_explicit_affects_override(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open", affects=("child-a",))
    _old, new = supersede(ledger, "D-001", question="q2", prose="x", affects=("child-b",), decided="2026-08-12")
    assert new.affects == ("child-b",)
    assert new.decided == "2026-08-12"


def test_supersede_rejects_an_already_superseded_entry(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    supersede(ledger, "D-001", question="q2", prose="x")
    with pytest.raises(ValueError, match="already superseded"):
        supersede(ledger, "D-001", question="q3", prose="y")


def test_set_fields_rejects_a_superseded_target(tmp_path: Path):
    """Edit the entry that replaced it instead."""
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    supersede(ledger, "D-001", question="q2", prose="x")
    with pytest.raises(ValueError, match="superseded"):
        set_fields(ledger, "D-001", status="answered")


def test_prose_merge_sees_the_value_read_under_the_same_lock(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="assumed", prose="**Answer:** guess")
    updated = set_fields(ledger, "D-001", status="answered", prose_merge=lambda old: old.replace("guess", "real"))
    assert updated.prose == "**Answer:** real"
    assert load(ledger).entries[0].prose == "**Answer:** real"


def test_prose_merge_callback_runs_once_under_lock_on_the_latest_value(tmp_path: Path, monkeypatch) -> None:
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="assumed", prose="**Answer:** guess")
    active = False
    acquisitions = 0

    @contextmanager
    def competing_lock(path: Path):
        nonlocal active, acquisitions
        acquisitions += 1
        if acquisitions == 1:
            path.write_text(path.read_text(encoding="utf-8").replace("guess", "external"), encoding="utf-8")
        active = True
        try:
            yield
        finally:
            active = False

    monkeypatch.setattr(decisions, "_locked", competing_lock)
    calls: list[tuple[bool, str]] = []

    def merge(old: str) -> str:
        calls.append((active, old))
        return old.replace("external", "real")

    updated = set_fields(ledger, "D-001", status="answered", prose_merge=merge)

    assert calls == [(True, "**Answer:** external")]
    assert acquisitions == 1
    assert updated.prose == "**Answer:** real"
    assert load(ledger).entries[0].prose == "**Answer:** real"


def test_set_fields_rejects_both_prose_and_prose_merge(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    with pytest.raises(ValueError, match="prose_merge"):
        set_fields(ledger, "D-001", prose="x", prose_merge=lambda old: old)


def test_the_unset_sentinel_reprs_as_itself():
    assert repr(UNSET) == "UNSET"


_WORKER = """\
import sys
from pathlib import Path

from work_tracker_okf import decisions

decisions.append(
    Path(sys.argv[1]),
    question="q" + sys.argv[2],
    status="open",
    prose="body " + sys.argv[2],
)
"""


def test_concurrent_appends_from_real_processes(tmp_path: Path):
    """The locking decision is why this module is not trivial — exercise it for
    real, with OS processes, not a mocked lock."""
    ledger = _ledger(tmp_path)
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")

    count = 8
    procs = [
        subprocess.Popen([sys.executable, str(worker), str(ledger), str(i)], stderr=subprocess.PIPE)
        for i in range(count)
    ]
    for proc in procs:
        _out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err.decode()

    entries = load(ledger).entries
    assert len(entries) == count, "lost write"
    assert [d.id for d in entries] == [f"D-{n:03d}" for n in range(1, count + 1)]
    assert len({d.prose for d in entries}) == count, "a write was overwritten"


def test_the_lock_is_a_sibling_dotfile_not_the_ledger_itself(tmp_path: Path):
    """§7: the ledger may not exist on the first append (nothing to flock), and
    each write replaces the file, so two writers flocking the ledger directly
    could hold locks on different inodes and both proceed. The lock is created
    on demand and never unlinked — unlinking races the next writer's open."""
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    assert (ledger.parent / ".00-decisions.lock").is_file()


def test_no_temp_file_survives_a_write(tmp_path: Path):
    ledger = _ledger(tmp_path)
    append(ledger, question="q", status="open")
    assert sorted(p.name for p in ledger.parent.iterdir()) == [".00-decisions.lock", "00-decisions.md"]
