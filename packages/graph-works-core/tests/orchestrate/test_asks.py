"""`run_ask` / `run_ask_answer` against a real initialized workspace."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import asks as core_asks
from work_tracker_okf import asks as pure_asks

TODAY = date(2026, 9, 27)
CREATED = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 27, 11, 0, 0, tzinfo=UTC)
ITEM = "work/feature-a"
CHOICES = (("merge", "Merge it"), ("hold", "Hold"))


def _layout(tmp_path: Path, *, phase: str = "plan", effort: str | None = "medium"):
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Asks")).layout
    page = layout.bundle_dir / f"{ITEM}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    effort_line = f"effort: {effort}\n" if effort else ""
    page.write_text(
        f"---\ntype: Feature\ntitle: A\ndescription: d\nstatus: stable\nwork_status: open\nphase: {phase}\n"
        f"{effort_line}opened: 2026-09-01\nupdated: 2026-09-01\naffects: []\n---\n\n## Summary\nd\n",
        encoding="utf-8",
        newline="",
    )
    spec = layout.bundle_dir / ITEM / "references" / "01-design.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# Design\n", encoding="utf-8", newline="")
    return layout


def _ask(layout, **overrides: object) -> core_asks.AskResult:
    kwargs: dict[str, object] = {
        "kind": "choice",
        "summary": "Finish: pick one.",
        "question": "## Detail\r\n\r\nFull text ü\n",
        "spec": None,
        "options": CHOICES,
        "created": CREATED,
        "dry_run": False,
    }
    kwargs.update(overrides)
    return core_asks.run_ask(layout, ITEM, **kwargs)  # type: ignore[arg-type]


def test_both_commands_default_to_dry_run() -> None:
    for fn in (core_asks.run_ask, core_asks.run_ask_answer):
        assert inspect.signature(fn).parameters["dry_run"].default is True


def test_ask_writes_the_payload_verbatim_and_renders_the_orca_strings(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    result = _ask(layout)
    assert result.ok and result.applied
    assert result.resource == f"/{ITEM}/references/asks/plan-001-choice.json"
    assert result.payload_path is not None and result.payload_path.is_file()
    data = json.loads(result.payload_path.read_bytes().decode("utf-8"))
    assert data["question"] == "## Detail\r\n\r\nFull text ü\n"
    assert data["current_effort"] == "medium"
    assert data["created"] == "2026-09-27T10:00:00Z"
    assert result.orca_question == f"Finish: pick one.\n\ngw-ask: {result.resource}"
    assert result.orca_options == "merge,hold"


def test_dry_run_names_the_file_and_writes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    result = _ask(layout, dry_run=True)
    assert result.ok and not result.applied
    assert result.payload_path is not None and not result.payload_path.exists()
    assert not (layout.bundle_dir / ITEM / "references" / "asks").exists()


def test_a_second_ask_takes_the_next_number_across_kinds(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert _ask(layout).resource.endswith("/plan-001-choice.json")  # type: ignore[union-attr]
    second = _ask(layout, kind="free", options=())
    assert second.resource is not None and second.resource.endswith("/plan-002-free.json")
    assert second.orca_options is None


def test_a_null_phase_is_named_none(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    page = layout.bundle_dir / f"{ITEM}.md"
    page.write_text(page.read_text(encoding="utf-8").replace("phase: plan\n", ""), encoding="utf-8", newline="")
    assert _ask(layout).resource.endswith("/none-001-choice.json")  # type: ignore[union-attr]


def test_spec_review_resolves_a_resource_or_a_filesystem_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    resource = f"/{ITEM}/references/01-design.md"
    for spec in (resource, str(layout.bundle_dir / ITEM / "references" / "01-design.md")):
        result = _ask(layout, kind="spec-review", options=(), spec=spec)
        assert result.ok, result.refusals
        assert result.payload_path is not None
        assert json.loads(result.payload_path.read_bytes())["spec"] == resource
        assert result.orca_options == "approve,changes"


@pytest.mark.parametrize("spec", [None, "/work/nope.md", "/etc/hosts"])
def test_spec_review_refuses_a_spec_that_does_not_resolve_beneath_the_bundle(tmp_path: Path, spec: str | None) -> None:
    layout = _layout(tmp_path)
    result = _ask(layout, kind="spec-review", options=(), spec=spec)
    assert result.refusals == ("spec-missing",)
    assert not (layout.bundle_dir / ITEM / "references" / "asks").exists()


def test_refusals_write_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    result = _ask(layout, options=(("a,b", "x"), ("c", "y")))
    assert result.refusals == ("option-token-invalid",)
    assert result.payload_path is None and not result.applied
    assert not (layout.bundle_dir / ITEM / "references" / "asks").exists()


def test_an_unknown_item_refuses(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    result = core_asks.run_ask(
        layout, "work/nope", kind="free", summary="s", question="q", spec=None, options=(), created=CREATED
    )
    assert result.refusals == ("unknown-item",)


def _answer(layout, payload: str, **overrides: object) -> core_asks.AskAnswerResult:
    kwargs: dict[str, object] = {
        "choice": "merge",
        "effort": None,
        "notes": None,
        "at": LATER,
        "by": "human",
        "dry_run": False,
    }
    kwargs.update(overrides)
    return core_asks.run_ask_answer(layout, payload, **kwargs)  # type: ignore[arg-type]


def test_answer_by_resource_records_it_and_returns_the_reply_body(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None and asked.payload_path is not None
    result = _answer(layout, asked.resource, by="policy:auto-merge")
    assert result.ok and result.changed and result.applied
    assert json.loads(result.reply_body or "") == {
        "ask": asked.resource,
        "choice": "merge",
        "effort": None,
        "notes": None,
    }
    stored = json.loads(asked.payload_path.read_bytes())["answer"]
    assert stored == {
        "choice": "merge",
        "effort": None,
        "notes": None,
        "at": "2026-09-27T11:00:00Z",
        "by": "policy:auto-merge",
    }
    assert pure_asks.parse_payload(asked.payload_path.read_text(encoding="utf-8")) is not None


def test_answer_by_filesystem_path_works_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.payload_path is not None
    before = asked.payload_path.read_bytes()
    result = _answer(layout, str(asked.payload_path), dry_run=True)
    assert result.ok and result.changed and not result.applied
    assert asked.payload_path.read_bytes() == before


@pytest.mark.parametrize("dry_run", [True, False])
def test_answer_by_relative_filesystem_path_from_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.payload_path is not None and asked.resource is not None
    monkeypatch.chdir(layout.root)
    relative = asked.payload_path.relative_to(layout.root).as_posix()

    result = _answer(layout, relative, dry_run=dry_run)

    assert result.ok and result.resource == asked.resource
    assert result.payload_path == asked.payload_path
    assert result.changed and result.applied is not dry_run
    stored_answer = json.loads(asked.payload_path.read_bytes())["answer"]
    assert (stored_answer is None) is dry_run


def test_an_identical_replay_is_a_no_op_and_a_different_answer_refuses(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None and asked.payload_path is not None
    first = _answer(layout, asked.resource, notes="ok")
    settled = asked.payload_path.read_bytes()
    replay = _answer(layout, asked.resource, notes="ok", at=datetime(2026, 9, 28, tzinfo=UTC))
    assert replay.ok and not replay.changed and not replay.applied
    assert replay.reply_body == first.reply_body
    assert pure_asks.parse_payload(asked.payload_path.read_text(encoding="utf-8")) is not None
    other = _answer(layout, asked.resource, choice="hold")
    assert other.refusals == ("already-answered",)
    assert asked.payload_path.read_bytes() == settled


def test_answer_validation_refusals_write_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None and asked.payload_path is not None
    before = asked.payload_path.read_bytes()
    result = _answer(layout, asked.resource, choice="pr")
    assert result.refusals == ("answer-choice-invalid",)
    assert asked.payload_path.read_bytes() == before


@pytest.mark.parametrize("by", ["", "   "])
@pytest.mark.parametrize("dry_run", [True, False])
def test_blank_answerer_refuses_before_preview_or_write(tmp_path: Path, by: str, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None and asked.payload_path is not None
    before = asked.payload_path.read_bytes()
    result = _answer(layout, asked.resource, by=by, dry_run=dry_run)
    assert result.refusals == ("answer-by-invalid",)
    assert not result.changed and not result.applied
    assert asked.payload_path.read_bytes() == before


@pytest.mark.parametrize("by", ["", "   "])
def test_blank_answerer_refuses_even_on_identical_replay(tmp_path: Path, by: str) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None and asked.payload_path is not None
    first = _answer(layout, asked.resource, by="policy:auto-merge")
    assert first.ok
    before = asked.payload_path.read_bytes()
    result = _answer(layout, asked.resource, by=by)
    assert result.refusals == ("answer-by-invalid",)
    assert asked.payload_path.read_bytes() == before


@pytest.mark.parametrize("case", ["missing", "outside", "not-json-suffix", "corrupt", "wrong-schema"])
def test_payload_invalid_covers_every_unusable_argument(tmp_path: Path, case: str) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)  # a real, valid payload: plan-001-choice.json
    assert asked.payload_path is not None
    asks_dir = asked.payload_path.parent
    outside = tmp_path / "outside.json"
    outside.write_bytes(asked.payload_path.read_bytes())  # valid content; refused only for its location
    (asks_dir / "plan-001-choice.md").write_bytes(b"{}")
    (asks_dir / "plan-002-choice.json").write_bytes(b"{not json")
    (asks_dir / "plan-003-choice.json").write_bytes(b'{"schema": "other"}')
    argument = {
        "missing": f"/{ITEM}/references/asks/plan-009-choice.json",
        "outside": str(outside),
        "not-json-suffix": f"/{ITEM}/references/asks/plan-001-choice.md",
        "corrupt": f"/{ITEM}/references/asks/plan-002-choice.json",
        "wrong-schema": f"/{ITEM}/references/asks/plan-003-choice.json",
    }[case]
    assert _answer(layout, argument).refusals == ("payload-invalid",)


def test_a_payload_whose_item_is_gone_refuses_unknown_item(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None
    (layout.bundle_dir / f"{ITEM}.md").unlink()
    assert _answer(layout, asked.resource).refusals == ("unknown-item",)


@pytest.mark.parametrize("dry_run", [True, False])
def test_ask_refuses_a_symlinked_asks_directory(tmp_path: Path, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    asks = layout.bundle_dir / ITEM / "references" / "asks"
    asks.symlink_to(outside, target_is_directory=True)
    result = _ask(layout, dry_run=dry_run)
    assert result.refusals == ("payload-invalid",)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_answer_refuses_a_symlinked_payload(tmp_path: Path, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.payload_path is not None and asked.resource is not None
    outside = tmp_path / "outside.json"
    outside.write_bytes(asked.payload_path.read_bytes())
    asked.payload_path.unlink()
    asked.payload_path.symlink_to(outside)
    result = _answer(layout, asked.resource, dry_run=dry_run)
    assert result.refusals == ("payload-invalid",)
    assert json.loads(outside.read_bytes())["answer"] is None


def test_answer_dry_run_and_replay_require_the_item(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.resource is not None
    _answer(layout, asked.resource)
    (layout.bundle_dir / f"{ITEM}.md").unlink()
    assert _answer(layout, asked.resource, dry_run=True).refusals == ("unknown-item",)
    assert _answer(layout, asked.resource).refusals == ("unknown-item",)


def test_payload_item_must_match_its_location(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    asked = _ask(layout)
    assert asked.payload_path is not None and asked.resource is not None
    data = json.loads(asked.payload_path.read_bytes())
    data["item"] = "work/other"
    asked.payload_path.write_text(json.dumps(data), encoding="utf-8", newline="")
    assert _answer(layout, asked.resource).refusals == ("payload-invalid",)
