"""The pure `gw.ask/1` model: build, name, and render."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from work_tracker_okf import asks

CREATED = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
ITEM = "work/feature-a"


def _build(**overrides: object) -> asks.AskBuild:
    kwargs: dict[str, object] = {
        "item": ITEM,
        "phase": "plan",
        "kind": "choice",
        "created": CREATED,
        "summary": "Pick one.",
        "question": "## Which?\n\nLong text.\n",
        "spec": None,
        "current_effort": "medium",
        "options": (asks.AskOption("merge", "Merge"), asks.AskOption("hold", "Hold")),
    }
    kwargs.update(overrides)
    return asks.build_ask(**kwargs)  # type: ignore[arg-type]


def test_a_valid_choice_builds_a_payload_with_the_question_verbatim() -> None:
    built = _build(question="line one\r\nline two ü\n")
    assert built.refusals == ()
    assert built.payload is not None
    assert built.payload.question == "line one\r\nline two ü\n"
    assert built.payload.kind == "choice"
    assert built.payload.phase == "plan"
    assert built.payload.created == "2026-09-27T10:00:00Z"
    assert built.payload.answer is None


def test_to_json_is_schema_tagged_ordered_and_newline_terminated() -> None:
    payload = _build().payload
    assert payload is not None
    text = payload.to_json()
    assert text.endswith("}\n")
    data = json.loads(text)
    assert list(data) == [
        "schema",
        "item",
        "phase",
        "kind",
        "created",
        "summary",
        "question",
        "spec",
        "current_effort",
        "options",
        "answer",
    ]
    assert data["schema"] == "gw.ask/1"
    assert data["options"] == [{"token": "merge", "label": "Merge"}, {"token": "hold", "label": "Hold"}]
    assert data["answer"] is None
    assert "ü" in _build(question="ü").payload.to_json()  # type: ignore[union-attr]


def test_a_null_phase_is_named_none() -> None:
    payload = _build(phase=None).payload
    assert payload is not None and payload.phase == "none"


def test_iso_instant_normalizes_any_offset_to_utc_z() -> None:
    plus_two = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert asks.iso_instant(plus_two) == "2026-09-27T10:00:00Z"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"summary": ""}, "summary-invalid"),
        ({"summary": "   "}, "summary-invalid"),
        ({"summary": "two\nlines"}, "summary-invalid"),
        ({"summary": "two\rlines"}, "summary-invalid"),
        ({"summary": "x" * 301}, "summary-invalid"),
        ({"question": ""}, "question-empty"),
        ({"question": " \n "}, "question-empty"),
        ({"options": (asks.AskOption("a,b", "A"), asks.AskOption("c", "C"))}, "option-token-invalid"),
        ({"options": (asks.AskOption("a b", "A"), asks.AskOption("c", "C"))}, "option-token-invalid"),
        ({"options": (asks.AskOption("Merge", "A"), asks.AskOption("c", "C"))}, "option-token-invalid"),
        ({"options": (asks.AskOption("1st", "A"), asks.AskOption("c", "C"))}, "option-token-invalid"),
        ({"options": (asks.AskOption("a" * 33, "A"), asks.AskOption("c", "C"))}, "option-token-invalid"),
        ({"options": (asks.AskOption("a", "A"), asks.AskOption("a", "B"))}, "option-duplicate"),
        ({"options": (asks.AskOption("a", "A"),)}, "option-count"),
        ({"options": tuple(asks.AskOption(f"o{i}", "x") for i in range(9))}, "option-count"),
        ({"kind": "free"}, "option-count"),
        ({"kind": "spec-review", "spec": "/work/a/references/01-design.md"}, "options-fixed"),
        ({"kind": "spec-review", "options": ()}, "spec-missing"),
        ({"kind": "bogus"}, "kind-invalid"),
    ],
)
def test_every_content_refusal_is_returned_not_raised(overrides: dict[str, object], code: str) -> None:
    built = _build(**overrides)
    assert built.payload is None
    assert code in built.refusals
    assert set(built.refusals) <= asks.ASK_REFUSALS


def test_a_300_character_summary_is_accepted() -> None:
    assert _build(summary="x" * 300).refusals == ()


def test_refusals_accumulate_in_one_pass_without_duplicates() -> None:
    built = _build(summary="", question="", options=(asks.AskOption("a", "A"), asks.AskOption("a", "A")))
    assert built.refusals == ("summary-invalid", "question-empty", "option-duplicate")
    lone_dup = _build(kind="free", options=(asks.AskOption("a", "A"), asks.AskOption("a", "A")))
    assert lone_dup.refusals == ("option-duplicate", "option-count")


def test_spec_review_carries_its_fixed_options_and_the_spec() -> None:
    built = _build(kind="spec-review", options=(), spec="/work/a/references/01-design.md")
    assert built.payload is not None
    assert built.payload.options == asks.SPEC_REVIEW_OPTIONS
    assert [o.token for o in asks.SPEC_REVIEW_OPTIONS] == ["approve", "changes"]
    assert built.payload.spec == "/work/a/references/01-design.md"


def test_a_spec_on_a_non_spec_review_ask_is_dropped() -> None:
    built = _build(spec="/work/a/references/01-design.md")
    assert built.payload is not None and built.payload.spec is None


def test_free_takes_no_options_and_renders_null_options() -> None:
    built = _build(kind="free", options=())
    assert built.payload is not None
    assert asks.orca_options(built.payload) is None


def test_orca_strings_carry_summary_marker_and_comma_joined_tokens() -> None:
    payload = _build().payload
    assert payload is not None
    resource = "/work/feature-a/references/asks/plan-001-choice.json"
    assert asks.orca_question(payload, resource) == f"Pick one.\n\ngw-ask: {resource}"
    assert asks.orca_question(payload, resource).splitlines()[-1].startswith(asks.ASK_MARKER)
    assert asks.orca_options(payload) == "merge,hold"


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ((), "plan-001-choice.json"),
        (("design-001-free.json", "design-002-spec-review.json"), "plan-003-choice.json"),
        (("execute-009-choice.json", "notes.md", "plan-abc-choice.json", ".DS_Store"), "plan-010-choice.json"),
        (("finish-041-free.json",), "plan-042-choice.json"),
    ],
)
def test_numbering_is_global_across_the_asks_directory(existing: tuple[str, ...], expected: str) -> None:
    assert asks.next_ask_name(existing, phase="plan", kind="choice") == expected


def _payload(kind: str = "choice", *, current_effort: str | None = "medium") -> asks.AskPayload:
    options: tuple[asks.AskOption, ...] = ()
    if kind == "choice":
        options = (asks.AskOption("merge", "Merge"), asks.AskOption("hold", "Hold"))
    built = _build(
        kind=kind,
        options=options,
        spec="/work/a/references/01-design.md" if kind == "spec-review" else None,
        current_effort=current_effort,
    )
    assert built.payload is not None
    return built.payload


def test_parse_round_trips_an_unanswered_and_an_answered_payload() -> None:
    payload = _payload()
    assert asks.parse_payload(payload.to_json()) == payload
    done = asks.answered(payload, choice="merge", effort=None, notes="ok", at=CREATED, by="human")
    assert asks.parse_payload(done.to_json()) == done


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"schema": "gw.ask/2"}',
        json.dumps({"schema": "gw.ask/1"}),
        _payload().to_json().replace('"choice",', '"bogus",', 1),
        _payload().to_json().replace('"current_effort": "medium"', '"current_effort": 3'),
        _payload().to_json().replace('"token": "merge"', '"token": 1'),
    ],
)
def test_parse_refuses_anything_that_is_not_a_well_formed_gw_ask_1(text: str) -> None:
    assert asks.parse_payload(text) is None


def test_parse_refuses_excessively_nested_json_without_raising() -> None:
    text = "[" * 10_000 + "]" * 10_000
    assert asks.parse_payload(text) is None


@pytest.mark.parametrize(
    ("updates", "kind"),
    [
        ({"item": 3}, "choice"),
        ({"phase": 3}, "choice"),
        ({"phase": "bogus"}, "choice"),
        ({"created": "yesterday"}, "choice"),
        ({"summary": ""}, "choice"),
        ({"question": "  "}, "choice"),
        ({"options": {}}, "choice"),
        ({"options": [None, {}]}, "choice"),
        ({"options": [{"token": "a", "label": "A"}]}, "choice"),
        ({"options": [{"token": "a,b", "label": "A"}, {"token": "c", "label": "C"}]}, "choice"),
        ({"spec": "/work/a.md"}, "choice"),
        ({"spec": None}, "spec-review"),
        ({"spec": "relative/design.md"}, "spec-review"),
        ({"options": []}, "spec-review"),
        ({"options": [{"token": "x", "label": "X"}]}, "free"),
        ({"current_effort": "huge"}, "choice"),
        ({"answer": []}, "choice"),
        ({"answer": {"choice": "merge", "effort": None, "notes": None, "at": "bad", "by": "human"}}, "choice"),
        (
            {"answer": {"choice": "bad", "effort": None, "notes": None, "at": "2026-09-27T10:00:00Z", "by": "human"}},
            "choice",
        ),
    ],
)
def test_parse_refuses_payloads_that_break_the_documented_contract(updates: dict[str, object], kind: str) -> None:
    data = _payload(kind).to_data()
    data.update(updates)
    assert asks.parse_payload(json.dumps(data)) is None


@pytest.mark.parametrize(
    ("kind", "current_effort", "answer", "expected"),
    [
        ("spec-review", "medium", {"choice": "approve"}, ()),
        ("spec-review", "medium", {"choice": "approve", "effort": "large"}, ()),
        ("spec-review", None, {"choice": "approve"}, ("answer-effort-required",)),
        ("spec-review", None, {"choice": "approve", "effort": "small"}, ()),
        ("spec-review", "medium", {"choice": "approve", "effort": "huge"}, ("answer-effort-invalid",)),
        ("spec-review", "medium", {"choice": "changes"}, ("answer-notes-required",)),
        ("spec-review", "medium", {"choice": "changes", "notes": "  "}, ("answer-notes-required",)),
        ("spec-review", "medium", {"choice": "changes", "notes": "fix §2"}, ()),
        ("spec-review", "medium", {"choice": "merge"}, ("answer-choice-invalid",)),
        ("choice", "medium", {"choice": "hold"}, ()),
        ("choice", "medium", {"choice": "hold", "notes": "later"}, ()),
        ("choice", "medium", {"choice": "pr"}, ("answer-choice-invalid",)),
        ("choice", "medium", {"choice": None}, ("answer-choice-invalid",)),
        ("choice", "medium", {"choice": "hold", "effort": "small"}, ("answer-effort-invalid",)),
        ("free", "medium", {"notes": "2026-10-01"}, ()),
        ("free", "medium", {}, ("answer-notes-required",)),
        ("free", "medium", {"choice": "x", "notes": "y"}, ("answer-choice-invalid",)),
    ],
)
def test_the_answer_table(
    kind: str, current_effort: str | None, answer: dict[str, str | None], expected: tuple[str, ...]
) -> None:
    payload = _payload(kind, current_effort=current_effort)
    refusals = asks.validate_answer(
        payload, choice=answer.get("choice"), effort=answer.get("effort"), notes=answer.get("notes")
    )
    assert refusals == expected
    assert set(refusals) <= asks.ASK_REFUSALS


def test_answered_records_the_answer_and_blank_notes_become_null() -> None:
    done = asks.answered(_payload(), choice="hold", effort=None, notes="", at=CREATED, by="policy:auto-merge")
    assert done.answer == asks.AskAnswer("hold", None, None, "2026-09-27T10:00:00Z", "policy:auto-merge")
    assert asks.parse_payload(done.to_json()) == done


@pytest.mark.parametrize("by", ["", "   "])
def test_answer_validation_refuses_blank_answerer_before_serialization(by: str) -> None:
    assert asks.validate_answer(_payload(), choice="hold", effort=None, notes=None, by=by) == ("answer-by-invalid",)


def test_same_answer_compares_choice_effort_and_normalized_notes_only() -> None:
    existing = asks.AskAnswer("hold", None, None, "2026-09-27T10:00:00Z", "human")
    assert asks.same_answer(existing, choice="hold", effort=None, notes="")
    assert asks.same_answer(existing, choice="hold", effort=None, notes=None)
    assert not asks.same_answer(existing, choice="merge", effort=None, notes=None)
    assert not asks.same_answer(existing, choice="hold", effort=None, notes="x")


def test_the_reply_body_is_one_line_of_json() -> None:
    answer = asks.AskAnswer("changes", None, "line one\nline two", "2026-09-27T10:00:00Z", "human")
    body = asks.reply_body("/work/a/references/asks/plan-001-spec-review.json", answer)
    assert "\n" not in body
    assert json.loads(body) == {
        "ask": "/work/a/references/asks/plan-001-spec-review.json",
        "choice": "changes",
        "effort": None,
        "notes": "line one\nline two",
    }


PLAN_HEAD = "# Plan\n\n## Global Constraints\n\n- one\n\n"


@pytest.mark.parametrize(
    ("text", "status", "items"),
    [
        (
            PLAN_HEAD + "## Human checkpoints\n\n- Task 3: the human skims the diff.\n"
            "- Task 5: manual smoke.\n\n## Task 1\n- not me\n",
            "declared",
            ("Task 3: the human skims the diff.", "Task 5: manual smoke."),
        ),
        (PLAN_HEAD + "## Human checkpoints\n\nNone.\n\n---\n\n### Task 1\n", "malformed", ()),
        (PLAN_HEAD + "## Human checkpoints\n\nNone.\n", "declared", ()),
        (
            PLAN_HEAD + "## Human checkpoints\n\n* star bullet\n  continued on an indented line\n",
            "declared",
            ("star bullet",),
        ),
        (PLAN_HEAD.replace("\n", "\r\n") + "## Human checkpoints\r\n\r\n- crlf item\r\n", "declared", ("crlf item",)),
        (PLAN_HEAD, "missing", ()),
        (PLAN_HEAD + "```markdown\n## Human checkpoints\n- fenced\n```\n", "missing", ()),
        (PLAN_HEAD + "````markdown\n```\n## Human checkpoints\n````\n", "missing", ()),
        (PLAN_HEAD + "~~~~\n~~~\n## Human checkpoints\n~~~~\n", "missing", ()),
        (PLAN_HEAD + "````\n~~~\n## Human checkpoints\n````\n", "missing", ()),
        (PLAN_HEAD + "~~~\n## Human checkpoints\n~~~\n## Human checkpoints\n\n- real\n", "declared", ("real",)),
        (PLAN_HEAD + "## Human checkpoints\n\n", "malformed", ()),
        (PLAN_HEAD + "## Human checkpoints\n\nMaybe a few.\n", "malformed", ()),
        (PLAN_HEAD + "## Human checkpoints\n\n### Sub\n\nNone.\n", "malformed", ()),
        (PLAN_HEAD + "## Human checkpoints\n\n```\n- fenced bullet\n```\n\nNone.\n", "declared", ()),
        (PLAN_HEAD + "## Human checkpoints (draft)\n\n- x\n", "missing", ()),
    ],
)
def test_plan_checkpoints(text: str, status: str, items: tuple[str, ...]) -> None:
    assert asks.plan_checkpoints(text) == asks.PlanCheckpoints(status, items)


def test_plan_checkpoints_fence_marker_with_trailing_text_does_not_close() -> None:
    text = "# Plan\n```python\n```still-code\n## Human checkpoints\n- hidden\n```\n## Human checkpoints\n- real\n"
    assert asks.plan_checkpoints(text) == asks.PlanCheckpoints("declared", ("real",))
