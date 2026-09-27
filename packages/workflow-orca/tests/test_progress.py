"""The SDD ledger reader: which ledger, how far, and what it cannot say."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from workflow_orca._progress import (
    LAST_LINE_MAX,
    LEDGER_UNREADABLE,
    NO_LEDGER,
    PLAN_UNREADABLE,
    SddProgress,
    find_progress,
)

SINCE = datetime(2026, 9, 27, 12, tzinfo=UTC)
FRESH = SINCE + timedelta(minutes=5)
STALE = SINCE - timedelta(days=1)
PLAN = "# Plan\n### Task 1: First\n### Task 2: Second\n### Task 3: Third\n"


def _ledger(worktree: Path, name: str, text: str, when: datetime = FRESH) -> Path:
    path = worktree / ".superpowers" / "sdd" / name / "progress.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def _plan(worktree: Path, text: str = PLAN, rel: str = "docs/plan.md") -> Path:
    path = worktree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_no_ledger_or_stale_ledger(tmp_path: Path) -> None:
    assert find_progress(tmp_path, since=SINCE) == (None, (NO_LEDGER,))
    (tmp_path / ".superpowers" / "sdd" / "plan").mkdir(parents=True)
    assert find_progress(tmp_path, since=SINCE) == (None, (NO_LEDGER,))
    _ledger(tmp_path, "plan", "# SDD ledger — plan: docs/plan.md\n", STALE)
    assert find_progress(tmp_path, since=SINCE) == (None, (NO_LEDGER,))


def test_fresh_ledger_reads_plan_and_completion(tmp_path: Path) -> None:
    _plan(tmp_path)
    ledger = _ledger(tmp_path, "plan", "# SDD ledger — plan: docs/plan.md\n\nTask 1: complete (review clean)\n")
    assert find_progress(tmp_path, since=SINCE) == (
        SddProgress(str(ledger), "docs/plan.md", 1, 3, "Task 1: complete (review clean)"),
        (),
    )


def test_exact_since_is_fresh(tmp_path: Path) -> None:
    _ledger(tmp_path, "plan", "", SINCE)
    progress, _ = find_progress(tmp_path, since=SINCE)
    assert progress is not None


def test_newest_ledger_and_path_tiebreak(tmp_path: Path) -> None:
    _plan(tmp_path)
    _ledger(tmp_path, "a", "# SDD ledger — plan: docs/plan.md\nTask 1: complete\n")
    b = _ledger(tmp_path, "b", "# SDD ledger — plan: docs/plan.md\nTask 2: complete\n")
    assert find_progress(tmp_path, since=SINCE)[0] == SddProgress(str(b), "docs/plan.md", 1, 3, "Task 2: complete")
    newer = _ledger(
        tmp_path, "a", "# SDD ledger — plan: docs/plan.md\nTask 1: complete\n", FRESH + timedelta(minutes=1)
    )
    progress, _ = find_progress(tmp_path, since=SINCE)
    assert progress is not None and progress.ledger == str(newer)


def test_distinct_task_numbers_include_zero(tmp_path: Path) -> None:
    _plan(tmp_path, "### Task 0: Setup\n### Task 1: Work\n### Task 1: Repeated\n")
    _ledger(tmp_path, "plan", "# SDD ledger — plan: docs/plan.md\nTask 0: complete\n- Task 0: complete\n")
    progress, notes = find_progress(tmp_path, since=SINCE)
    assert notes == ()
    assert progress is not None
    assert (progress.completed, progress.total) == (1, 2)


def test_missing_or_empty_plan_keeps_ledger_facts(tmp_path: Path) -> None:
    _ledger(tmp_path, "plan", "# SDD ledger — plan: docs/gone.md\nTask 1: complete\n")
    progress, notes = find_progress(tmp_path, since=SINCE)
    assert notes == (PLAN_UNREADABLE,)
    assert progress is not None
    assert (progress.plan, progress.completed, progress.total) == ("docs/gone.md", 1, None)
    _plan(tmp_path, "# No tasks\n", "docs/gone.md")
    assert find_progress(tmp_path, since=SINCE)[1] == (PLAN_UNREADABLE,)


def test_malformed_plan_path_keeps_ledger_facts(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        "plan",
        "# SDD ledger — plan: docs/\x00plan.md\nTask 1: complete\nTask 2: fix round 1\n",
    )
    assert find_progress(tmp_path, since=SINCE) == (
        SddProgress(str(ledger), "docs/\x00plan.md", 1, None, "Task 2: fix round 1"),
        (PLAN_UNREADABLE,),
    )


@pytest.mark.parametrize("first_line", ["not a header", "", "  "])
def test_actual_first_line_must_be_header(tmp_path: Path, first_line: str) -> None:
    _ledger(tmp_path, "plan", first_line + "\n# SDD ledger — plan: docs/plan.md\nTask 1: complete\n")
    progress, notes = find_progress(tmp_path, since=SINCE)
    assert notes == (PLAN_UNREADABLE,)
    assert progress is not None
    assert (progress.plan, progress.total, progress.completed) == (None, None, 1)


def test_empty_ledger(tmp_path: Path) -> None:
    _ledger(tmp_path, "plan", "")
    progress, notes = find_progress(tmp_path, since=SINCE)
    assert notes == (PLAN_UNREADABLE,)
    assert progress is not None
    assert (progress.plan, progress.completed, progress.last_line) == (None, 0, "")


def test_mid_fix_last_line_and_truncation(tmp_path: Path) -> None:
    _plan(tmp_path)
    _ledger(tmp_path, "plan", "# SDD ledger — plan: docs/plan.md\nTask 1: complete\nTask 2: fix round 1\n\n")
    progress, _ = find_progress(tmp_path, since=SINCE)
    assert progress is not None
    assert (progress.completed, progress.last_line) == (1, "Task 2: fix round 1")
    _ledger(tmp_path, "plan", "# SDD ledger — plan: docs/plan.md\n" + "x" * 500 + "\n")
    progress, _ = find_progress(tmp_path, since=SINCE)
    assert progress is not None and progress.last_line == "x" * LAST_LINE_MAX


def test_non_utf8_ledger_is_unreadable(tmp_path: Path) -> None:
    path = _ledger(tmp_path, "plan", "")
    path.write_bytes(b"\xff\xfe")
    assert find_progress(tmp_path, since=SINCE) == (None, (LEDGER_UNREADABLE,))


def test_absolute_plan_path(tmp_path: Path) -> None:
    vault_plan = _plan(tmp_path / "vault", rel="02-plan.md")
    _ledger(tmp_path / "wt", "plan", f"# SDD ledger — plan: {vault_plan}\nTask 3: complete\n")
    progress, notes = find_progress(tmp_path / "wt", since=SINCE)
    assert notes == ()
    assert progress is not None and (progress.plan, progress.total) == (str(vault_plan), 3)


@pytest.mark.parametrize("dash", ["—", "-", "--"])
def test_header_dash_variants(tmp_path: Path, dash: str) -> None:
    _plan(tmp_path)
    _ledger(tmp_path, "plan", f"# SDD ledger {dash} plan: docs/plan.md\n")
    progress, _ = find_progress(tmp_path, since=SINCE)
    assert progress is not None and progress.plan == "docs/plan.md"


def test_discovery_failure_degrades_to_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_glob(self: Path, pattern: str):  # type: ignore[no-untyped-def]
        raise OSError("denied")

    monkeypatch.setattr(Path, "glob", fail_glob)
    assert find_progress(tmp_path, since=SINCE) == (None, (LEDGER_UNREADABLE,))


def test_stat_failure_degrades_to_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _ledger(tmp_path, "plan", "")
    original_stat = Path.stat

    def fail_stat(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self == ledger:
            raise OSError("denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    assert find_progress(tmp_path, since=SINCE) == (None, (LEDGER_UNREADABLE,))
