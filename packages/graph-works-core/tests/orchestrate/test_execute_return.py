"""Durable return preparation against real workspace Git checkouts."""

from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
from _transaction_helpers import _git, _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.orchestrate.workspace_prepare import run_prepare_workspace
from okf_io import load

TODAY = date(2026, 10, 5)
PATH = "work/feature-return"
COVERAGE = f"{PATH}/references/03-execute-coverage.md"
PLAN = f"{PATH}/references/02-plan.md"
SCOPE = ("Implement Task 4 in the canonical plan",)


def git(root, *args):
    return _git(root, *args).strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture update")


def document(layout):
    return load(layout.bundle_dir / f"{PATH}.md")


@pytest.fixture
def ws(tmp_path):
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Return")).layout
    write(
        layout.bundle_dir / f"{PATH}.md",
        "---\ntype: Feature\ntitle: Return\ndescription: d\nstatus: stable\n"
        "work_status: in-progress\nphase: execute\neffort: medium\nowner: pat\n"
        "opened: 2026-10-05\nupdated: 2026-10-05\naffects: [gw:workspace]\n---\n\n## Summary\nd\n",
    )
    write(layout.bundle_dir / COVERAGE, "- [x] Original acceptance delivered\n")
    write(layout.bundle_dir / PLAN, "# Plan\n")
    _init_git(layout.root)
    git(layout.root, "config", "core.autocrlf", "false")
    prepared = run_prepare_workspace(layout, PATH, today=TODAY, apply=True)
    assert prepared.refusal is None and prepared.applied, prepared
    content = Path(prepared.steps[-1].worktree)
    doc = document(layout)
    doc.set("phase", "finish")
    doc.save()
    write(layout.bundle_dir / PLAN, "# Plan\n\n### Task 4\nNew canonical task\n")
    commit(layout.root)
    return layout, content


def advance(layout, **kwargs):
    return stage.run_stage_advance(
        layout, PATH, today=TODAY, return_=True, return_scope=SCOPE, dry_run=False, infer_worktree=False, **kwargs
    )


def test_return_with_explicit_scope_reopens_content_coverage_and_records_intent(ws):
    layout, content = ws
    before = (layout.bundle_dir / COVERAGE).read_bytes()
    result = advance(layout)
    assert result.outcome.plan.refusal is None, result
    assert result.application.ok, result.application
    record = document(layout).fm_data(dates="iso")["execute_return"]
    assert document(layout).fm_data()["phase"] == "execute"
    assert record["state"] == "active"
    assert record["plan_sha256"] == sha256((layout.bundle_dir / PLAN).read_bytes()).hexdigest()
    assert "Report state: prepared" in (content / "okf" / COVERAGE).read_text(encoding="utf-8")
    assert git(content, "log", "-1", "--format=%s").startswith("workspace: reopen")
    assert (layout.bundle_dir / COVERAGE).read_bytes() == before
    assert not tuple((layout.cache_dir / "execute-returns").glob("*.json"))


def snapshot(layout, content):
    return (
        git(layout.root, "status", "--porcelain"),
        git(content, "status", "--porcelain"),
        git(layout.root, "rev-parse", "HEAD"),
        git(content, "rev-parse", "HEAD"),
        sorted(str(p.relative_to(layout.cache_dir)) for p in layout.cache_dir.rglob("*")),
    )


def test_dry_run_writes_nothing(ws):
    layout, content = ws
    before = snapshot(layout, content)
    result = stage.run_stage_advance(layout, PATH, today=TODAY, return_=True, return_scope=SCOPE)
    assert result.outcome.plan.refusal is None
    assert result.execute_return is not None
    assert snapshot(layout, content) == before


@pytest.mark.parametrize(
    ("scope", "return_", "reason"),
    [
        ((), True, "return-scope-required"),
        (SCOPE, False, "return-scope-invalid"),
        ((" ",), True, "return-scope-invalid"),
    ],
)
def test_invalid_scope_refuses_without_effects(ws, scope, return_, reason):
    layout, content = ws
    before = snapshot(layout, content)
    result = stage.run_stage_advance(layout, PATH, today=TODAY, return_=return_, return_scope=scope, dry_run=False)
    assert result.outcome.plan.refusal == reason
    assert snapshot(layout, content) == before


def test_malformed_execute_return_refuses(ws):
    layout, _ = ws
    doc = document(layout)
    doc.set("execute_return", {"state": "active"})
    doc.save()
    result = advance(layout)
    assert result.outcome.plan.refusal == "return-metadata-invalid"


@pytest.mark.parametrize("damage", ["dirty", "missing"])
def test_unverified_content_refuses(ws, damage):
    layout, content = ws
    if damage == "dirty":
        write(content / "untracked", "dirt")
    else:
        doc = document(layout)
        stamps = doc.fm_data()["repo_stamps"]
        stamps["_workspace"]["worktree"] = str(content / "missing")
        doc.set("repo_stamps", stamps)
        doc.save()
    result = advance(layout)
    assert result.outcome.plan.refusal == "return-destination-unverified"
    assert document(layout).fm_data()["phase"] == "finish"


@pytest.mark.parametrize("mode", ["disabled", "unstamped"])
def test_canonical_bundle_uses_one_transaction(ws, mode):
    layout, content = ws
    doc = document(layout)
    if mode == "unstamped":
        doc.delete("repo_stamps")
        doc.save()
    else:
        write(layout.local_manifest_path, "workflow:\n  workspace_commits: off\n")
    old = git(content, "rev-parse", "HEAD")
    result = advance(layout)
    assert result.application.ok, result
    assert "Report state: prepared" in (layout.bundle_dir / COVERAGE).read_text(encoding="utf-8")
    assert git(content, "rev-parse", "HEAD") == old
    assert not tuple((layout.cache_dir / "execute-returns").glob("*.json"))


@pytest.mark.parametrize("change", ["missing-coverage", "no-plan", "custom-coverage"])
def test_optional_and_configured_artifacts(ws, change):
    layout, content = ws
    member = COVERAGE
    if change == "no-plan":
        (layout.bundle_dir / PLAN).unlink()
        commit(layout.root)
    else:
        (content / "okf" / COVERAGE).unlink()
        commit(content)
        if change == "custom-coverage":
            dispatch = layout.root / "dispatch.yaml"
            write(
                dispatch,
                dispatch.read_text(encoding="utf-8").replace(
                    "pipeline:\n", "pipeline:\n  artifacts:\n    execute:\n      file: 09-cov.md\n", 1
                ),
            )
            member = f"{PATH}/references/09-cov.md"
    result = advance(layout)
    assert result.application.ok, result
    record = document(layout).fm_data()["execute_return"]
    assert (content / "okf" / member).exists()
    if change == "no-plan":
        assert "plan" not in record and "plan_sha256" not in record


