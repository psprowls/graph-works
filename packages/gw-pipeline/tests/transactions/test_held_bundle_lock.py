"""A held bundle lock spans Git and journaled mutations without reacquiring."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from gw_kernel.testing.transactions import git as _git
from gw_kernel.testing.transactions import init_git as _init_git
from gw_kernel.testing.transactions import plan as _plan
from gw_kernel.testing.transactions import workspace as _workspace
from gw_kernel.workspace import bundle_lock
from gw_kernel.workspace.commits import WorkspaceCommit
from gw_pipeline.transactions import (
    LOCK_TIMEOUT_FAILURE,
    apply_mutation,
    commit_pending,
    held_bundle_lock,
)
from work_tracker_okf.mutation import PlannedWrite


def plan(layout, name):
    return _plan(layout, writes=(PlannedWrite(name, None, b"authored\n"),))


def test_nested_apply_and_commit_pending_do_not_deadlock(tmp_path, monkeypatch):
    layout = _workspace(tmp_path)
    _init_git(layout.root)
    acquisitions = []
    original = bundle_lock.bundle_root_lock

    @contextmanager
    def counted(anchor, *, timeout=None):
        acquisitions.append(anchor)
        with original(anchor, timeout=timeout):
            yield

    monkeypatch.setattr(bundle_lock, "bundle_root_lock", counted)
    with held_bundle_lock(layout), held_bundle_lock(layout):
        result = apply_mutation(
            layout,
            plan(layout, "a.md"),
            lock_timeout=0.1,
            commit=WorkspaceCommit("workspace: nested", extra_paths=("okf/a.md",)),
        )
        assert result.ok and result.commit.status == "committed"
        assert commit_pending(layout, WorkspaceCommit("workspace: pending"), lock_timeout=0.1).status == "skipped"
    assert len(acquisitions) == 1
    assert _git(layout.root, "log", "-1", "--format=%s").strip() == "workspace: nested"


def test_other_threads_wait_and_time_out(tmp_path):
    layout = _workspace(tmp_path)
    started = Event()

    def other():
        started.set()
        return apply_mutation(layout, plan(layout, "b.md"), lock_timeout=5, commit=None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with held_bundle_lock(layout):
            future = pool.submit(other)
            assert started.wait(2)
            timed = pool.submit(apply_mutation, layout, plan(layout, "c.md"), lock_timeout=0.05, commit=None).result(2)
            assert timed.failures == (LOCK_TIMEOUT_FAILURE,)
            assert not (layout.bundle_dir / "c.md").exists()
            assert not future.done()
        assert future.result(10).ok


def test_exception_releases_lock_and_context(tmp_path):
    layout = _workspace(tmp_path)
    with pytest.raises(RuntimeError), held_bundle_lock(layout):
        raise RuntimeError("stop")
    with ThreadPoolExecutor() as pool:
        assert pool.submit(apply_mutation, layout, plan(layout, "a.md"), lock_timeout=1, commit=None).result(5).ok
