"""`run_file`: one graph-aware page/index/log filing composition."""

from __future__ import annotations

import json
from datetime import date

import pytest
from _transaction_helpers import _git, _init_git, assert_workspace_commit
from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from work_tracker_okf.dependencies import DependencyEdge

TODAY = date(2026, 8, 17)
EPIC = "work/epic-parent"
SIBLING = f"{EPIC}/children/feature-sibling"


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout


def _config(layout):
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def seeded_workspace(tmp_path):
    layout = _workspace(tmp_path)
    work_dir = layout.bundle_dir / "work"
    work_dir.mkdir(exist_ok=True)
    (layout.bundle_dir / f"{EPIC}.md").write_text(
        "---\ntype: Epic\ntitle: Epic\ndescription: d\nstatus: draft\n"
        "work_status: open\nopened: 2026-08-01\nupdated: 2026-08-01\n---\n",
        encoding="utf-8",
    )
    sibling_page = layout.bundle_dir / f"{SIBLING}.md"
    sibling_page.parent.mkdir(parents=True, exist_ok=True)
    sibling_page.write_text(
        "---\ntype: Feature\ntitle: Sibling\ndescription: d\nstatus: draft\n"
        "work_status: open\nopened: 2026-08-02\nupdated: 2026-08-02\n---\n",
        encoding="utf-8",
    )
    return layout, _config(layout)


def test_run_file_accepts_typed_edges_and_all_metadata(tmp_path) -> None:
    layout, config = seeded_workspace(tmp_path)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Compatibility child",
        description="d",
        on=TODAY,
        effort="medium",
        blast_radius="package",
        owner="pat",
        parent_path=EPIC,
        depends_on=(DependencyEdge(SIBLING, blocks="plan", needs="design"),),
        affects=("packages/graph-works-core",),
        tags=("compat",),
    )
    assert result.plan.filing.frontmatter["owner"] == "pat"
    assert result.application is None


def test_run_file_accepts_release_only_metadata_for_a_release(tmp_path) -> None:
    layout, config = seeded_workspace(tmp_path)
    result = work.run_file(
        layout,
        config,
        type="Release",
        title="Q4 release",
        description="d",
        on=TODAY,
        version="1.2.0",
        target_date=date(2026, 12, 1),
    )
    assert result.plan.filing.frontmatter["version"] == "1.2.0"
    assert result.plan.filing.frontmatter["target_date"] == date(2026, 12, 1)
    assert result.application is None


def test_a_dry_run_writes_nothing(tmp_path):
    layout = _workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="A new feature",
        description="d",
        on=TODAY,
    )
    assert outcome.application is None
    assert outcome.plan.filing.refusal is None
    assert not outcome.plan.filing.target.exists()


def test_run_file_apply_matches_its_plan(tmp_path) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    dry = work.run_file(layout, config, type="Feature", title="Child", description="d", on=TODAY)
    real = work.run_file(layout, config, type="Feature", title="Child", description="d", on=TODAY, dry_run=False)
    assert real.plan == dry.plan
    assert real.application is not None and real.application.ok
    assert real.plan.filing.target.is_file()


def test_run_file_commits_its_work_and_parent_references(tmp_path) -> None:
    layout, _ = seeded_workspace(tmp_path)
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    _init_git(layout.root)
    reference = layout.bundle_dir / EPIC / "references/parent-guidance.txt"
    reference.parent.mkdir(parents=True)
    reference.write_text("parent guidance\n", encoding="utf-8", newline="\n")
    result = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="New child",
        description="d",
        parent_path=EPIC,
        on=TODAY,
        dry_run=False,
    )
    assert result.application is not None and result.application.ok
    assert result.application.commit is not None and result.application.commit.status == "committed"
    assert_workspace_commit(layout.root, "workspace: file feature-new-child")
    assert result.plan.filing.path == f"{EPIC}/children/feature-new-child"
    assert set(_git(layout.root, "show", "--name-only", "--format=", "HEAD").splitlines()) == {
        "okf/log.md",
        f"okf/{EPIC}/children/index.md",
        f"okf/{EPIC}/children/feature-new-child.md",
        f"okf/{EPIC}/children/feature-new-child/children/index.md",
        f"okf/{EPIC}/children/feature-new-child/references/.gitkeep",
        f"okf/{EPIC}/references/parent-guidance.txt",
    }
    assert _git(layout.root, "status", "--porcelain") == ""


