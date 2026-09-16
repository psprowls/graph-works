"""A renamed fork-child's recorded placement retains its execute commit at finish.

This is a real-Git behavioral recurrence, not evidence of the historical
installed-runtime cause of the filed incident.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.orchestrate import placement
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import provenance
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import load
from subagents_io.dispatch import PlannedDispatch

TODAY = date(2026, 9, 14)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/bug-retention"
EPIC_BRANCH = "epic/epic-a-1a2b3c4d"
PLANNED = "bug/retention-5e6f7a8b"
OBSERVED = "psprowls/bug-retention-5e6f7a8b"  # Orca's rename of PLANNED


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _page(layout: WorkspaceLayout, path: str, body: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(body, encoding="utf-8", newline="")


@pytest.fixture
def world(tmp_path: Path) -> tuple[WorkspaceLayout, Path, Path]:
    repo = tmp_path / "repo"
    (repo / "packages/a").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "packages/a/x.txt").write_text("one\n", encoding="utf-8", newline="")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    epic_wt = tmp_path / "wt-epic"
    _git(repo, "worktree", "add", "-b", EPIC_BRANCH, str(epic_wt), "main")
    fork = tmp_path / "wt-fork"
    _git(repo, "worktree", "add", "-b", OBSERVED, str(fork), EPIC_BRANCH)

    # Keep the vault outside the checkout so stage writes cannot dirty the repo.
    layout = apply_init(plan_init(tmp_path / "works", today=TODAY, topic="Recurrence")).layout
    (layout.root / "dispatch.yaml").write_text(
        "pipeline:\n  rules:\n    - match: {variant: branch}\n"
        '      prompt_tail: "Auto-drive context: merge target is `{merge_target}`."\n',
        encoding="utf-8",
        newline="",
    )
    # Git resolves macOS's /var symlink; comparisons use its own path spelling.
    epic_toplevel = _git(epic_wt, "rev-parse", "--show-toplevel")
    front = "description: d\nstatus: stable\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
    _page(
        layout,
        EPIC,
        f"---\ntype: Epic\ntitle: Epic\n{front}work_status: in-progress\nphase: execute\n"
        f"worktree: {epic_toplevel}\nbranch: {EPIC_BRANCH}\naffects:\n- packages\n---\n\n## Summary\nd\n",
    )
    _page(
        layout,
        CHILD,
        f"---\ntype: Bug\ntitle: Retention\n{front}work_status: in-progress\nowner: pat\nphase: execute\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
    )
    regenerated = work.run_regen_indexes(layout, dry_run=False)
    assert regenerated.application is not None and regenerated.application.ok
    return layout, repo, fork


def _finish_dispatch(layout: WorkspaceLayout, repo: Path) -> PlannedDispatch:
    result = orchestrate.run_orchestrate(layout, EPIC, repo=repo)
    matches = [d for d in result.dispatches if d.slug == CHILD]
    assert matches, ("finish was not dispatched", result.blocked)
    [dispatch] = matches
    assert dispatch.phase == "finish"
    return dispatch


def _execute_in_fork(layout: WorkspaceLayout, repo: Path, fork: Path, *, explicit_start: bool = True) -> str:
    start = _git(fork, "rev-parse", "HEAD")
    (fork / "packages/a/x.txt").write_text("two\n", encoding="utf-8", newline="")
    _git(fork, "commit", "-am", "execute work")
    commit = _git(fork, "rev-parse", "HEAD")
    assert commit != start
    # Without a recorded worktree the gate reads main, where an explicit start
    # would see zero commits. Only the control omits it, deliberately exercising
    # the weaker absent-start warning so finish placement can be inspected.
    advanced = stage.run_stage_advance(
        layout,
        CHILD,
        today=TODAY,
        repo=repo,
        start_sha=start if explicit_start else None,
        infer_worktree=False,
        cwd=fork,
        dry_run=False,
    )
    assert advanced.outcome.plan.refusal is None, advanced.outcome.plan.detail
    assert advanced.application is not None and advanced.application.ok
    if explicit_start:
        assert advanced.warnings == ()
    else:
        assert len(advanced.warnings) == 1
        assert "no explicit `start_sha` to read the commit range from" in advanced.warnings[0]
    return commit


def test_a_recorded_renamed_fork_child_reuses_its_execute_commit_at_finish(
    world: tuple[WorkspaceLayout, Path, Path],
) -> None:
    layout, repo, fork = world
    epic_start = _git(repo, "rev-parse", EPIC_BRANCH)
    observed = provenance.worktree_state(fork, repo)
    assert observed == (_git(fork, "rev-parse", "--show-toplevel"), OBSERVED)
    assert observed[1] != PLANNED

    record = placement.run_record_placement(
        layout,
        CHILD,
        root=EPIC,
        phase="execute",
        worktree=observed[0],
        branch=observed[1],
        today=TODAY,
        dry_run=False,
    )
    assert record.written, record.plan.detail
    commit = _execute_in_fork(layout, repo, fork)

    fm = load(layout.bundle_dir / f"{CHILD}.md").fm_data()
    assert (fm["phase"], fm["worktree"], fm["branch"]) == ("finish", observed[0], OBSERVED)

    dispatch = _finish_dispatch(layout, repo)
    assert dispatch.worktree.action == "reuse"
    assert dispatch.worktree.path == observed[0]
    assert dispatch.worktree.branch == OBSERVED
    assert _git(Path(dispatch.worktree.path), "rev-parse", "HEAD") == commit
    assert dispatch.merge_target == EPIC_BRANCH
    assert _git(repo, "rev-parse", EPIC_BRANCH) == epic_start != commit
    assert load(layout.bundle_dir / f"{EPIC}.md").fm_data()["branch"] == EPIC_BRANCH


def test_without_a_record_the_finish_stage_forks_away_from_the_commit(
    world: tuple[WorkspaceLayout, Path, Path],
) -> None:
    """The unstamped child under a stamped epic takes the anchor's fork rule.

    That rule precedes cold-start inventory adoption in `_resolve_worktree`.
    Also, this fixture's fixed branch suffix differs from the current path hash,
    and `wt-fork` differs from the item's basename, so `_adopt`'s exact branch,
    flattened-branch and directory matches would not recover it. Other
    unstamped placements can be rescued by adoption; this is not a claim about
    every renamed branch or proof of the historical installed-runtime cause.
    """
    layout, repo, fork = world
    current_planned = orchestrate.branch_name(CHILD, "Bug")
    assert current_planned != PLANNED
    assert OBSERVED.rsplit("/", 1)[-1] != current_planned.replace("/", "-")
    assert fork.name != Path(CHILD).name
    commit = _execute_in_fork(layout, repo, fork, explicit_start=False)

    fm = load(layout.bundle_dir / f"{CHILD}.md").fm_data()
    assert fm["phase"] == "finish"
    assert fm.get("worktree") is None and fm.get("branch") is None
    dispatch = _finish_dispatch(layout, repo)
    assert dispatch.worktree.action == "fork-child"
    assert dispatch.worktree.path is None
    assert dispatch.worktree.base_branch == EPIC_BRANCH
    assert dispatch.merge_target == EPIC_BRANCH
    assert _git(repo, "rev-parse", dispatch.worktree.base_branch) != commit
    assert _git(fork, "rev-parse", "HEAD") == commit
