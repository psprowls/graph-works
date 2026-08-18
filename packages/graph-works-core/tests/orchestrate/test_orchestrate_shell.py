"""The IO shell: config in, plan out. No dispatch decisions here."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core.orchestrate import commands as orchestrate
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for

_ITEM = """---
type: {type}
title: {title}
description: d
status: stable
workflow_status: open
phase: {phase}
effort: medium
opened: 2026-08-01
updated: 2026-08-01
affects:
- packages/a
{extra}---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""
# The empty plan table is load-bearing, not decoration: `advance_and_stamp`
# splices the plan row into it on the plan->execute transition, and a page
# without the section reads as `missing` rather than `empty`. It is the same
# body `work_helpers.EMPTY_PLAN_BODY` seeds for the same reason.


def _workspace(tmp_path, manifest_text="version: 1\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace.yaml").write_text(manifest_text, encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write_item(layout, slug, *, type="Feature", phase="plan", extra="") -> None:
    (layout.bundle_dir / "work" / f"{slug}.md").write_text(
        _ITEM.format(type=type, title=slug, phase=phase, extra=extra), encoding="utf-8"
    )


def test_a_lone_item_plans_with_empty_decision_fields(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    result = orchestrate.run_orchestrate(layout, "a")
    assert [d.slug for d in result.dispatches] == ["a"]
    assert result.decisions_epic_slug is None
    assert result.open_decisions == () and result.decision_counts == {}


def test_the_manifest_scalars_reach_the_plan(tmp_path):
    layout = _workspace(
        tmp_path,
        "version: 1\nworkflow:\n  auto_drive:\n    max_parallel: 5\n    permission_mode: default\n",
    )
    _write_item(layout, "a")
    result = orchestrate.run_orchestrate(layout, "a")
    assert result.max_parallel == 5
    assert result.permission_mode == "default"


@pytest.mark.parametrize(
    ("manifest_text", "match"),
    [
        # `"4"` fails `isinstance(..., int)` and silently became 2 — the same
        # value a workspace that set nothing gets.
        ('version: 1\nworkflow:\n  auto_drive:\n    max_parallel: "4"\n', "expects an integer"),
        # `true` *passes* `isinstance(True, int)` and silently became 1.
        ("version: 1\nworkflow:\n  auto_drive:\n    max_parallel: true\n", "expects an integer"),
        # A real default (2) is inherited while the file reads as deliberate.
        ("version: 1\nworkflow:\n  auto_drive:\n    max_parallel: null\n", "explicitly null"),
        # `str()`-laundered into the plan as "3".
        ("version: 1\nworkflow:\n  auto_drive:\n    permission_mode: 3\n", "expects a string"),
        ("version: 1\nworkflow:\n  auto_drive:\n    permission_mode: null\n", "explicitly null"),
    ],
)
def test_a_mistyped_auto_drive_scalar_refuses_rather_than_falling_back(tmp_path, manifest_text, match):
    layout = _workspace(tmp_path, manifest_text)
    _write_item(layout, "a")
    with pytest.raises(WorkspaceError, match=match) as excinfo:
        orchestrate.run_orchestrate(layout, "a")
    assert str(layout.manifest_path) in str(excinfo.value)


def test_a_bad_routing_rule_is_refused_loudly(tmp_path):
    layout = _workspace(
        tmp_path,
        "version: 1\nworkflow:\n  auto_drive:\n    overrides:\n    - match:\n        phase: nope\n      model: opus\n",
    )
    _write_item(layout, "a")
    with pytest.raises(WorkspaceError, match="nope"):
        orchestrate.run_orchestrate(layout, "a")


def test_an_unknown_match_key_is_refused(tmp_path):
    layout = _workspace(
        tmp_path,
        "version: 1\nworkflow:\n  auto_drive:\n    overrides:\n    - match:\n        colour: red\n      model: opus\n",
    )
    _write_item(layout, "a")
    with pytest.raises(WorkspaceError, match="colour"):
        orchestrate.run_orchestrate(layout, "a")


def test_the_owning_epics_ledger_is_read(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "epic-x", type="Epic", phase="execute")
    _write_item(layout, "a", extra="parent: epic-x\n")
    ledger = layout.bundle_dir / "work" / "epic-x" / "references" / "00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("# Decisions\n\n## D-001 — question one\nstatus: open\n\nprose\n", encoding="utf-8")
    result = orchestrate.run_orchestrate(layout, "a")
    assert result.decisions_epic_slug == "epic-x"
    assert result.decisions_ledger_path == str(ledger)
    assert [d.id for d in result.open_decisions] == ["D-001"]
    assert result.decision_counts["total"] == 1


def test_an_item_held_by_an_open_decision_is_not_redispatched(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "epic-x", type="Epic", phase="execute")
    _write_item(layout, "a", phase="design", extra="parent: epic-x\n")
    ledger = layout.bundle_dir / "work" / "epic-x" / "references" / "00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("# Decisions\n\n## D-001 — question one\nstatus: open\naffects: [a]\n\nprose\n", encoding="utf-8")
    result = orchestrate.run_orchestrate(layout, "a")
    assert result.dispatches == ()
    assert [(b.slug, b.kind) for b in result.blocked] == [("a", "decisions")]


def test_an_open_decision_not_naming_the_item_does_not_hold_it(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "epic-x", type="Epic", phase="execute")
    _write_item(layout, "a", phase="design", extra="parent: epic-x\n")
    ledger = layout.bundle_dir / "work" / "epic-x" / "references" / "00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decisions\n\n## D-001 — question one\nstatus: open\naffects: [some-other-slug]\n\nprose\n",
        encoding="utf-8",
    )
    result = orchestrate.run_orchestrate(layout, "a")
    assert [d.slug for d in result.dispatches] == ["a"]


def test_a_missing_ledger_reads_as_empty(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "epic-x", type="Epic", phase="execute")
    _write_item(layout, "a", extra="parent: epic-x\n")
    result = orchestrate.run_orchestrate(layout, "a")
    assert result.decisions_epic_slug == "epic-x"
    assert result.open_decisions == ()
    assert result.decision_counts["total"] == 0


def test_the_default_base_degrades_without_a_repo(tmp_path):
    assert orchestrate.default_base(None) == "main"
    assert orchestrate.default_base(tmp_path) == "main"


def test_stamped_worktrees_are_stat_once(tmp_path):
    layout = _workspace(tmp_path)
    live = tmp_path / "wt"
    live.mkdir()
    _write_item(layout, "a", extra=f"worktree: {live}\nbranch: feature/a\n")
    result = orchestrate.run_orchestrate(layout, "a")
    assert result.dispatches[0].worktree.exists is True


def test_stat_worktrees_dedupes_a_path_shared_by_two_items(tmp_path, monkeypatch):
    from test_orchestrate_plan import _item

    live = tmp_path / "wt"
    live.mkdir()
    items = (
        _item("a", worktree=str(live), branch="feature/a"),
        _item("b", worktree=str(live), branch="feature/a", opened="2026-08-02"),
    )
    calls: list[Path] = []
    original_is_dir = Path.is_dir

    def _counting_is_dir(self: Path) -> bool:
        calls.append(self)
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", _counting_is_dir)
    stats = orchestrate._stat_worktrees(items)
    assert stats == {str(live): True}
    assert calls == [live]


def test_a_dry_run_stage_advance_writes_nothing(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    page = layout.bundle_dir / "work" / "a.md"
    before = page.read_bytes()
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14))
    assert result.outcome.plan.refusal is None
    assert result.outcome.written is False
    assert result.results_path is None and result.pointer_path is None
    assert page.read_bytes() == before