def test_run_file_refuses_a_target_created_after_domain_planning(tmp_path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    original = work.plan_file_and_reconcile
    external = b"external owner\n"

    def inject_after_planning(*args, **kwargs):
        outcome = original(*args, **kwargs)
        outcome.plan.filing.target.parent.mkdir(parents=True, exist_ok=True)
        outcome.plan.filing.target.write_bytes(external)
        return outcome

    monkeypatch.setattr(work, "plan_file_and_reconcile", inject_after_planning)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Raced target",
        description="d",
        on=TODAY,
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert result.plan.filing.target.read_bytes() == external


def test_run_file_refuses_a_log_changed_after_domain_planning(tmp_path, monkeypatch) -> None:
    layout = _workspace(tmp_path)
    config = _config(layout)
    original = work.plan_file_and_reconcile
    log_path = layout.bundle_dir / "log.md"
    external = log_path.read_bytes() + b"\nexternal log owner\n"

    def inject_after_planning(*args, **kwargs):
        outcome = original(*args, **kwargs)
        log_path.write_bytes(external)
        return outcome

    monkeypatch.setattr(work, "plan_file_and_reconcile", inject_after_planning)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Raced log",
        description="d",
        on=TODAY,
        dry_run=False,
    )
    assert result.application is not None and not result.application.ok
    assert log_path.read_bytes() == external
    assert not result.plan.filing.target.exists()


def _split_workspace(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    code = tmp_path / "code"
    (code / "packages/foo").mkdir(parents=True)
    layout = apply_init(plan_init(vault / ".works", today=TODAY, topic="Split")).layout
    layout.manifest_path.write_text(
        f'version: 1\nrepositories:\n  "code":\n    path: {json.dumps(str(code))}\n',
        encoding="utf-8",
    )
    return layout


def test_split_topology_files_against_the_declared_code_repo_not_the_vault(tmp_path) -> None:
    layout = _split_workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="Split-topology filing",
        description="d",
        on=TODAY,
        affects=("packages/foo",),
        dry_run=False,
    )
    assert outcome.plan.filing.refusal is None
    assert outcome.application is not None
    assert outcome.application.ok, outcome.application.failures


def _two_repo_workspace(tmp_path):
    """A split vault declaring two code repositories, each owning one path."""
    layout = _split_workspace(tmp_path)
    other = tmp_path / "other"
    (other / "apps/ui").mkdir(parents=True)
    code = tmp_path / "code"
    layout.manifest_path.write_text(
        "version: 1\nrepositories:\n"
        f'  "code":\n    path: {json.dumps(str(code))}\n'
        f'  "other":\n    path: {json.dumps(str(other))}\n',
        encoding="utf-8",
    )
    return layout


def test_run_file_refuses_an_undeclared_repo(tmp_path) -> None:
    layout = _two_repo_workspace(tmp_path)
    result = work.run_file(
        layout, _config(layout), type="Feature", title="Unknown repo", description="d", on=TODAY, repo="nope"
    )
    assert result.plan.refusal == "unknown-repo"
    assert f"code → {tmp_path / 'code'}, other → {tmp_path / 'other'}" in result.plan.filing.detail
    assert result.application is None


def test_run_file_warns_on_an_unresolved_repo_in_a_two_repo_workspace(tmp_path) -> None:
    layout = _two_repo_workspace(tmp_path)
    result = work.run_file(layout, _config(layout), type="Feature", title="Unassigned", description="d", on=TODAY)
    [warning] = [item for item in result.plan.warnings if item.startswith("repo unresolved:")]
    assert f"code → {tmp_path / 'code'}, other → {tmp_path / 'other'}" in warning


def test_run_file_in_a_single_repo_workspace_never_warns(tmp_path) -> None:
    layout = _split_workspace(tmp_path)
    result = work.run_file(layout, _config(layout), type="Feature", title="One repo", description="d", on=TODAY)
    assert not any(item.startswith("repo unresolved:") for item in result.plan.warnings)


