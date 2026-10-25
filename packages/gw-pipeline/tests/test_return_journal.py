"""The pipeline's durable return fence is independently readable and fail-closed."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from gw_kernel.workspace.layout import layout_for
from gw_pipeline.execute_return import (
    LocalPublication,
    ReturnOperation,
    digest,
    finish_return,
    journal_path,
    pending_return,
    return_dispatch_admission,
    write_operation,
)
from work_tracker_okf.returns import ExecuteReturn, ScopeRow

PATH = "work/feature-return"
COVERAGE = "work/feature-return/references/03-execute-coverage.md"
PLAN = "work/feature-return/references/02-plan.md"
SHA = "a" * 64


def operation(kind: str = "same-root-return") -> ReturnOperation:
    return ReturnOperation(
        PATH,
        return_id="ret-20261011-12345678",
        record=ExecuteReturn(
            "ret-20261011-12345678", "2026-10-11", "active", "/" + COVERAGE, (ScopeRow("R1", "Repair task"),)
        ),
        plan_member=PLAN,
        local=LocalPublication(kind, "c" * 40, "Publish return", ((PATH + ".md", SHA), (COVERAGE, SHA))),
    )


def test_return_intent_roundtrips_and_clears_only_matching_journal(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    op = operation()
    assert pending_return(layout, PATH) is None
    with return_dispatch_admission(layout, PATH):
        write_operation(layout, op)
        assert pending_return(layout, PATH) == op
        with pytest.raises(ValueError, match="journal changed"):
            finish_return(layout, replace(op, plan_sha256=SHA))
        assert pending_return(layout, PATH) == op
        finish_return(layout, op)
    assert pending_return(layout, PATH) is None
    assert not list(journal_path(layout, PATH).parent.glob(".return-*"))
    assert digest(None) is None


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
def test_local_publication_preserves_exact_members(tmp_path: Path, kind: str) -> None:
    layout = layout_for(tmp_path)
    op = operation(kind)
    write_operation(layout, op)
    loaded = pending_return(layout, PATH)
    assert loaded is not None and not loaded.malformed
    assert loaded == op
    assert "worktree" not in json.loads(journal_path(layout, PATH).read_text(encoding="utf-8"))
    finish_return(layout, loaded)
    assert pending_return(layout, PATH) is None


def test_journal_with_primary_worktree_keys_still_reads(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    write_operation(layout, operation())
    path = journal_path(layout, PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(worktree=str(layout.root.resolve()), branch="main", identity=str(tmp_path / ".git"))
    path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")
    assert pending_return(layout, PATH) == operation()


@pytest.mark.parametrize("legacy", ["v1", "v2"])
def test_journal_naming_another_worktree_is_retired_and_fenced(tmp_path: Path, legacy: str) -> None:
    layout = layout_for(tmp_path)
    write_operation(layout, operation())
    path = journal_path(layout, PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if legacy == "v1":
        raw = {"version": 1, "path": PATH, "destination": {"worktree": "/elsewhere", "branch": "b", "identity": "/g"}}
    else:
        raw["worktree"] = "/elsewhere"
    path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")
    fence = pending_return(layout, PATH)
    assert fence is not None and fence.malformed and fence.retired_worktree == "/elsewhere"


@pytest.mark.parametrize(
    "change",
    [
        {"plan_member": "../outside.md"},
        {"plan_member": "windows\\path.md"},
        {"plan_sha256": "invalid"},
        {"record": None},
    ],
)
def test_inconsistent_return_intent_remains_an_admission_fence(tmp_path: Path, change: dict[str, object]) -> None:
    layout = layout_for(tmp_path)
    op = replace(operation(), **change)
    write_operation(layout, op)
    fence = pending_return(layout, PATH)
    assert fence is not None and fence.malformed
    before = journal_path(layout, PATH).read_bytes()
    with pytest.raises(ValueError, match="journal changed"):
        finish_return(layout, op)
    assert journal_path(layout, PATH).read_bytes() == before


@pytest.mark.parametrize(
    "members",
    [
        ((COVERAGE, SHA),),
        ((PATH + ".md", SHA),),
        ((PATH + ".md", SHA), (PATH + ".md", SHA), (COVERAGE, SHA)),
        ((PATH + ".md", SHA), ("../outside.md", SHA)),
    ],
)
def test_local_publication_requires_unique_owned_members(tmp_path: Path, members: tuple[tuple[str, str], ...]) -> None:
    layout = layout_for(tmp_path)
    op = replace(operation(), local=LocalPublication("same-root-return", "c" * 40, "Return", members))
    write_operation(layout, op)
    fence = pending_return(layout, PATH)
    assert fence is not None and fence.malformed


def test_unreadable_or_foreign_journal_is_preserved_for_inspection(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    path = journal_path(layout, PATH)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8", newline="\n")
    assert pending_return(layout, PATH).malformed
    path.unlink()
    path.mkdir()
    assert pending_return(layout, PATH).malformed
    path.rmdir()
    write_operation(layout, replace(operation(), path="work/other/feature-return"))
    assert pending_return(layout, PATH).malformed
    assert path.exists()


def test_only_local_publications_are_journaled() -> None:
    with pytest.raises(ValueError, match="only a local publication"):
        replace(operation(), local=None).to_data()


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"version": 3}',
        '{"version": 1, "destination": {"worktree": 5}}',
        '{"version": 2, "worktree": "", "x": 1}',
    ],
)
def test_unrecognised_journal_shapes_are_fences(tmp_path: Path, raw: str) -> None:
    layout = layout_for(tmp_path)
    path = journal_path(layout, PATH)
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8", newline="\n")
    fence = pending_return(layout, PATH)
    assert fence is not None and fence.malformed