def test_a_real_stage_advance_writes_the_pointer(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), dry_run=False)
    assert result.outcome.written is True
    assert result.pointer_path == layout.cache_dir / "active-work.json"
    assert "phase: execute" in (layout.bundle_dir / "work" / "a.md").read_text(encoding="utf-8")


def test_a_done_landing_skips_the_pointer(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "epic-x", type="Epic", phase="finish")
    result = orchestrate.run_stage_advance(layout, "epic-x", today=date(2026, 8, 14), dry_run=False)
    assert result.outcome.plan.transition.phase == "done"
    assert result.pointer_path is None
    assert not (layout.cache_dir / "active-work.json").exists()


def test_a_refusal_writes_nothing(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a", phase="execute")
    _write_item(layout, "b", extra="parent: a\n")
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), dry_run=False)
    assert result.outcome.plan.refusal is not None
    assert result.results_path is None and result.pointer_path is None


def test_a_recorded_present_worktree_is_not_repointed(tmp_path):
    layout = _workspace(tmp_path)
    existing = tmp_path / "wt"
    existing.mkdir()
    _write_item(layout, "a", extra=f"worktree: {existing}\nbranch: feature/a\n")
    result = orchestrate.run_stage_advance(
        layout, "a", today=date(2026, 8, 14), cwd=tmp_path, repo=tmp_path, dry_run=False
    )
    changed = {c.key for c in result.outcome.plan.changes}
    assert "worktree" not in changed and "branch" not in changed