def test_two_declared_repos_file_against_every_declared_repo(tmp_path) -> None:
    layout = _two_repo_workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="Two-repo filing",
        description="d",
        on=TODAY,
        affects=("packages/foo", "apps/ui"),
        dry_run=False,
    )
    assert outcome.plan.filing.refusal is None
    assert outcome.application is not None
    assert outcome.application.ok, outcome.application.failures


def test_two_declared_repos_still_refuse_a_path_under_neither(tmp_path) -> None:
    layout = _two_repo_workspace(tmp_path)
    outcome = work.run_file(
        layout,
        _config(layout),
        type="Feature",
        title="Two-repo filing",
        description="d",
        on=TODAY,
        affects=("apps/gone",),
        dry_run=False,
    )
    assert outcome.application is not None
    assert not outcome.application.ok
    assert any("targets.affects-missing" in failure for failure in outcome.application.failures)


def test_the_vertical_is_not_hoisted_to_the_front_door():
    # Deliberate: `graph_works_core.work.commands.run_lint` would collide
    # with the already-hoisted `graph_works_core.run_lint` (from
    # `lint_drift.lint`) if this vertical were hoisted too. This vertical
    # stays reachable only at `graph_works_core.work.commands.*`.
    import graph_works_core

    assert not hasattr(graph_works_core, "run_file")
    assert not hasattr(graph_works_core, "run_status")
    assert not hasattr(graph_works_core, "StatusReport")
    assert graph_works_core.run_lint is not work.run_lint


def test_run_file_replans_after_a_sibling_advance_between_plan_and_apply(tmp_path, monkeypatch) -> None:
    """Filing a child writes the parent's children lane index, planned from
    pages read before the lock -- the same race as `regen-index`."""
    layout, config = seeded_workspace(tmp_path)
    sibling_page = layout.bundle_dir / f"{SIBLING}.md"
    real = work.apply_mutation
    calls: list[int] = []

    def sibling_advances_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            sibling_page.write_text(
                sibling_page.read_text(encoding="utf-8").replace("work_status: open", "work_status: accepted"),
                encoding="utf-8",
            )
        return real(*args, **kwargs)

    monkeypatch.setattr(work, "apply_mutation", sibling_advances_once)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Raced child",
        description="d",
        on=TODAY,
        parent_path=EPIC,
        dry_run=False,
    )

    assert len(calls) == 2
    assert result.application is not None and result.application.ok
    assert result.plan.filing.target.is_file()
    index = (layout.bundle_dir / EPIC / "children" / "index.md").read_text(encoding="utf-8")
    assert "(feature-sibling.md) — accepted · not started" in index


@pytest.mark.parametrize("parent", (None, EPIC))
def test_filing_excludes_existing_ancestors_during_sibling_appends(tmp_path, monkeypatch, parent):
    """An ensure-directory entry must not grant custody of a sibling transcript."""
    from graph_works_core.workspace import transactions

    layout, config = seeded_workspace(tmp_path / str(parent is None))
    transcript = layout.bundle_dir / SIBLING / "references/live.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"start\n")
    copied = []
    real = transactions._copy_live_entry

    def append_after_copy(root, member, destination, **kwargs):
        copied.append(member)
        real(root, member, destination, **kwargs)
        with transcript.open("ab") as stream:
            stream.write(b"live\n")

    with monkeypatch.context() as patch:
        patch.setattr(transactions, "_copy_live_entry", append_after_copy)
        result = work.run_file(
            layout,
            config,
            type="Feature",
            title="Live filing",
            description="d",
            on=TODAY,
            parent_path=parent,
            dry_run=False,
        )
    assert result.application.ok, result.application.failures
    assert result.plan.filing.target.is_file()
    assert all((layout.bundle_dir / member).is_file() for member in copied)
    assert transcript.read_bytes() == b"start\n" + b"live\n" * len(copied)


