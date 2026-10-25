"""Read-back: what the launch record says about the worktree it ran in.

The planner resolves an existing path before launch, so the backend asks Orca
nothing about it: the record carries the path it was launched into and leaves
the branch unknown rather than copying it from the plan.
"""

from __future__ import annotations

from orca_fakes import FakeRunner
from subagents_io.dispatch import PlannedDispatch, WorktreePlacement
from workflow_orca import OrcaBackend

TARGET = "auto-drive:2026-08-14-epic-feature-orca-dispatch-backend"

PROMPT = "Run /gw:workflow my-slug.\nDispatch key: my-slug#execute\nSend worker_done when done."

ROUTES = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-use",), "run_create"),
    (("task-list",), "task_list"),
    (("task-create",), "task_create"),
    (("worker-start",), "worker_start"),
    (("worker-show",), "worker_show_created"),
    (("worktree", "show"), "worktree_show_renamed"),
]


def planned(**overrides):
    fields = dict(
        agent="claude",
        key="my-slug#execute",
        slug="my-slug",
        phase="execute",
        kind="feature",
        effort="medium",
        skill="subagent-driven-development",
        mode="autonomous",
        model=None,
        reasoning_effort=None,
        worktree=WorktreePlacement(path="/tmp/wt", branch="psprowls/my-slug", parent_path=None),
        merge_target="main",
        auto_merge=False,
        prompt=PROMPT,
    )
    fields.update(overrides)
    return PlannedDispatch(**fields)


def session(routes=ROUTES):
    runner = FakeRunner(routes)
    return OrcaBackend(run=runner).open_session(TARGET), runner


def test_the_record_reports_the_given_path_and_makes_no_extra_call():
    # Nothing was created, so there is nothing to learn and no call to pay
    # for. The branch stays unknown rather than being copied from the plan.
    sess, runner = session()
    record = sess.launch(planned())
    assert record.worktree_path == "/tmp/wt"
    assert record.worktree_branch is None
    assert not runner.calls_matching("worktree", "show")
    assert not runner.calls_matching("worktree", "set")
    assert not runner.calls_matching("worker-show")


def test_a_planned_parent_makes_no_lineage_call():
    sess, runner = session()
    sess.launch(planned(worktree=WorktreePlacement("/wt/child", "bug/child", "/wt/epic")))
    assert not runner.calls_matching("worktree", "set")


def test_a_record_carries_the_ordinary_launch_fields():
    sess, _ = session()
    record = sess.launch(planned())
    assert record.key == "my-slug#execute"
    assert record.handle == "ctx_new000000001"
    assert record.state == "pending"


def test_enumeration_does_not_claim_a_worktree_it_never_read():
    # Deliberate scope line: `workers()` reconstructs from `task-list` /
    # `worker-list`, which carry no worktree, so it reports unknown rather
    # than a value it did not fetch.
    routes = [
        (("run-list", "--cursor"), "run_list_page2"),
        (("run-list",), "run_list"),
        (("run-use",), "run_create"),
        (("task-list",), "task_list"),
        (("worker-list",), "worker_list"),
        (("worker-show",), "worker_show_live"),
    ]
    sess, _ = session(routes=routes)
    assert all(r.worktree_path is None and r.worktree_branch is None for r in sess.workers())