def test_the_results_stub_needs_a_start_sha_and_an_execute_phase(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a", phase="execute")
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), owner="pat", dry_run=False)
    assert result.results_path is None  # no start_sha supplied


def test_a_results_stub_lands_when_the_stage_actually_completed(tmp_path):
    import subprocess

    def _git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "t")
    layout = _workspace(tmp_path)
    _write_item(layout, "a", phase="execute")
    _git("add", "-A")
    _git("commit", "-q", "-m", "start")
    start_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    # First call only flips workflow_status to in-progress (on_dispatch, no phase
    # change) -- exactly `test_the_results_stub_needs_a_start_sha_and_an_execute_phase`'s
    # setup. Commit it so the second call has a real commit range to summarize.
    first = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), owner="pat", dry_run=False)
    assert first.results_path is None
    _git("add", "-A")
    _git("commit", "-q", "-m", "in progress")

    result = orchestrate.run_stage_advance(
        layout,
        "a",
        today=date(2026, 8, 14),
        cwd=tmp_path,
        repo=tmp_path,
        start_sha=start_sha,
        dry_run=False,
    )
    assert result.outcome.plan.transition.phase == "finish"
    assert result.results_path is not None
    assert result.results_path.exists()
    assert "Execute — results" in result.results_path.read_text(encoding="utf-8")


# --- resolve_repo: the code repo this workspace describes --------------------


def _repositories(layout, body: str) -> None:
    """Write `_repositories.yaml` under the bundle. `graph_dir` is required."""
    (layout.bundle_dir / "_repositories.yaml").write_text(f'graph_dir: "{layout.cache_dir}"\n{body}', encoding="utf-8")