def test_filing_rollback_preserves_foreign_append_and_existing_directories(tmp_path, monkeypatch):
    from graph_works_core.workspace import transactions

    layout, config = seeded_workspace(tmp_path)
    transcript = layout.bundle_dir / SIBLING / "references/live.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"start\n")
    before_log = (layout.bundle_dir / "log.md").read_bytes()
    real = transactions._commit_effect

    def fail_after_write(*args, **kwargs):
        real(*args, **kwargs)
        if args[1].kind == "write" and args[1].member == f"{EPIC}/children/index.md":
            assert (layout.bundle_dir / EPIC / "children/feature-rollback-filing.md").is_file()
            transcript.write_bytes(b"start\nlive\n")
            raise OSError("injected after write")

    monkeypatch.setattr(transactions, "_commit_effect", fail_after_write)
    result = work.run_file(
        layout,
        config,
        type="Feature",
        title="Rollback filing",
        description="d",
        on=TODAY,
        parent_path=EPIC,
        dry_run=False,
    )
    assert result.application.rolled_back, result.application.failures
    assert not result.plan.filing.target.exists()
    assert not result.plan.filing.target.with_suffix("").exists()
    assert (layout.bundle_dir / EPIC / "children").is_dir()
    assert not (layout.bundle_dir / EPIC / "children/index.md").exists()
    assert (layout.bundle_dir / "log.md").read_bytes() == before_log
    assert transcript.read_bytes() == b"start\nlive\n"


@pytest.mark.parametrize("races, expected_attempts", ((1, 2), (3, 3)))
def test_owned_snapshot_race_replans_with_bounded_backoff(tmp_path, monkeypatch, races, expected_attempts):
    from graph_works_core.workspace import transactions

    layout, config = seeded_workspace(tmp_path / str(races))
    real_copy = transactions._copy_live_entry
    real_apply = work.apply_mutation
    attempts = []
    sleeps = []

    def observe_attempt(*args, **kwargs):
        attempts.append(args[1])
        return real_apply(*args, **kwargs)

    def race_log(root, member, destination, **kwargs):
        real_copy(root, member, destination, **kwargs)
        if member == "log.md" and len(attempts) <= races:
            with (layout.bundle_dir / member).open("ab") as stream:
                stream.write(b"\nforeign append\n")

    with monkeypatch.context() as patch:
        patch.setattr(work, "apply_mutation", observe_attempt)
        patch.setattr(transactions, "_copy_live_entry", race_log)
        # Intercept only the wait; planning, snapshotting and writes remain real.
        import time

        patch.setattr(time, "sleep", sleeps.append)
        result = work.run_file(
            layout, config, type="Feature", title="Raced snapshot", description="d", on=TODAY, dry_run=False
        )
    assert len(attempts) == expected_attempts
    assert sleeps == ([0.05] if races == 1 else [0.05, 0.1])
    log_digests = [next(w.before_digest for w in plan.writes if w.member == "log.md") for plan in attempts]
    assert len(set(log_digests)) == expected_attempts
    assert result.plan.filing.target.exists() is (races == 1)
    assert result.application.ok is (races == 1)
    if races == 3:
        assert result.application.created_directories == ()
        assert result.application.written == ()
        assert not result.application.rolled_back


def test_mixed_snapshot_and_inventory_races_share_three_attempts(tmp_path, monkeypatch):
    import time

    from graph_works_core.workspace import transactions

    layout, config = seeded_workspace(tmp_path)
    real_apply = work.apply_mutation
    real_copy = transactions._copy_live_entry
    attempts = []
    sleeps = []
    sibling = layout.bundle_dir / f"{SIBLING}.md"

    def apply_with_inventory_race(*args, **kwargs):
        attempts.append(args[1])
        if len(attempts) == 2:
            sibling.write_bytes(sibling.read_bytes().replace(b"work_status: open", b"work_status: accepted"))
        return real_apply(*args, **kwargs)

    def race_snapshot(root, member, destination, **kwargs):
        real_copy(root, member, destination, **kwargs)
        if member == "log.md" and len(attempts) in (1, 3):
            with (layout.bundle_dir / member).open("ab") as stream:
                stream.write(b"\nforeign append\n")

    monkeypatch.setattr(work, "apply_mutation", apply_with_inventory_race)
    monkeypatch.setattr(transactions, "_copy_live_entry", race_snapshot)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    result = work.run_file(
        layout, config, type="Feature", title="Mixed races", description="d", on=TODAY, parent_path=EPIC, dry_run=False
    )
    assert len(attempts) == 3
    assert sleeps == [0.05]
    assert not result.application.ok
    assert not result.plan.filing.target.exists()
    assert b"work_status: accepted" in sibling.read_bytes()


