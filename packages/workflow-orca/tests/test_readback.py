"""Read-back: what Orca actually provisioned, recorded on the launch record.

Orca's `--name` is a worktree *display* name, not a branch name — it derives
the branch itself and offers no flag to ask for one. These tests pin the one
correction that is ours: stop discarding the answer Orca already gives.
"""

from __future__ import annotations

import json
from dataclasses import replace

from orca_fakes import FakeRunner, fixture
from subagents_io.dispatch import PlannedDispatch, WorktreeAction
from workflow_orca import OrcaBackend
from workflow_orca._cli import OrcaResult

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
        worktree=WorktreeAction(
            action="reuse", path="/tmp/wt", branch="psprowls/my-slug", base_branch=None, exists=True, parent_path=None
        ),
        merge_target="main",
        auto_merge=False,
        prompt=PROMPT,
    )
    fields.update(overrides)
    return PlannedDispatch(**fields)


def session(routes=ROUTES, runner_class=FakeRunner, **backend_kwargs):
    backend_kwargs.setdefault("repo_selector", "name:agent-workspace")
    runner = runner_class(routes)
    return OrcaBackend(run=runner, **backend_kwargs).open_session(TARGET), runner


class LineageRunner(FakeRunner):
    """Synthetic `orca worktree set` answers: no capture exists (see FIXTURES.md)."""

    set_returncode = 0

    def __call__(self, argv):
        argv = tuple(argv)
        if argv[:3] == ("orca", "worktree", "set"):
            self.calls.append(argv)
            body = {"ok": self.set_returncode == 0, "result": {"worktree": {}}}
            return OrcaResult(self.set_returncode, json.dumps(body), "")
        return super().__call__(argv)


CHILD_ID = "a5d7cb85-fc68-4596-bffd-ecf75466124a::/Users/pat/orca/workspaces/graph-works/child"


FORK_CHILD = WorktreeAction(
    action="fork-child", path=None, branch="bug/child", base_branch="epic/x", exists=None, parent_path=None
)


def test_a_planned_parent_is_linked_by_id_after_start():
    sess, runner = session(runner_class=LineageRunner)
    sess.launch(planned(worktree=replace(FORK_CHILD, parent_path="/wt/epic")))
    assert runner.calls_matching("worktree", "set") == [
        ("orca", "worktree", "set", "--worktree", f"id:{CHILD_ID}", "--parent-worktree", "path:/wt/epic", "--json")
    ]


def test_no_planned_parent_makes_no_lineage_call():
    sess, runner = session(runner_class=LineageRunner)
    sess.launch(planned(worktree=FORK_CHILD))
    assert not runner.calls_matching("worktree", "set")


def test_a_failed_lineage_link_is_reported_not_raised():
    # The worker is already running; losing its record would be the worse bug.
    class Refusing(LineageRunner):
        set_returncode = 1

    sess, _ = session(runner_class=Refusing)
    record = sess.launch(planned(worktree=replace(FORK_CHILD, parent_path="/wt/epic")))
    assert record.handle == "ctx_new000000001"
    assert "lineage unset" in (record.detail or "")


def test_a_planned_parent_resolves_the_worktree_id_only_once():
    # `_link_parent` and `_resolve_worktree` both need the created worktree's
    # id; a launch with a `parent_path` must not pay for `worker-show` twice
    # to learn the one id both of them use.
    sess, runner = session(runner_class=LineageRunner)
    sess.launch(planned(worktree=replace(FORK_CHILD, parent_path="/wt/epic")))
    assert len(runner.calls_matching("worker-show")) == 1
    assert len(runner.calls_matching("worktree", "set")) == 1


def test_fork_child_records_the_branch_orca_actually_created():
    # The requested `--name` is `bug/child`; Orca created
    # `psprowls/child`. The record must carry what exists on disk, because
    # the planner's name is a wish and this is the only place the truth is
    # observable.
    sess, runner = session()
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert runner.argv_after("--name", runner.calls_matching("worker-start")[0]) == "bug/child"
    assert record.worktree_branch == "psprowls/child"
    assert record.worktree_path == "/Users/pat/orca/workspaces/graph-works/child"


def test_the_branch_is_stored_short_not_as_a_full_ref():
    # `orca worktree show` reports `refs/heads/<branch>`; every consumer
    # (merge targets, `git branch -d`, the vault's `branch:` stamp) wants the
    # short name, so the ref prefix is stripped exactly once, here.
    sess, _ = session()
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert not record.worktree_branch.startswith("refs/")


def test_the_worktree_is_resolved_by_id_from_the_worker_payload():
    # `worker-show`'s `worker.worktree_id` is the `<repoId>::<path>` selector
    # `worktree show` takes; nothing here reconstructs a selector by hand.
    sess, runner = session()
    sess.launch(planned(worktree=FORK_CHILD))
    show = runner.calls_matching("worktree", "show")[0]
    assert runner.argv_after("--worktree", show) == (
        "id:a5d7cb85-fc68-4596-bffd-ecf75466124a::/Users/pat/orca/workspaces/graph-works/child"
    )
    # `worktree show` is top-level, not `orchestration worktree show` — the
    # subsequence match above can't tell those two argvs apart, and the
    # argv is the vendor contract, so pin the prefix explicitly.
    assert "orchestration" not in show


TOP_LEVEL = WorktreeAction(
    action="create-top-level", path=None, branch="bug/top", base_branch="main", exists=None, parent_path=None
)