@pytest.mark.parametrize("failure", ["raise", "failed-application"])
def test_interrupted_publish_resumes_exact_intent(ws, monkeypatch, failure):
    from graph_works_core.orchestrate.execute_return import pending_return

    layout, content = ws
    original = stage.apply_mutation

    def fail(*args, **kwargs):
        if failure == "raise":
            raise OSError("canonical interrupted")

        # A failed read guard is a real transaction failure with no canonical effect.
        def reject():
            raise OSError("canonical interrupted")

        return original(*args, **{**kwargs, "validate_read_set": reject})

    monkeypatch.setattr(stage, "apply_mutation", fail)
    try:
        first = advance(layout)
        assert not first.application.ok
        assert "pending" in " ".join(first.warnings)
    except OSError:
        assert failure == "raise"
    op = pending_return(layout, PATH)
    assert op is not None and op.state == "content-committed"
    assert document(layout).fm_data()["phase"] == "finish"
    head = git(content, "rev-parse", "HEAD")
    changed = stage.run_stage_advance(layout, PATH, today=TODAY, return_=True, return_scope=("different",))
    assert changed.outcome.plan.refusal == "return-pending"
    finished = stage.run_stage_advance(layout, PATH, today=TODAY)
    assert finished.outcome.plan.refusal == "return-pending"
    monkeypatch.setattr(stage, "apply_mutation", original)
    result = advance(layout)
    assert result.application.ok, result
    assert document(layout).fm_data()["execute_return"]["id"] == op.return_id
    assert git(content, "rev-parse", "HEAD") == head
    assert (content / "okf" / COVERAGE).read_text(encoding="utf-8").count("## Returned scope") == 1
    assert pending_return(layout, PATH) is None


def test_exact_published_return_recovers_after_journal_cleanup_crash(ws, monkeypatch):
    from graph_works_core.orchestrate import execute_return as returns

    layout, content = ws
    original = returns.finish_return
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    first = advance(layout)
    assert first.application.ok, first
    assert returns.pending_return(layout, PATH) is not None
    before = (git(layout.root, "rev-parse", "HEAD"), git(content, "rev-parse", "HEAD"))
    monkeypatch.setattr(returns, "finish_return", original)
    replay = advance(layout, expected_phase="finish")
    assert replay.outcome.plan.refusal is None, replay
    assert returns.pending_return(layout, PATH) is None
    assert (git(layout.root, "rev-parse", "HEAD"), git(content, "rev-parse", "HEAD")) == before


@pytest.mark.parametrize("change", ["coverage", "plan", "page", "placement"])
def test_changed_evidence_before_publish_refuses(ws, monkeypatch, change):
    from graph_works_core.orchestrate import execute_return as returns

    layout, content = ws
    original = returns.apply_content

    def changed(*args, **kwargs):
        op = original(*args, **kwargs)
        if change == "coverage":
            write(content / "okf" / COVERAGE, "tampered\n")
        elif change == "plan":
            write(layout.bundle_dir / PLAN, "changed plan\n")
        else:
            doc = document(layout)
            if change == "page":
                doc.set("owner", "someone-else")
            else:
                stamps = doc.fm_data()["repo_stamps"]
                stamps["_workspace"]["worktree"] = str(content / "missing")
                doc.set("repo_stamps", stamps)
            doc.save()
        return op

    monkeypatch.setattr(returns, "apply_content", changed)
    result = advance(layout)
    assert not result.application.ok, result
    assert document(layout).fm_data()["phase"] == "finish"
    assert returns.pending_return(layout, PATH) is not None


@pytest.mark.parametrize("raw", ["{", "[]", '{"version": true}', '{"version": 1, "path": "different"}'])
def test_malformed_pending_return_fences_return_and_finish(ws, raw):
    from graph_works_core.orchestrate.execute_return import pending_return

    layout, _ = ws
    journal = layout.cache_dir / "execute-returns" / "feature-return.json"
    write(journal, raw)
    assert pending_return(layout, PATH).malformed
    assert advance(layout).outcome.plan.refusal == "return-pending"
    assert stage.run_stage_advance(layout, PATH, today=TODAY).outcome.plan.refusal == "return-pending"


def test_pending_publication_blocks_next_and_dispatch_plan(ws, monkeypatch):
    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.orchestrate.commands import run_orchestrate
    from graph_works_core.work.commands import run_next

    layout, _ = ws
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    assert advance(layout).application.ok
    preview = run_next(layout, PATH)
    assert preview.route.dispatch is None
    assert "pending" in preview.route.reason
    dispatched = run_orchestrate(layout, PATH)
    assert not dispatched.dispatches
    assert any("pending" in block.reason for block in dispatched.blocked)


@pytest.mark.parametrize("root", ["content", "canonical"])
def test_commit_failure_retains_fence_and_finish(ws, monkeypatch, root):
    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace import transactions
    from graph_works_core.workspace.commits import CommitOutcome
    from graph_works_core.workspace.errors import WorkspaceError

    layout, content = ws
    original = transactions.commit_workspace
    target = content if root == "content" else layout.root

    def fail(checkout, request, *args, **kwargs):
        if checkout.root == target:
            if root == "canonical":
                git(checkout.root, "add", f"okf/{PATH}.md")
            return CommitOutcome("failed", None, request.subject, (), "test commit failure")
        return original(checkout, request, *args, **kwargs)

    monkeypatch.setattr(transactions, "commit_workspace", fail)
    try:
        result = advance(layout)
        assert not result.outcome.written
        assert not result.application.ok
    except WorkspaceError:
        assert root == "content"
    assert document(layout).fm_data()["phase"] == "finish"
    assert pending_return(layout, PATH) is not None
    if root == "canonical":
        assert git(layout.root, "status", "--porcelain") == ""


def test_pending_return_rejects_saved_dispatch_plan_before_orca(ws, monkeypatch):
    from datetime import UTC, datetime

    from fake_orca_port import FakeOrcaPort
    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.orchestrate.dispatch import run_dispatch

    layout, _ = ws
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    assert advance(layout).application.ok
    port = FakeOrcaPort()
    plan = {
        "path": PATH,
        "dispatches": [
            {
                "key": "saved",
                "path": PATH,
                "phase": "execute",
                "mode": "autonomous",
                "agent": "claude",
                "prompt": "old plan",
                "worktree": {"action": "reuse"},
            }
        ],
    }
    result = run_dispatch(
        layout,
        "saved",
        plan=plan,
        run_id="run_1",
        port=port,
        today=TODAY,
        clock=lambda: datetime(2026, 10, 5, tzinfo=UTC),
    )
    assert result.failure is not None and "pending" in result.failure.detail
    assert not port.calls


