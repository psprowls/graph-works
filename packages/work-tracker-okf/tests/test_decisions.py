import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from okf_io import load as load_document
from work_tracker_okf import compose, decisions
from work_tracker_okf.decisions import (
    HOLD_PHASES,
    HOLD_SHAPES,
    RECOGNIZED_KEYS,
    HoldFact,
    append,
    apply_plan,
    counts,
    hold_fact,
    holds_for,
    ledger_ref,
    load,
    next_id,
    parse,
    plan_amend,
    plan_append,
    plan_refusal,
    plan_supersede,
    plan_update,
    query,
    render,
    set_fields,
    supersede,
)

DAY = date(2026, 8, 22)
CANONICAL = """\
# Decisions

## D-001 — Choose a path?
status: open
affects: [work/feature-a]

**Rationale:** pending
"""

HELD = """\
# Decisions

## D-001 — Resume where?
status: open
affects: [work/feature-a]
hold: park
phase: execute
checkpoint: /work/feature-a/references/03-execute-checkpoint-D-001.md

**Rationale:** parked mid-stage

## D-002 — Stop dispatching?
status: open
affects: [work/feature-a]
hold: skip
phase: execute

## D-003 — Old question
status: answered
affects: [work/feature-a]
decided: 2026-09-01 by user
hold: skip
phase: plan
"""


def _ledger(tmp_path: Path, owner: str = "work/release-r1/children/epic-migration") -> Path:
    ledger = ledger_ref(owner).path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    return ledger


def _lock(tmp_path: Path, owner: str = "work/release-r1/children/epic-migration") -> Path:
    import hashlib

    digest = hashlib.sha256(owner.encode()).hexdigest()
    return tmp_path / ".gw" / "cache" / "decisions" / f"{digest}.lock"


def test_ledger_ref_uses_the_canonical_owner_path_and_filename(tmp_path: Path) -> None:
    ref = ledger_ref("work/release-r1/children/epic-migration")
    assert ref.rel == "work/release-r1/children/epic-migration/references/00-decisions.md"
    assert ref.source_id == "decisions"


def test_parse_render_round_trip_is_stable() -> None:
    parsed = parse(CANONICAL)
    assert parsed.warnings == []
    assert render(parsed.preamble, parsed.entries) == CANONICAL


def test_hold_keys_are_recognized_in_canonical_order() -> None:
    assert RECOGNIZED_KEYS == ("status", "affects", "decided", "supersedes", "hold", "phase", "checkpoint")
    assert frozenset({"park", "skip"}) == HOLD_SHAPES
    assert frozenset({"design", "plan", "execute", "finish", "done", "entry"}) == HOLD_PHASES


def test_hold_keys_round_trip_byte_stable() -> None:
    parsed = parse(HELD)
    assert parsed.warnings == []
    park, skip, history = parsed.entries
    assert (park.hold, park.phase, park.checkpoint) == (
        "park",
        "execute",
        "/work/feature-a/references/03-execute-checkpoint-D-001.md",
    )
    assert (skip.hold, skip.phase, skip.checkpoint) == ("skip", "execute", None)
    assert (history.hold, history.phase) == ("skip", "plan")
    assert all(entry.extra_keys == () for entry in parsed.entries)
    assert render(parsed.preamble, parsed.entries) == HELD


def test_hand_written_hold_keys_normalize_into_canonical_order() -> None:
    text = "## D-001 — q\nphase: plan\nhold: skip\nstatus: open\naffects: [work/a]\n"
    parsed = parse(text)
    assert render(parsed.preamble, parsed.entries) == (
        "## D-001 — q\nstatus: open\naffects: [work/a]\nhold: skip\nphase: plan\n"
    )


def test_unknown_hold_and_phase_values_warn_and_are_kept() -> None:
    parsed = parse("## D-001 — q\nstatus: open\naffects: [work/a]\nhold: pause\nphase: someday\n")
    entry = parsed.entries[0]
    assert (entry.hold, entry.phase) == ("pause", "someday")
    assert any("invalid hold 'pause'" in warning for warning in parsed.warnings)
    assert any("invalid phase 'someday'" in warning for warning in parsed.warnings)


def test_holds_for_returns_open_entries_naming_the_path_in_id_order() -> None:
    entries = parse(HELD).entries
    assert [entry.id for entry in holds_for(list(reversed(entries)), "work/feature-a")] == ["D-001", "D-002"]
    assert holds_for(entries, "work/other") == ()


