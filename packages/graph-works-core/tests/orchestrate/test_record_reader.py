"""Reader receipts bind attempts under the owner lock without touching pages."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.orchestrate import placement
from graph_works_core.workspace import decision_owner as owners
from graph_works_core.workspace.errors import WorkspaceError
from test_record_placement import EPIC, NESTED, WT, _snapshot, _vault
from work_tracker_okf.placement import ReaderObservation

OBS = ReaderObservation("task_1", "ctx_1", "dispatch-key", "repo", WT, "a" * 40)


def _record(layout, *, observation=OBS, phase="plan", dry_run=False, path=NESTED):
    return placement.run_record_reader(layout, path, root=EPIC, phase=phase, observation=observation, dry_run=dry_run)


def test_live_receipt_has_exact_schema_and_preserves_all_page_bytes(tmp_path):
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{NESTED}.md"
    # Include authored stamps and CRLF: no reader is allowed to normalize them.
    data = page.read_bytes().replace(b"phase: plan", b"worktree: /old\nbranch: old\nphase: plan")
    page.write_bytes(data.replace(b"\n", b"\r\n"))
    before = _snapshot(layout)
    record = _record(layout)
    expected = layout.cache_dir / "reader-receipts" / hashlib.sha256(NESTED.encode()).hexdigest()[:16] / "ctx_1.json"
    assert record.receipt_path == expected
    assert record.written and not record.replayed and record.conflict is None
    assert placement.read_reader_receipt(layout, NESTED, "ctx_1") == {
        "schema": "gw-reader-receipt",
        "version": 1,
        "path": NESTED,
        "root": EPIC,
        "phase": "plan",
        "task_id": "task_1",
        "dispatch_id": "ctx_1",
        "dispatch_key": "dispatch-key",
        "repo": "repo",
        "worktree": WT,
        "start_sha": "a" * 40,
    }
    assert _snapshot(layout) == before


def test_default_dry_run_and_missing_read_create_nothing(tmp_path, monkeypatch):
    layout = _vault(tmp_path)
    monkeypatch.setattr(placement, "locked_decision_owner", lambda *a: pytest.fail("dry run locked"))
    record = placement.run_record_reader(layout, NESTED, root=EPIC, phase="plan", observation=OBS)
    assert not record.written and not record.replayed
    assert record.receipt_path is not None
    assert placement.read_reader_receipt(layout, NESTED, "ctx_1") is None
    assert not (layout.cache_dir / "reader-receipts").exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_exact_replay_preserves_receipt_bytes_and_mtime(tmp_path, dry_run):
    layout = _vault(tmp_path)
    first = _record(layout)
    before, stat = first.receipt_path.read_bytes(), first.receipt_path.stat()
    replay = _record(layout, dry_run=dry_run)
    assert replay.replayed and not replay.written and replay.conflict is None
    assert first.receipt_path.read_bytes() == before
    assert first.receipt_path.stat().st_mtime_ns == stat.st_mtime_ns


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("field,value", [("start_sha", "b" * 40), ("task_id", "task_2"), ("dispatch_key", "other")])
def test_attempt_mismatch_preserves_original(tmp_path, dry_run, field, value):
    layout = _vault(tmp_path)
    first = _record(layout)
    before = first.receipt_path.read_bytes()
    record = _record(layout, observation=replace(OBS, **{field: value}), dry_run=dry_run)
    assert record.conflict == "attempt-mismatch" and not record.written and not record.replayed
    assert first.receipt_path.read_bytes() == before


def test_phase_mismatch_precedes_replay_but_eligible_phase_change_conflicts(tmp_path):
    layout = _vault(tmp_path)
    first = _record(layout)
    before = first.receipt_path.read_bytes()
    assert _record(layout, phase="design").plan.refusal == "phase-mismatch"
    page = layout.bundle_dir / f"{NESTED}.md"
    page.write_bytes(page.read_bytes().replace(b"phase: plan", b"phase: design"))
    assert _record(layout).plan.refusal == "phase-mismatch"
    assert _record(layout, phase="design").conflict == "attempt-mismatch"
    assert first.receipt_path.read_bytes() == before


def test_retry_has_distinct_receipt(tmp_path):
    layout = _vault(tmp_path)
    first = _record(layout)
    before = first.receipt_path.read_bytes()
    retry = _record(layout, observation=replace(OBS, dispatch_id="ctx_2", start_sha="b" * 40))
    assert retry.written and retry.receipt_path != first.receipt_path
    assert first.receipt_path.read_bytes() == before
    assert len(list(first.receipt_path.parent.glob("*.json"))) == 2


def test_undeclared_repository_is_rejected(tmp_path):
    layout = _vault(tmp_path)
    with pytest.raises(WorkspaceError, match="names no declared repository"):
        _record(layout, observation=replace(OBS, repo="missing"))
    assert not (layout.cache_dir / "reader-receipts").exists()


@pytest.mark.parametrize("phase,refusal", [("execute", "code-phase"), ("design", "phase-mismatch")])
def test_refusals_write_nothing_and_known_path_still_has_receipt_path(tmp_path, phase, refusal):
    layout = _vault(tmp_path)
    record = _record(layout, phase=phase)
    assert record.plan.refusal == refusal and not record.written
    assert record.receipt_path is not None
    assert not (layout.cache_dir / "reader-receipts").exists()


def test_unknown_path_does_not_lock(tmp_path, monkeypatch):
    layout = _vault(tmp_path)
    monkeypatch.setattr(placement, "locked_decision_owner", lambda *a: pytest.fail("unknown path locked"))
    record = _record(layout, path="work/missing")
    assert record.plan.refusal == "unknown-path" and record.receipt_path is None


def test_advance_before_lock_refuses_stale_evidence(tmp_path, monkeypatch):
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{NESTED}.md"

    @contextmanager
    def advance_then_lock(layout, path):
        page.write_bytes(page.read_bytes().replace(b"phase: plan", b"phase: execute"))
        with owners.locked_decision_owner(layout, path) as context:
            yield context

    monkeypatch.setattr(placement, "locked_decision_owner", advance_then_lock)
    record = _record(layout)
    assert record.plan.refusal == "phase-mismatch" and not record.written
    assert not (layout.cache_dir / "reader-receipts").exists()


@pytest.mark.parametrize("dispatch_id", ["", "../escape", "/absolute", r"a\b", "a/b", "a..b", ".hidden", "a\n", "a:"])
@pytest.mark.parametrize("entry", ["path", "read", "record"])
def test_attempt_ids_validated_before_path_access(tmp_path, monkeypatch, dispatch_id, entry):
    layout = _vault(tmp_path)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("read before validation"))
    monkeypatch.setattr(
        placement, "load_workspace_bundle", lambda *a, **k: pytest.fail("bundle read before validation")
    )
    with pytest.raises(WorkspaceError, match="attempt identifier"):
        if entry == "path":
            placement.reader_receipt_path(layout, NESTED, dispatch_id)
        elif entry == "read":
            placement.read_reader_receipt(layout, NESTED, dispatch_id)
        else:
            _record(layout, observation=replace(OBS, dispatch_id=dispatch_id))


@pytest.mark.parametrize("marker", ["dcap_secret", "--dispatch-capability"])
@pytest.mark.parametrize("field", ["task_id", "dispatch_id", "dispatch_key", "repo", "worktree", "start_sha"])
def test_capabilities_are_rejected_in_every_observation_field(tmp_path, marker, field):
    layout = _vault(tmp_path)
    with pytest.raises(WorkspaceError, match="dispatch capabilities are never recorded"):
        _record(layout, observation=replace(OBS, **{field: marker}))
    assert not (layout.cache_dir / "reader-receipts").exists()


@pytest.mark.parametrize("raw", [b"{", b"[]", b"null", b"\xff"])
def test_malformed_receipt_requires_repair_and_is_never_overwritten(tmp_path, raw):
    layout = _vault(tmp_path)
    target = placement.reader_receipt_path(layout, NESTED, OBS.dispatch_id)
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    with pytest.raises(WorkspaceError, match="reader receipt"):
        placement.read_reader_receipt(layout, NESTED, OBS.dispatch_id)
    with pytest.raises(WorkspaceError, match="reader receipt"):
        _record(layout)
    assert target.read_bytes() == raw


def test_unreadable_receipt_is_not_treated_as_missing(tmp_path):
    layout = _vault(tmp_path)
    target = placement.reader_receipt_path(layout, NESTED, OBS.dispatch_id)
    target.mkdir(parents=True)
    with pytest.raises(WorkspaceError, match="reader receipt"):
        _record(layout)
    assert target.is_dir()


def test_capability_bearing_stored_receipt_is_rejected(tmp_path):
    layout = _vault(tmp_path)
    target = placement.reader_receipt_path(layout, NESTED, OBS.dispatch_id)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"dispatch_key": "dcap_secret"}), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="dispatch capabilities are never recorded"):
        placement.read_reader_receipt(layout, NESTED, OBS.dispatch_id)


def test_atomic_publish_failure_removes_temp_and_preserves_pages(tmp_path, monkeypatch):
    layout = _vault(tmp_path)
    before = _snapshot(layout)

    def fail_replace(source, target):
        assert source.parent == target.parent
        assert json.loads(source.read_text(encoding="utf-8"))["dispatch_id"] == OBS.dispatch_id
        assert not target.exists()
        raise OSError("publish failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(WorkspaceError, match="publish failed"):
        _record(layout)
    assert _snapshot(layout) == before
    assert not list((layout.cache_dir / "reader-receipts").rglob("*.json"))
    assert not list((layout.cache_dir / "reader-receipts").rglob("*.tmp"))


def test_publish_and_temp_cleanup_failures_keep_publish_error(tmp_path, monkeypatch):
    layout = _vault(tmp_path)
    before = _snapshot(layout)

    def fail_replace(source, target):
        raise OSError("publish failed")

    def fail_unlink(path, *, missing_ok=False):
        raise OSError("cleanup failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(WorkspaceError, match="cannot write reader receipt") as raised:
        _record(layout)
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "publish failed"
    assert any("cleanup failed" in note for note in raised.value.__notes__)
    assert _snapshot(layout) == before


def test_temp_cleanup_failure_after_publish_is_workspace_error(tmp_path, monkeypatch):
    layout = _vault(tmp_path)

    def fail_unlink(path, *, missing_ok=False):
        raise OSError("cleanup failed")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(WorkspaceError, match="cannot clean up temporary reader receipt") as raised:
        _record(layout)
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "cleanup failed"
    assert placement.read_reader_receipt(layout, NESTED, OBS.dispatch_id) is not None


def test_concurrent_attempts_serialize_and_refuse_different_evidence(tmp_path, monkeypatch):
    from test_placement_interleaving import WAIT, _join_all, _pause, _Run

    layout = _vault(tmp_path)
    entered, release = _pause(monkeypatch, placement, "_write_receipt")
    first = _Run(lambda: _record(layout))
    second = _Run(lambda: _record(layout, observation=replace(OBS, start_sha="b" * 40)))
    try:
        first.start()
        assert entered.wait(WAIT)
        second.start()
        second.join(timeout=0.5)
        assert second.is_alive(), "receipt publication must hold the decision-owner lock"
    finally:
        release.set()
        _join_all(first, second)
    assert first.error is None and second.error is None
    assert first.result.written
    assert second.result.conflict == "attempt-mismatch"
    assert placement.read_reader_receipt(layout, NESTED, OBS.dispatch_id)["start_sha"] == OBS.start_sha
