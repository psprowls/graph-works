"""Opening a session: bind by objective, or create."""

from __future__ import annotations

import json

import pytest
from orca_fakes import FakeRunner, fixture
from subagents_io.backend import BackendError, DispatchBackend, UnsupportedMode
from subagents_io.dispatch import DISPATCH_MODES, PlannedDispatch, WorktreePlacement
from workflow_orca import OrcaBackend
from workflow_orca._cli import OrcaResult

TARGET = "auto-drive:2026-08-14-epic-feature-orca-dispatch-backend"

FOUND = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-use",), "run_create"),
]
ABSENT = [
    (("run-list", "--cursor"), "run_list_page2"),
    (("run-list",), "run_list"),
    (("run-create",), "run_create"),
]


def test_the_backend_declares_its_capabilities():
    backend = OrcaBackend(run=FakeRunner([]))
    assert backend.name == "orca"
    assert backend.supported_modes == DISPATCH_MODES
    assert not hasattr(backend, "provisions_worktrees")


def test_the_backend_satisfies_the_runtime_checkable_protocol():
    assert isinstance(OrcaBackend(run=FakeRunner([])), DispatchBackend)


def test_open_session_pages_run_list_until_the_objective_matches():
    # `run-list` is paginated: it returns `nextCursor` and takes `--cursor`.
    # An unpaged scan silently misses any Run past the first page, which is
    # the bug this test exists to prevent.
    runner = FakeRunner(FOUND)
    session = OrcaBackend(run=runner).open_session(TARGET)
    assert session.run_id == "run_4b284b42f4a3"
    assert len(runner.calls_matching("run-list")) == 2
    assert runner.calls_matching("run-list", "--cursor")


def test_the_reuse_path_binds_the_terminal_to_the_run():
    # `check --run <id>` fails with `consumer_fenced` unless the calling
    # terminal is bound to that Run. Verified against the live CLI; this is
    # the one place `run-use` is called, and it is not optional.
    runner = FakeRunner(FOUND)
    OrcaBackend(run=runner).open_session(TARGET)
    binds = runner.calls_matching("run-use", "--id")
    assert len(binds) == 1
    assert runner.argv_after("--id", binds[0]) == "run_4b284b42f4a3"


def test_the_create_path_creates_and_does_not_re_bind():
    # `run-create` binds the calling terminal on its own, so calling
    # `run-use` after it would be a second mutation of global state for no
    # gain.
    runner = FakeRunner(ABSENT)
    session = OrcaBackend(run=runner).open_session("auto-drive:brand-new")
    assert session.run_id == "run_9c1e837caf86"
    creates = runner.calls_matching("run-create", "--objective")
    assert len(creates) == 1
    assert runner.argv_after("--objective", creates[0]) == "auto-drive:brand-new"
    assert not runner.calls_matching("run-use")


def test_the_create_path_exhausts_every_page_before_creating():
    runner = FakeRunner(ABSENT)
    OrcaBackend(run=runner).open_session("auto-drive:brand-new")
    assert len(runner.calls_matching("run-list")) == 2


def test_objective_matching_is_exact_not_prefix():
    # `auto-drive:my-slug` must not bind `auto-drive:my-slug-2`. A prefix
    # match here would silently join two items' workers into one ledger.
    runner = FakeRunner(ABSENT)
    session = OrcaBackend(run=runner).open_session("auto-drive:2026-08-13-test-gap")
    assert session.run_id == "run_9c1e837caf86"


def test_the_session_name_is_the_objective():
    runner = FakeRunner(FOUND)
    session = OrcaBackend(run=runner).open_session(TARGET)
    assert session.name == TARGET


LAUNCH = [
    (("task-list",), "task_list"),
    (("task-create",), "task_create"),
    (("worker-start",), "worker_start"),
    (("worker-show",), "worker_show_created"),
    (("worktree", "show"), "worktree_show_renamed"),
]

PROMPT = "Run /gw:workflow my-slug.\nDispatch key: my-slug#execute\nSend worker_done when done."