@pytest.mark.parametrize("boundary", ["before-content", "after-content-commit"])
def test_intent_journal_replays_only_proven_content_progress(ws, monkeypatch, boundary):
    from graph_works_core.orchestrate import execute_return as returns

    layout, content = ws
    original = returns.write_operation

    def interrupt(layout, op):
        if boundary == "before-content" and op.state == "intent":
            original(layout, op)
            raise OSError("crash after intent")
        if boundary == "after-content-commit" and op.state == "content-committed":
            raise OSError("crash after content commit")
        original(layout, op)

    monkeypatch.setattr(returns, "write_operation", interrupt)
    with pytest.raises(OSError):
        advance(layout)
    op = returns.pending_return(layout, PATH)
    assert op.state == "intent"
    head = git(content, "rev-parse", "HEAD")
    monkeypatch.setattr(returns, "write_operation", original)
    result = advance(layout)
    assert result.application.ok
    assert document(layout).fm_data()["execute_return"]["id"] == op.return_id
    if boundary == "after-content-commit":
        assert git(content, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("damage", ["utf8", "escape", "commit-off"])
def test_unreadable_or_uncommittable_destination_refuses(ws, tmp_path, damage):
    layout, content = ws
    target = content / "okf" / COVERAGE
    if damage == "utf8":
        target.write_bytes(b"\xff")
        commit(content)
    elif damage == "escape":
        external = tmp_path / "external.md"
        write(external, "outside\n")
        target.unlink()
        target.symlink_to(external)
        commit(content)
    else:
        write(content / "workspace.local.yaml", "workflow:\n  workspace_commits: off\n")
    result = advance(layout)
    assert result.outcome.plan.refusal == "return-destination-unverified"
    assert document(layout).fm_data()["phase"] == "finish"
    assert not tuple((layout.cache_dir / "execute-returns").glob("*.json"))


def test_committed_publication_survives_unknown_commit_exit(ws, monkeypatch):
    from dataclasses import replace

    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace import transactions

    layout, _ = ws
    original = transactions.commit_workspace

    def unknown(checkout, request, *args, **kwargs):
        committed = original(checkout, request, *args, **kwargs)
        if checkout.root == layout.root:
            return replace(committed, status="failed", sha=None, reason="lost commit response")
        return committed

    monkeypatch.setattr(transactions, "commit_workspace", unknown)
    result = advance(layout)
    assert result.application.ok and result.outcome.written
    assert pending_return(layout, PATH) is None
    assert document(layout).fm_data()["phase"] == "execute"


def test_unknown_canonical_outcome_stays_fenced_until_exact_proof(ws, monkeypatch):
    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.work.commands import run_next
    from graph_works_core.workspace.errors import WorkspaceError

    layout, content = ws
    original_apply = stage.apply_mutation
    original_head, original_blob = returns._head, returns._committed_member

    def unavailable(*_args, **_kwargs):
        raise WorkspaceError("unavailable Git evidence")

    def lose_readback(*args, **kwargs):
        applied = original_apply(*args, **kwargs)
        monkeypatch.setattr(returns, "_head", unavailable)
        monkeypatch.setattr(returns, "_committed_member", unavailable)
        return applied

    monkeypatch.setattr(stage, "apply_mutation", lose_readback)
    result = advance(layout)
    assert not result.application.ok and not result.outcome.written
    assert returns.pending_return(layout, PATH) is not None
    assert run_next(layout, PATH).route.dispatch is None
    content_head = git(content, "rev-parse", "HEAD")
    monkeypatch.setattr(stage, "apply_mutation", original_apply)
    monkeypatch.setattr(returns, "_head", original_head)
    monkeypatch.setattr(returns, "_committed_member", original_blob)
    replay = advance(layout, expected_phase="finish")
    assert replay.outcome.plan.refusal is None
    assert returns.pending_return(layout, PATH) is None
    assert git(content, "rev-parse", "HEAD") == content_head


def test_completed_publication_with_different_scope_stays_pending(ws, monkeypatch):
    from graph_works_core.orchestrate import execute_return as returns

    layout, _ = ws
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    assert advance(layout).application.ok
    result = stage.run_stage_advance(
        layout, PATH, today=TODAY, expected_phase="finish", return_=True, return_scope=("different",), dry_run=False
    )
    assert result.outcome.plan.refusal == "return-pending"
    assert returns.pending_return(layout, PATH) is not None


@pytest.mark.parametrize("autocrlf", ["false", "input"])
def test_return_preserves_crlf_and_no_final_newline_in_committed_blob(ws, autocrlf):
    layout, content = ws
    git(layout.root, "config", "core.autocrlf", autocrlf)
    before = b"# Original\r\n- [x] Original acceptance delivered"
    (content / "okf" / COVERAGE).write_bytes(before)
    commit(content)
    result = advance(layout)
    assert result.application.ok
    assert (content / "okf" / COVERAGE).read_bytes().startswith(before + b"\r\n\r\n## Returned scope")


def test_pending_journal_rejects_malformed_fields_all_or_nothing(ws, monkeypatch):
    import json
    from copy import deepcopy

    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.workspace.execute_return import journal_path

    layout, _ = ws
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    assert advance(layout).application.ok
    path = journal_path(layout, PATH)
    valid = json.loads(path.read_text(encoding="utf-8"))
    for key, value in [
        ("destination", []),
        ("state", []),
        ("content_commit", None),
        ("version", True),
        ("record", {}),
        ("coverage_member", "../outside"),
        ("canonical_after_sha256", False),
        ("page_sha256", ""),
        ("plan_member", "/outside"),
        ("coverage_after_sha256", []),
    ]:
        raw = deepcopy(valid)
        raw[key] = value
        write(path, json.dumps(raw))
        assert returns.pending_return(layout, PATH).malformed, key
    raw = deepcopy(valid)
    raw["destination"]["identity"] = "relative"
    write(path, json.dumps(raw))
    assert returns.pending_return(layout, PATH).malformed
    path.unlink()
    path.mkdir()
    assert returns.pending_return(layout, PATH).malformed


def test_custom_bundle_layout_is_preserved(ws):
    from dataclasses import replace

    layout, content = ws
    # Both checkouts use the same authored relative layout; no basename guess.
    (layout.root / "okf").rename(layout.root / "knowledge")
    (content / "okf").rename(content / "knowledge")
    for root in (layout.root, content):
        manifest = root / "workspace.yaml"
        write(manifest, manifest.read_text(encoding="utf-8") + "\nlayout:\n  bundle_dir: knowledge\n")
        commit(root)
    layout = replace(layout, bundle_dir=layout.root / "knowledge")
    result = advance(layout)
    assert result.application.ok, result
    assert (content / "knowledge" / COVERAGE).read_text(encoding="utf-8").count("## Returned scope") == 1
    assert not (content / "okf").exists()


@pytest.mark.parametrize("replay", [False, True])
def test_content_lock_is_held_through_publication_cleanup(ws, monkeypatch, replay):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.workspace.layout import layout_for
    from graph_works_core.workspace.transactions import held_bundle_lock

    layout, content = ws
    cleanup = returns.finish_return
    if replay:
        monkeypatch.setattr(returns, "finish_return", lambda *_: None)
        assert advance(layout).application.ok
    enter = Event()
    attempting = Event()
    acquired = Event()
    observed = []
    content_layout = layout_for(content)

    def competing_writer():
        assert enter.wait(10)
        attempting.set()
        # A real new thread has no inherited held_bundle_lock ContextVar.
        with held_bundle_lock(content_layout):
            observed.append(returns.pending_return(layout, PATH) is not None)
            acquired.set()
            write(content / "okf" / COVERAGE, "- [x] Original acceptance delivered\n")
            commit(content)

    def paused_cleanup(workspace, operation):
        enter.set()
        assert attempting.wait(10)
        entered_before_cleanup = acquired.wait(0.25)
        cleanup(workspace, operation)
        assert not entered_before_cleanup, "cooperating content writer entered before journal cleanup"

    monkeypatch.setattr(returns, "finish_return", paused_cleanup)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(competing_writer)
        try:
            result = advance(layout, expected_phase="finish")
            assert result.outcome.plan.refusal is None
        finally:
            enter.set()
        writer.result(timeout=10)
    assert observed == [False]


def test_saved_dispatch_admission_serializes_concurrent_return(ws, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from threading import Event

    from fake_orca_port import FakeOrcaPort
    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.orchestrate.dispatch import run_dispatch

    layout, _content = ws
    code = tmp_path / "dispatch-code"
    code.mkdir()
    _init_git(code)
    code_worktree = tmp_path / "dispatch-worktree"
    git(code, "worktree", "add", "-b", "feature/return", str(code_worktree))
    manifest = layout.manifest_path
    write(
        manifest,
        manifest.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {code.as_posix()}"
        ),
    )
    commit(layout.root)
    port = FakeOrcaPort(repos=[{"id": "repo1", "path": str(code)}])
    port.worktrees["id:wt1"] = {
        "id": "wt1",
        "repo_id": "repo1",
        "path": str(code_worktree),
        "branch": "feature/return",
        "display_name": "feature/return",
        "is_main": False,
        "parent_id": None,
    }
    plan = {
        "path": PATH,
        "dispatches": [
            {
                "key": "saved-concurrent",
                "path": PATH,
                "phase": "execute",
                "mode": "autonomous",
                "agent": "claude",
                "prompt": "saved plan",
                "worktree": {
                    "action": "create-top-level",
                    "path": None,
                    "branch": "feature/return",
                    "base_branch": "main",
                    "exists": False,
                    "parent_path": None,
                },
                "repo": {"name": "code", "path": str(code), "source": "manifest"},
            }
        ],
    }
    admission = Event()
    attempted = Event()
    return_done = Event()
    pending_at_effects = []
    entered_during_dispatch = []
    original_list, original_create, original_start = port.task_list, port.task_create, port.worker_start

    def attempt_return():
        assert admission.wait(10)
        attempted.set()
        try:
            advance(layout)
        except OSError:
            pass
        finally:
            return_done.set()

    def interrupted_publish(*_args, **_kwargs):
        raise OSError("interrupt canonical return publication")

    monkeypatch.setattr(stage, "apply_mutation", interrupted_publish)

    def task_list(run_id):
        admission.set()
        assert attempted.wait(10)
        entered_during_dispatch.append(return_done.wait(3))
        return original_list(run_id)

    def task_create(*args, **kwargs):
        pending_at_effects.append(returns.pending_return(layout, PATH) is not None)
        return original_create(*args, **kwargs)

    def worker_start(*args, **kwargs):
        pending_at_effects.append(returns.pending_return(layout, PATH) is not None)
        return original_start(*args, **kwargs)

    monkeypatch.setattr(port, "task_list", task_list)
    monkeypatch.setattr(port, "task_create", task_create)
    monkeypatch.setattr(port, "worker_start", worker_start)
    with ThreadPoolExecutor(max_workers=1) as pool:
        returner = pool.submit(attempt_return)
        try:
            result = run_dispatch(
                layout,
                "saved-concurrent",
                plan=plan,
                run_id="run_1",
                port=port,
                today=TODAY,
                clock=lambda: datetime(2026, 10, 5, tzinfo=UTC),
                sleep=lambda _: None,
            )
        finally:
            admission.set()
        returner.result(timeout=10)
    # Placement records take the existing decision lock: reaching record proves
    # admission ownership did not make that nested writer deadlock.
    assert "task_create" in port.names() and "worker_start" in port.names()
    assert result.failure is None or result.failure.step == "record", result.failure
    assert entered_during_dispatch == [False]
    assert pending_at_effects == [False, False]
    assert returns.pending_return(layout, PATH) is not None


def test_return_dry_run_creates_no_control_plane_locks(ws):
    import shutil

    layout, content = ws
    # Remove fixture setup's control-plane history: no pre-existing decision or
    # admission lock may conceal an accidental dry-run write.
    shutil.rmtree(layout.cache_dir)
    content_cache = content / ".gw" / "cache"
    if content_cache.exists():
        shutil.rmtree(content_cache)
    before = snapshot(layout, content)
    result = stage.run_stage_advance(
        layout, PATH, today=TODAY, expected_phase="finish", return_=True, return_scope=SCOPE
    )
    assert result.outcome.plan.refusal is None
    assert result.execute_return is not None
    assert snapshot(layout, content) == before
    assert not layout.cache_dir.exists()
    assert not content_cache.exists()


def test_saved_dispatch_waits_for_return_and_refuses_pending_without_calls(ws, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from datetime import UTC, datetime
    from threading import Event

    from fake_orca_port import FakeOrcaPort
    from graph_works_core.orchestrate import dispatch
    from graph_works_core.orchestrate import execute_return as returns

    layout, _ = ws
    return_holds_admission = Event()
    dispatch_attempting = Event()
    release_return = Event()
    original_admission = dispatch.return_dispatch_admission

    def pause_candidate(_candidate):
        return_holds_admission.set()
        assert release_return.wait(10)

    def interrupt(*_args, **_kwargs):
        raise OSError("canonical return interrupted")

    def returner():
        with pytest.raises(OSError, match="canonical return interrupted"):
            advance(layout, before_apply=pause_candidate)

    @contextmanager
    def observe_admission(*args, **kwargs):
        dispatch_attempting.set()
        with original_admission(*args, **kwargs):
            yield

    monkeypatch.setattr(stage, "apply_mutation", interrupt)
    monkeypatch.setattr(dispatch, "return_dispatch_admission", observe_admission)
    port = FakeOrcaPort()
    plan = {
        "path": PATH,
        "dispatches": [
            {
                "key": "queued",
                "path": PATH,
                "phase": "execute",
                "mode": "autonomous",
                "agent": "claude",
                "prompt": "saved",
                "worktree": {"action": "reuse"},
            }
        ],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        returning = pool.submit(returner)
        assert return_holds_admission.wait(10)
        dispatched = pool.submit(
            dispatch.run_dispatch,
            layout,
            "queued",
            plan=plan,
            run_id="run_1",
            port=port,
            today=TODAY,
            clock=lambda: datetime(2026, 10, 5, tzinfo=UTC),
        )
        try:
            assert dispatch_attempting.wait(10)
            assert not dispatched.done()
            assert not port.calls
        finally:
            release_return.set()
        returning.result(timeout=10)
        result = dispatched.result(timeout=10)
    assert result.failure is not None and result.failure.reason == "recovery-inspection"
    assert returns.pending_return(layout, PATH) is not None
    assert not port.calls


def complete(layout, **kwargs):
    return stage.run_stage_advance(layout, PATH, today=TODAY, expected_phase="execute", infer_worktree=False, **kwargs)


def report(layout, content, *, checked=True):
    rid = document(layout).fm_data()["execute_return"]["id"]
    target = content / "okf" / COVERAGE
    text = target.read_text(encoding="utf-8").replace("Report state: prepared", "Report state: reported")
    text = text.replace(f"- [ ] R1: {SCOPE[0]}", f"- [{'x' if checked else ' '}] R1: {SCOPE[0]} -- verified limitation")
    write(target, text)
    commit(content)
    return rid


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("old", "return-evidence-missing"),
        ("missing", "return-evidence-missing"),
        ("wrong-id", "return-evidence-missing"),
        ("prepared", "return-evidence-stale"),
        ("omitted", "return-evidence-incomplete"),
        ("duplicate", "return-evidence-incomplete"),
        ("utf8", "return-evidence-missing"),
    ],
)
def test_completion_refuses_unreported_or_incomplete_destination(ws, damage, reason):
    layout, content = ws
    assert advance(layout).application.ok
    target = content / "okf" / COVERAGE
    if damage == "old":
        write(target, "- [x] Original acceptance delivered\n")
    elif damage == "missing":
        target.unlink()
    elif damage == "utf8":
        target.write_bytes(b"\xff")
    elif damage != "prepared":
        rid = report(layout, content)
        text = target.read_text(encoding="utf-8")
        if damage == "wrong-id":
            text = text.replace(rid, "ret-20261005-00000000")
        elif damage == "omitted":
            text = "\n".join(line for line in text.splitlines() if "R1:" not in line) + "\n"
        else:
            text += f"- [x] R1: {SCOPE[0]} -- duplicate\n"
        write(target, text)
    if damage != "prepared":
        commit(content)
    before = document(layout).serialize()
    for dry_run in (True, False):
        result = complete(layout, dry_run=dry_run)
        assert result.outcome.plan.refusal == reason, result
        assert not result.outcome.written
        assert document(layout).serialize() == before


@pytest.mark.parametrize("damage", ["changed", "missing", "byte-change"])
def test_changed_canonical_plan_refuses_completion(ws, damage):
    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    target = layout.bundle_dir / PLAN
    if damage == "missing":
        target.unlink()
    elif damage == "byte-change":
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    else:
        write(target, "# Changed plan\n")
    commit(layout.root)
    result = complete(layout)
    assert result.outcome.plan.refusal == "return-plan-changed", result
    assert "return again with refreshed scope" in result.outcome.plan.detail
    assert document(layout).fm_data()["execute_return"]["state"] == "active"


def code_gate(layout, tmp_path, *, receipt=True):
    from _gate_helpers import gate_ready

    code = tmp_path / "code"
    code.mkdir()
    write(code / "src/a.py", "old\n")
    _init_git(code)
    baseline = git(code, "rev-parse", "HEAD")
    write(code / "src/a.py", "new\n")
    commit(code)
    gate_ready(layout, code, PATH)
    doc = document(layout)
    doc.set("repo", "code")
    doc.set("worktree", str(code))
    doc.set("start_sha", baseline)
    doc.set("affects", ["src/a.py"])
    doc.save()
    if not receipt:
        (layout.bundle_dir / PATH / "references/03-gate-receipts.md").unlink()
    commit(layout.root)
    return code


@pytest.mark.parametrize("canonical_coverage", [True, False])
def test_reported_rows_complete_and_derive_obligations_from_destination(ws, tmp_path, canonical_coverage):
    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content, checked=False)
    if not canonical_coverage:
        (layout.bundle_dir / COVERAGE).unlink()
        commit(layout.root)
    code_gate(layout, tmp_path)
    result = complete(layout, dry_run=False)
    assert result.application is not None and result.application.ok, result
    assert result.outcome.written
    data = document(layout).fm_data(dates="iso")
    assert data["phase"] == "finish"
    assert data["execute_return"]["state"] == "completed"
    assert data["finish_obligations"] == [
        {"text": f"R1: {SCOPE[0]} -- verified limitation", "origin": "coverage", "recorded": TODAY.isoformat()}
    ]
    assert git(layout.root, "status", "--porcelain") == ""


