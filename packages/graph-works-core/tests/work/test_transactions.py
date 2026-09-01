"""Durable, atomic application of path-native work mutations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from _transaction_helpers import _plan, _snapshot, _workspace
from graph_works_core.work import MutationApplication, apply_mutation
from graph_works_core.work import transactions as public_transactions
from graph_works_core.workspace import anchors, transactions
from okf_ext.moves import Move
from okf_io import load_bundle
from work_tracker_okf.indexes import plan_indexes as path_plan_indexes
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import (
    DirectoryPrecondition,
    MutationRefusal,
    PlannedWrite,
    WorkMutationPlan,
    directory_manifest_digest,
)
from work_tracker_okf.reparent import plan_reparent


@pytest.fixture(params=["posix", "windows"], autouse=True)
def tier(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run every test in this module under both durability tiers.

    `_WindowsAnchor` is path join-and-re-validate, which runs correctly on
    POSIX -- so the weak tier gets continuous logic coverage here even though
    no Windows CI exists (D-001).  Cases needing a genuinely POSIX-only
    primitive as their SUBJECT are xfailed via `_xfail_on_weak_tier`, and
    ADR-0042 names every one of them.

    The parametrization is unidirectional by construction, and the `skipif`
    below is that asymmetry made explicit rather than left to fail as an
    `AttributeError`.  The weak tier is pure path logic and runs anywhere; the
    strong tier needs `openat`, `flock` and `os.O_DIRECTORY`, which Windows
    does not have at all.  Forcing the weak tier from POSIX works.  Forcing
    the strong tier from Windows cannot -- not an oversight, an absence.  A
    POSIX host still runs both arms exactly as D-001 intended.

    The two delegators patched here are the ones `transactions`' own call
    sites resolve through this module's globals -- the same property the
    `transactions.py:290-305` comment block exists to protect.  Patching
    `anchors.open_anchor` instead would NOT bite.
    """
    if request.param == "posix" and sys.platform == "win32":
        pytest.skip("the strong tier needs openat/flock/os.O_DIRECTORY, none of which exist on Windows")
    platform_name = "win32" if request.param == "windows" else "linux"
    monkeypatch.setattr(
        transactions,
        "_open_root",
        lambda root: anchors.open_anchor(root, platform_name=platform_name),
    )
    monkeypatch.setattr(
        transactions,
        "_open_absolute_directory",
        lambda path: anchors.open_absolute_anchor(path, platform_name=platform_name),
    )
    return request.param


def _xfail_on_weak_tier(request: pytest.FixtureRequest, reason: str) -> None:
    """Mark the current test xfail when it is running under the weak tier."""
    if request.getfixturevalue("tier") == "windows":
        request.node.add_marker(pytest.mark.xfail(strict=True, reason=reason))


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_public_work_transaction_surface_reexports_the_workspace_executor() -> None:
    assert public_transactions.apply_mutation is apply_mutation
    assert public_transactions.MutationApplication is MutationApplication


def _states(journal: Path) -> list[str]:
    return [json.loads(line)["state"] for line in journal.read_text(encoding="utf-8").splitlines()]


def _write_item(
    root: Path,
    path: str,
    *,
    type: str,
    affects: tuple[str, ...] = (),
    depends_on: tuple[dict[str, str], ...] = (),
) -> None:
    page = root / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    affects_block = "".join(f"  - {entry}\n" for entry in affects)
    depends_block = "".join(
        "  - " + ", ".join(f"{key}: {value}" for key, value in sorted(edge.items())).join(("{", "}")) + "\n"
        for edge in depends_on
    )
    page.write_text(
        "---\n"
        f"type: {type}\n"
        f"title: {path.rsplit('/', 1)[-1]}\n"
        "description: D\n"
        "status: stable\n"
        "work_status: open\n"
        "phase: design\n"
        "effort: small\n"
        + (f"affects:\n{affects_block}" if affects else "affects: []\n")
        + (f"depends_on:\n{depends_block}" if depends_on else "")
        + "opened: 2026-08-22\n"
        "updated: 2026-08-22\n"
        "---\n\n"
        "## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
    )


def test_success_applies_deterministic_effects_and_records_complete_journal(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    deleted = layout.bundle_dir / "work/delete.bin"
    source.parent.mkdir()
    source.write_bytes(b"source")
    deleted.write_bytes(b"delete")
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source.bin", "work/destination/source.bin", is_asset=True),),
        writes=(PlannedWrite("work/destination/final.bin", None, b"final"),),
        deletes=("work/delete.bin",),
        directory_preconditions=(DirectoryPrecondition("work/destination", None),),
    )

    result = apply_mutation(layout, plan)

    assert isinstance(result, MutationApplication)
    assert result.ok is True
    assert result.moved == (("work/source.bin", "work/destination/source.bin"),)
    assert result.written == ("work/destination/final.bin",)
    assert result.created_directories == ("work/destination",)
    assert result.warnings == ("opaque reference retained",)
    assert (layout.bundle_dir / "work/destination/source.bin").read_bytes() == b"source"
    assert (layout.bundle_dir / "work/destination/final.bin").read_bytes() == b"final"
    assert not source.exists()
    assert not deleted.exists()
    assert _states(result.journal) == ["planned", "applying", "validating", "complete"]