@pytest.mark.parametrize(
    "cause", ("collision", "stale-log", "permission", "identity", "generic-message", "journal", "rollback")
)
def test_non_snapshot_failures_do_not_retry(tmp_path, monkeypatch, cause):
    import time

    from graph_works_core.workspace import transactions

    layout, config = seeded_workspace(tmp_path / cause)
    real_apply = work.apply_mutation
    real_copy = transactions._copy_live_entry
    real_journal = transactions._append_journal
    attempts = []
    sleeps = []

    def apply_with_refusal(*args, **kwargs):
        attempts.append(args[1])
        if cause == "collision":
            (layout.bundle_dir / "work/feature-refusal.md").write_bytes(b"foreign page")
        elif cause == "stale-log":
            with (layout.bundle_dir / "log.md").open("ab") as stream:
                stream.write(b"\nforeign append\n")
        return real_apply(*args, **kwargs)

    def copy_with_failure(root, member, destination, **kwargs):
        if cause == "permission":
            raise PermissionError("injected permission error")
        if cause == "identity":
            raise ValueError("bundle identity changed")
        if cause == "generic-message":
            raise ValueError(f"{member}: changed while snapshotting; re-plan")
        real_copy(root, member, destination, **kwargs)
        if cause == "journal" and member == "log.md":
            with (layout.bundle_dir / member).open("ab") as stream:
                stream.write(b"\nforeign append\n")

    def fail_cleanup(*args, **kwargs):
        if cause == "journal" and args[1] == "rolled-back":
            raise OSError("injected journal failure")
        return real_journal(*args, **kwargs)

    def fail_validation(*args, **kwargs):
        return ("injected validation failure",)

    def fail_restore(*args, **kwargs):
        raise OSError("injected restore failure")

    with monkeypatch.context() as patch:
        patch.setattr(work, "apply_mutation", apply_with_refusal)
        patch.setattr(transactions, "_copy_live_entry", copy_with_failure)
        patch.setattr(transactions, "_append_journal", fail_cleanup)
        patch.setattr(time, "sleep", sleeps.append)
        if cause == "rollback":
            patch.setattr(transactions, "_validate_postconditions", fail_validation)
            patch.setattr(transactions, "_restore_snapshot", fail_restore)
        result = work.run_file(
            layout, config, type="Feature", title="Refusal", description="d", on=TODAY, dry_run=False
        )
    assert len(attempts) == 1, cause
    assert sleeps == [], cause
    assert not result.application.ok, cause
    assert not result.application.snapshot_retryable, cause
    if cause != "rollback":
        assert result.application.created_directories == (), cause
    if cause == "collision":
        assert result.plan.filing.target.read_bytes() == b"foreign page"


def test_dry_filing_does_not_apply_or_wait(tmp_path, monkeypatch):
    import time

    layout, config = seeded_workspace(tmp_path)

    def unexpected(*args, **kwargs):
        raise AssertionError("dry run attempted effects or backoff")

    monkeypatch.setattr(work, "apply_mutation", unexpected)
    monkeypatch.setattr(time, "sleep", unexpected)
    result = work.run_file(layout, config, type="Feature", title="Dry", description="d", on=TODAY)
    assert result.application is None
    assert not result.plan.filing.target.exists()


@pytest.mark.parametrize("write_number", range(1, 5))
def test_rollback_removes_absent_lane_and_restores_owned_files(tmp_path, monkeypatch, write_number):
    import shutil

    from graph_works_core.workspace import transactions

    # Fail after each actual write, including after the page and index exist.
    layout = _workspace(tmp_path / str(write_number))
    lane = layout.bundle_dir / "work"
    if lane.exists():
        shutil.rmtree(lane)
    before_log = (layout.bundle_dir / "log.md").read_bytes()
    real = transactions._commit_effect
    writes = []

    def fail_after_write(*args, **kwargs):
        real(*args, **kwargs)
        if args[1].kind == "write":
            writes.append(args[1].member)
            if len(writes) == write_number:
                raise OSError("injected after owned write")

    with monkeypatch.context() as patch:
        patch.setattr(transactions, "_commit_effect", fail_after_write)
        result = work.run_file(
            layout, _config(layout), type="Feature", title="Absent lane", description="d", on=TODAY, dry_run=False
        )
    assert len(writes) == write_number
    assert result.application.rolled_back, result.application.failures
    assert not lane.exists()
    assert (layout.bundle_dir / "log.md").read_bytes() == before_log
