"""The pipeline's durable return fence is independently readable and fail-closed."""

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
    mark_publication,
    pending_return,
    return_dispatch_admission,
    write_operation,
)
from work_tracker_okf.returns import ExecuteReturn, ScopeRow

PATH = "work/feature-return"
COVERAGE = "work/feature-return/references/03-execute-coverage.md"
PLAN = "work/feature-return/references/02-plan.md"
SHA = "a" * 64


def operation(tmp_path: Path) -> ReturnOperation:
    return ReturnOperation(
        PATH,
        return_id="ret-20261011-12345678",
        fingerprint=SHA,
        page_sha256=SHA,
        worktree=str(tmp_path),
        branch="feature",
        identity=str(tmp_path / ".git"),
        coverage_member=COVERAGE,
        coverage_after_sha256=SHA,
        record=ExecuteReturn(
            "ret-20261011-12345678", "2026-10-11", "active", "/" + COVERAGE, (ScopeRow("R1", "Repair task"),)
        ),
        plan_member=PLAN,
        content_before_head="b" * 40,
    )


@pytest.mark.parametrize("committed", [False, True])
def test_return_intent_roundtrips_and_clears_only_matching_journal(tmp_path: Path, committed: bool) -> None:
    layout = layout_for(tmp_path)
    op = operation(tmp_path)
    if committed:
        op = replace(op, state="content-committed", content_commit="c" * 40)
    assert pending_return(layout, PATH) is None
    with return_dispatch_admission(layout, PATH):
        write_operation(layout, op)
        assert pending_return(layout, PATH) == op
        published = mark_publication(layout, op, b"canonical publication")
        assert published.canonical_after_sha256 == digest(b"canonical publication")
        assert pending_return(layout, PATH) == published
        with pytest.raises(ValueError, match="journal changed"):
            finish_return(layout, op)
        assert pending_return(layout, PATH) == published
        finish_return(layout, published)
    assert pending_return(layout, PATH) is None
    assert not list(journal_path(layout, PATH).parent.glob(".return-*"))
    assert digest(None) is None


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
def test_local_publication_preserves_exact_members_and_identity(tmp_path: Path, kind: str) -> None:
    layout = layout_for(tmp_path)
    op = operation(tmp_path)
    publication = LocalPublication(kind, "c" * 40, "Publish return", ((PATH + ".md", SHA), (COVERAGE, SHA)))
    op = replace(op, local=publication)
    write_operation(layout, op)
    loaded = pending_return(layout, PATH)
    assert loaded is not None and not loaded.malformed
    assert loaded.local == publication
    assert loaded.record == op.record
    assert (loaded.worktree, loaded.branch, loaded.identity) == (op.worktree, op.branch, op.identity)
    finish_return(layout, loaded)
    assert pending_return(layout, PATH) is None


@pytest.mark.parametrize(
    "change",
    [
        {"return_id": "ret-20261011-87654321"},
        {"worktree": "relative"},
        {"fingerprint": "invalid"},
        {"coverage_before_sha256": "invalid"},
        {"content_before_head": "invalid"},
        {"coverage_member": "../outside.md"},
        {"plan_member": "windows\\path.md"},
        {"state": "content-committed"},
        {"coverage_member": "other.md"},
        {"plan_sha256": SHA},
    ],
)
def test_inconsistent_return_intent_remains_an_admission_fence(tmp_path: Path, change: dict[str, object]) -> None:
    layout = layout_for(tmp_path)
    op = replace(operation(tmp_path), **change)
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
    op = replace(operation(tmp_path), local=LocalPublication("same-root-return", "c" * 40, "Return", members))
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
    write_operation(layout, replace(operation(tmp_path), path="work/other/feature-return"))
    assert pending_return(layout, PATH).malformed
    assert path.exists()