def test_gate_refusal_leaves_return_active(ws, tmp_path):
    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    code_gate(layout, tmp_path, receipt=False)
    result = complete(layout, dry_run=False)
    assert result.outcome.plan.refusal == "no-gate-receipt"
    assert document(layout).fm_data()["execute_return"]["state"] == "active"
    assert document(layout).fm_data()["phase"] == "execute"


def test_never_returned_item_keeps_optional_coverage(ws):
    layout, _content = ws
    doc = document(layout)
    doc.set("phase", "execute")
    doc.save()
    (layout.bundle_dir / COVERAGE).unlink()
    commit(layout.root)
    result = complete(layout, dry_run=False)
    assert result.application.ok
    assert "execute_return" not in document(layout).fm_data()


def test_malformed_return_cannot_complete(ws):
    layout, _content = ws
    doc = document(layout)
    doc.set("phase", "execute")
    doc.set("execute_return", {"state": "active"})
    doc.save()
    commit(layout.root)
    result = complete(layout)
    assert result.outcome.plan.refusal == "return-metadata-invalid"


def test_pending_return_cannot_complete_even_with_reported_content(ws, monkeypatch):
    from graph_works_core.orchestrate import execute_return as returns

    layout, content = ws
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    assert advance(layout).application.ok
    report(layout, content)
    result = complete(layout, dry_run=False)
    assert result.outcome.plan.refusal == "return-pending"
    assert document(layout).fm_data()["execute_return"]["state"] == "active"


