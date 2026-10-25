"""End-to-end placement conformance: attended and auto-drive drivers over a temp-git epic."""

from __future__ import annotations

from pathlib import Path

import pytest
from conformance_drivers import RunLog
from conformance_fixture import (
    REPOS,
    Fixture,
    Inventory,
    Oracle,
    assert_conforms,
    build_fixture,
    gw,
    normalized,
    observe,
)
from gw_kernel.testing.transactions import git


def test_bare_fixture_conforms(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    assert_conforms(fx, Oracle(fx), "bare")


def test_checker_rejects_a_worktree_at_an_uncomputed_path(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    git(fx.repos["core"], "worktree", "add", "-b", "feature/stray", str(tmp_path / "stray"), "main")
    with pytest.raises(AssertionError, match="stray"):
        assert_conforms(fx, Oracle(fx), "stray")


def test_checker_rejects_a_stored_placement_key(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    path = gw(fx, "work", "file", "--title", "Probe", "--kind", "Feature", "--summary", "s")["path"]
    page = fx.workspace / "okf" / f"{path}.md"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace("\n---\n", "\nbranch: x\n---\n", 1), encoding="utf-8", newline="\n")
    with pytest.raises(AssertionError, match="probe"):
        assert_conforms(fx, Oracle(fx), "stored")


def _only_main(fx: Fixture, repo: str) -> Inventory:
    checkout = str(fx.repos[repo].resolve())
    return Inventory(frozenset({(checkout, "main")}), frozenset({"main"}))


def test_attended_run_conforms_at_every_step(attended: tuple[Fixture, RunLog]) -> None:
    fx, log = attended
    for repo in REPOS:
        assert observe(fx.repos[repo]) == _only_main(fx, repo)
    assert (fx.children["b"], "merge") in log.refreshes
    assert log.created == 5
    assert len(log.notified) == log.created
    assert not any(call[0] == "worktree_create" for call in log.port_calls)


def test_auto_drive_run_conforms_at_every_step(auto_driven: tuple[Fixture, RunLog]) -> None:
    fx, log = auto_driven
    for repo in REPOS:
        assert observe(fx.repos[repo]) == _only_main(fx, repo)
    assert log.created == 0, "dispatch places through the engine; the CLI notifier fires per created worktree"
    assert len(log.notified) == 5
    assert (fx.children["b"], "dispatch") in log.refreshes
    assert not any(call[0] == "worktree_create" for call in log.port_calls), "Orca must never create a worktree"
    # Epic design, plan and finish, three children of four stages each, and one retried launch.
    assert sum(1 for call in log.port_calls if call[0] == "worker_start") == 16


def test_drivers_converge_on_identical_git_state(
    attended: tuple[Fixture, RunLog], auto_driven: tuple[Fixture, RunLog]
) -> None:
    assert normalized(attended[0]) == normalized(auto_driven[0])


def test_prune_after_archive_is_a_no_op(auto_driven: tuple[Fixture, RunLog]) -> None:
    fx, _ = auto_driven
    before = {repo: observe(fx.repos[repo]) for repo in REPOS}
    assert fx.epic_archived_path is not None
    payload = gw(fx, "work", "place", fx.epic_archived_path, "--prune")
    assert payload["refusal"] is None
    assert payload["pruned"]["removed_worktrees"] == [] and payload["pruned"]["deleted_branches"] == []
    assert {repo: observe(fx.repos[repo]) for repo in REPOS} == before


@pytest.mark.xfail(
    strict=True, reason="the cleanup plan names a --repo-name command `place` refuses for a tagged owner"
)
def test_cleanup_plan_commands_are_all_accepted(attended: tuple[Fixture, RunLog]) -> None:
    _, log = attended
    assert log.refused_cleanup == []