def test_create_top_level_reads_back_the_same_way():
    # The 2026-08-13 observation was a `new-top-level` dispatch and the
    # 2026-08-15 one was `new-child`; the rename is the same either way, so
    # the read-back must be too.
    sess, _ = session(repo_selector="name:agent-workspace")
    record = sess.launch(planned(worktree=TOP_LEVEL))
    assert record.worktree_branch == "psprowls/child"
    assert record.worktree_path == "/Users/pat/orca/workspaces/graph-works/child"


def test_reuse_reports_its_given_path_and_makes_no_extra_call():
    # Nothing was created, so there is nothing to learn and no call to pay
    # for. The branch stays unknown rather than being copied from the plan.
    sess, runner = session()
    record = sess.launch(planned())
    assert record.worktree_path == "/tmp/wt"
    assert record.worktree_branch is None
    assert not runner.calls_matching("worktree", "show")
    assert not runner.calls_matching("worker-show")


def test_main_reports_its_given_path_and_makes_no_extra_call():
    sess, runner = session()
    action = WorktreeAction(action="main", path="/repo", branch="main", base_branch=None, exists=True, parent_path=None)
    record = sess.launch(planned(worktree=action))
    assert record.worktree_path == "/repo"
    assert record.worktree_branch is None
    assert not runner.calls_matching("worktree", "show")


def test_a_mismatch_produces_a_normal_record_and_nothing_else():
    # The rename is unconditional, so a mismatch check that raised would fail
    # every dispatch this backend makes. This is the test that pins "record,
    # do not reconcile".
    sess, _ = session()
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert record.worktree_branch != FORK_CHILD.branch
    assert record.key == "my-slug#execute"
    assert record.handle == "ctx_new000000001"
    assert record.state == "pending"


def test_the_start_payload_id_short_circuits_the_worker_show_call():
    # If Orca ever returns the worktree id from `worker-start`, the extra
    # per-launch call disappears with no further change.
    started = json.loads(fixture("worker_start"))
    started["result"]["worktreeId"] = (
        "a5d7cb85-fc68-4596-bffd-ecf75466124a::/Users/pat/orca/workspaces/graph-works/child"
    )

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-start" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(started), stderr="")
            return super().__call__(argv)

    runner = Runner(ROUTES)
    sess = OrcaBackend(run=runner, repo_selector="name:agent-workspace").open_session(TARGET)
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert record.worktree_branch == "psprowls/child"
    assert not runner.calls_matching("worker-show")


def test_an_unreadable_worker_leaves_both_fields_unknown():
    # `worker-show` failing is not a reason to undo a launch that already
    # started a real worker — the same treatment `_heartbeat` gives it.
    routes = [r for r in ROUTES if r[0] != ("worker-show",)]

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-show" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=1, stdout="", stderr="worker_identity_changed")
            return super().__call__(argv)

    runner = Runner(routes)
    sess = OrcaBackend(run=runner, repo_selector="name:agent-workspace").open_session(TARGET)
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert record.worktree_path is None
    assert record.worktree_branch is None
    assert record.handle == "ctx_new000000001"


def test_a_worker_with_no_worktree_id_leaves_both_fields_unknown():
    shown = json.loads(fixture("worker_show_created"))
    del shown["result"]["worker"]["worktree_id"]

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-show" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(shown), stderr="")
            return super().__call__(argv)

    runner = Runner(ROUTES)
    sess = OrcaBackend(run=runner, repo_selector="name:agent-workspace").open_session(TARGET)
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert (record.worktree_path, record.worktree_branch) == (None, None)
    assert not runner.calls_matching("worktree", "show")


def test_an_unreadable_worktree_leaves_both_fields_unknown():
    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worktree" in argv and "show" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=1, stdout="", stderr="worktree_not_found")
            return super().__call__(argv)

    runner = Runner(ROUTES)
    sess = OrcaBackend(run=runner, repo_selector="name:agent-workspace").open_session(TARGET)
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert (record.worktree_path, record.worktree_branch) == (None, None)


def test_a_worktree_row_missing_path_and_branch_leaves_both_unknown():
    shown = json.loads(fixture("worktree_show_renamed"))
    shown["result"]["worktree"] = {"id": "x::y"}

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worktree" in argv and "show" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(shown), stderr="")
            return super().__call__(argv)

    runner = Runner(ROUTES)
    sess = OrcaBackend(run=runner, repo_selector="name:agent-workspace").open_session(TARGET)
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert (record.worktree_path, record.worktree_branch) == (None, None)


def test_a_branch_that_is_not_a_full_ref_is_stored_verbatim():
    shown = json.loads(fixture("worktree_show_renamed"))
    shown["result"]["worktree"]["branch"] = "detached-thing"

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worktree" in argv and "show" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(shown), stderr="")
            return super().__call__(argv)

    runner = Runner(ROUTES)
    sess = OrcaBackend(run=runner, repo_selector="name:agent-workspace").open_session(TARGET)
    record = sess.launch(planned(worktree=FORK_CHILD))
    assert record.worktree_branch == "detached-thing"


def test_enumeration_does_not_claim_a_worktree_it_never_read():
    # Deliberate scope line: read-back happens at dispatch, where the plan is
    # formed before any worker exists to ask. `workers()` reconstructs from
    # `task-list`/`worker-list`, which carry no worktree, so it reports
    # unknown rather than a value it did not fetch.
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
