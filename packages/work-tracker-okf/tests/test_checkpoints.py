from __future__ import annotations

import ast
from pathlib import Path

import pytest
from work_tracker_okf import checkpoints

ITEM = "work/epic-a/children/feature-b"

VALID = f"""\
---
title: 'Checkpoint: Feature B (execute)'
item: {ITEM}
decision: pending
phase: execute
dispatch_key: gw-execute-b-1234abcd
branch: feature/b-1234abcd
worktree: /tmp/wt
base: main
head: 0123456789abcdef0123456789abcdef01234567
created: 2026-09-13T10:00:00Z
---

## Completed work

Tasks 1-3 landed.

## Remaining actions

Tasks 4-6.

## Question

Which lock order?

- option A
- option B

## Placement

worktree /tmp/wt, branch feature/b-1234abcd, base main, head 0123456, clean

## Validation evidence

`just check` green at head.

## Notes

extra sections are preserved
"""


def test_template_parses_clean() -> None:
    parsed = checkpoints.parse(checkpoints.template())
    assert parsed.problems == ()
    assert [heading for heading, _ in parsed.sections] == list(checkpoints.REQUIRED_SECTIONS)


def test_template_created_placeholder_must_be_filled_before_use() -> None:
    parsed = checkpoints.parse(checkpoints.template())
    problems = checkpoints.validate(
        parsed, item_path=parsed.frontmatter["item"], phase=parsed.frontmatter["phase"], decision_id="pending"
    )
    assert any("created" in problem and "ISO-8601 instant" in problem for problem in problems)


def test_valid_draft_stamps_and_validates() -> None:
    stamped = checkpoints.stamp(VALID, "D-004")
    assert stamped == VALID.replace("decision: pending", "decision: D-004")
    parsed = checkpoints.parse(stamped)
    assert parsed.problems == ()
    assert parsed.frontmatter["created"].startswith("2026-09-13")
    assert checkpoints.validate(parsed, item_path=ITEM, phase="execute", decision_id="D-004") == ()


def test_stamp_leaves_an_explicit_id_alone() -> None:
    draft = VALID.replace("decision: pending", "decision: D-009")
    assert checkpoints.stamp(draft, "D-004") == draft


def test_stamp_leaves_unparseable_frontmatter_alone() -> None:
    malformed = "---\ndecision: [unclosed\n---\n"
    assert checkpoints.stamp(malformed, "D-004") == malformed


@pytest.mark.parametrize("key", checkpoints.REQUIRED_KEYS)
def test_each_missing_frontmatter_key_is_a_problem(key: str) -> None:
    lines = [line for line in VALID.splitlines(keepends=True) if not line.startswith(f"{key}:")]
    parsed = checkpoints.parse("".join(lines))
    assert any(key in problem for problem in parsed.problems), parsed.problems


@pytest.mark.parametrize("section", checkpoints.REQUIRED_SECTIONS)
def test_each_missing_section_is_a_problem(section: str) -> None:
    parsed = checkpoints.parse(VALID.replace(f"## {section}\n", "## Renamed\n"))
    assert any(section in problem for problem in parsed.problems), parsed.problems


def test_an_empty_section_is_a_problem() -> None:
    parsed = checkpoints.parse(VALID.replace("Tasks 4-6.\n", ""))
    assert any("Remaining actions" in problem and "empty" in problem for problem in parsed.problems)


def test_sections_out_of_order_are_a_problem() -> None:
    swapped = VALID.replace("## Completed work", "## TMP").replace("## Remaining actions", "## Completed work")
    parsed = checkpoints.parse(swapped.replace("## TMP", "## Remaining actions"))
    assert any("order" in problem for problem in parsed.problems)


def test_prose_before_the_first_section_is_ignored() -> None:
    parsed = checkpoints.parse(VALID.replace("---\n\n## Completed work", "---\n\nDraft context.\n\n## Completed work"))
    assert parsed.problems == ()


def test_unparseable_frontmatter_is_a_problem_not_an_exception() -> None:
    parsed = checkpoints.parse("---\nitem: [unclosed\n---\n")
    assert parsed.problems


def test_validate_reports_identity_mismatches() -> None:
    parsed = checkpoints.parse(checkpoints.stamp(VALID, "D-009"))
    problems = checkpoints.validate(parsed, item_path="work/other", phase="plan", decision_id="D-004")
    assert any("item" in problem for problem in problems)
    assert any("phase" in problem for problem in problems)
    assert any("decision" in problem for problem in problems)


def test_validate_rejects_a_pending_id() -> None:
    parsed = checkpoints.parse(VALID)
    assert any(
        "decision" in problem
        for problem in checkpoints.validate(parsed, item_path=ITEM, phase="execute", decision_id="D-1")
    )


@pytest.mark.parametrize(
    "created",
    [
        "not-an-instant",
        "2026-09-13",
        "2026-09-13T10:00:00",
        "2026-09-13X10:00:00Z",
        "[]",
        "true",
        "null",
    ],
)
def test_created_must_be_an_iso_8601_instant(created: str) -> None:
    parsed = checkpoints.parse(VALID.replace("2026-09-13T10:00:00Z", created))
    assert any("created" in problem and "ISO-8601 instant" in problem for problem in parsed.problems)


def test_checkpoints_imports_only_its_declared_dependencies() -> None:
    source = Path(checkpoints.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    package = {name for name in imported if name.startswith("work_tracker_okf")}
    assert package <= {"work_tracker_okf.paths", "work_tracker_okf.vocabulary"}
    assert not {name for name in imported if name.split(".")[0] in {"graph_works_core", "subprocess"}}