def planned(**overrides):
    fields = dict(
        agent="claude",
        key="gw-execute-my-slug-2f1a9c3d",
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


def session(routes=LAUNCH):
    runner = FakeRunner([*FOUND, *routes])
    return OrcaBackend(run=runner).open_session(TARGET), runner


def test_launch_passes_path_selector_only():
    # `worker-start --help`: creation flags are rejected for existing
    # worktrees. The planner resolved an existing path; the backend never
    # creates, forks or links a worktree.
    sess, runner = session()
    sess.launch(planned())
    start = runner.calls_matching("worker-start")[0]
    assert runner.argv_after("--worktree", start) == "path:/tmp/wt"
    for flag in ("--name", "--base-branch", "--repo", "new-top-level", "new-child"):
        assert flag not in start
    assert not runner.calls_matching("worktree", "set")
    assert not runner.calls_matching("worktree", "show")


def test_a_detached_placement_launches_like_any_other_path():
    sess, runner = session()
    sess.launch(planned(worktree=WorktreePlacement(path="/ws/.gw/worktrees/demo/main", branch=None, parent_path=None)))
    start = runner.calls_matching("worker-start")[0]
    assert runner.argv_after("--worktree", start) == "path:/ws/.gw/worktrees/demo/main"


def test_the_prompt_reaches_spec_unedited():
    # Never edited, wrapped, or re-worded: it already carries its own
    # `Dispatch key:` line and worker_done instruction.
    sess, runner = session()
    sess.launch(planned())
    create = runner.calls_matching("task-create")[0]
    assert runner.argv_after("--spec", create).split("\n", 1)[1] == PROMPT
    assert runner.argv_after("--task-title", create) == "gw-execute-my-slug-2f1a9c3d"
    assert runner.argv_after("--display-name", create) == "gw-execute-my-slug-2f1a9c3d"


def test_both_orca_name_fields_are_the_one_key():
    # One identifier end-to-end: the string a human reads in the task row IS
    # the string the ledger round-trip looks up.
    sess, runner = session()
    sess.launch(planned())
    create = runner.calls_matching("task-create")[0]
    title = runner.argv_after("--task-title", create)
    assert title == runner.argv_after("--display-name", create)
    assert title.startswith("gw-")
    assert len(title) <= 64


def test_model_alone_emits_model_and_no_effort():
    sess, runner = session()
    sess.launch(planned(model="claude-sonnet-5"))
    start = runner.calls_matching("worker-start")[0]
    assert runner.argv_after("--model", start) == "claude-sonnet-5"
    assert "--effort" not in start


def test_effort_without_model_is_refused_before_task_create():
    sess, runner = session()
    with pytest.raises(BackendError, match="Set a model or clear reasoning_effort"):
        sess.launch(planned(model=None, reasoning_effort="high"))
    assert not runner.calls_matching("task-create")


def test_model_and_effort_together_emit_both():
    sess, runner = session()
    sess.launch(planned(model="claude-opus-5", reasoning_effort="high"))
    start = runner.calls_matching("worker-start")[0]
    assert runner.argv_after("--model", start) == "claude-opus-5"
    assert runner.argv_after("--effort", start) == "high"


def test_neither_model_nor_effort_emits_neither():
    sess, runner = session()
    sess.launch(planned())
    start = runner.calls_matching("worker-start")[0]
    assert "--model" not in start
    assert "--effort" not in start


def test_the_record_handle_is_the_dispatch_id():
    sess, _ = session()
    record = sess.launch(planned())
    assert record.handle == "ctx_new000000001"
    assert record.key == "gw-execute-my-slug-2f1a9c3d"
    assert record.state == "pending"


def test_a_mode_outside_the_set_is_refused():
    sess, _ = session()
    with pytest.raises(UnsupportedMode):
        sess.launch(planned(mode="telepathy"))


def test_a_duplicate_key_is_refused():
    # The session is the ledger. Silently returning the existing record would
    # make a re-dispatch after a failure read as a success.
    sess, _ = session()
    with pytest.raises(BackendError, match="already"):
        sess.launch(planned(key="2026-08-13-test-gap-code-wiki-smoke-script#execute"))


def test_task_create_with_no_id_is_refused():
    # A degraded "success" response is still a response `launch()` cannot act
    # on: the same treatment `worker-start`'s missing `dispatchId` gets below.
    created = json.loads(fixture("task_create"))
    del created["result"]["task"]["id"]

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "task-create" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(created), stderr="")
            return super().__call__(argv)

    runner = Runner([*FOUND, (("task-list",), "task_list")])
    sess = OrcaBackend(run=runner).open_session(TARGET)
    with pytest.raises(BackendError, match="no id"):
        sess.launch(planned())


def test_run_create_with_no_id_is_refused():
    created = json.loads(fixture("run_create"))
    del created["result"]["run"]["id"]

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "run-create" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(created), stderr="")
            return super().__call__(argv)

    runner = Runner([(("run-list", "--cursor"), "run_list_page2"), (("run-list",), "run_list")])
    with pytest.raises(BackendError, match="no id"):
        OrcaBackend(run=runner).open_session("auto-drive:brand-new")


def test_worker_start_with_no_dispatch_id_is_refused():
    # Unlike the read paths, `launch()` already started a real worker by the
    # time this response comes back — a missing handle is raised here rather
    # than degraded to a `WorkerRecord` no later call could ever address.
    started = json.loads(fixture("worker_start"))
    del started["result"]["dispatchId"]

    class Runner(FakeRunner):
        def __call__(self, argv):
            if "worker-start" in argv:
                self.calls.append(tuple(argv))
                return OrcaResult(returncode=0, stdout=json.dumps(started), stderr="")
            return super().__call__(argv)

    runner = Runner([*FOUND, (("task-list",), "task_list"), (("task-create",), "task_create")])
    sess = OrcaBackend(run=runner).open_session(TARGET)
    with pytest.raises(BackendError, match="no dispatchId"):
        sess.launch(planned())
