"""graph_works_core.proposals: plan-by-default decide and file, no clock."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init, resolve
from graph_works_core.proposals import (
    ProposalRefusal,
    find_proposal,
    normalize_target,
    run_proposal_decide,
    run_proposal_file,
)
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import load, load_bundle

TODAY = date(2026, 8, 23)
AT = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
TARGET = "docs/explanations/typed-cli.md"
MEMBER = "proposals/docs-explanations-typed-cli.md"
SOURCE = {"id": "s1", "resource": "/sources/one.md"}


def _layout(root: Path) -> WorkspaceLayout:
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Proposals")).layout


def _file(layout: WorkspaceLayout, **overrides: object) -> None:
    kwargs: dict[str, object] = {
        "lane": "explanation",
        "title": "Typed CLI",
        "description": "d",
        "source": SOURCE,
        "by": "agent:test",
        "at": AT,
        "dry_run": False,
    }
    kwargs.update(overrides)
    run = run_proposal_file(layout, **kwargs)  # type: ignore[arg-type]
    assert run.ok and run.result is not None and run.result.ok


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    built = _layout(tmp_path / "a")
    _file(built)
    return built


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (r"\concepts\a.md", "concepts/a.md"),
        ("./concepts/a.md", "concepts/a.md"),
        ("dir/../concepts/a.md", "concepts/a.md"),
        ("/concepts/a.md", "concepts/a.md"),
        ("../escape.md", ""),
        ("   ", ""),
    ),
)
def test_normalize_target(raw: str, expected: str) -> None:
    assert normalize_target(raw) == expected


def test_find_proposal_matches_only_by_normalized_target(layout: WorkspaceLayout) -> None:
    bundle = load_bundle(layout.bundle_dir)
    found = find_proposal(bundle, "./" + TARGET)
    assert found is not None and found.member == MEMBER
    assert find_proposal(bundle, "docs/explanations/other.md") is None
    assert find_proposal(bundle, "") is None


def test_decide_plans_by_default_and_writes_nothing(layout: WorkspaceLayout) -> None:
    before = _snapshot(layout.bundle_dir)

    run = run_proposal_decide(layout, TARGET, "approved", by="human", at=AT)

    assert run.ok and run.result is None and run.plan is not None
    assert run.proposal == MEMBER and run.target == TARGET
    assert _snapshot(layout.bundle_dir) == before


def test_decide_apply_flips_status_and_stamps_verified_with_exactly_at(layout: WorkspaceLayout) -> None:
    run = run_proposal_decide(layout, TARGET, "rejected", by="human", at=AT, dry_run=False)

    assert run.ok and run.result is not None and run.result.written == (MEMBER,)
    document = load(layout.bundle_dir / MEMBER)
    assert document.fm_raw["page_status"] == "rejected"
    assert document.fm_raw["verified"][-1]["by"] == "human"
    assert document.fm_raw["verified"][-1]["at"] == AT.isoformat()


def test_two_dry_runs_over_two_copies_are_identical(tmp_path: Path, layout: WorkspaceLayout) -> None:
    copy_root = tmp_path / "copy"
    shutil.copytree(layout.root.parent.parent, copy_root)
    other = resolve(workspace=copy_root / "repo" / ".works")
    first = run_proposal_decide(layout, TARGET, "approved", by="human", at=AT)
    second = run_proposal_decide(other, TARGET, "approved", by="human", at=AT)
    assert first.refusals == second.refusals
    assert first.plan is not None and second.plan is not None
    assert first.plan.writes == second.plan.writes


@pytest.mark.parametrize("target", ("docs/explanations/nope.md", "../escape.md", ""))
def test_unknown_or_escaping_target_is_a_no_proposal_refusal(layout: WorkspaceLayout, target: str) -> None:
    run = run_proposal_decide(layout, target, "approved", by="human", at=AT)

    assert not run.ok and run.plan is None and run.proposal is None and run.result is None
    assert run.refusals == (
        ProposalRefusal(
            path=normalize_target(target) or target,
            kind="no-proposal",
            detail=f"no proposal targets {normalize_target(target) or target!r}",
        ),
    )
    assert str(layout.root) not in run.refusals[0].detail


def test_already_decided_is_refused_not_raised(layout: WorkspaceLayout) -> None:
    run_proposal_decide(layout, TARGET, "approved", by="human", at=AT, dry_run=False)

    again = run_proposal_decide(layout, TARGET, "rejected", by="human", at=AT, dry_run=False)

    assert not again.ok and again.result is None
    assert [refusal.kind for refusal in again.refusals] == ["not-proposed"]


def test_malformed_proposal_is_refused(layout: WorkspaceLayout) -> None:
    page = layout.bundle_dir / MEMBER
    text = page.read_text(encoding="utf-8").replace("page_status: proposed", "page_status: [bogus]")
    page.write_text(text, encoding="utf-8", newline="")

    run = run_proposal_decide(layout, TARGET, "approved", by="human", at=AT)

    assert not run.ok
    assert [refusal.kind for refusal in run.refusals] == ["malformed-proposal"]


@pytest.mark.parametrize("target", (TARGET, "docs/explanations/nope.md"))
def test_decide_naive_at_raises_before_target_lookup(layout: WorkspaceLayout, target: str) -> None:
    with pytest.raises(ValueError):
        run_proposal_decide(layout, target, "approved", by="human", at=datetime(2026, 8, 24))


def test_file_plans_by_default_then_creates_then_merges(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    before = _snapshot(layout.bundle_dir)

    planned = run_proposal_file(
        layout, lane="explanation", title="Typed CLI", description="d", source=SOURCE, by="agent:t", at=AT
    )
    assert planned.ok and planned.result is None and planned.lane == "explanation"
    assert planned.target == TARGET and planned.proposal == MEMBER
    assert [write.mode for write in planned.plan.writes] == ["create"]
    assert _snapshot(layout.bundle_dir) == before

    _file(layout)
    assert (layout.bundle_dir / MEMBER).is_file()
    merged = run_proposal_file(
        layout,
        lane="explanation",
        title="Typed CLI",
        description="d",
        source={"id": "s2", "resource": "/sources/two.md"},
        by="agent:t",
        at=AT,
    )
    assert merged.ok and [write.mode for write in merged.plan.writes] == ["update"]


def test_file_unknown_lane_raises_key_error(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(KeyError):
        run_proposal_file(layout, lane="nope", title="T", description="", source=SOURCE, by="a", at=AT)


def test_file_naive_at_raises(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(ValueError):
        run_proposal_file(
            layout,
            lane="explanation",
            title="T",
            description="",
            source=SOURCE,
            by="a",
            at=datetime(2026, 8, 24),
        )