def test_real_reparent_plan_applies_through_transaction_and_reloads_final_path(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    release = "work/release-cutover"
    source = "work/bug-source"
    _write_item(layout.bundle_dir, release, type="Release")
    _write_item(layout.bundle_dir, source, type="Bug")
    (layout.bundle_dir / release / "children").mkdir(parents=True)
    (layout.bundle_dir / release / "children/_archive").mkdir()
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    plan = plan_reparent(bundle, load_items(bundle), source, release)

    result = apply_mutation(layout, plan)

    destination = f"{release}/children/bug-source"
    reloaded = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    assert plan.ok is True
    assert result.ok is True
    assert plan.validate_paths == (destination,)
    assert {item.path for item in reloaded} == {release, destination}
    assert not (layout.bundle_dir / f"{source}.md").exists()


def test_mid_apply_failure_restores_every_original_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    (work / "source/empty").mkdir(parents=True)
    (work / "source/data.bin").write_bytes(b"source")
    (work / "replace.bin").write_bytes(b"before")
    (work / "delete/empty").mkdir(parents=True)
    (work / "delete/data.bin").write_bytes(b"delete")
    (work / "link").symlink_to("source/data.bin")
    (work / "source/data.bin").chmod(0o640)
    before = _snapshot(layout.bundle_dir)
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source/data.bin", "work/destination/data.bin", is_asset=True),),
        writes=(PlannedWrite("work/replace.bin", _digest(b"before"), b"after"),),
        deletes=("work/delete",),
        directory_preconditions=(DirectoryPrecondition("work/destination", None),),
    )
    real_commit = transactions._commit_effect
    calls = 0

    def fail_after_move(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected commit failure")
        real_commit(*args, **kwargs)

    monkeypatch.setattr(transactions, "_commit_effect", fail_after_move)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert result.failures == ("apply failed: injected commit failure",)
    assert _snapshot(layout.bundle_dir) == before
    assert _states(result.journal) == ["planned", "applying", "rolling-back", "rolled-back"]


def test_delete_error_after_a_write_restores_both_targets(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    delete = work / "delete"
    delete.mkdir(parents=True)
    (delete / "unexpected.bin").write_bytes(b"keeps directory nonempty")
    replacement = work / "replace.bin"
    replacement.write_bytes(b"before")
    before = _snapshot(layout.bundle_dir)
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/replace.bin", _digest(b"before"), b"after"),),
        deletes=("work/delete",),
        directory_preconditions=(DirectoryPrecondition("work/delete", directory_manifest_digest(delete)),),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert result.failures[0].startswith("apply failed:")
    assert _snapshot(layout.bundle_dir) == before


def test_journal_and_backups_never_land_in_bundle(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    plan = _plan(layout, mkdirs=("work/new",), directory_preconditions=(DirectoryPrecondition("work/new", None),))

    result = apply_mutation(layout, plan)

    transaction_dir = result.journal.parent
    assert result.journal.relative_to(layout.cache_dir).parts[0] == "work-mutations"
    assert (transaction_dir / "backups").is_dir()
    assert (transaction_dir.parent / "executor.lock").is_file()
    assert not any(path.name in {"journal.jsonl", "backups"} for path in layout.bundle_dir.rglob("*"))


def test_executor_lock_serializes_snapshot_through_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    first = _plan(
        layout,
        mkdirs=("work/first",),
        directory_preconditions=(DirectoryPrecondition("work/first", None),),
    )
    second = _plan(
        layout,
        mkdirs=("work/second",),
        directory_preconditions=(DirectoryPrecondition("work/second", None),),
    )
    first_snapshot = threading.Event()
    release_first = threading.Event()
    second_snapshot = threading.Event()
    results: list[MutationApplication] = []
    real_snapshot = transactions._create_snapshot

    def pause_first(plan: WorkMutationPlan, *args: object, **kwargs: object):
        if "work/first" in plan.mkdirs:
            first_snapshot.set()
            assert release_first.wait(timeout=2)
        else:
            second_snapshot.set()
        return real_snapshot(plan, *args, **kwargs)

    monkeypatch.setattr(transactions, "_create_snapshot", pause_first)
    first_thread = threading.Thread(target=lambda: results.append(apply_mutation(layout, first)))
    second_thread = threading.Thread(target=lambda: results.append(apply_mutation(layout, second)))
    first_thread.start()
    assert first_snapshot.wait(timeout=2)
    second_thread.start()
    assert second_snapshot.wait(timeout=0.1) is False
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_snapshot.is_set()
    assert len(results) == 2
    assert all(result.ok for result in results)


def test_bundle_root_lock_survives_executor_lock_inode_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    first = _plan(
        layout,
        mkdirs=("work/first",),
        directory_preconditions=(DirectoryPrecondition("work/first", None),),
    )
    second = _plan(
        layout,
        mkdirs=("work/second",),
        directory_preconditions=(DirectoryPrecondition("work/second", None),),
    )
    first_snapshot = threading.Event()
    release_first = threading.Event()
    second_snapshot = threading.Event()
    results: list[MutationApplication] = []
    real_snapshot = transactions._create_snapshot

    def replace_lock_and_pause(plan: WorkMutationPlan, *args: object, **kwargs: object):
        if "work/first" in plan.mkdirs:
            lock = layout.cache_dir / "work-mutations/executor.lock"
            lock.rename(lock.with_name("executor-held.lock"))
            lock.write_bytes(b"replacement lock inode")
            first_snapshot.set()
            assert release_first.wait(timeout=2)
        else:
            second_snapshot.set()
        return real_snapshot(plan, *args, **kwargs)

    monkeypatch.setattr(transactions, "_create_snapshot", replace_lock_and_pause)
    first_thread = threading.Thread(target=lambda: results.append(apply_mutation(layout, first)))
    second_thread = threading.Thread(target=lambda: results.append(apply_mutation(layout, second)))
    first_thread.start()
    assert first_snapshot.wait(timeout=2)
    second_thread.start()
    assert second_snapshot.wait(timeout=0.1) is False
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_snapshot.is_set()
    assert len(results) == 2
    assert all(result.ok for result in results)


def test_bundle_root_locks_do_not_serialize_different_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_layout = _workspace(tmp_path / "first")
    second_layout = _workspace(tmp_path / "second")
    first = _plan(
        first_layout,
        mkdirs=("work/first",),
        directory_preconditions=(DirectoryPrecondition("work/first", None),),
    )
    second = _plan(
        second_layout,
        mkdirs=("work/second",),
        directory_preconditions=(DirectoryPrecondition("work/second", None),),
    )
    first_snapshot = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    results: list[MutationApplication] = []
    real_snapshot = transactions._create_snapshot

    def pause_first(plan: WorkMutationPlan, *args: object, **kwargs: object):
        if plan.root == first_layout.bundle_dir:
            first_snapshot.set()
            assert release_first.wait(timeout=2)
        return real_snapshot(plan, *args, **kwargs)

    monkeypatch.setattr(transactions, "_create_snapshot", pause_first)
    first_thread = threading.Thread(target=lambda: results.append(apply_mutation(first_layout, first)))

    def apply_second() -> None:
        results.append(apply_mutation(second_layout, second))
        second_done.set()

    second_thread = threading.Thread(target=apply_second)
    first_thread.start()
    assert first_snapshot.wait(timeout=2)
    second_thread.start()
    assert second_done.wait(timeout=2)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(results) == 2
    assert all(result.ok for result in results)
    assert (second_layout.bundle_dir / "work/second").is_dir()


def test_journal_records_every_effect_target_including_absent_nested_targets(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    source.parent.mkdir()
    source.write_bytes(b"source")
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source.bin", "work/destination/moved.bin", is_asset=True),),
        writes=(PlannedWrite("work/destination/written.bin", None, b"written"),),
        directory_preconditions=(DirectoryPrecondition("work/destination", None),),
    )

    result = apply_mutation(layout, plan)

    applying = json.loads(result.journal.read_text(encoding="utf-8").splitlines()[1])
    snapshots = {entry["member"]: entry["existed"] for entry in applying["snapshots"]}
    assert snapshots == {
        "work/destination": False,
        "work/destination/moved.bin": False,
        "work/destination/written.bin": False,
        "work/source.bin": True,
    }
    assert applying["backups"] == [
        {"member": "work/destination", "path": None},
        {"member": "work/source.bin", "path": "backups/000001"},
    ]
    assert (result.journal.parent / "backups/000001").read_bytes() == b"source"


@pytest.mark.parametrize("stale_kind", ["appeared", "manifest-changed"])
def test_stale_directory_precondition_refuses_without_live_effects(tmp_path: Path, stale_kind: str) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    source = work / "source"
    source.mkdir(parents=True)
    (source / "page.bin").write_bytes(b"planned")
    destination = work / "destination"
    conditions = (
        DirectoryPrecondition("work/source", directory_manifest_digest(source)),
        DirectoryPrecondition("work/destination", None),
    )
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source/page.bin", "work/destination/page.bin", is_asset=True),),
        directory_preconditions=conditions,
    )
    if stale_kind == "appeared":
        destination.mkdir()
        (destination / "late.bin").write_bytes(b"late")
    else:
        (source / "late.bin").write_bytes(b"late")
    before_apply = _snapshot(layout.bundle_dir)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "preflight failed:" in result.failures[0]
    assert "directory changed since planning" in result.failures[0]
    assert _snapshot(layout.bundle_dir) == before_apply
    assert _states(result.journal) == ["planned", "rolling-back", "rolled-back"]


def test_directory_preconditions_use_the_exported_manifest_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source"
    source.mkdir(parents=True)
    (source / "page.bin").write_bytes(b"source")
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        directory_preconditions=(
            DirectoryPrecondition("work/source", directory_manifest_digest(source)),
            DirectoryPrecondition("work/destination", None),
        ),
    )
    calls: list[Path] = []
    real_digest = transactions.directory_manifest_digest

    def record_digest(path: Path) -> str:
        calls.append(path)
        return real_digest(path)

    monkeypatch.setattr(transactions, "directory_manifest_digest", record_digest)

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert len(calls) == 2
    assert all(path.is_relative_to(result.journal.parent) for path in calls)


def test_unsafe_symlink_escape_refuses_before_touching_bundle_or_external_target(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.bin"
    sentinel.write_bytes(b"outside")
    work = layout.bundle_dir / "work"
    work.mkdir()
    (work / "escape").symlink_to(external, target_is_directory=True)
    before = _snapshot(layout.bundle_dir)
    plan = _plan(layout, writes=(PlannedWrite("work/escape/sentinel.bin", None, b"overwritten"),))

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "unsafe ancestor" in result.failures[0]
    assert _snapshot(layout.bundle_dir) == before
    assert sentinel.read_bytes() == b"outside"


def test_preconditions_are_rechecked_after_snapshot_before_first_live_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source"
    source.mkdir(parents=True)
    page = source / "page.bin"
    page.write_bytes(b"planned")
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source/page.bin", "work/destination/page.bin", is_asset=True),),
        directory_preconditions=(
            DirectoryPrecondition("work/source", directory_manifest_digest(source)),
            DirectoryPrecondition("work/destination", None),
        ),
    )
    real_snapshot = transactions._create_snapshot

    def mutate_after_snapshot(*args: object, **kwargs: object):
        snapshot = real_snapshot(*args, **kwargs)
        page.write_bytes(b"late external change")
        return snapshot

    monkeypatch.setattr(transactions, "_create_snapshot", mutate_after_snapshot)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "directory changed since planning" in result.failures[0]
    assert page.read_bytes() == b"late external change"
    assert not (layout.bundle_dir / "work/destination").exists()


def test_successful_write_preserves_the_effective_preimage_mode(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    target.chmod(0o640)
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert target.read_bytes() == b"after"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_distinct_source_member_supplies_final_bytes_precondition_and_mode(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    source_directory = layout.bundle_dir / "work/source"
    source_directory.mkdir(parents=True)
    source = source_directory / "page.md"
    source.write_bytes(b"before")
    source.chmod(0o600)
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        writes=(
            PlannedWrite(
                "work/destination/page.md",
                _digest(b"before"),
                b"composed final bytes",
                source_member="work/source/page.md",
            ),
        ),
        deletes=("work/source/page.md", "work/source"),
        directory_preconditions=(
            DirectoryPrecondition("work/source", directory_manifest_digest(source_directory)),
            DirectoryPrecondition("work/destination", None),
        ),
    )

    result = apply_mutation(layout, plan)

    destination = layout.bundle_dir / "work/destination/page.md"
    assert result.ok is True
    assert destination.read_bytes() == b"composed final bytes"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not source_directory.exists()


def test_new_write_requires_target_absence_at_final_preflight(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/new.bin"
    target.parent.mkdir()
    plan = _plan(layout, writes=(PlannedWrite("work/new.bin", None, b"planned"),))
    target.write_bytes(b"late")

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "now exists" in result.failures[0]
    assert target.read_bytes() == b"late"


def test_explicit_directory_conditions_do_not_lock_unrelated_work_tree_bytes(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source"
    source.mkdir(parents=True)
    (source / "page.bin").write_bytes(b"source")
    unrelated = layout.bundle_dir / "work/unrelated"
    unrelated.mkdir()
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source/page.bin", "work/destination/page.bin", is_asset=True),),
        directory_preconditions=(
            DirectoryPrecondition("work/source", directory_manifest_digest(source)),
            DirectoryPrecondition("work/destination", None),
        ),
    )
    (unrelated / "late.bin").write_bytes(b"not part of this plan")

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert (unrelated / "late.bin").read_bytes() == b"not part of this plan"


def test_absent_implicit_mkdir_ancestor_is_revalidated_without_effects(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    plan = _plan(
        layout,
        mkdirs=("work/new/deep",),
        directory_preconditions=(
            DirectoryPrecondition("work/new", None),
            DirectoryPrecondition("work/new/deep", None),
        ),
    )
    late = layout.bundle_dir / "work/new"
    late.mkdir(parents=True)
    (late / "authored.bin").write_bytes(b"authored")
    before = _snapshot(layout.bundle_dir)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert _snapshot(layout.bundle_dir) == before


def test_direct_moves_preserve_symlink_target_and_empty_directory_effects(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    _xfail_on_weak_tier(
        request,
        "Moving a symlink member must succeed for this plan; the "
        "windows-revalidated tier refuses every planned symlink member "
        "categorically at preflight (D-002's backstop), so this move can "
        "never succeed on the weak tier.",
    )
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    source = work / "source"
    (source / "empty").mkdir(parents=True)
    (source / "target.bin").write_bytes(b"target")
    (source / "link.bin").symlink_to("target.bin")
    plan = _plan(
        layout,
        mkdirs=("work/destination", "work/destination/empty"),
        moves=(
            Move("work/source/link.bin", "work/destination/link.bin", is_asset=True),
            Move("work/source/target.bin", "work/destination/target.bin", is_asset=True),
        ),
        deletes=("work/source/empty", "work/source"),
        directory_preconditions=(
            DirectoryPrecondition("work/source", directory_manifest_digest(source)),
            DirectoryPrecondition("work/destination", None),
        ),
    )

    result = apply_mutation(layout, plan)

    destination = work / "destination"
    assert result.ok is True
    assert (destination / "empty").is_dir()
    assert (destination / "link.bin").is_symlink()
    assert destination.joinpath("link.bin").readlink() == Path("target.bin")
    assert destination.joinpath("link.bin").read_bytes() == b"target"
    assert not source.exists()


def test_snapshot_and_rollback_helpers_are_iterative_for_deep_owned_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    cursor = source
    for _ in range(80):
        cursor /= "d"
        cursor.mkdir()
    (cursor / "leaf.bin").write_bytes(b"leaf")
    destination = tmp_path / "destination"
    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(120)
        transactions._copy_entry(source, destination)
        copied = destination.joinpath(*(Path("d") for _ in range(80)), "leaf.bin")
        assert copied.read_bytes() == b"leaf"
        transactions._remove_entry(destination)
    finally:
        sys.setrecursionlimit(previous_limit)

    assert not destination.exists()


def test_targeted_index_validation_failure_restores_original_index(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    work.mkdir()
    _write_item(layout.bundle_dir, "work/feature-target", type="Feature")
    (work / "feature-target").mkdir()
    index = work / "index.md"
    index.write_text("# Human prose\n", encoding="utf-8")
    before = _snapshot(layout.bundle_dir)
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/index.md", _digest(b"# Human prose\n"), b"# Human prose\n"),),
        validate_paths=("work/feature-target",),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert result.failures == ("validation failed: work/index.md: generated direct-descendant inventory is stale",)
    assert _snapshot(layout.bundle_dir) == before
    assert _states(result.journal) == ["planned", "applying", "validating", "rolling-back", "rolled-back"]


def test_targeted_index_validation_reads_the_anchored_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    work.mkdir()
    _write_item(layout.bundle_dir, "work/feature-target", type="Feature")
    (work / "feature-target").mkdir()
    index = work / "index.md"
    index.write_text("# Human prose\n", encoding="utf-8")
    decoy = layout.bundle_dir.with_name("okf-index-decoy")
    held = layout.bundle_dir.with_name("okf-index-held")
    shutil.copytree(layout.bundle_dir, decoy, symlinks=True)
    decoy.joinpath("work/index.md").write_text(
        "# Human prose\n\n"
        "<!-- graph-works:work-items:start -->\n"
        "- [Feature: feature-target](feature-target.md) — open · design\n"
        "<!-- graph-works:work-items:end -->\n",
        encoding="utf-8",
    )
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/index.md", _digest(b"# Human prose\n"), b"# Human prose\n"),),
        validate_paths=("work/feature-target",),
    )
    real_plan_indexes = path_plan_indexes
    real_read_text = Path.read_text
    planned = False

    def arm_after_planning(*args: object, **kwargs: object):
        nonlocal planned
        result = real_plan_indexes(*args, **kwargs)
        planned = True
        return result

    def swap_only_while_reading_index(path: Path, *args: object, **kwargs: object) -> str:
        if planned and path == index:
            layout.bundle_dir.rename(held)
            decoy.rename(layout.bundle_dir)
            try:
                return real_read_text(path, *args, **kwargs)
            finally:
                layout.bundle_dir.rename(decoy)
                held.rename(layout.bundle_dir)
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(transactions, "plan_indexes", arm_after_planning, raising=False)
    monkeypatch.setattr(Path, "read_text", swap_only_while_reading_index)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert result.failures == ("validation failed: work/index.md: generated direct-descendant inventory is stale",)
    assert index.read_bytes() == b"# Human prose\n"


def test_validate_path_does_not_broaden_to_an_untouched_lane_index(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    work.mkdir()
    page = work / "feature-target.md"
    before_page = (
        b"---\ntype: Feature\ntitle: Target\ndescription: D\nstatus: stable\n"
        b"work_status: open\nphase: design\neffort: small\naffects: []\n"
        b"opened: 2026-08-22\nupdated: 2026-08-22\n---\n\n## Plan\n\n"
        b"| Action | Done when | Rationale |\n| --- | --- | --- |\n"
    )
    page.write_bytes(before_page)
    (work / "feature-target").mkdir()
    (work / "index.md").write_text("# Pre-existing stale index\n", encoding="utf-8")
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/feature-target.md", _digest(before_page), before_page + b"\nBody\n"),),
        validate_paths=("work/feature-target",),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert page.read_bytes() == before_page + b"\nBody\n"


def test_registered_references_index_is_not_mistaken_for_a_lane_index(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    work.mkdir()
    _write_item(layout.bundle_dir, "work/feature-target", type="Feature")
    references = work / "feature-target/references"
    references.mkdir(parents=True)
    attachment = references / "index.md"
    attachment.write_bytes(b"# Authored attachment\n")
    plan = _plan(
        layout,
        writes=(
            PlannedWrite(
                "work/feature-target/references/index.md",
                _digest(b"# Authored attachment\n"),
                b"# Repaired authored attachment\n",
            ),
        ),
        validate_paths=("work/feature-target",),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert attachment.read_bytes() == b"# Repaired authored attachment\n"


def test_unrelated_malformed_item_does_not_enter_targeted_postconditions(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    malformed = layout.bundle_dir / "work/feature-unrelated.md"
    malformed.parent.mkdir()
    malformed.write_bytes(b"not frontmatter")
    plan = _plan(
        layout,
        mkdirs=("work/new-directory",),
        directory_preconditions=(DirectoryPrecondition("work/new-directory", None),),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert (layout.bundle_dir / "work/new-directory").is_dir()


def test_removed_lane_index_is_not_revalidated_as_a_final_index(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    removed_lane = layout.bundle_dir / "work/removed-lane"
    removed_lane.mkdir(parents=True)
    (removed_lane / "index.md").write_text("# Removed lane\n", encoding="utf-8")
    plan = _plan(
        layout,
        deletes=("work/removed-lane/index.md", "work/removed-lane"),
        directory_preconditions=(DirectoryPrecondition("work/removed-lane", directory_manifest_digest(removed_lane)),),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert not removed_lane.exists()


def test_rollback_failure_reports_original_and_recovery_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise OSError("commit exploded")

    def fail_restore(*_args: object, **_kwargs: object) -> None:
        raise OSError("restore exploded")

    monkeypatch.setattr(transactions, "_commit_effect", fail_commit)
    monkeypatch.setattr(transactions, "_restore_snapshot", fail_restore)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert result.failures == (
        "apply failed: commit exploded",
        "rollback failed: restore exploded",
    )
    assert _states(result.journal) == ["planned", "applying", "rolling-back", "rolled-back"]
    final_record = json.loads(result.journal.read_text(encoding="utf-8").splitlines()[-1])
    assert final_record["complete"] is False


def test_complete_journal_failure_rolls_back_live_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    before = _snapshot(layout.bundle_dir)
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    real_append = transactions._append_journal

    def fail_complete(journal: Path, state: str, **details: object) -> None:
        if state == "complete":
            raise OSError("journal disk full")
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", fail_complete)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert result.failures == ("validation failed: journal disk full",)
    assert _snapshot(layout.bundle_dir) == before
    assert _states(result.journal) == ["planned", "applying", "validating", "rolling-back", "rolled-back"]


def test_late_absent_write_target_is_never_overwritten_or_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/late.bin"
    target.parent.mkdir()
    plan = _plan(layout, writes=(PlannedWrite("work/late.bin", None, b"transaction"),))
    real_append = transactions._append_journal

    def introduce_late_target(journal: Path, state: str, **details: object) -> None:
        if state == "applying":
            target.write_bytes(b"external")
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", introduce_late_target)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "changed since planning" in result.failures[0]
    assert target.read_bytes() == b"external"


def test_late_existing_write_preimage_is_cas_refused_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/existing.bin"
    target.parent.mkdir()
    target.write_bytes(b"planned-before")
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/existing.bin", _digest(b"planned-before"), b"transaction"),),
    )
    real_append = transactions._append_journal

    def replace_preimage(journal: Path, state: str, **details: object) -> None:
        if state == "applying":
            target.write_bytes(b"external-late")
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", replace_preimage)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "changed since planning" in result.failures[0]
    assert target.read_bytes() == b"external-late"


def test_late_absent_mkdir_is_refused_without_merging_or_removing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/new"
    target.parent.mkdir()
    plan = _plan(
        layout,
        mkdirs=("work/new",),
        directory_preconditions=(DirectoryPrecondition("work/new", None),),
    )
    real_append = transactions._append_journal

    def create_directory(journal: Path, state: str, **details: object) -> None:
        if state == "applying":
            target.mkdir()
            (target / "external.bin").write_bytes(b"external")
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", create_directory)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "directory changed since planning" in result.failures[0]
    assert (target / "external.bin").read_bytes() == b"external"


def test_late_delete_preimage_is_refused_without_unlinking_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/delete.bin"
    target.parent.mkdir()
    target.write_bytes(b"planned-before")
    plan = _plan(layout, deletes=("work/delete.bin",))
    real_append = transactions._append_journal

    def replace_preimage(journal: Path, state: str, **details: object) -> None:
        if state == "applying":
            target.write_bytes(b"external-late")
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", replace_preimage)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "changed since planning" in result.failures[0]
    assert target.read_bytes() == b"external-late"


def test_ancestor_swap_after_final_preflight_cannot_escape_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    owned = layout.bundle_dir / "work/owned"
    owned.mkdir(parents=True)
    (owned / "kept.bin").write_bytes(b"kept")
    external = tmp_path / "external"
    external.mkdir()
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/owned/new.bin", None, b"transaction"),),
        directory_preconditions=(DirectoryPrecondition("work/owned", directory_manifest_digest(owned)),),
    )
    real_append = transactions._append_journal

    def swap_ancestor(journal: Path, state: str, **details: object) -> None:
        if state == "applying":
            owned.rename(layout.bundle_dir / "work/owned-held")
            owned.symlink_to(external, target_is_directory=True)
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", swap_ancestor)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert "unsafe ancestor" in result.failures[0]
    assert not (external / "new.bin").exists()


def test_symlink_move_cannot_make_a_later_effect_traverse_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    _xfail_on_weak_tier(
        request,
        "The windows-revalidated tier refuses the planned symlink member "
        "categorically at preflight (D-002's backstop), before the moved "
        "symlink is ever created -- so the later-effect traversal this test "
        "guards against cannot be constructed on the weak tier.",
    )
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    physical = work / "physical"
    physical.mkdir(parents=True)
    child = physical / "child.bin"
    child.write_bytes(b"physical-before")
    source_link = work / "source-link"
    source_link.symlink_to("physical", target_is_directory=True)
    plan = _plan(
        layout,
        moves=(Move("work/source-link", "work/link", is_asset=True),),
        writes=(PlannedWrite("work/link/child.bin", None, b"escaped-write"),),
    )
    monkeypatch.setattr(transactions, "_validate_postconditions", lambda *_args: ("forced validation failure",))

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert "unsafe ancestor" in result.failures[0]
    assert child.read_bytes() == b"physical-before"
    assert source_link.is_symlink()
    assert not (work / "link").exists()


def test_item_conditions_are_kinded_pairs_for_parent_and_dependency_problems(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/bug-orphan"
    _write_item(
        layout.bundle_dir,
        path,
        type="Bug",
        depends_on=({"path": "work/bug-absent", "blocks": "execute", "needs": "resolved"},),
    )
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    by_path = {item.path: item for item in items}

    conditions = transactions._item_conditions(by_path[path], by_path)

    assert [kind for kind, _message in conditions] == ["dependency-missing"]
    assert "work/bug-absent" in conditions[0][1]


def test_baseline_capture_counts_findings_under_their_post_move_paths(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "work/bug-source"
    release = "work/release-cutover"
    _write_item(layout.bundle_dir, release, type="Release")
    _write_item(layout.bundle_dir, path, type="Bug", affects=("src/gone.py",))
    (layout.bundle_dir / release / "children").mkdir(parents=True)
    (layout.bundle_dir / release / "children/_archive").mkdir()
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    plan = plan_reparent(bundle, load_items(bundle), path, release)
    root = transactions._open_root(layout.bundle_dir)
    try:
        state = transactions._capture_validation_state(layout, plan, root, repo_root=repo)
    finally:
        root.close()

    destination = f"{release}/children/bug-source.md"
    assert state.findings[(destination, "targets.affects-missing")] == 1
    assert (f"{path}.md", "targets.affects-missing") not in state.findings


def test_pre_existing_error_on_a_targeted_item_is_excused_and_reported(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "work/bug-target"
    _write_item(layout.bundle_dir, path, type="Bug", affects=("src/gone.py",))
    (layout.bundle_dir / path).mkdir()
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    after = before.replace(b"updated: 2026-08-22", b"updated: 2026-08-23")
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{path}.md", _digest(before), after),),
        validate_paths=(path,),
    )

    result = apply_mutation(layout, plan, repo_root=repo)

    assert result.ok is True
    assert result.failures == ()
    assert page.read_bytes() == after
    assert any("targets.affects-missing" in warning and "pre-existing" in warning for warning in result.warnings)
    assert any("gw lint" in warning for warning in result.warnings)


def test_unrelated_excused_note_does_not_fabricate_a_pre_existing_summary(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-clean"
    _write_item(layout.bundle_dir, path, type="Feature")
    (layout.bundle_dir / path).mkdir()
    plan = _plan(layout, validate_paths=(path,))
    root = transactions._open_root(layout.bundle_dir)
    excused = ["baseline capture failed, falling back to absolute postcondition gate for this mutation: boom"]
    try:
        failures = transactions._validate_postconditions(
            layout,
            plan,
            root,
            baseline=None,
            excused=excused,
        )
    finally:
        root.close()

    assert failures == ()
    assert excused == ["baseline capture failed, falling back to absolute postcondition gate for this mutation: boom"]


def test_targeted_malformed_yaml_is_a_postcondition_failure_and_rolls_back(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-target"
    _write_item(layout.bundle_dir, path, type="Feature")
    (layout.bundle_dir / path).mkdir()
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    malformed = b"---\ntype: [\n---\n\n## Plan\n"
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{path}.md", _digest(before), malformed),),
        validate_paths=(path,),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert "frontmatter.unparseable" in result.failures[0]
    assert page.read_bytes() == before


def test_targeted_schema_error_is_a_postcondition_failure_and_rolls_back(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-target"
    _write_item(layout.bundle_dir, path, type="Feature")
    (layout.bundle_dir / path).mkdir()
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    invalid = before.replace(b"description: D\n", b"")
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{path}.md", _digest(before), invalid),),
        validate_paths=(path,),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert "schemas.invalid" in result.failures[0]
    assert page.read_bytes() == before


@pytest.mark.parametrize("force_rollback", [False, True])
def test_live_file_and_directory_fsync_precede_terminal_journal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_rollback: bool
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    events: list[str] = []
    real_file_sync = transactions._fsync_live_file
    real_directory_sync = transactions._fsync_live_directory
    real_append = transactions._append_journal

    def record_file_sync(*args: object, **kwargs: object) -> None:
        events.append("live-file-fsync")
        real_file_sync(*args, **kwargs)

    def record_directory_sync(*args: object, **kwargs: object) -> None:
        events.append("live-directory-fsync")
        real_directory_sync(*args, **kwargs)

    def record_journal(journal: Path, state: str, **details: object) -> None:
        events.append(state)
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_fsync_live_file", record_file_sync)
    monkeypatch.setattr(transactions, "_fsync_live_directory", record_directory_sync)
    monkeypatch.setattr(transactions, "_append_journal", record_journal)
    if force_rollback:
        monkeypatch.setattr(transactions, "_validate_postconditions", lambda *_args: ("forced",))

    result = apply_mutation(layout, plan)

    terminal = "rolled-back" if force_rollback else "complete"
    assert result.rolled_back is force_rollback
    assert "live-file-fsync" in events
    assert "live-directory-fsync" in events
    assert max(events.index("live-file-fsync"), events.index("live-directory-fsync")) < events.index(terminal)


def test_real_reparent_preserves_mapped_nested_and_empty_directory_modes(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    release = "work/release-cutover"
    source = "work/bug-source"
    _write_item(layout.bundle_dir, release, type="Release")
    _write_item(layout.bundle_dir, source, type="Bug")
    source_root = layout.bundle_dir / source
    nested = source_root / "nested"
    empty = nested / "empty"
    empty.mkdir(parents=True)
    source_root.chmod(0o700)
    nested.chmod(0o710)
    empty.chmod(0o750)
    (layout.bundle_dir / release / "children").mkdir(parents=True)
    (layout.bundle_dir / release / "children/_archive").mkdir()
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    plan = plan_reparent(bundle, load_items(bundle), source, release)

    result = apply_mutation(layout, plan)

    destination = layout.bundle_dir / release / "children/bug-source"
    assert result.ok is True
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.joinpath("nested").stat().st_mode) == 0o710
    assert stat.S_IMODE(destination.joinpath("nested/empty").stat().st_mode) == 0o750


def test_mapped_directory_mode_change_after_snapshot_refuses_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    release = "work/release-cutover"
    source = "work/bug-source"
    _write_item(layout.bundle_dir, release, type="Release")
    _write_item(layout.bundle_dir, source, type="Bug")
    source_root = layout.bundle_dir / source
    source_root.mkdir()
    source_root.chmod(0o700)
    (layout.bundle_dir / release / "children").mkdir(parents=True)
    (layout.bundle_dir / release / "children/_archive").mkdir()
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    plan = plan_reparent(bundle, load_items(bundle), source, release)
    real_snapshot = transactions._create_snapshot

    def chmod_after_snapshot(*args: object, **kwargs: object):
        snapshot = real_snapshot(*args, **kwargs)
        source_root.chmod(0o755)
        return snapshot

    monkeypatch.setattr(transactions, "_create_snapshot", chmod_after_snapshot)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert "directory mode changed since planning" in result.failures[0]
    assert stat.S_IMODE(source_root.stat().st_mode) == 0o755


def test_rollback_is_not_reported_complete_when_snapshot_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    monkeypatch.setattr(transactions, "_validate_postconditions", lambda *_args: ("forced",))
    monkeypatch.setattr(transactions, "_restore_snapshot", lambda *_args, **_kwargs: None)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert any("rollback verification failed" in failure for failure in result.failures)
    assert target.read_bytes() == b"after"


def test_delete_takes_custody_before_check_and_preserves_a_recreated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/delete.bin"
    target.parent.mkdir()
    target.write_bytes(b"planned-before")
    plan = _plan(layout, deletes=("work/delete.bin",))
    armed = False
    injected = False
    real_append = transactions._append_journal
    real_fingerprint = transactions._entry_fingerprint_at

    def arm_after_preflight(journal: Path, state: str, **details: object) -> None:
        nonlocal armed
        real_append(journal, state, **details)
        if state == "applying":
            armed = True

    def recreate_after_custody(root_fd: int, member: str) -> str:
        nonlocal injected
        fingerprint = real_fingerprint(root_fd, member)
        if armed and not injected and (member == "work/delete.bin" or member.endswith(".quarantine")):
            injected = True
            if member == "work/delete.bin":
                target.rename(target.with_name("review-captured-delete.bin"))
            target.write_bytes(b"external-recreated")
        return fingerprint

    monkeypatch.setattr(transactions, "_append_journal", arm_after_preflight)
    monkeypatch.setattr(transactions, "_entry_fingerprint_at", recreate_after_custody)

    result = apply_mutation(layout, plan)

    quarantines = tuple(target.parent.glob(".delete.bin.*.quarantine"))
    assert result.ok is False
    assert result.rolled_back is False
    assert target.read_bytes() == b"external-recreated"
    assert [path.read_bytes() for path in quarantines] == [b"planned-before"]
    assert any("quarantine" in failure for failure in result.failures)


def test_direct_move_takes_source_custody_before_check_and_preserves_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    destination = layout.bundle_dir / "work/destination.bin"
    source.parent.mkdir()
    source.write_bytes(b"same-bytes")
    plan = _plan(layout, moves=(Move("work/source.bin", "work/destination.bin", is_asset=True),))
    armed = False
    injected = False
    real_append = transactions._append_journal
    real_fingerprint = transactions._entry_fingerprint_at

    def arm_after_preflight(journal: Path, state: str, **details: object) -> None:
        nonlocal armed
        real_append(journal, state, **details)
        if state == "applying":
            armed = True

    def recreate_after_custody(root_fd: int, member: str) -> str:
        nonlocal injected
        fingerprint = real_fingerprint(root_fd, member)
        if armed and not injected and (member == "work/source.bin" or member.endswith(".quarantine")):
            injected = True
            if member == "work/source.bin":
                source.rename(source.with_name("review-captured-source.bin"))
            source.write_bytes(b"same-bytes")
        return fingerprint

    monkeypatch.setattr(transactions, "_append_journal", arm_after_preflight)
    monkeypatch.setattr(transactions, "_entry_fingerprint_at", recreate_after_custody)

    result = apply_mutation(layout, plan)

    quarantines = tuple(source.parent.glob(".source.bin.*.quarantine"))
    assert result.ok is False
    assert result.rolled_back is False
    assert source.read_bytes() == b"same-bytes"
    assert not destination.exists()
    assert [path.read_bytes() for path in quarantines] == [b"same-bytes"]
    assert any("quarantine" in failure for failure in result.failures)


def test_direct_move_destination_open_failure_preserves_a_recreated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    destination = layout.bundle_dir / "work/destination/child.bin"
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"captured")
    plan = _plan(
        layout,
        moves=(Move("work/source.bin", "work/destination/child.bin", is_asset=True),),
    )
    armed = False
    injected = False
    real_append = transactions._append_journal
    real_open_parent = transactions._open_parent

    def arm_after_preflight(journal: Path, state: str, **details: object) -> None:
        nonlocal armed
        real_append(journal, state, **details)
        if state == "applying":
            armed = True

    def fail_destination_parent(root_fd: int, member: str, **kwargs: object):
        nonlocal injected
        if armed and not injected and member == "work/destination/child.bin":
            injected = True
            source.write_bytes(b"external-source")
            raise OSError("injected destination-parent open failure")
        return real_open_parent(root_fd, member, **kwargs)

    monkeypatch.setattr(transactions, "_append_journal", arm_after_preflight)
    monkeypatch.setattr(transactions, "_open_parent", fail_destination_parent)

    result = apply_mutation(layout, plan)

    quarantines = tuple(source.parent.glob(".source.bin.*.quarantine"))
    assert result.ok is False
    assert result.rolled_back is False
    assert source.read_bytes() == b"external-source"
    assert [path.read_bytes() for path in quarantines] == [b"captured"]
    assert not destination.exists()
    assert any("custody" in failure or "quarantine" in failure for failure in result.failures)


def test_direct_move_destination_open_failure_restores_custody_without_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    destination = layout.bundle_dir / "work/destination/child.bin"
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"captured")
    plan = _plan(
        layout,
        moves=(Move("work/source.bin", "work/destination/child.bin", is_asset=True),),
    )
    armed = False
    injected = False
    real_append = transactions._append_journal
    real_open_parent = transactions._open_parent

    def arm_after_preflight(journal: Path, state: str, **details: object) -> None:
        nonlocal armed
        real_append(journal, state, **details)
        if state == "applying":
            armed = True

    def fail_destination_parent(root_fd: int, member: str, **kwargs: object):
        nonlocal injected
        if armed and not injected and member == "work/destination/child.bin":
            injected = True
            raise OSError("injected destination-parent open failure")
        return real_open_parent(root_fd, member, **kwargs)

    monkeypatch.setattr(transactions, "_append_journal", arm_after_preflight)
    monkeypatch.setattr(transactions, "_open_parent", fail_destination_parent)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert source.read_bytes() == b"captured"
    assert tuple(source.parent.glob(".source.bin.*.quarantine")) == ()
    assert not destination.exists()


def test_direct_move_single_conflict_does_not_claim_an_absent_destination_is_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    destination = layout.bundle_dir / "work/destination.bin"
    source.parent.mkdir()
    source.write_bytes(b"captured")
    plan = _plan(layout, moves=(Move("work/source.bin", "work/destination.bin", is_asset=True),))
    injected = False
    real_rename = transactions._rename_noreplace_at

    def recreate_source_after_move(
        source_fd: int,
        source_name: str,
        destination_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal injected
        real_rename(source_fd, source_name, destination_fd, destination_name)
        if not injected and source_name.startswith(".source.bin.") and destination_name == "destination.bin":
            injected = True
            source.write_bytes(b"external-source")

    monkeypatch.setattr(transactions, "_rename_noreplace_at", recreate_source_after_move)

    result = apply_mutation(layout, plan)

    quarantines = tuple(source.parent.glob(".source.bin.*.quarantine"))
    assert result.ok is False
    assert result.rolled_back is False
    assert source.read_bytes() == b"external-source"
    assert [path.read_bytes() for path in quarantines] == [b"captured"]
    assert not destination.exists()
    assert not any(
        failure.startswith("rollback verification failed: work/destination.bin: external entry preserved")
        for failure in result.failures
    )


def test_existing_write_mismatch_recovery_never_exchanges_over_a_recreated_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/existing.bin"
    target.parent.mkdir()
    target.write_bytes(b"planned-before")
    plan = _plan(
        layout,
        writes=(PlannedWrite("work/existing.bin", _digest(b"planned-before"), b"transaction"),),
    )
    armed = False
    injected = False
    real_append = transactions._append_journal
    real_digest = transactions._digest_in_directory

    def replace_before_commit(journal: Path, state: str, **details: object) -> None:
        nonlocal armed
        real_append(journal, state, **details)
        if state == "applying":
            target.write_bytes(b"late-preimage")
            armed = True

    def recreate_during_mismatch(parent_fd: int, name: str) -> str:
        nonlocal injected
        digest = real_digest(parent_fd, name)
        if armed and not injected:
            injected = True
            if target.exists():
                target.rename(target.with_name("review-candidate.bin"))
            target.write_bytes(b"external-recreated")
        return digest

    monkeypatch.setattr(transactions, "_append_journal", replace_before_commit)
    monkeypatch.setattr(transactions, "_digest_in_directory", recreate_during_mismatch)

    result = apply_mutation(layout, plan)

    quarantines = tuple(target.parent.glob(".existing.bin.*.quarantine"))
    assert result.ok is False
    assert result.rolled_back is False
    assert target.read_bytes() == b"external-recreated"
    assert [path.read_bytes() for path in quarantines] == [b"late-preimage"]
    assert any("quarantine" in failure for failure in result.failures)


def test_validation_uses_live_root_for_absolute_internal_registered_sources(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    item = "work/feature-target"
    _write_item(layout.bundle_dir, item, type="Feature")
    page = layout.bundle_dir / f"{item}.md"
    owner = layout.bundle_dir / item
    references = owner / "references"
    references.mkdir(parents=True)
    actual = references / "actual.md"
    actual.write_text("# Actual\n", encoding="utf-8")
    registered = references / "design.md"
    registered.symlink_to(actual)
    before = page.read_bytes().replace(
        b"affects: []\n",
        b"affects: []\nsources:\n  - id: design\n    resource: /work/feature-target/references/design.md\n",
    )
    page.write_bytes(before)
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{item}.md", _digest(before), before + b"\nLive-root validation.\n"),),
        validate_paths=(item,),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert registered.readlink() == actual


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fifo semantics")
def test_unrelated_unreadable_member_is_filtered_by_live_targeted_validation(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    unrelated = layout.bundle_dir / "work/unrelated.bin"
    unrelated.parent.mkdir()
    os.mkfifo(unrelated)
    plan = _plan(
        layout,
        mkdirs=("work/new",),
        directory_preconditions=(DirectoryPrecondition("work/new", None),),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert unrelated.exists()


def test_targeted_invalid_utf8_is_an_unreadable_postcondition_and_rolls_back(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    item = "work/feature-target"
    _write_item(layout.bundle_dir, item, type="Feature")
    (layout.bundle_dir / item).mkdir()
    page = layout.bundle_dir / f"{item}.md"
    before = page.read_bytes()
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{item}.md", _digest(before), b"\xff\xfe"),),
        validate_paths=(item,),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert "frontmatter.unreadable" in result.failures[0]
    assert page.read_bytes() == before


def test_cache_name_swap_after_containment_never_places_transaction_in_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    held_cache = layout.cache_dir.with_name("cache-held")
    escaped_cache = layout.bundle_dir / ".transaction-escape"
    plan = _plan(
        layout,
        mkdirs=("work/new",),
        directory_preconditions=(DirectoryPrecondition("work/new", None),),
    )
    real_lock = transactions._executor_lock

    @contextmanager
    def swap_cache_before_lock(*args: object, **kwargs: object):
        layout.cache_dir.rename(held_cache)
        escaped_cache.mkdir()
        layout.cache_dir.symlink_to(escaped_cache, target_is_directory=True)
        with real_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(transactions, "_executor_lock", swap_cache_before_lock)

    with pytest.raises(ValueError, match=r"cache.*changed|changed.*cache"):
        apply_mutation(layout, plan)

    assert not (escaped_cache / "work-mutations").exists()
    assert not (layout.bundle_dir / "work/new").exists()


def test_exact_durable_journal_history_is_terminal_even_when_append_raises_after_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    real_append = transactions._append_journal

    def raise_after_persist(journal: Path, state: str, **details: object) -> None:
        real_append(journal, state, **details)
        if state == "complete":
            raise OSError("reported after durable write")

    monkeypatch.setattr(transactions, "_append_journal", raise_after_persist)

    result = apply_mutation(layout, plan)

    assert result.ok is True
    assert target.read_bytes() == b"after"
    assert _states(result.journal) == ["planned", "applying", "validating", "complete"]
    assert [json.loads(line) for line in result.journal.read_text(encoding="utf-8").splitlines()] == [
        {
            "state": "planned",
            "deletes": [],
            "mkdirs": [],
            "moves": [],
            "operation": "reparent",
            "transaction_id": result.transaction_id,
            "validate_paths": [],
            "writes": ["work/page.bin"],
        },
        {
            "state": "applying",
            "backups": [{"member": "work/page.bin", "path": "backups/000000"}],
            "snapshots": [{"member": "work/page.bin", "existed": True}],
        },
        {"state": "validating", "validate_paths": []},
        {
            "state": "complete",
            "transaction_id": result.transaction_id,
            "moved": [],
            "written": ["work/page.bin"],
            "created_directories": [],
        },
    ]


@pytest.mark.parametrize(
    "corruption",
    [
        "minimal-prior",
        "forged-prior",
        "wrong-prior-transaction",
        "wrong-prior-metadata",
        "wrong-prior-field",
        "non-object-prior",
        "extra-record",
        "truncated-record",
        "missing-newline",
        "duplicate-normalized-key",
        "nested-duplicate-key",
        "nan",
        "infinity",
        "negative-infinity",
        "nested-boolean-as-integer",
    ],
)
def test_corrupt_prior_journal_history_never_certifies_terminal_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    real_append = transactions._append_journal

    def read_retained(descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        os.lseek(descriptor, 0, os.SEEK_END)
        return b"".join(chunks)

    def replace_retained(descriptor: int, content: bytes) -> None:
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)

    def encode(records: list[object]) -> bytes:
        return b"".join(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode() for record in records
        )

    def persist_corrupt_history(journal: Path, state: str, **details: object) -> None:
        if state != "complete":
            real_append(journal, state, **details)
            return
        journal_fd = details["_journal_fd"]
        assert isinstance(journal_fd, int)
        records = [json.loads(line) for line in read_retained(journal_fd).decode().splitlines()]
        assert len(records) == 3
        if corruption == "minimal-prior":
            records[0] = {"state": "planned"}
        elif corruption == "forged-prior":
            assert isinstance(records[0], dict)
            records[0]["forged"] = True
        elif corruption == "wrong-prior-transaction":
            assert isinstance(records[0], dict)
            records[0]["transaction_id"] = "wrong-transaction"
        elif corruption == "wrong-prior-metadata":
            assert isinstance(records[0], dict)
            records[0]["operation"] = "forged-operation"
        elif corruption == "wrong-prior-field":
            assert isinstance(records[2], dict)
            records[2]["validate_paths"] = ["work/forged"]
        elif corruption == "non-object-prior":
            records[1] = ["applying"]
        elif corruption == "extra-record":
            records.insert(2, {"state": "applying"})
        elif corruption == "nan":
            assert isinstance(records[0], dict)
            records[0]["non_finite"] = float("nan")
        elif corruption == "infinity":
            assert isinstance(records[0], dict)
            records[0]["non_finite"] = float("inf")
        elif corruption == "negative-infinity":
            assert isinstance(records[0], dict)
            records[0]["non_finite"] = float("-inf")

        content = encode(records)
        if corruption == "truncated-record":
            lines = content.splitlines(keepends=True)
            lines[1] = b'{"state":"applying"\n'
            content = b"".join(lines)
        elif corruption == "missing-newline":
            content = content.removesuffix(b"\n")
        elif corruption == "duplicate-normalized-key":
            content = content.replace(
                b'"state":"planned"',
                b'"state":"planned","\\u0073tate":"planned"',
                1,
            )
        elif corruption == "nested-duplicate-key":
            content = content.replace(
                b'"member":"work/page.bin"',
                b'"member":"work/page.bin","member":"work/page.bin"',
                1,
            )
        elif corruption == "nested-boolean-as-integer":
            content = content.replace(b'"existed":true', b'"existed":1', 1)
        replace_retained(journal_fd, content)
        real_append(journal, state, **details)
        raise OSError("reported after corrupt durable history")

    monkeypatch.setattr(transactions, "_append_journal", persist_corrupt_history)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert target.read_bytes() == b"before"
    assert b'"state":"rolling-back"' in result.journal.read_bytes()


@pytest.mark.parametrize(
    ("expected_value", "persisted_value"),
    [
        (True, "1"),
        (False, "0"),
        (7, "7.0"),
        (7.0, "7"),
    ],
)
def test_terminal_journal_history_requires_recursive_json_type_identity(
    tmp_path: Path,
    expected_value: object,
    persisted_value: str,
) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        "".join(
            [
                '{"state":"planned"}\n',
                (f'{{"snapshots":[{{"existed":{persisted_value}}}],"state":"applying"}}\n'),
                '{"state":"validating"}\n',
                '{"state":"complete"}\n',
            ]
        ),
        encoding="utf-8",
    )
    expected_history = (
        {"state": "planned"},
        {"state": "applying", "snapshots": [{"existed": expected_value}]},
        {"state": "validating"},
        {"state": "complete"},
    )

    assert transactions._journal_has_terminal_complete(journal, expected_history) is False


def test_absolute_internal_symlink_moves_byte_for_byte_while_external_still_refuses(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    _xfail_on_weak_tier(
        request,
        "Moving a symlink member must succeed for the internal case; the "
        "windows-revalidated tier refuses every planned symlink member "
        "categorically at preflight (D-002's backstop), so this move can "
        "never succeed on the weak tier regardless of where its target "
        "resolves.",
    )
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    source = work / "source"
    destination = work / "destination"
    source.mkdir(parents=True)
    target = source / "target.bin"
    target.write_bytes(b"target")
    link = source / "link.bin"
    link.symlink_to(target)
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source/link.bin", "work/destination/link.bin", is_asset=True),),
        directory_preconditions=(DirectoryPrecondition("work/destination", None),),
    )

    result = apply_mutation(layout, plan)

    moved = destination / "link.bin"
    assert result.ok is True
    assert moved.is_symlink()
    assert moved.readlink() == target
    assert moved.read_bytes() == b"target"


def test_absolute_external_symlink_move_is_refused_before_live_effects(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    external = tmp_path / "external.bin"
    external.write_bytes(b"external")
    source = layout.bundle_dir / "work/source/link.bin"
    source.parent.mkdir(parents=True)
    source.symlink_to(external)
    plan = _plan(
        layout,
        mkdirs=("work/destination",),
        moves=(Move("work/source/link.bin", "work/destination/link.bin", is_asset=True),),
        directory_preconditions=(DirectoryPrecondition("work/destination", None),),
    )

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    # The strong tier reaches the containment check and reports "escape the
    # bundle root"; the weak tier refuses the symlink member categorically at
    # preflight, before any containment analysis runs. Both refuse before any
    # live effect, which is the guarantee under test.
    assert "escape the bundle root" in result.failures[0] or "is a symlink" in result.failures[0]
    assert source.is_symlink()
    assert source.readlink() == external
    assert not (layout.bundle_dir / "work/destination").exists()
    assert external.read_bytes() == b"external"


def test_root_name_loss_after_validation_prevents_a_truthy_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    _xfail_on_weak_tier(
        request,
        "Asserts that the pre-write content survives at the swapped-away "
        "original location after a failed rollback attempt -- true only "
        "because the strong tier's descriptor-pinned restore keeps writing "
        "to the original inode even after it is renamed out from under the "
        "path. The weak tier's `_revalidate()` detects the identity mismatch "
        "immediately and refuses every further anchor operation, including "
        "the restore itself, so no write ever reaches the swapped-away "
        "location. This is the tier's documented defining weakness "
        "(ADR-0042), not a bug this task can fix.",
    )
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    held_root = layout.bundle_dir.with_name("okf-held")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))

    def replace_root_name(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        layout.bundle_dir.rename(held_root)
        layout.bundle_dir.mkdir()
        return ("forced validation failure",)

    monkeypatch.setattr(transactions, "_validate_postconditions", replace_root_name)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert any("bundle root changed" in failure for failure in result.failures)
    assert (held_root / "work/page.bin").read_bytes() == b"before"
    assert not (layout.bundle_dir / "work/page.bin").exists()


def test_transaction_validation_survives_an_unrelated_tree_beyond_recursion_limit(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    cursor = layout.bundle_dir / "unrelated"
    cursor.mkdir()
    for _ in range(300):
        cursor /= "d"
        cursor.mkdir()
    (cursor / "leaf.bin").write_bytes(b"leaf")
    plan = _plan(
        layout,
        mkdirs=("work/new",),
        directory_preconditions=(DirectoryPrecondition("work/new", None),),
    )
    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(250)
        result = apply_mutation(layout, plan)
    finally:
        sys.setrecursionlimit(previous_limit)

    assert result.ok is True
    assert (layout.bundle_dir / "work/new").is_dir()


def test_post_preflight_empty_directory_replacement_is_not_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/empty"
    target.mkdir(parents=True)
    plan = _plan(
        layout,
        deletes=("work/empty",),
        directory_preconditions=(DirectoryPrecondition("work/empty", directory_manifest_digest(target)),),
    )
    replacement_identity: tuple[int, int] | None = None
    real_append = transactions._append_journal

    def replace_empty_directory(journal: Path, state: str, **details: object) -> None:
        nonlocal replacement_identity
        real_append(journal, state, **details)
        if state == "applying":
            target.rmdir()
            target.mkdir()
            info = target.stat()
            replacement_identity = (info.st_dev, info.st_ino)

    monkeypatch.setattr(transactions, "_append_journal", replace_empty_directory)

    result = apply_mutation(layout, plan)

    current = target.stat()
    assert result.ok is False
    assert result.rolled_back is False
    assert (current.st_dev, current.st_ino) == replacement_identity
    assert "changed since planning" in result.failures[0]


def test_direct_move_double_conflict_preserves_source_quarantine_and_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    source = layout.bundle_dir / "work/source.bin"
    destination = layout.bundle_dir / "work/destination.bin"
    source.parent.mkdir()
    source.write_bytes(b"captured")
    plan = _plan(layout, moves=(Move("work/source.bin", "work/destination.bin", is_asset=True),))
    injected = False
    real_rename = transactions._rename_noreplace_at

    def recreate_both_vacated_names(
        source_fd: int,
        source_name: str,
        destination_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal injected
        real_rename(source_fd, source_name, destination_fd, destination_name)
        if not injected and source_name.startswith(".source.bin.") and destination_name == "destination.bin":
            injected = True
            source.write_bytes(b"external-source")
            source.with_name(source_name).write_bytes(b"external-quarantine")

    monkeypatch.setattr(transactions, "_rename_noreplace_at", recreate_both_vacated_names)

    result = apply_mutation(layout, plan)

    quarantines = tuple(source.parent.glob(".source.bin.*.quarantine"))
    assert result.ok is False
    assert result.rolled_back is False
    assert source.read_bytes() == b"external-source"
    assert [path.read_bytes() for path in quarantines] == [b"external-quarantine"]
    assert destination.read_bytes() == b"captured"
    assert any("custody" in failure or "quarantine" in failure for failure in result.failures)


def test_validation_reads_anchored_root_when_configured_name_swaps_during_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    _xfail_on_weak_tier(
        request,
        "Asserts the strong tier's defining protection: a descriptor-pinned "
        "read survives a path swap performed while validation is loading the "
        "bundle. `_load_bundle_through`'s weak-tier arm walks the plain path "
        "(anchors.py:361-375) rather than a pinned descriptor, and the swap "
        "in this test is undone before `_assert_root_identity` re-checks -- "
        "so the swap window is not detected. This is the tier's documented "
        "defining weakness (ADR-0042), not a bug this task can fix.",
    )
    layout = _workspace(tmp_path)
    item = "work/feature-target"
    _write_item(layout.bundle_dir, item, type="Feature")
    (layout.bundle_dir / item).mkdir()
    page = layout.bundle_dir / f"{item}.md"
    before = page.read_bytes()
    decoy = layout.bundle_dir.with_name("okf-validation-decoy")
    held = layout.bundle_dir.with_name("okf-validation-held")
    shutil.copytree(layout.bundle_dir, decoy, symlinks=True)
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{item}.md", _digest(before), b"\xff\xfe"),),
        validate_paths=(item,),
    )
    real_load = transactions._load_bundle_through

    def swap_only_while_loading(root: transactions.Anchor, path: Path, **kwargs: object):
        layout.bundle_dir.rename(held)
        decoy.rename(layout.bundle_dir)
        try:
            return real_load(root, path, **kwargs)
        finally:
            layout.bundle_dir.rename(decoy)
            held.rename(layout.bundle_dir)

    monkeypatch.setattr(transactions, "_load_bundle_through", swap_only_while_loading)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert "frontmatter.unreadable" in result.failures[0]
    assert page.read_bytes() == before


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="monkeypatches fcntl.flock to swap the lock file mid-acquire; Windows locks via msvcrt.locking",
)
def test_executor_lock_name_swap_never_redirects_lock_io_into_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl  # E1: transactions.py no longer imports fcntl at module scope (Task 5).

    layout = _workspace(tmp_path)
    lock_path = layout.cache_dir / "work-mutations/executor.lock"
    held_lock = lock_path.with_name("executor-held.lock")
    escaped = layout.bundle_dir / ".lock-escape"
    real_flock = fcntl.flock
    swapped = False

    def swap_after_lock(descriptor: int, operation: int) -> None:
        # Identify the EXECUTOR lock's own file by inode, not merely "the first
        # regular-file LOCK_EX" -- the windows-revalidated tier's bundle-root
        # lock (`.gw-bundle.lock`) is ALSO a regular-file `fcntl.flock` on this
        # host (both tiers bottom out in `okf_ext.locking.locked`, which only
        # forks on the real `sys.platform`, not this test's forced tier), and
        # it is acquired first. Matching by inode keeps the swap trained on the
        # executor lock regardless of which lock a given tier takes en route.
        nonlocal swapped
        real_flock(descriptor, operation)
        if (
            operation == fcntl.LOCK_EX
            and not swapped
            and stat.S_ISREG(os.fstat(descriptor).st_mode)
            and lock_path.exists()
            and os.fstat(descriptor).st_ino == lock_path.stat().st_ino
        ):
            swapped = True
            lock_path.rename(held_lock)
            lock_path.symlink_to(escaped)

    monkeypatch.setattr(fcntl, "flock", swap_after_lock)

    with pytest.raises(ValueError, match=r"lock.*changed|changed.*lock"):
        apply_mutation(layout, _plan(layout))

    assert not escaped.exists()
    assert held_lock.is_file()


def test_journal_name_swap_never_redirects_journal_io_into_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    escaped = layout.bundle_dir / ".journal-escape"
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    real_append = transactions._append_journal
    held_journal: Path | None = None

    def swap_before_applying(journal: Path, state: str, **details: object) -> None:
        nonlocal held_journal
        if state == "applying" and held_journal is None:
            held_journal = journal.with_name("journal-held.jsonl")
            journal.rename(held_journal)
            journal.symlink_to(escaped)
        real_append(journal, state, **details)

    monkeypatch.setattr(transactions, "_append_journal", swap_before_applying)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is False
    assert target.read_bytes() == b"before"
    assert not escaped.exists()
    assert held_journal is not None
    assert _states(held_journal) == ["planned"]


@pytest.mark.parametrize("forgery", ["minimal", "wrong-fields", "wrong-transaction"])
def test_forged_complete_record_never_certifies_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forgery: str
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    real_append = transactions._append_journal

    def persist_forgery(journal: Path, state: str, **details: object) -> None:
        if state != "complete":
            real_append(journal, state, **details)
            return
        transaction_id = details["transaction_id"]
        if forgery == "minimal":
            record = {"state": "complete", "transaction_id": transaction_id}
        else:
            record = {
                "state": "complete",
                "transaction_id": "wrong" if forgery == "wrong-transaction" else transaction_id,
                "moved": [["forged", "destination"]] if forgery == "wrong-fields" else details["moved"],
                "written": details["written"],
                "created_directories": details["created_directories"],
            }
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        journal_fd = details.get("_journal_fd")
        if isinstance(journal_fd, int):
            os.write(journal_fd, encoded)
            os.fsync(journal_fd)
        else:
            parent_fd = details["_parent_fd"]
            assert isinstance(parent_fd, int)
            descriptor = os.open(journal.name, os.O_WRONLY | os.O_APPEND, dir_fd=parent_fd)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        raise OSError("reported after forged durable record")

    monkeypatch.setattr(transactions, "_append_journal", persist_forgery)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert target.read_bytes() == b"before"


@pytest.mark.parametrize("duplicate_key", ["state", "transaction_id", "written"])
def test_duplicate_journal_keys_never_certify_terminal_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duplicate_key: str
) -> None:
    layout = _workspace(tmp_path)
    target = layout.bundle_dir / "work/page.bin"
    target.parent.mkdir()
    target.write_bytes(b"before")
    plan = _plan(layout, writes=(PlannedWrite("work/page.bin", _digest(b"before"), b"after"),))
    real_append = transactions._append_journal

    def persist_duplicate(journal: Path, state: str, **details: object) -> None:
        if state != "complete":
            real_append(journal, state, **details)
            return
        fields = [
            '"state":"complete"',
            f'"transaction_id":{json.dumps(details["transaction_id"])}',
            f'"moved":{json.dumps(details["moved"], separators=(",", ":"))}',
            f'"written":{json.dumps(details["written"], separators=(",", ":"))}',
            f'"created_directories":{json.dumps(details["created_directories"], separators=(",", ":"))}',
        ]
        duplicate = {
            "state": '"state":"forged"',
            "transaction_id": '"transaction_id":"forged"',
            "written": '"written":["forged"]',
        }[duplicate_key]
        fields.insert(0, duplicate)
        encoded = ("{" + ",".join(fields) + "}\n").encode()
        journal_fd = details["_journal_fd"]
        assert isinstance(journal_fd, int)
        os.write(journal_fd, encoded)
        os.fsync(journal_fd)
        raise OSError("reported after duplicate-key record")

    monkeypatch.setattr(transactions, "_append_journal", persist_duplicate)

    result = apply_mutation(layout, plan)

    assert result.ok is False
    assert result.rolled_back is True
    assert target.read_bytes() == b"before"


@pytest.mark.parametrize("target_location", ["internal", "external"])
def test_relative_moved_symlink_projection_follows_destination_ancestor_links(
    tmp_path: Path, target_location: str, request: pytest.FixtureRequest
) -> None:
    if target_location == "internal":
        _xfail_on_weak_tier(
            request,
            "Moving a symlink member must succeed for the internal case; the "
            "windows-revalidated tier refuses every planned symlink member "
            "categorically at preflight (D-002's backstop), so this move can "
            "never succeed on the weak tier regardless of where its target "
            "resolves.",
        )
    layout = _workspace(tmp_path)
    work = layout.bundle_dir / "work"
    source_dir = work / "source"
    destination_dir = work / "destination"
    source_dir.mkdir(parents=True)
    destination_dir.mkdir()
    source = source_dir / "link.bin"
    source.symlink_to("gateway/target.bin")
    target_root = work / "physical" if target_location == "internal" else tmp_path / "external"
    target_root.mkdir()
    (target_root / "target.bin").write_bytes(target_location.encode())
    (destination_dir / "gateway").symlink_to(target_root, target_is_directory=True)
    plan = _plan(
        layout,
        moves=(Move("work/source/link.bin", "work/destination/link.bin", is_asset=True),),
        directory_preconditions=(
            DirectoryPrecondition("work/destination", directory_manifest_digest(destination_dir)),
        ),
    )

    result = apply_mutation(layout, plan)

    moved = destination_dir / "link.bin"
    if target_location == "internal":
        assert result.ok is True
        assert moved.readlink() == Path("gateway/target.bin")
        assert moved.read_bytes() == b"internal"
        assert any("baseline capture failed" in warning for warning in result.warnings)
        assert not any("pre-existing validation failure(s) excused" in warning for warning in result.warnings)
    else:
        assert result.ok is False
        assert result.rolled_back is False
        # See test_absolute_external_symlink_move_is_refused_before_live_effects:
        # the weak tier refuses the symlink member categorically at preflight
        # rather than reaching the containment check, so its message differs.
        assert "escape the bundle root" in result.failures[0] or "is a symlink" in result.failures[0]
        assert source.is_symlink()
        assert source.readlink() == Path("gateway/target.bin")
        assert not moved.exists()


def test_work_mutations_open_failure_does_not_leak_cache_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _workspace(tmp_path)
    descriptor_directory = Path("/dev/fd" if sys.platform == "darwin" else "/proc/self/fd")
    before = len(tuple(descriptor_directory.iterdir()))
    real_open_directory = transactions._open_or_create_directory

    def fail_work_mutations(parent_fd: int, name: str) -> int:
        if name == "work-mutations":
            raise OSError("injected work-mutations open failure")
        return real_open_directory(parent_fd, name)

    monkeypatch.setattr(transactions, "_open_or_create_directory", fail_work_mutations)

    with pytest.raises(OSError, match="injected work-mutations open failure"):
        apply_mutation(layout, _plan(layout))

    after = len(tuple(descriptor_directory.iterdir()))
    assert after == before


def test_transaction_journal_helpers_reject_malformed_histories_and_support_anchored_io(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    transactions._append_journal(journal, "prepared", transaction_id="tx")
    expected = [{"state": "prepared", "transaction_id": "tx"}]
    assert transactions._journal_has_terminal_complete(journal, expected)

    journal.write_text('{"state":"prepared"}', encoding="utf-8")
    assert not transactions._journal_has_terminal_complete(journal, expected)
    journal.write_text('{"state":"prepared","state":"complete"}\n', encoding="utf-8")
    assert not transactions._journal_has_terminal_complete(journal, expected)
    journal.write_text('["prepared"]\n', encoding="utf-8")
    assert not transactions._journal_has_terminal_complete(journal, expected)

    parent = transactions._open_absolute_directory(tmp_path)
    descriptor = parent.open_file("journal.jsonl", os.O_RDWR | os.O_APPEND)
    try:
        journal.write_text("", encoding="utf-8")
        transactions._append_journal(
            journal,
            "prepared",
            _parent=parent,
            _journal_fd=descriptor,
            transaction_id="tx",
        )
        assert transactions._journal_has_terminal_complete(journal, expected, journal_fd=descriptor)
        with pytest.raises(ValueError, match="anchored parent"):
            transactions._append_journal(journal, "bad", _journal_fd=descriptor)
    finally:
        os.close(descriptor)
        parent.close()


def test_transaction_json_and_descriptor_guards_cover_type_and_identity_failures(tmp_path: Path) -> None:
    assert not transactions._json_values_equal(True, 1)
    assert not transactions._json_values_equal({"a": 1}, {"b": 1})
    assert not transactions._json_values_equal([1], [1, 2])
    with pytest.raises(ValueError, match="duplicate JSON"):
        transactions._unique_json_object([("a", 1), ("a", 2)])
    with pytest.raises(ValueError, match="non-finite JSON"):
        transactions._reject_json_constant("NaN")
    with pytest.raises(ValueError, match="non-finite JSON"):
        transactions._finite_json_float("1e999")

    directory_fd = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            transactions._require_regular_file(directory_fd, "directory")
    finally:
        os.close(directory_fd)

    entry = tmp_path / "entry"
    entry.write_text("one", encoding="utf-8")
    parent = transactions._open_absolute_directory(tmp_path)
    entry_fd = os.open(entry, os.O_RDONLY)
    entry.unlink()
    entry.write_text("two", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="changed during mutation"):
            transactions._assert_regular_entry_identity(parent, "entry", entry_fd, "entry")
    finally:
        os.close(entry_fd)
        parent.close()


def test_transaction_directory_open_lock_and_identity_helpers(tmp_path: Path) -> None:
    lock_root = tmp_path / "locks"
    with transactions._executor_lock(lock_root):
        assert (lock_root / "executor.lock").is_file()

    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        transactions._open_root(file)  # refuses a non-directory on both tiers

    with pytest.raises(ValueError, match="must be absolute"):
        transactions._open_absolute_directory(Path("relative"))
    opened = transactions._open_absolute_directory(tmp_path)
    opened.close()

    parent = transactions._open_absolute_directory(tmp_path)
    try:
        child = transactions._open_or_create_directory(parent, "created")
        child.close()
        child = transactions._open_or_create_directory(parent, "created")
        try:
            moved = tmp_path / "moved"
            (tmp_path / "created").rename(moved)
            with pytest.raises(ValueError, match="directory changed"):
                transactions._assert_directory_identity(tmp_path / "created", child, "child")
        finally:
            child.close()
    finally:
        parent.close()


def test_transaction_path_helpers_create_ancestors_and_hash_entry_kinds(tmp_path: Path) -> None:
    root = transactions._open_root(tmp_path)
    touched: set[str] = set()
    try:
        parent, name = transactions._open_parent(root, "new/deep/file", create=True, touched=touched)
        parent.close()
        assert name == "file" and touched == {"new", "new/deep"}
        with pytest.raises(ValueError, match="now exists"):
            transactions._open_parent(
                root,
                "new/deep/file",
                create=True,
                touched=set(),
                must_create={"new"},
            )
        for invalid in ("", "/absolute", "../escape", "a/../b"):
            with pytest.raises(ValueError, match="canonical bundle-relative"):
                transactions._lexical_member(invalid)

        (tmp_path / "tree").mkdir()
        (tmp_path / "tree/file").write_text("payload", encoding="utf-8")
        (tmp_path / "tree/link").symlink_to("file")
        assert transactions._entry_fingerprint_at(root, "tree")
        transactions._validate_ancestor_chain(root, "missing/child")
        (tmp_path / "ancestor-link").symlink_to(tmp_path / "tree")
        with pytest.raises(ValueError, match="unsafe ancestor"):
            transactions._validate_ancestor_chain(root, "ancestor-link/child")
    finally:
        root.close()

    transactions._fsync_entry(tmp_path / "tree")
    transactions._fsync_entry(tmp_path / "tree/file")
    transactions._fsync_entry(tmp_path / "tree/link")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fifo semantics")
def test_transaction_snapshot_and_copy_helpers_cover_nested_entries_and_special_files(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    plan = _plan(
        layout,
        mkdirs=("work/a", "work/a/nested", "work/b"),
        deletes=("work/a/file",),
    )
    assert transactions._snapshot_members(plan) == ("work/a", "work/b")

    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("x", encoding="utf-8")
    (source / "link").symlink_to("file")
    nested = source / "nested"
    nested.mkdir()
    (nested / "payload").write_bytes(b"y")
    copied = tmp_path / "copied"
    transactions._copy_entry(source, copied)
    assert (copied / "file").read_text(encoding="utf-8") == "x"
    assert (copied / "link").is_symlink()

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="unsupported filesystem entry"):
        transactions._copy_entry(fifo, tmp_path / "fifo-copy")


def test_transaction_preflight_rejects_refused_foreign_and_duplicate_effect_plans(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    refused = WorkMutationPlan(
        root=layout.bundle_dir,
        operation="reparent",
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(MutationRefusal("work/a", "bad", "no"),),
        validate_paths=(),
    )
    with pytest.raises(ValueError, match="cannot apply a refused"):
        transactions._preflight(layout, refused)

    foreign = _plan(layout)
    foreign = WorkMutationPlan(
        root=tmp_path,
        operation=foreign.operation,
        path_mapping={},
        move_plan=None,
        moves=(),
        writes=(),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(),
    )
    with pytest.raises(ValueError, match="different bundle root"):
        transactions._preflight(layout, foreign)

    duplicate_conditions = _plan(
        layout,
        directory_preconditions=(DirectoryPrecondition("absent", None), DirectoryPrecondition("absent", None)),
    )
    root = transactions._open_root(layout.bundle_dir)
    try:
        with pytest.raises(ValueError, match="duplicate members"):
            transactions._preflight_anchored(duplicate_conditions, root, tmp_path / "scratch")
        duplicate_writes = _plan(
            layout,
            writes=(PlannedWrite("new", None, b"a"), PlannedWrite("new", None, b"b")),
        )
        with pytest.raises(ValueError, match="duplicate targets"):
            transactions._preflight_anchored(duplicate_writes, root, tmp_path / "scratch")
    finally:
        root.close()


def test_anchored_preflight_rejects_each_stale_write_and_move_shape(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    root = layout.bundle_dir
    (root / "existing").write_bytes(b"old")
    (root / "source").write_bytes(b"source")
    (root / "destination").write_bytes(b"destination")
    root_anchor = transactions._open_root(root)

    cases = [
        (_plan(layout, directory_preconditions=(DirectoryPrecondition("missing", "digest"),)), "now missing"),
        (_plan(layout, writes=(PlannedWrite("same", _digest(b"x"), b"y", "same"),)), "distinct preimage"),
        (_plan(layout, writes=(PlannedWrite("new", None, b"y", "source"),)), "without a source digest"),
        (_plan(layout, writes=(PlannedWrite("existing", None, b"y"),)), "now exists"),
        (_plan(layout, writes=(PlannedWrite("missing", _digest(b"x"), b"y"),)), "changed since planning"),
        (_plan(layout, writes=(PlannedWrite("existing", _digest(b"different"), b"y"),)), "changed since planning"),
        (
            _plan(layout, writes=(PlannedWrite("destination", _digest(b"source"), b"y", "source"),)),
            "now exists",
        ),
        (_plan(layout, moves=(Move("missing", "new", False),)), "source missing"),
        (_plan(layout, moves=(Move("source", "destination", False),)), "destination exists"),
        (
            _plan(layout, moves=(Move("source", "new", False), Move("existing", "new", False))),
            "multiple effects claim",
        ),
        (
            _plan(layout, moves=(Move("source", "new", False),), writes=(PlannedWrite("new", None, b"x"),)),
            "multiple effects claim final targets",
        ),
    ]
    try:
        for index, (plan, message) in enumerate(cases):
            with pytest.raises(ValueError, match=message):
                transactions._preflight_anchored(plan, root_anchor, tmp_path / f"scratch-{index}")
    finally:
        root_anchor.close()


def test_copy_entry_handles_top_level_files_and_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("payload", encoding="utf-8")
    target = tmp_path / "target"
    transactions._copy_entry(source, target)
    assert target.read_text(encoding="utf-8") == "payload"

    link = tmp_path / "link"
    link.symlink_to("source")
    copied_link = tmp_path / "copied-link"
    transactions._copy_entry(link, copied_link)
    assert copied_link.readlink() == Path("source")


def test_append_journal_supports_parent_descriptor_without_retained_file(tmp_path: Path) -> None:
    parent = transactions._open_absolute_directory(tmp_path)
    try:
        journal = tmp_path / "anchored.jsonl"
        transactions._append_journal(journal, "prepared", _parent=parent, transaction_id="tx")
    finally:
        parent.close()
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "prepared"


def test_rollback_restores_a_backed_up_directory_tree_with_nested_content(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """`_copy_backup_to_live`'s directory arm, which the file and symlink arms
    return before ever reaching.

    A rollback that has to put a *directory* back is the case where a half-done
    restore is invisible: the member exists again, so `_verify_snapshot`'s
    existence check passes while the subtree beneath it is missing. This walks
    a real preimage back into place and compares the whole tree.
    """
    _xfail_on_weak_tier(
        request,
        "Restoring a backed-up directory containing a symlink member calls "
        "`Anchor.symlink()`, which the windows-revalidated tier always refuses "
        "(D-002's backstop: Windows symlink creation needs Developer Mode or "
        "SeCreateSymbolicLinkPrivilege); there is no way to construct this "
        "rollback scenario on the weak tier.",
    )
    root = tmp_path / "live"
    backup = tmp_path / "backup" / "owned"
    backup.mkdir(parents=True)
    (backup / "top.bin").write_bytes(b"top")
    nested = backup / "nested"
    nested.mkdir()
    (nested / "leaf.bin").write_bytes(b"leaf")
    (nested / "link.bin").symlink_to("leaf.bin")
    deep = nested / "deeper"
    deep.mkdir()
    (deep / "buried.bin").write_bytes(b"buried")
    nested.chmod(0o750)

    root.mkdir()
    (root / "owned").mkdir()
    (root / "owned" / "clobbered.bin").write_bytes(b"written during the failed apply")

    entries = [transactions._SnapshotEntry(member="owned", existed=True, backup=backup, fingerprint=None)]
    transactions._restore_snapshot(root, entries)

    restored = root / "owned"
    assert sorted(path.relative_to(restored).as_posix() for path in restored.rglob("*")) == [
        "nested",
        "nested/deeper",
        "nested/deeper/buried.bin",
        "nested/leaf.bin",
        "nested/link.bin",
        "top.bin",
    ]
    assert (restored / "top.bin").read_bytes() == b"top"
    assert (restored / "nested" / "deeper" / "buried.bin").read_bytes() == b"buried"
    assert (restored / "nested" / "link.bin").is_symlink()
    assert (restored / "nested" / "link.bin").readlink() == Path("leaf.bin")
    assert stat.S_IMODE((restored / "nested").stat().st_mode) == 0o750
    assert not (restored / "clobbered.bin").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fifo semantics")
def test_rollback_refuses_a_backup_holding_an_unsupported_entry_type(tmp_path: Path) -> None:
    """A preimage graph-works cannot faithfully reproduce must raise, not be
    silently skipped -- a skipped entry is a rollback reported as complete with
    the tree still wrong."""
    root = tmp_path / "live"
    root.mkdir()
    backup = tmp_path / "backup" / "owned"
    backup.mkdir(parents=True)
    os.mkfifo(backup / "pipe")

    entries = [transactions._SnapshotEntry(member="owned", existed=True, backup=backup, fingerprint=None)]

    with pytest.raises(ValueError, match="unsupported filesystem entry type"):
        transactions._restore_snapshot(root, entries)


def test_map_member_rewrites_pages_and_owned_directory_contents_deepest_first() -> None:
    mapping = {
        "work/epic-a": "work/_archive/epic-a",
        "work/epic-a/children/bug-b": "work/epic-a/children/_archive/bug-b",
    }

    assert transactions._map_member(mapping, "work/epic-a.md") == "work/_archive/epic-a.md"
    assert (
        transactions._map_member(mapping, "work/epic-a/references/01-design.md")
        == "work/_archive/epic-a/references/01-design.md"
    )
    assert (
        transactions._map_member(mapping, "work/epic-a/children/bug-b.md") == "work/epic-a/children/_archive/bug-b.md"
    )
    assert transactions._map_member(mapping, "work/index.md") == "work/index.md"
    assert transactions._map_member({}, "work/epic-a.md") == "work/epic-a.md"


def test_pre_existing_error_survives_a_real_move_and_is_excused_at_its_new_path(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    release = "work/release-cutover"
    source = "work/bug-source"
    _write_item(layout.bundle_dir, release, type="Release")
    _write_item(layout.bundle_dir, source, type="Bug", affects=("src/gone.py",))
    (layout.bundle_dir / release / "children").mkdir(parents=True)
    (layout.bundle_dir / release / "children/_archive").mkdir()
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    plan = plan_reparent(bundle, load_items(bundle), source, release)

    result = apply_mutation(layout, plan, repo_root=repo)

    destination = f"{release}/children/bug-source"
    assert result.ok is True
    assert result.failures == ()
    assert (layout.bundle_dir / f"{destination}.md").exists()
    assert any(
        "pre-existing" in warning and f"{destination}.md: targets.affects-missing" in warning
        for warning in result.warnings
    )


def test_a_second_failure_of_the_same_code_on_the_same_page_still_fails(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "work/bug-target"
    _write_item(layout.bundle_dir, path, type="Bug", affects=("src/gone.py",))
    (layout.bundle_dir / path).mkdir()
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    after = before.replace(b"  - src/gone.py\n", b"  - src/gone.py\n  - src/also-gone.py\n")
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{path}.md", _digest(before), after),),
        validate_paths=(path,),
    )

    result = apply_mutation(layout, plan, repo_root=repo)

    assert result.ok is False
    assert result.rolled_back is True
    assert page.read_bytes() == before
    introduced = [failure for failure in result.failures if "targets.affects-missing" in failure]
    assert len(introduced) == 1
    assert "src/also-gone.py" in introduced[0] or "src/gone.py" in introduced[0]


def test_pre_existing_dangling_dependency_is_excused_by_the_structural_half(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "work/bug-target"
    _write_item(
        layout.bundle_dir,
        path,
        type="Bug",
        depends_on=({"path": "work/bug-absent", "blocks": "execute", "needs": "resolved"},),
    )
    (layout.bundle_dir / path).mkdir()
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    after = before.replace(b"updated: 2026-08-22", b"updated: 2026-08-23")
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{path}.md", _digest(before), after),),
        validate_paths=(path,),
    )

    result = apply_mutation(layout, plan, repo_root=repo)

    assert result.ok is True
    assert result.failures == ()
    assert any(
        "pre-existing" in warning and "dependency target 'work/bug-absent'" in warning for warning in result.warnings
    )


def test_a_new_dangling_dependency_added_on_top_of_a_pre_existing_one_still_fails(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "work/bug-target"
    _write_item(
        layout.bundle_dir,
        path,
        type="Bug",
        depends_on=({"path": "work/bug-absent", "blocks": "execute", "needs": "resolved"},),
    )
    (layout.bundle_dir / path).mkdir()
    page = layout.bundle_dir / f"{path}.md"
    before = page.read_bytes()
    after = before.replace(
        b"  - {blocks: execute, needs: resolved, path: work/bug-absent}\n",
        b"  - {blocks: execute, needs: resolved, path: work/bug-absent}\n"
        b"  - {blocks: execute, needs: resolved, path: work/bug-also-absent}\n",
    )
    assert after != before
    plan = _plan(
        layout,
        writes=(PlannedWrite(f"{path}.md", _digest(before), after),),
        validate_paths=(path,),
    )

    result = apply_mutation(layout, plan, repo_root=repo)

    assert result.ok is False
    assert result.rolled_back is True
    assert page.read_bytes() == before
    introduced = [failure for failure in result.failures if "dependency target" in failure]
    assert len(introduced) == 1
    assert "work/bug-absent" in introduced[0] or "work/bug-also-absent" in introduced[0]