def test_resolve_repo_returns_the_one_declared_repository(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    resolved, note = orchestrate.resolve_repo(layout)
    assert resolved == code.resolve()
    assert note is None


def test_resolve_repo_selects_by_name_when_several_are_declared(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    resolved, note = orchestrate.resolve_repo(layout, repo_name="two")
    assert resolved == two.resolve()
    assert note is None


def test_resolve_repo_refuses_a_repo_name_nothing_declares(tmp_path):
    layout = _workspace(tmp_path)
    one = tmp_path / "one"
    one.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n')
    with pytest.raises(WorkspaceError, match="one") as excinfo:
        orchestrate.resolve_repo(layout, repo_name="nope")
    assert "nope" in str(excinfo.value)


def test_resolve_repo_refuses_to_guess_between_several(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    with pytest.raises(WorkspaceError) as excinfo:
        orchestrate.resolve_repo(layout)
    message = str(excinfo.value)
    assert "repo_name" in message and "one" in message and "two" in message


def test_resolve_repo_degrades_when_none_are_declared(tmp_path):
    layout = _workspace(tmp_path)
    _repositories(layout, "repositories: {}\n")
    resolved, note = orchestrate.resolve_repo(layout)
    assert resolved is None
    assert note  # a sentence, not silence


def test_resolve_repo_degrades_when_the_file_is_missing(tmp_path):
    # `load_config` propagates OSError for a missing file; a workspace that has
    # not been initialized is not an error here, it is a degrade.
    layout = _workspace(tmp_path)
    resolved, note = orchestrate.resolve_repo(layout)
    assert resolved is None
    assert note


def test_resolve_repo_refuses_a_malformed_declarations_file(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "_repositories.yaml").write_text("graph_dir: [not, a, string]\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match=r"_repositories\.yaml"):
        orchestrate.resolve_repo(layout)


def test_resolve_repo_is_exported():
    assert "resolve_repo" in orchestrate.__all__


# --- the shells resolve repo from the declarations, not from layout.repo_root


def test_an_explicit_repo_beats_a_declared_one_and_skips_the_read(tmp_path):
    # An argument is not a default: decision 1 replaced `layout.repo_root` as
    # the *default*, it did not outrank a caller that already knows. The
    # malformed file proves the read is skipped, not merely overridden.
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    (layout.bundle_dir / "_repositories.yaml").write_text("graph_dir: [bad]\n", encoding="utf-8")
    result = orchestrate.run_orchestrate(layout, "a", repo=tmp_path)
    assert [d.slug for d in result.dispatches] == ["a"]


def test_a_missing_declarations_file_warns_rather_than_failing_the_plan(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    result = orchestrate.run_orchestrate(layout, "a")
    assert [d.slug for d in result.dispatches] == ["a"]
    assert any("_repositories.yaml" in warning for warning in result.warnings)


def test_the_declared_repo_supplies_the_default_base(tmp_path):
    import subprocess

    code = tmp_path / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=code, check=True, capture_output=True)
    # `-b trunk` only names the initial branch; `default_base` reads
    # `refs/remotes/origin/HEAD`, which nothing sets by default. Set it
    # explicitly so a real value flows out of the declared repo -- otherwise
    # this test would pass identically even if `resolve_repo` returned `None`
    # instead of `code`, since both cases fall back to `FALLBACK_BASE`.
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"],
        cwd=code,
        check=True,
        capture_output=True,
    )
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    result = orchestrate.run_orchestrate(layout, "a")
    assert result.dispatches[0].merge_target == "trunk"


def test_an_ambiguous_declaration_set_refuses_the_plan(tmp_path):
    layout = _workspace(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  one:\n    path: "{one}"\n  two:\n    path: "{two}"\n')
    with pytest.raises(WorkspaceError, match="repo_name"):
        orchestrate.run_orchestrate(layout, "a")
    # …and naming one resolves it.
    assert orchestrate.run_orchestrate(layout, "a", repo_name="two").dispatches[0].slug == "a"


def test_a_stage_advance_carries_the_repo_note(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14))
    assert result.repo_note is not None
    assert "_repositories.yaml" in result.repo_note


def test_a_stage_advance_with_an_explicit_repo_carries_no_note(tmp_path):
    layout = _workspace(tmp_path)
    _write_item(layout, "a")
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), repo=tmp_path)
    assert result.repo_note is None


def test_a_stage_advance_resolves_the_declared_repo(tmp_path):
    layout = _workspace(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')
    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14))
    assert result.repo_note is None


def test_the_module_no_longer_reads_layout_repo_root(tmp_path):
    # The narrowing decision 1 took: `repo_root` stays on the layout (gitignore
    # placement and `scanner_excludes` are its documented job) but this module
    # stops being one of its readers.
    from pathlib import Path as _Path

    source = _Path(orchestrate.__file__).read_text(encoding="utf-8")
    assert "layout.repo_root" not in source


# --- the headline integration: a real worktree, a declared repo, a stamp -----


def _init_repo(root: Path) -> None:
    import subprocess

    root.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True, capture_output=True, text=True)


def test_an_advance_from_inside_a_worktree_stamps_the_provenance(tmp_path):
    """The one integration point of the headline feature, end to end.

    Everything below is required for a stamp, and each half was separately
    broken: `resolve_repo` has to find the *code* repo (the workspace is a
    different repository here, which is the live topology), and
    `worktree_state` has to accept a worktree whose common dir does not sit
    directly under `repo`.
    """
    import subprocess

    code = tmp_path / "code"
    _init_repo(code)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/a", str(worktree)],
        cwd=code,
        check=True,
        capture_output=True,
        text=True,
    )

    layout = _workspace(tmp_path / "ws")
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')

    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), cwd=worktree, dry_run=False)
    assert result.repo_note is None
    changed = {c.key: c.after for c in result.outcome.plan.changes}
    assert "worktree" in changed and "branch" in changed
    assert Path(changed["worktree"]).resolve() == worktree.resolve()
    assert changed["branch"] == "feature/a"