def test_hold_fact_projects_shape_with_question_default() -> None:
    park = parse(HELD).entries[0]
    assert hold_fact(park, "work/feature-a") == HoldFact("work/feature-a", "D-001", "park", "execute")
    question = parse(CANONICAL).entries[0]
    assert hold_fact(question, "work/feature-a") == HoldFact("work/feature-a", "D-001", "question", None)


def test_malformed_hold_shape_still_projects_as_a_hold() -> None:
    entry = parse("## D-001 — q\nstatus: open\naffects: [work/a]\nhold: pause\n").entries[0]
    assert hold_fact(entry, "work/a").shape == "pause"
    assert holds_for([entry], "work/a") == (entry,)


def test_next_id_is_max_plus_one() -> None:
    assert next_id(parse(HELD).entries) == ("D-004", 4)
    assert next_id([]) == ("D-001", 1)


def test_plan_append_records_hold_keys(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    plan = plan_append(
        ledger,
        question="Stop?",
        status="open",
        answer=None,
        rationale=None,
        if_wrong=None,
        affects=("work/feature-a",),
        on=DAY,
        decided_by="coordinator",
        hold="park",
        phase="execute",
        checkpoint="/work/feature-a/references/03-execute-checkpoint-D-001.md",
    )
    assert plan.refusal is None and plan.primary is not None
    rendered = render("", plan.after)
    assert (
        "hold: park\nphase: execute\ncheckpoint: /work/feature-a/references/03-execute-checkpoint-D-001.md" in rendered
    )


def test_plan_append_refuses_a_non_open_hold(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    plan = plan_append(
        ledger,
        question="Stop?",
        status="answered",
        answer="yes",
        rationale=None,
        if_wrong=None,
        affects=("work/feature-a",),
        on=DAY,
        decided_by="user",
        hold="skip",
        phase="plan",
    )
    assert plan.refusal == "hold-status"
    assert plan.after == plan.before


def test_plan_refusal_snapshots_without_writing(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(HELD, encoding="utf-8", newline="")
    plan = plan_refusal(ledger, "checkpoint-exists", "already there")
    assert (plan.refusal, plan.detail, plan.snapshot.text) == ("checkpoint-exists", "already there", HELD)
    assert plan.primary is None and plan.after == plan.before


def test_parse_render_round_trip_is_idempotent_on_a_second_pass() -> None:
    parsed = parse(CANONICAL)
    once = render(parsed.preamble, parsed.entries)
    reparsed = parse(once)
    assert render(reparsed.preamble, reparsed.entries) == once


def test_unknown_keys_round_trip_instead_of_being_dropped() -> None:
    text = "## D-003 — q\nstatus: open\nowner: pat\nticket: ABC-1\n\nbody\n"
    entry = parse(text).entries[0]
    assert entry.extra_keys == (("owner", "pat"), ("ticket", "ABC-1"))
    assert render("", [entry]) == text


def test_parse_tolerates_bad_entries_and_counts_them_invalid() -> None:
    parsed = parse("## D-001 — q\nstatus: maybe\n")
    assert parsed.warnings
    assert counts(parsed.entries)["invalid"] == 1


def test_query_filters_status_affects_and_citations() -> None:
    parsed = parse(
        CANONICAL + "\n## D-002 — Follow D-001?\nstatus: answered\naffects: [work/feature-b]\n\n**Answer:** yes\n"
    )
    assert [entry.id for entry in query(parsed.entries, status="answered")] == ["D-002"]
    assert [entry.id for entry in query(parsed.entries, affects="work/feature-a")] == ["D-001"]
    assert [entry.id for entry in query(parsed.entries, cites="D-001")] == ["D-002"]


def test_plan_append_is_write_free_and_allocates_max_plus_one(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL, encoding="utf-8")
    before = ledger.read_bytes()
    plan = plan_append(
        ledger,
        question="Second?",
        status="open",
        answer=None,
        rationale=None,
        if_wrong=None,
        affects=(),
        on=DAY,
        decided_by="pat",
    )
    assert plan.primary is not None and plan.primary.id == "D-002"
    assert ledger.read_bytes() == before


def test_apply_uses_only_the_supplied_cache_lock(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    plan = plan_append(
        ledger,
        question="First?",
        status="open",
        answer=None,
        rationale=None,
        if_wrong=None,
        affects=(),
        on=DAY,
        decided_by="pat",
    )
    lock = _lock(tmp_path)
    application = apply_plan(plan, lock=lock)
    assert application.written
    assert lock.is_file()
    assert not tuple(ledger.parent.glob("*.lock"))
    assert not tuple(ledger.parent.glob(".*.lock"))


def test_apply_rejects_a_stale_snapshot_without_writing(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL, encoding="utf-8")
    plan = plan_update(ledger, "D-001", answer="settled", rationale=None, on=DAY, decided_by="pat")
    ledger.write_text(CANONICAL + "\nexternal edit\n", encoding="utf-8")
    before = ledger.read_bytes()
    applied = apply_plan(plan, lock=_lock(tmp_path))
    assert applied.stale and not applied.written
    assert ledger.read_bytes() == before


def test_apply_treats_a_crlf_to_lf_external_edit_as_stale(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_bytes(b"# Decisions\r\n\r\n## D-001 - q\r\nstatus: open\r\n")
    plan = plan_update(ledger, "D-001", answer="settled", rationale=None, on=DAY, decided_by="pat")
    ledger.write_bytes(ledger.read_bytes().replace(b"\r\n", b"\n"))
    externally_edited = ledger.read_bytes()

    applied = apply_plan(plan, lock=_lock(tmp_path))

    assert applied.stale and not applied.written
    assert ledger.read_bytes() == externally_edited


def test_update_answers_open_and_assumed_entries(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL, encoding="utf-8")
    plan = plan_update(ledger, "D-001", answer="settled", rationale="evidence", on=DAY, decided_by="pat")
    assert plan.primary is not None
    assert plan.primary.status == "answered"
    assert "**Answer:** settled" in plan.primary.prose


def test_update_refuses_unknown_and_already_answered_entries(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL.replace("status: open", "status: answered"), encoding="utf-8")
    answered = plan_update(ledger, "D-001", answer="x", rationale=None, on=DAY, decided_by="pat")
    missing = plan_update(ledger, "D-999", answer="x", rationale=None, on=DAY, decided_by="pat")
    assert answered.refusal == "transition-disallowed"
    assert missing.refusal == "unknown-decision"


def test_supersession_applies_both_entries_atomically(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL, encoding="utf-8")
    plan = plan_supersede(
        ledger,
        "D-001",
        question="Replacement?",
        answer="yes",
        rationale=None,
        on=DAY,
        decided_by="pat",
    )
    applied = apply_plan(plan, lock=_lock(tmp_path))
    assert applied.written
    parsed = load(ledger)
    assert [entry.status for entry in parsed.entries] == ["superseded", "answered"]
    assert parsed.entries[1].supersedes == "D-001"


def test_absent_ledger_reads_as_empty(tmp_path: Path) -> None:
    parsed = load(tmp_path / "missing" / "00-decisions.md")
    assert parsed.entries == [] and parsed.warnings == []


def test_parser_and_prose_helpers_preserve_diagnostics_and_canonical_blocks() -> None:
    parsed = parse(
        "## D-001\nstatus: answered # copied comment\n\n**Answer:** first\ncontinued\n\n"
        "## D-002 — Bad\nstatus: impossible\n"
    )
    assert "heading has no question" in " ".join(parsed.warnings)
    assert "dropped trailing comment" in " ".join(parsed.warnings)
    assert "invalid status" in " ".join(parsed.warnings)
    assert decisions.prose_block(parsed.entries[0], "Answer") == "first\ncontinued"
    assert decisions.prose_block(parsed.entries[0], "Missing") is None
    merged = decisions.merge_prose(
        "**Answer:** old\n\n**Answer:** duplicate\n\nfree note",
        "**Answer:** new\n\n**Rationale:** because\n\nnew note",
    )
    assert merged.count("**Answer:**") == 1
    assert merged.index("**Rationale:**") > merged.index("**Answer:**")
    assert decisions.extract_cited_decisions("D-001 and D-001 then D-2") == ("D-001", "D-2")
    assert repr(decisions.UNSET) == "UNSET"


def test_decision_planners_cover_every_refusal(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert (
        plan_append(
            ledger,
            question="q",
            status="superseded",
            answer=None,
            rationale=None,
            if_wrong=None,
            affects=(),
            on=DAY,
            decided_by="pat",
        ).refusal
        == "status-disallowed"
    )
    assert (
        plan_append(
            ledger,
            question="q",
            status="answered",
            answer=" ",
            rationale=None,
            if_wrong=None,
            affects=(),
            on=DAY,
            decided_by="pat",
        ).refusal
        == "answer-required"
    )
    assert (
        plan_append(
            ledger,
            question="q",
            status="assumed",
            answer="guess",
            rationale=None,
            if_wrong=" ",
            affects=(),
            on=DAY,
            decided_by="pat",
        ).refusal
        == "if-wrong-required"
    )

    ledger.write_text(CANONICAL.replace("status: open", "status: superseded"), encoding="utf-8")
    assert (
        plan_update(ledger, "D-001", answer="x", rationale=None, on=DAY, decided_by="pat").refusal
        == "superseded-decision"
    )
    ledger.write_text(CANONICAL, encoding="utf-8")
    assert (
        plan_update(ledger, "D-001", answer=" ", rationale=None, on=DAY, decided_by="pat").refusal == "answer-required"
    )
    assert (
        plan_supersede(ledger, "bad", question="q", answer="x", rationale=None, on=DAY, decided_by="pat").refusal
        == "unknown-decision"
    )
    assert (
        plan_supersede(ledger, "D-001", question="q", answer=" ", rationale=None, on=DAY, decided_by="pat").refusal
        == "answer-required"
    )
    ledger.write_text(CANONICAL.replace("status: open", "status: superseded"), encoding="utf-8")
    assert (
        plan_supersede(ledger, "D-001", question="q", answer="x", rationale=None, on=DAY, decided_by="pat").refusal
        == "superseded-decision"
    )


def test_direct_mutation_guards_and_query_errors(tmp_path: Path, monkeypatch) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL, encoding="utf-8")
    with pytest.raises(ValueError, match="either prose"):
        set_fields(ledger, "D-001", lock=_lock(tmp_path), prose="x", prose_merge=lambda old: old)
    with pytest.raises(ValueError, match="unknown status"):
        set_fields(ledger, "D-001", lock=_lock(tmp_path), status="invalid")
    with pytest.raises(ValueError, match="no decision"):
        set_fields(ledger, "D-999", lock=_lock(tmp_path), question="x")
    with pytest.raises(ValueError, match="cites expects"):
        query(parse(CANONICAL).entries, cites="bad")

    refused = plan_update(ledger, "D-999", answer="x", rationale=None, on=DAY, decided_by="pat")
    assert not apply_plan(refused, lock=_lock(tmp_path)).written

    applications = iter((decisions.DecisionApplication(stale=True), decisions.DecisionApplication()))
    monkeypatch.setattr(decisions, "apply_plan", lambda plan, lock: next(applications))
    with pytest.raises(AssertionError, match="unexpectedly refused"):
        decisions._apply_until_current(lambda: refused, lock=_lock(tmp_path))


def test_direct_supersede_retires_old_entry_and_inherits_affects(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(CANONICAL, encoding="utf-8")

    retired, replacement = supersede(
        ledger,
        "D-001",
        lock=_lock(tmp_path),
        question="Replacement?",
        prose="**Answer:** yes",
        decided=DAY.isoformat(),
    )

    assert retired.status == "superseded"
    assert replacement.id == "D-002"
    assert replacement.supersedes == retired.id
    assert replacement.affects == retired.affects
    with pytest.raises(ValueError, match="already superseded"):
        supersede(
            ledger,
            "D-001",
            lock=_lock(tmp_path),
            question="Again?",
            prose="no",
            affects=("work/other",),
        )


def test_prose_merge_callback_runs_once_under_lock_on_the_latest_value(tmp_path: Path, monkeypatch) -> None:
    ledger = _ledger(tmp_path)
    lock = _lock(tmp_path)
    append(ledger, lock=lock, question="q", status="assumed", prose="**Answer:** guess")
    active = False
    acquisitions = 0

    @contextmanager
    def competing_lock(_lock_path: Path):
        nonlocal active, acquisitions
        acquisitions += 1
        if acquisitions == 1:
            ledger.write_text(ledger.read_text(encoding="utf-8").replace("guess", "external"), encoding="utf-8")
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

    updated = set_fields(ledger, "D-001", lock=lock, status="answered", prose_merge=merge)

    assert calls == [(True, "**Answer:** external")]
    assert acquisitions == 1
    assert updated.prose == "**Answer:** real"
    assert load(ledger).entries[0].prose == "**Answer:** real"


_WORKER = """\
import sys
from pathlib import Path

from work_tracker_okf import decisions

decisions.append(
    Path(sys.argv[1]),
    lock=Path(sys.argv[2]),
    question="q" + sys.argv[3],
    status="open",
    prose="body " + sys.argv[3],
)
"""


def test_concurrent_appends_from_real_processes_use_the_cache_lock(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    lock = _lock(tmp_path)
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    count = 8
    processes = [
        subprocess.Popen(
            [sys.executable, str(worker), str(ledger), str(lock), str(index)],
            stderr=subprocess.PIPE,
        )
        for index in range(count)
    ]
    for process in processes:
        _stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr.decode()

    entries = load(ledger).entries
    assert len(entries) == count
    assert [entry.id for entry in entries] == [f"D-{number:03d}" for number in range(1, count + 1)]
    assert len({entry.prose for entry in entries}) == count


def test_no_temp_file_survives_a_decision_write(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    append(ledger, lock=_lock(tmp_path), question="q", status="open")
    assert [path.name for path in ledger.parent.iterdir()] == ["00-decisions.md"]


def test_decision_composition_registers_the_durable_parent_ledger(tmp_path: Path) -> None:
    owner = "work/release-r1/children/epic-migration"
    page = tmp_path / f"{owner}.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: Epic\ntitle: Migration\n---\n", encoding="utf-8")
    ledger = _ledger(tmp_path, owner)
    plan = plan_append(
        ledger,
        question="First?",
        status="open",
        answer=None,
        rationale=None,
        if_wrong=None,
        affects=(),
        on=DAY,
        decided_by="pat",
    )

    applied = compose.apply_decision_and_register(tmp_path, owner, plan, lock=_lock(tmp_path, owner))

    assert applied.written
    assert load_document(page).fm_data()["sources"] == [
        {
            "id": "decisions",
            "resource": "/work/release-r1/children/epic-migration/references/00-decisions.md",
            "title": "Decisions",
        }
    ]


def test_decision_composition_refuses_to_make_a_leaf_ledger_semantic(tmp_path: Path) -> None:
    owner = "work/bug-leaf"
    page = tmp_path / f"{owner}.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: Bug\ntitle: Leaf\n---\n", encoding="utf-8")
    ledger = _ledger(tmp_path, owner)
    plan = plan_append(
        ledger,
        question="No ledger here?",
        status="open",
        answer=None,
        rationale=None,
        if_wrong=None,
        affects=(),
        on=DAY,
        decided_by="pat",
    )

    with pytest.raises(ValueError, match="parent-capable"):
        compose.apply_decision_and_register(tmp_path, owner, plan, lock=_lock(tmp_path, owner))

    assert not ledger.exists()
    assert "sources" not in load_document(page).fm_data()


def test_stale_decision_composition_does_not_register_the_ledger(tmp_path: Path) -> None:
    owner = "work/epic-stale"
    page = tmp_path / f"{owner}.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: Epic\ntitle: Stale\n---\n", encoding="utf-8")
    ledger = _ledger(tmp_path, owner)
    plan = plan_append(
        ledger,
        question="Stale?",
        status="open",
        answer=None,
        rationale=None,
        if_wrong=None,
        affects=(),
        on=DAY,
        decided_by="pat",
    )
    ledger.write_text("external edit\n", encoding="utf-8")

    applied = compose.apply_decision_and_register(tmp_path, owner, plan, lock=_lock(tmp_path, owner))

    assert applied.stale and not applied.written
    assert "sources" not in load_document(page).fm_data()


ANSWERED = """\
# Decisions

## D-001 — Choose a path?
status: answered
affects: [work/feature-a]
decided: 2026-09-01 by pat

**Answer:** use the left path

**Rationale:** shorter

**If wrong:** walk back
"""


def _amend(ledger: Path, decision_id: str = "D-001", **overrides):
    kwargs = {
        "answer": "use the left path, paved",
        "note": "narrowed to the paved route",
        "rationale": None,
        "affects": None,
        "on": DAY,
        "decided_by": "pat",
    }
    kwargs.update(overrides)
    return plan_amend(ledger, decision_id, **kwargs)


def test_amend_replaces_answer_and_appends_one_amended_block(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    plan = _amend(ledger)
    assert plan.refusal is None
    assert plan.detail == "amend D-001"
    assert plan.superseded is None
    entry = plan.primary
    assert entry is not None
    assert (entry.id, entry.status, entry.decided) == ("D-001", "answered", "2026-09-01 by pat")
    assert entry.affects == ("work/feature-a",)
    assert decisions.prose_block(entry, "Answer") == "use the left path, paved"
    assert decisions.prose_block(entry, "Rationale") == "shorter"
    assert decisions.prose_block(entry, "If wrong") == "walk back"
    assert decisions.prose_block(entry, "Amended") == (
        "2026-08-22 by pat — narrowed to the paved route. Previously: use the left path"
    )
    assert entry.prose.count("**Amended:**") == 1
    assert ledger.read_text(encoding="utf-8") == ANSWERED  # write-free


def test_amend_applies_and_round_trips_without_warnings(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    assert apply_plan(_amend(ledger), lock=_lock(tmp_path)).written
    text = ledger.read_text(encoding="utf-8")
    parsed = parse(text)
    assert parsed.warnings == []
    assert [entry.status for entry in parsed.entries] == ["answered"]
    assert render(parsed.preamble, parsed.entries) == text


def test_repeated_amends_stack_oldest_first(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    assert apply_plan(_amend(ledger), lock=_lock(tmp_path)).written
    second = _amend(ledger, answer="use the left path, paved and lit", note="added lighting")
    assert apply_plan(second, lock=_lock(tmp_path)).written
    prose = load(ledger).entries[0].prose
    first_at = prose.index("Previously: use the left path")  # first occurrence = first Amended block
    second_at = prose.index("Previously: use the left path, paved")
    assert prose.count("**Amended:**") == 2
    assert first_at < second_at
    assert prose.rstrip().endswith("Previously: use the left path, paved")


def test_amend_rationale_and_affects_replace_only_when_given(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    plan = _amend(ledger, rationale="shorter and paved", affects=["work/feature-a", "work/feature-b"])
    assert plan.primary is not None
    assert decisions.prose_block(plan.primary, "Rationale") == "shorter and paved"
    assert plan.primary.affects == ("work/feature-a", "work/feature-b")
    assert "affects: [work/feature-a, work/feature-b]" in render("", plan.after)


def test_amend_refusals(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(
        ANSWERED + "\n## D-002 — Still open?\nstatus: open\n" + "\n## D-003 — Retired?\nstatus: superseded\n",
        encoding="utf-8",
    )
    assert _amend(ledger, "D-999").refusal == "unknown-decision"
    assert _amend(ledger, "D-003").refusal == "superseded-decision"
    open_plan = _amend(ledger, "D-002")
    assert open_plan.refusal == "transition-disallowed"
    assert "answer it with gw work decision answer" in open_plan.detail
    assert _amend(ledger, answer="  ").refusal == "answer-required"
    assert _amend(ledger, note=" ").refusal == "note-required"
    unchanged = _amend(ledger, answer="use  the left\npath")
    assert unchanged.refusal == "answer-unchanged"
    assert unchanged.detail == "D-001 already says this; nothing to amend"
    assert _amend(ledger, answer="use the left path", rationale="shorter").refusal == "answer-unchanged"
    assert _amend(ledger, answer="use the left path", affects=["work/feature-a"]).refusal == "answer-unchanged"
    for refused in (_amend(ledger, "D-999"), unchanged):
        assert refused.after == refused.before and refused.primary is None


def test_amend_same_answer_with_new_rationale_or_affects_is_accepted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    assert _amend(ledger, answer="use the left path", rationale="safer").refusal is None
    assert _amend(ledger, answer="use the left path", affects=["work/feature-b"]).refusal is None


def test_amend_collapses_multiline_note_and_previous_answer(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(
        ANSWERED.replace("**Answer:** use the left path", "**Answer:** use the left\npath"), encoding="utf-8"
    )
    plan = _amend(ledger, note="narrowed\n\nto the paved route.")
    assert plan.primary is not None
    assert decisions.prose_block(plan.primary, "Amended") == (
        "2026-08-22 by pat — narrowed to the paved route. Previously: use the left path"
    )


def test_amend_entry_without_an_answer_block(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(
        "# Decisions\n\n## D-001 — Legacy?\nstatus: answered\ndecided: 2026-09-01 by pat\n\nfree prose only\n",
        encoding="utf-8",
    )
    plan = _amend(ledger, answer="spelled out now", note="made explicit")
    assert plan.primary is not None
    assert decisions.prose_block(plan.primary, "Answer") == "spelled out now"
    assert "free prose only" in plan.primary.prose
    assert plan.primary.prose.rstrip().endswith("Previously: (no answer recorded)")


def test_amend_resolves_unpadded_ids_and_refuses_malformed_ones(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    assert _amend(ledger, "D-1").primary.id == "D-001"  # type: ignore[union-attr]
    assert _amend(ledger, "bad").refusal == "unknown-decision"


def test_amend_plan_goes_stale_after_an_external_edit(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(ANSWERED, encoding="utf-8")
    plan = _amend(ledger)
    ledger.write_text(ANSWERED + "\nexternal edit\n", encoding="utf-8")
    before = ledger.read_bytes()
    applied = apply_plan(plan, lock=_lock(tmp_path))
    assert applied.stale and not applied.written
    assert ledger.read_bytes() == before


MULTIPARAGRAPH = ANSWERED.replace(
    "**Answer:** use the left path", "**Answer:** use the left path\n\nKeep to the pavement."
).replace("**Rationale:** shorter", "**Rationale:** shorter\n\nSafer at night.")


def test_amend_replaces_complete_paragraph_sections_and_records_history(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(MULTIPARAGRAPH, encoding="utf-8")
    plan = _amend(ledger, rationale="well marked\n\nEasy to follow.")
    assert plan.primary is not None
    assert "Previously: use the left path Keep to the pavement." in plan.primary.prose
    before_history = plan.primary.prose.split("**Amended:**")[0]
    assert "Keep to the pavement." not in before_history
    assert "Safer at night." not in before_history
    assert "**Rationale:** well marked\n\nEasy to follow." in before_history
    assert "**If wrong:** walk back" in before_history


@pytest.mark.parametrize("rationale", [None, "shorter\n\nSafer at night."])
def test_amend_refuses_unchanged_complete_answer_and_rationale(tmp_path: Path, rationale: str | None) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(MULTIPARAGRAPH, encoding="utf-8")
    assert (
        _amend(ledger, answer="use the left path\n\nKeep to the pavement.", rationale=rationale).refusal
        == "answer-unchanged"
    )


@pytest.mark.parametrize("rationale", [None, "shorter"])
def test_amend_accepts_removing_later_answer_or_rationale_paragraph(tmp_path: Path, rationale: str | None) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(MULTIPARAGRAPH, encoding="utf-8")
    answer = "use the left path" if rationale is None else "use the left path\n\nKeep to the pavement."
    assert _amend(ledger, answer=answer, rationale=rationale).refusal is None


def test_amended_ledger_passes_decision_rules(tmp_path: Path) -> None:
    from work_helpers import lane_report, write_item

    owner = "work/feature-a"
    write_item(
        tmp_path,
        owner,
        "type: Feature\nstatus: stable\nwork_status: in-progress\nphase: execute\n"
        "effort: small\nowner: pat\nopened: 2026-08-01\nupdated: 2026-08-22\n",
    )
    ledger = _ledger(tmp_path, owner)
    ledger.write_text(MULTIPARAGRAPH, encoding="utf-8")
    assert apply_plan(_amend(ledger), lock=_lock(tmp_path, owner)).written
    findings = [finding for finding in lane_report(tmp_path).findings if finding.code.startswith("decisions.")]
    assert findings == []


def test_amend_preserves_unknown_labelled_notes_and_history(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.write_text(MULTIPARAGRAPH + "\n**Notes:** authored note\n\nsecond paragraph\n", encoding="utf-8")
    assert apply_plan(_amend(ledger), lock=_lock(tmp_path)).written
    plan = _amend(ledger, answer="another route", note="adjusted")
    assert plan.primary is not None
    assert "**Notes:** authored note\n\nsecond paragraph" in plan.primary.prose
    assert plan.primary.prose.count("**Amended:**") == 2