def test_consecutive_return_complete_return_requires_the_new_report(ws):
    layout, content = ws
    assert advance(layout).application.ok
    first = report(layout, content)
    assert complete(layout, dry_run=False).application.ok
    assert advance(layout).application.ok
    second = document(layout).fm_data()["execute_return"]["id"]
    assert first != second
    text = (content / "okf" / COVERAGE).read_text(encoding="utf-8")
    assert text.count("## Returned scope") == 2
    assert complete(layout).outcome.plan.refusal == "return-evidence-stale"
    write(content / "okf" / COVERAGE, text[: text.index(f"## Returned scope {second}")])
    commit(content)
    assert complete(layout).outcome.plan.refusal == "return-evidence-missing"


@pytest.mark.parametrize("member", ["coverage", "plan"])
def test_completion_revalidates_evidence_at_mutation_boundary(ws, monkeypatch, member):
    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    original = stage.apply_mutation
    target = content / "okf" / COVERAGE if member == "coverage" else layout.bundle_dir / PLAN

    def change_then_apply(*args, **kwargs):
        # Simulates an external writer ignoring bundle locks after preflight.
        write(target, "changed after verification\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(stage, "apply_mutation", change_then_apply)
    result = complete(layout, dry_run=False)
    assert result.application is not None and not result.application.ok
    assert not result.outcome.written
    assert document(layout).fm_data()["phase"] == "execute"
    assert document(layout).fm_data()["execute_return"]["state"] == "active"


def test_unreadable_report_refuses_completion(ws, monkeypatch):
    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    original = Path.read_bytes
    target = content / "okf" / COVERAGE

    def unreadable(path):
        if path == target:
            raise PermissionError("fixture coverage is unreadable")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    result = complete(layout)
    assert result.outcome.plan.refusal == "return-evidence-missing"


@pytest.mark.parametrize("writer_root", ["content", "canonical"])
def test_completion_holds_evidence_locks_through_commit(ws, monkeypatch, writer_root):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from graph_works_core.workspace import transactions
    from graph_works_core.workspace.layout import layout_for
    from graph_works_core.workspace.transactions import held_bundle_lock

    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    original = transactions.commit_workspace
    committing, attempting, acquired = Event(), Event(), Event()
    root = layout if writer_root == "canonical" else layout_for(content)
    held_at_commit = []

    def writer():
        assert committing.wait(10)
        attempting.set()
        with held_bundle_lock(root):
            acquired.set()

    def paused_commit(*args, **kwargs):
        committing.set()
        assert attempting.wait(10)
        held_at_commit.append(not acquired.wait(0.25))
        return original(*args, **kwargs)

    monkeypatch.setattr(transactions, "commit_workspace", paused_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        competing = pool.submit(writer)
        try:
            result = complete(layout, dry_run=False)
        finally:
            committing.set()
        competing.result(timeout=10)
    assert result.application.ok
    assert held_at_commit == [True], "evidence lock released before completion commit"


def test_completion_refuses_destination_changed_while_acquiring_locks(ws, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from graph_works_core.orchestrate import execute_return as returns

    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    other = tmp_path / "other-content"
    git(layout.root, "worktree", "add", "-b", "alternate", str(other))
    write(other / "okf" / COVERAGE, (content / "okf" / COVERAGE).read_text(encoding="utf-8"))
    commit(other)
    original = returns.completion_locks

    @contextmanager
    def change_after_lock(*args, **kwargs):
        with original(*args, **kwargs) as locked:
            doc = document(layout)
            stamps = doc.fm_data()["repo_stamps"]
            stamps["_workspace"].update(worktree=str(other), branch="alternate")
            doc.set("repo_stamps", stamps)
            doc.save()
            yield locked

    monkeypatch.setattr(returns, "completion_locks", change_after_lock)
    result = complete(layout, dry_run=False)
    assert result.outcome.plan.refusal == "return-destination-unverified", result
    assert document(layout).fm_data()["phase"] == "execute"
    assert document(layout).fm_data()["execute_return"]["state"] == "active"


@pytest.mark.parametrize("destination", ["no-plan", "unstamped", "disabled"])
def test_reported_completion_supports_optional_plan_and_canonical_destination(ws, destination):
    layout, content = ws
    if destination == "no-plan":
        (layout.bundle_dir / PLAN).unlink()
    elif destination == "unstamped":
        doc = document(layout)
        doc.delete("repo_stamps")
        doc.save()
    else:
        write(layout.local_manifest_path, "workflow:\n  workspace_commits: off\n")
    if destination != "disabled":
        commit(layout.root)
    assert advance(layout).application.ok
    target = content if destination == "no-plan" else layout.root
    report(layout, target)
    result = complete(layout, dry_run=False)
    assert result.application.ok, result
    assert document(layout).fm_data()["execute_return"]["state"] == "completed"
    if destination == "no-plan":
        assert "plan" not in document(layout).fm_data()["execute_return"]


def test_completed_return_keeps_existing_optional_coverage_behavior(ws):
    layout, content = ws
    assert advance(layout).application.ok
    report(layout, content)
    assert complete(layout, dry_run=False).application.ok
    doc = document(layout)
    doc.set("phase", "execute")
    doc.save()
    (layout.bundle_dir / COVERAGE).unlink()
    write(layout.bundle_dir / PLAN, "# Later plan\n")
    commit(layout.root)
    result = complete(layout, dry_run=False)
    assert result.application.ok
    assert document(layout).fm_data()["execute_return"]["state"] == "completed"


def test_completion_refuses_decision_owner_changed_while_acquiring_locks(ws, monkeypatch):
    from contextlib import contextmanager

    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.workspace.decision_owner import decision_context
    from graph_works_core.workspace.errors import WorkspaceError
    from okf_io import parse

    layout, content = ws
    parent = "work/owner"
    child = f"{parent}/children/bug-return"
    coverage = f"{child}/references/03-execute-coverage.md"
    plan = f"{child}/references/02-plan.md"
    write(layout.bundle_dir / f"{parent}.md", document(layout).serialize())
    doc = parse(document(layout).serialize())
    doc.set("type", "Bug")
    write(layout.bundle_dir / f"{child}.md", doc.serialize())
    write(layout.bundle_dir / coverage, "- [x] Original acceptance delivered\n")
    write(layout.bundle_dir / plan, "# Plan\n")
    write(content / "okf" / coverage, "- [x] Original acceptance delivered\n")
    commit(layout.root)
    commit(content)
    returned = stage.run_stage_advance(
        layout, child, today=TODAY, return_=True, return_scope=SCOPE, dry_run=False, infer_worktree=False
    )
    assert returned.application.ok, returned
    target = content / "okf" / coverage
    write(
        target,
        target.read_text(encoding="utf-8")
        .replace("Report state: prepared", "Report state: reported")
        .replace("- [ ] R1:", "- [x] R1:"),
    )
    commit(content)
    assert decision_context(layout, child).owner.owner_path == parent
    original = returns.completion_locks

    @contextmanager
    def change_after_lock(*args, **kwargs):
        with original(*args, **kwargs) as locked:
            # Changing an ancestor's kind makes the Bug its own decision owner.
            # The caller still holds only the parent's decision lock.
            owner = load(layout.bundle_dir / f"{parent}.md")
            owner.set("type", "Bug")
            owner.save()
            assert decision_context(layout, child).owner.owner_path == child
            yield locked

    monkeypatch.setattr(returns, "completion_locks", change_after_lock)
    with pytest.raises(WorkspaceError, match="decision owner changed; inspect and retry"):
        stage.run_stage_advance(layout, child, today=TODAY, dry_run=False, infer_worktree=False)
    data = load(layout.bundle_dir / f"{child}.md").fm_data()
    assert data["phase"] == "execute"
    assert data["execute_return"]["state"] == "active"


def local_publication_case(ws, kind):
    layout, content = ws
    if kind == "completion":
        assert advance(layout).application.ok
        report(layout, content)

        def invoke():
            return complete(layout, dry_run=False)
    else:
        doc = document(layout)
        doc.delete("repo_stamps")
        doc.save()
        commit(layout.root)

        def invoke():
            return advance(layout, expected_phase="finish")

    return layout, invoke


def rejecting_hook(layout, tmp_path):
    hooks = tmp_path / "rejecting-hooks"
    hook = hooks / "pre-commit"
    write(hook, "#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    git(layout.root, "config", "core.hooksPath", str(hooks))


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
def test_local_return_publication_rejected_commit_restores_owned_bytes_and_index(ws, tmp_path, kind):
    from graph_works_core.orchestrate.execute_return import pending_return

    layout, invoke = local_publication_case(ws, kind)
    page = layout.bundle_dir / f"{PATH}.md"
    coverage = layout.bundle_dir / COVERAGE
    before = (page.read_bytes(), coverage.read_bytes())
    head = git(layout.root, "rev-parse", "HEAD")
    write(layout.root / "unrelated", "preserve staged work\n")
    git(layout.root, "add", "unrelated")
    index = git(layout.root, "ls-files", "--stage")
    rejecting_hook(layout, tmp_path)
    result = invoke()
    assert result.application.commit.status == "failed"
    assert not result.application.ok
    assert not result.outcome.written
    assert (page.read_bytes(), coverage.read_bytes()) == before
    assert git(layout.root, "rev-parse", "HEAD") == head
    assert git(layout.root, "ls-files", "--stage") == index
    assert pending_return(layout, PATH) is None
    git(layout.root, "config", "--unset", "core.hooksPath")
    assert invoke().application.ok


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
def test_local_return_publication_unknown_outcome_fences_routing(ws, monkeypatch, kind):
    from dataclasses import replace

    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.work.commands import run_next
    from graph_works_core.workspace import transactions

    layout, invoke = local_publication_case(ws, kind)
    original = transactions.commit_workspace

    def interrupted_commit(*args, **kwargs):
        outcome = original(*args, **kwargs)
        # A landed but changed HEAD cannot be restored or accepted by identity.
        write(layout.bundle_dir / f"{PATH}.md", document(layout).serialize() + "\nexternal edit\n")
        return replace(outcome, status="failed", sha=None)

    monkeypatch.setattr(transactions, "commit_workspace", interrupted_commit)
    result = invoke()
    assert not result.application.ok
    assert not result.outcome.written
    pending = pending_return(layout, PATH)
    assert pending is not None and not pending.malformed
    assert run_next(layout, PATH).route.dispatch is None
    assert invoke().outcome.plan.refusal == "return-pending"


def test_same_root_return_recovers_exact_landed_publication(ws, monkeypatch):
    from graph_works_core.orchestrate import execute_return as returns

    layout, invoke = local_publication_case(ws, "same-root-return")
    original = returns.finish_return

    def crash(*args):
        raise OSError("crash after publication proof")

    monkeypatch.setattr(returns, "finish_return", crash)
    with pytest.raises(OSError, match="crash after publication proof"):
        invoke()
    head = git(layout.root, "rev-parse", "HEAD")
    coverage = (layout.bundle_dir / COVERAGE).read_bytes()
    monkeypatch.setattr(returns, "finish_return", original)
    result = invoke()
    assert result.outcome.plan.refusal is None
    assert not result.outcome.written
    assert returns.pending_return(layout, PATH) is None
    assert git(layout.root, "rev-parse", "HEAD") == head
    assert (layout.bundle_dir / COVERAGE).read_bytes() == coverage


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
@pytest.mark.parametrize("mode", ["off", "no-git"])
def test_local_return_publication_preserves_noncommitting_modes(ws, kind, mode):
    import shutil

    from graph_works_core.orchestrate.execute_return import pending_return

    layout, invoke = local_publication_case(ws, kind)
    if kind == "completion":
        _, content = ws
        write(layout.bundle_dir / COVERAGE, (content / "okf" / COVERAGE).read_text(encoding="utf-8"))
        doc = document(layout)
        doc.delete("repo_stamps")
        doc.save()
    if mode == "off":
        write(layout.local_manifest_path, "workflow:\n  workspace_commits: off\n")
    else:
        shutil.rmtree(layout.root / ".git")
    result = invoke()
    assert result.application.ok, result
    assert result.outcome.written
    assert result.application.commit.status == "skipped"
    assert pending_return(layout, PATH) is None


def test_local_return_failed_commit_removes_only_new_coverage(ws, tmp_path):
    from graph_works_core.orchestrate.execute_return import pending_return

    layout, invoke = local_publication_case(ws, "same-root-return")
    target = layout.bundle_dir / COVERAGE
    target.unlink()
    commit(layout.root)
    before = document(layout).serialize()
    rejecting_hook(layout, tmp_path)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert document(layout).serialize() == before
    assert not target.exists()
    assert git(layout.root, "status", "--porcelain") == ""
    assert pending_return(layout, PATH) is None


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
@pytest.mark.parametrize("damage", ["index", "page", "head"])
def test_local_return_restoration_requires_owned_representations(ws, tmp_path, monkeypatch, kind, damage):
    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.work.commands import run_next
    from graph_works_core.workspace import transactions

    layout, invoke = local_publication_case(ws, kind)
    rejecting_hook(layout, tmp_path)
    original = transactions.commit_workspace
    relative = f"okf/{PATH}.md"
    changed = {}

    def reject_then_external_change(*args, **kwargs):
        outcome = original(*args, **kwargs)
        assert outcome.status == "failed"
        if damage == "index":
            other = tmp_path / "external-version"
            write(other, "separately staged content\n")
            oid = git(layout.root, "hash-object", "-w", str(other))
            git(layout.root, "update-index", "--cacheinfo", "100644", oid, relative)
        elif damage == "page":
            write(layout.root / relative, document(layout).serialize() + "\nexternal edit\n")
        else:
            git(layout.root, "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "external commit")
        changed["page"] = (layout.root / relative).read_bytes()
        changed["index"] = git(layout.root, "ls-files", "--stage")
        changed["head"] = git(layout.root, "rev-parse", "HEAD")
        return outcome

    monkeypatch.setattr(transactions, "commit_workspace", reject_then_external_change)
    result = invoke()
    assert not result.application.ok
    assert not result.application.rolled_back
    assert (layout.root / relative).read_bytes() == changed["page"]
    assert git(layout.root, "ls-files", "--stage") == changed["index"]
    assert git(layout.root, "rev-parse", "HEAD") == changed["head"]
    assert pending_return(layout, PATH) is not None
    assert run_next(layout, PATH).route.dispatch is None


@pytest.mark.parametrize("kind", ["same-root-return", "completion"])
def test_local_return_accepts_positive_commit_proof_after_lost_reply(ws, monkeypatch, kind):
    from dataclasses import replace

    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace import transactions

    layout, invoke = local_publication_case(ws, kind)
    original = transactions.commit_workspace

    def lose_reply(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, sha=None, status="failed", reason="lost reply")

    monkeypatch.setattr(transactions, "commit_workspace", lose_reply)
    result = invoke()
    assert result.application.ok
    assert result.outcome.written
    assert pending_return(layout, PATH) is None
    assert git(layout.root, "status", "--porcelain") == ""


@pytest.mark.parametrize("damage", ["kind", "version", "members", "duplicate", "record"])
def test_local_return_journal_rejects_unknown_or_malformed_variants(ws, monkeypatch, damage):
    import json

    from graph_works_core.orchestrate import execute_return as returns
    from graph_works_core.work.commands import run_next
    from graph_works_core.workspace.execute_return import journal_path

    layout, invoke = local_publication_case(ws, "same-root-return")
    monkeypatch.setattr(returns, "finish_return", lambda *_: None)
    assert invoke().application.ok
    path = journal_path(layout, PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert returns.pending_return(layout, PATH).local.kind == "same-root-return"
    if damage == "kind":
        raw["publication"]["kind"] = "unknown"
    elif damage == "version":
        raw["version"] = 1
    elif damage == "members":
        raw["publication"]["members"] = [["../outside", "0" * 64]]
    elif damage == "duplicate":
        raw["publication"]["members"].append(raw["publication"]["members"][0])
    else:
        raw["record"]["state"] = "completed"
    write(path, json.dumps(raw))
    assert returns.pending_return(layout, PATH).malformed
    assert invoke().outcome.plan.refusal == "return-pending"
    assert run_next(layout, PATH).route.dispatch is None


def test_completion_refuses_new_return_without_admission_ownership(ws, monkeypatch):
    from contextlib import contextmanager

    from graph_works_core.workspace.errors import WorkspaceError

    layout, content = ws
    original = stage.locked_decision_owner

    @contextmanager
    def return_before_owner_lock(*args, **kwargs):
        # Another return wins ownership after completion's initial item read.
        with monkeypatch.context() as patch:
            patch.setattr(stage, "locked_decision_owner", original)
            assert advance(layout).application.ok
            report(layout, content)
        with original(*args, **kwargs) as context:
            yield context

    monkeypatch.setattr(stage, "locked_decision_owner", return_before_owner_lock)
    with pytest.raises(WorkspaceError, match="admission changed"):
        complete(layout, dry_run=False)
    assert document(layout).fm_data()["phase"] == "execute"
    assert document(layout).fm_data()["execute_return"]["state"] == "active"


def test_local_return_restores_distinct_preexisting_staged_page(ws, tmp_path):
    layout, invoke = local_publication_case(ws, "same-root-return")
    relative = f"okf/{PATH}.md"
    page = layout.root / relative
    before = page.read_bytes()
    write(page, document(layout).serialize() + "\npreviously staged text\n")
    git(layout.root, "add", relative)
    index = git(layout.root, "ls-files", "--stage")
    page.write_bytes(before)
    rejecting_hook(layout, tmp_path)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert page.read_bytes() == before
    assert git(layout.root, "ls-files", "--stage") == index


def test_completion_rejected_commit_preserves_staged_reference_preimage(ws, tmp_path):
    from graph_works_core.orchestrate.execute_return import pending_return

    layout, invoke = local_publication_case(ws, "completion")
    relative = f"okf/{PATH}/references/notes.md"
    write(layout.root / relative, "staged version\n")
    git(layout.root, "add", relative)
    write(layout.root / relative, "unstaged working version\n")
    index = git(layout.root, "ls-files", "--stage")
    rejecting_hook(layout, tmp_path)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == index
    assert (layout.root / relative).read_bytes() == b"unstaged working version\n"
    assert pending_return(layout, PATH) is None


@pytest.mark.parametrize("kind", ["same-root-return", "completion", "cross-root-return"])
def test_return_failed_commit_preserves_external_index_mode(ws, tmp_path, monkeypatch, kind):
    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace import transactions

    if kind == "cross-root-return":
        layout, _ = ws

        def invoke():
            return advance(layout)

        # The shared repository hook must reject only canonical publication.
        hooks = tmp_path / "rejecting-hooks"
        hook = hooks / "pre-commit"
        write(hook, f'#!/bin/sh\n[ "$(pwd)" != "{layout.root}" ]\n')
        hook.chmod(0o755)
        git(layout.root, "config", "core.hooksPath", str(hooks))
    else:
        layout, invoke = local_publication_case(ws, kind)
        rejecting_hook(layout, tmp_path)
    original = transactions.commit_workspace
    relative = f"okf/{PATH}.md"
    changed = {}

    def reject_then_mode_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0].root == layout.root:
            assert result.status == "failed"
            git(layout.root, "update-index", "--chmod=+x", "--", relative)
            changed["index"] = git(layout.root, "ls-files", "--stage")
        return result

    monkeypatch.setattr(transactions, "commit_workspace", reject_then_mode_edit)
    result = invoke()
    assert not result.application.ok
    assert not result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == changed["index"]
    assert pending_return(layout, PATH) is not None


@pytest.mark.parametrize("state", ["deleted", "untracked"])
def test_completion_rejected_commit_restores_additional_reference_index_states(ws, tmp_path, state):
    from graph_works_core.orchestrate.execute_return import pending_return

    layout, invoke = local_publication_case(ws, "completion")
    relative = f"okf/{PATH}/references/notes.md"
    target = layout.root / relative
    write(target, "reference content\n")
    if state == "deleted":
        git(layout.root, "add", relative)
        target.unlink()
    index = git(layout.root, "ls-files", "--stage")
    rejecting_hook(layout, tmp_path)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == index
    assert target.exists() == (state == "untracked")
    assert pending_return(layout, PATH) is None


def test_completion_still_commits_authored_references(ws):
    layout, invoke = local_publication_case(ws, "completion")
    relative = f"okf/{PATH}/references/notes.md"
    write(layout.root / relative, "reference content\n")
    assert invoke().application.ok
    assert git(layout.root, "show", f"HEAD:{relative}") == "reference content"


def test_completion_preserves_external_reference_mode_and_fence(ws, tmp_path, monkeypatch):
    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace import transactions

    layout, invoke = local_publication_case(ws, "completion")
    relative = f"okf/{PATH}/references/notes.md"
    write(layout.root / relative, "reference content\n")
    git(layout.root, "add", relative)
    rejecting_hook(layout, tmp_path)
    original = transactions.commit_workspace
    edited = {}

    def reject_then_mode_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        git(layout.root, "update-index", "--chmod=+x", "--", relative)
        edited["index"] = git(layout.root, "ls-files", "--stage")
        return result

    monkeypatch.setattr(transactions, "commit_workspace", reject_then_mode_edit)
    result = invoke()
    assert not result.application.ok
    assert not result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == edited["index"]
    assert pending_return(layout, PATH) is not None


def test_return_restoration_respects_disabled_filemode_tracking(ws, tmp_path):
    layout, invoke = local_publication_case(ws, "same-root-return")
    git(layout.root, "config", "core.filemode", "false")
    relative = f"okf/{PATH}.md"
    git(layout.root, "update-index", "--chmod=+x", "--", relative)
    index = git(layout.root, "ls-files", "--stage")
    rejecting_hook(layout, tmp_path)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == index


@pytest.mark.parametrize("scope", ["extra", "root"])
def test_completion_rejected_commit_restores_explicit_additional_commit_paths(ws, tmp_path, monkeypatch, scope):
    from graph_works_core.workspace.commits import WorkspaceCommit

    layout, invoke = local_publication_case(ws, "completion")
    relative = "okf/extra.md" if scope == "extra" else "root-extra.md"
    write(layout.root / relative, "staged extra\n")
    git(layout.root, "add", relative)
    write(layout.root / relative, "working extra\n")
    index = git(layout.root, "ls-files", "--stage")
    rejecting_hook(layout, tmp_path)

    def include_extra(subject, **kwargs):
        return WorkspaceCommit(
            subject,
            **kwargs,
            extra_paths=("extra.md",) if scope == "extra" else (),
            root_paths=("root-extra.md",) if scope == "root" else (),
        )

    monkeypatch.setattr(stage, "WorkspaceCommit", include_extra)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == index
    assert (layout.root / relative).read_bytes() == b"working extra\n"


@pytest.mark.parametrize("root_path", ["root-link.md", "linked-dir/root-extra.md", "./root-extra.md"])
def test_completion_failed_commit_preserves_resolved_root_target_index(ws, tmp_path, monkeypatch, root_path):
    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace.commits import WorkspaceCommit

    layout, invoke = local_publication_case(ws, "completion")
    relative = "root-extra.md"
    target = layout.root / relative
    write(target, "staged extra\n")
    git(layout.root, "add", relative)
    write(target, "working extra\n")
    if root_path == "root-link.md":
        (layout.root / root_path).symlink_to(relative)
    elif root_path.startswith("linked-dir"):
        (layout.root / "linked-dir").symlink_to(".", target_is_directory=True)
    index = git(layout.root, "ls-files", "--stage")
    rejecting_hook(layout, tmp_path)

    def include_root(subject, **kwargs):
        return WorkspaceCommit(subject, **kwargs, root_paths=(root_path,))

    monkeypatch.setattr(stage, "WorkspaceCommit", include_root)
    result = invoke()
    assert not result.application.ok
    assert result.application.rolled_back
    assert git(layout.root, "ls-files", "--stage") == index
    assert target.read_bytes() == b"working extra\n"
    assert pending_return(layout, PATH) is None


@pytest.mark.parametrize("kind", ["outside-symlink", "parent-traversal", "absolute"])
def test_completion_refuses_invalid_root_commit_path_before_effects(ws, tmp_path, monkeypatch, kind):
    from graph_works_core.orchestrate.execute_return import pending_return
    from graph_works_core.workspace.commits import WorkspaceCommit
    from graph_works_core.workspace.errors import WorkspaceError

    layout, invoke = local_publication_case(ws, "completion")
    outside = tmp_path / "outside.md"
    write(outside, "outside content\n")
    if kind == "outside-symlink":
        (layout.root / "root-link.md").symlink_to(outside)
        root_path = "root-link.md"
    elif kind == "parent-traversal":
        root_path = "../outside.md"
    else:
        root_path = str(outside)
    before = document(layout).serialize()
    head = git(layout.root, "rev-parse", "HEAD")
    index = git(layout.root, "ls-files", "--stage")

    def include_root(subject, **kwargs):
        return WorkspaceCommit(subject, **kwargs, root_paths=(root_path,))

    monkeypatch.setattr(stage, "WorkspaceCommit", include_root)
    with pytest.raises(WorkspaceError, match="path is outside the workspace"):
        invoke()
    assert document(layout).serialize() == before
    assert git(layout.root, "rev-parse", "HEAD") == head
    assert git(layout.root, "ls-files", "--stage") == index
    assert outside.read_bytes() == b"outside content\n"
    assert pending_return(layout, PATH) is None