def test_the_split_topology_failure_stamps_nothing_when_the_wrong_repo_is_declared(tmp_path):
    """The negative half. Declare the *workspace's* repo instead of the code
    repo -- the exact shape the item describes -- and the stamp does not land.
    This is what makes the positive test above a test of both fixes rather
    than of `worktree_state` alone."""
    import subprocess

    code = tmp_path / "code"
    _init_repo(code)
    vault = tmp_path / "vault"
    _init_repo(vault)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/a", str(worktree)],
        cwd=code,
        check=True,
        capture_output=True,
        text=True,
    )

    layout = _workspace(vault / "ws")
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  vault:\n    path: "{vault}"\n')

    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), cwd=worktree, dry_run=False)
    changed = {c.key for c in result.outcome.plan.changes}
    assert "worktree" not in changed and "branch" not in changed


def test_an_explicit_pair_repoints_a_stamp_that_is_still_a_live_directory(tmp_path):
    """The eviction path. The recorded stamp is a real directory, so the
    cwd-inference guard would refuse to touch it. An explicit pair must
    overwrite it anyway -- otherwise plan()'s eviction decision can never
    land, and an item stays in the main checkout forever."""
    code = tmp_path / "code"
    _init_repo(code)
    live = tmp_path / "live"
    live.mkdir()

    layout = _workspace(tmp_path / "ws")
    _write_item(layout, "a", extra=f'worktree: "{live}"\nbranch: psprowls/old\n')
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')

    result = orchestrate.run_stage_advance(
        layout,
        "a",
        today=date(2026, 8, 14),
        worktree="/evicted",
        branch="psprowls/new",
        dry_run=False,
    )
    changed = {c.key: c.after for c in result.outcome.plan.changes}
    assert changed["worktree"] == "/evicted"
    assert changed["branch"] == "psprowls/new"


def test_without_an_explicit_pair_a_live_stamp_is_still_never_repointed(tmp_path):
    """The conservative fallback, unchanged. This is the guard that stops an
    advance run from an unrelated cwd silently stealing a live item."""
    code = tmp_path / "code"
    _init_repo(code)
    live = tmp_path / "live"
    live.mkdir()

    layout = _workspace(tmp_path / "ws")
    _write_item(layout, "a", extra=f'worktree: "{live}"\nbranch: psprowls/old\n')
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')

    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), dry_run=False)
    changed = {c.key for c in result.outcome.plan.changes}
    assert "worktree" not in changed and "branch" not in changed


def test_a_partial_pair_falls_through_to_inference_rather_than_half_stamping(tmp_path):
    """A worktree with no branch is not a resolved plan -- it is a caller bug.
    Treating it as authoritative would write a path with a stale branch."""
    code = tmp_path / "code"
    _init_repo(code)
    layout = _workspace(tmp_path / "ws")
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')

    result = orchestrate.run_stage_advance(layout, "a", today=date(2026, 8, 14), worktree="/only-a-path", dry_run=False)
    changed = {c.key: c.after for c in result.outcome.plan.changes}
    assert changed.get("worktree") != "/only-a-path"


def test_a_clean_main_checkout_is_offered_to_a_cold_start(tmp_path):
    code = tmp_path / "code"
    _init_repo(code)
    layout = _workspace(tmp_path / "ws")
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')

    result = orchestrate.run_orchestrate(layout, "a")
    assert result.plan.dispatches[0].worktree.action == "main"


def test_a_dirty_main_checkout_is_withheld_and_a_worktree_is_provisioned(tmp_path):
    """A human's uncommitted work is invisible to occupancy tracking, which
    only ever sees work items. Falling back to create-top-level is the same
    path taken when no repo is resolvable at all."""
    code = tmp_path / "code"
    _init_repo(code)
    (code / "scratch.txt").write_text("someone is working here\n", encoding="utf-8")

    layout = _workspace(tmp_path / "ws")
    _write_item(layout, "a")
    _repositories(layout, f'repositories:\n  code:\n    path: "{code}"\n')

    result = orchestrate.run_orchestrate(layout, "a")
    assert result.plan.dispatches[0].worktree.action == "create-top-level"


def test_an_unreadable_checkout_is_treated_as_dirty(tmp_path):
    """Fail closed. `run_git` answers `None` when the directory is not a git
    repository at all, and a checkout we cannot interrogate is not one to
    dispatch a worker into."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert orchestrate._checkout_is_dirty(not_a_repo) is True
