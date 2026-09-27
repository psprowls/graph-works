"""The dispatch record: schema, journal helpers, secrets, overrides."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fake_orca_port import FakeOrcaPort
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import dispatch_record as dr
from graph_works_core.orchestrate.orca_port import OrcaPort
from graph_works_core.workspace.layout import WorkspaceLayout


def base() -> dr.DispatchRecord:
    return dr.DispatchRecord(key="gw-execute-x-1a2b", work_path="work/x", phase="execute", run_id="run_1")


@pytest.fixture
def workspace_layout(tmp_path: Path) -> WorkspaceLayout:
    layout = apply_init(plan_init(tmp_path / ".works", today=date(2026, 9, 26), topic="Dispatch")).layout
    page = layout.bundle_dir / "work/x.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\ntitle: X\ndescription: d\nstatus: stable\n"
        "work_status: in-progress\nphase: execute\neffort: medium\n"
        "opened: 2026-09-26\nupdated: 2026-09-26\naffects: []\n---\n\n## Summary\nd\n",
        encoding="utf-8",
        newline="\n",
    )
    return layout


def test_the_fake_satisfies_the_port() -> None:
    assert isinstance(FakeOrcaPort(), OrcaPort)


def test_round_trip(tmp_path: Path) -> None:
    attempt = dr.Attempt(
        task_id="task_1",
        dispatch_id=None,
        display_name="work/x · execute",
        envelope={"version": 2},
        placement=None,
        steps={"create": dr.StepState("done", at="t", result={"task_id": "task_1"})},
    )
    record = base().with_attempt(attempt)
    path = tmp_path / "r.json"
    dr.write_json_atomic(path, dr.to_json(record))
    assert dr.load_record(path) == record
    assert path.read_bytes().endswith(b"\n") and b"\r" not in path.read_bytes()


def test_absent_is_none_and_malformed_is_refused(tmp_path: Path) -> None:
    assert dr.load_record(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    for payload in ({"schema": "nope"}, {**dr.to_json(base()), "version": 2}, [], "x"):
        bad.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
        with pytest.raises(dr.DispatchRecordError):
            dr.load_record(bad)


def test_duplicate_json_fields_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    payload = json.dumps(dr.to_json(base()))
    path.write_text(payload.replace('"schema":', '"schema": "shadow", "schema":', 1), encoding="utf-8", newline="\n")
    with pytest.raises(dr.DispatchRecordError, match="schema") as error:
        dr.load_record(path)
    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    "edit,field",
    [
        (lambda p: p.pop("key"), "key"),
        (lambda p: p.update(extra=True), "extra"),
        (lambda p: p.update(run_id=7), "run_id"),
        (lambda p: p["attempts"].append({"task_id": "t"}), "attempts\\[1\\]"),
        (lambda p: p["attempts"][0]["steps"].update(bogus={"state": "done"}), "bogus"),
        (lambda p: p["attempts"][0]["steps"]["create"].update(state="bad"), "state"),
        (lambda p: p["attempts"][0]["steps"]["create"].update(result=[]), "result"),
        (lambda p: p["attempts"][0]["steps"]["create"].update(at=None), "at"),
        (lambda p: p["attempts"][0]["envelope"].update(token="dcap_secret"), "secret"),
        (lambda p: p["reroutes"][0]["overrides"].update(effort=3), "effort"),
    ],
)
def test_nested_journal_is_strict(tmp_path: Path, edit, field: str) -> None:
    attempt = dr.Attempt("task_1", "ctx_1", "d", {}, None, {"create": dr.StepState("done")})
    reroute = dr.Reroute("t", "stall", "task_1", "ctx_1", dr.Overrides(agent="codex"))
    payload = dr.to_json(base().with_attempt(attempt).with_reroute(reroute))
    edit(payload)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    with pytest.raises(dr.DispatchRecordError, match=field) as error:
        dr.load_record(path)
    assert str(path) in str(error.value)


def test_a_secret_anywhere_is_refused(workspace_layout: WorkspaceLayout) -> None:
    attempt = dr.Attempt(
        task_id=None,
        dispatch_id=None,
        display_name="work/x · execute",
        envelope={"placement_argv": ["--dispatch-capability", "dcap_abc"]},
        placement=None,
        steps={},
    )
    with pytest.raises(dr.DispatchRecordError, match="secret"):
        dr.save_record(workspace_layout, base().with_attempt(attempt))
    assert not dr.record_file(workspace_layout, "work/x", base().key).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("envelope", {"prompt": "human task text"}),
        ("envelope", {"metadata": [{"launchPreamble": "human task text"}]}),
        ("placement", {"nested": {"prompt_tail": "human task text"}}),
        ("result", {"events": [{"worker-preamble": "human task text"}]}),
        ("result", {"events": [{"note": "GW_LAUNCH_V1 encoded human task text"}]}),
    ],
)
def test_prompt_carriers_are_refused_on_save_and_load(
    workspace_layout: WorkspaceLayout, tmp_path: Path, field: str, value: dict[str, object]
) -> None:
    attempt = dr.Attempt(
        "task_1",
        "ctx_1",
        "d",
        value if field == "envelope" else {"version": 2},
        value if field == "placement" else None,
        {"launch": dr.StepState("done", result=value if field == "result" else {"state": "ready"})},
    )
    record = base().with_attempt(attempt)
    with pytest.raises(dr.DispatchRecordError, match=r"prompt|preamble"):
        dr.save_record(workspace_layout, record)
    assert not dr.record_file(workspace_layout, "work/x", base().key).exists()

    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(dr.to_json(record)), encoding="utf-8", newline="\n")
    with pytest.raises(dr.DispatchRecordError, match=r"prompt|preamble"):
        dr.load_record(path)


def test_journal_metadata_without_prompt_carriers_round_trips(tmp_path: Path) -> None:
    attempt = dr.Attempt(
        "task_1",
        "ctx_1",
        "d",
        {"agent": "codex", "version": 2},
        {"worktree_id": "wt_1", "source": "existing"},
        {"launch": dr.StepState("done", reason="prompt was not delivered", result={"state": "ready"})},
    )
    record = base().with_attempt(attempt)
    path = tmp_path / "safe.json"
    dr.write_json_atomic(path, dr.to_json(record))
    assert dr.load_record(path) == record


def test_malformed_journal_is_refused_before_write(workspace_layout: WorkspaceLayout) -> None:
    attempt = dr.Attempt("task_1", None, "d", {}, None, {"unexpected": dr.StepState("done")})
    with pytest.raises(dr.DispatchRecordError, match="unexpected"):
        dr.save_record(workspace_layout, base().with_attempt(attempt))
    assert not dr.record_file(workspace_layout, "work/x", base().key).exists()


def test_existing_malformed_journal_is_not_overwritten(workspace_layout: WorkspaceLayout) -> None:
    path = dr.record_file(workspace_layout, "work/x", base().key)
    path.parent.mkdir(parents=True)
    payload = dr.to_json(base())
    payload["schema"] = "broken"
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    before = path.read_bytes()
    with pytest.raises(dr.DispatchRecordError, match="schema"):
        dr.save_record(workspace_layout, base())
    assert path.read_bytes() == before


def test_record_parent_symlink_cannot_escape_owner(workspace_layout: WorkspaceLayout, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    owner = workspace_layout.bundle_dir / "work/x"
    owner.mkdir(parents=True, exist_ok=True)
    (owner / "references").symlink_to(outside, target_is_directory=True)
    with pytest.raises(dr.DispatchRecordError, match="owner"):
        dr.save_record(workspace_layout, base())
    assert list(outside.iterdir()) == []


def test_owner_symlink_cannot_redirect_into_another_item(workspace_layout: WorkspaceLayout) -> None:
    work = workspace_layout.bundle_dir / "work"
    other = work / "other"
    other.mkdir(parents=True)
    (work / "x").symlink_to(other, target_is_directory=True)
    with pytest.raises(dr.DispatchRecordError, match="owner"):
        dr.save_record(workspace_layout, base())
    assert not (other / "references").exists()


def test_record_path_is_confined_to_owner(workspace_layout: WorkspaceLayout) -> None:
    assert dr.record_ref("work/x", "key") == "work/x/references/orca-dispatch/key.json"
    assert dr.save_record(workspace_layout, base()) == (
        workspace_layout.bundle_dir / "work/x/references/orca-dispatch/gw-execute-x-1a2b.json"
    )
    for path, key in (
        ("../other", "key"),
        ("work/x", "../escape"),
        ("/tmp", "key"),
        ("work/x/../y", "key"),
        ("work/x", "a/b"),
    ):
        with pytest.raises(dr.DispatchRecordError):
            dr.record_file(workspace_layout, path, key)


def test_current_and_superseded() -> None:
    reroute = dr.Reroute(
        at="t",
        reason="stall",
        superseded_task_id="task_1",
        superseded_dispatch_id="ctx_1",
        overrides=dr.Overrides(agent="codex"),
    )
    record = base().with_reroute(reroute)
    assert record.superseded == frozenset({"task_1"})
    assert record.latest_overrides() == dr.Overrides(agent="codex")


def test_attempt_replace_append_and_step_update() -> None:
    first = dr.Attempt(None, None, "d", {}, None, {})
    record = base().with_attempt(first)
    created = dr.Attempt("task_1", None, "d", {}, None, {})
    record = record.with_attempt(created)
    assert len(record.attempts) == 1
    record = record.with_step(0, "create", dr.StepState("done", at="t"), dispatch_id="ctx_1")
    assert record.attempts[0].steps["create"] == dr.StepState("done", at="t")
    assert record.attempts[0].dispatch_id == "ctx_1"
    assert len(record.with_attempt(dr.Attempt("task_2", None, "d", {}, None, {})).attempts) == 2


def test_attempt_complete_and_pending() -> None:
    steps = {name: dr.StepState("done", at="t") for name in dr.STEPS}
    attempt = dr.Attempt("task_1", "ctx_1", "d", {}, None, steps)
    assert attempt.complete and attempt.pending() is None
    steps["probe"] = dr.StepState("attempted", at="t")
    attempt = dr.Attempt("task_1", "ctx_1", "d", {}, None, steps)
    assert not attempt.complete and attempt.pending() == "probe"


def test_agent_change_clears_model_and_effort() -> None:
    assert dr.apply_overrides("claude", "opus", "high", dr.Overrides(agent="codex")) == ("codex", None, None)
    assert dr.apply_overrides("claude", "opus", "high", dr.Overrides(agent="claude")) == ("claude", "opus", "high")
    assert dr.apply_overrides("claude", "opus", None, dr.Overrides(effort="max")) == ("claude", "opus", "max")
    assert dr.apply_overrides("claude", "opus", "high", None) == ("claude", "opus", "high")
    with pytest.raises(dr.OverrideInvalid):
        dr.apply_overrides("claude", "opus", None, dr.Overrides(agent="codex", effort="high"))
    with pytest.raises(dr.OverrideInvalid):
        dr.apply_overrides("claude", None, None, dr.Overrides(model=" "))


def test_compare_and_swap_claims_absent_then_updates_only_matching_record(workspace_layout):
    original = base()
    path = dr.compare_and_swap_record(workspace_layout, original, expected=None)
    changed = replace(original, phase="review")
    assert dr.compare_and_swap_record(workspace_layout, changed, expected=original) == path
    before = path.read_bytes()
    for stale in (None, original):
        with pytest.raises(dr.DispatchRecordConflict):
            dr.compare_and_swap_record(workspace_layout, original, expected=stale)
        assert path.read_bytes() == before
    # The existing API still permits unconditional replacement and returns Path.
    assert dr.save_record(workspace_layout, original) == path
    assert dr.load_record(path) == original


def test_compare_and_swap_detects_removed_record(workspace_layout):
    with pytest.raises(dr.DispatchRecordConflict):
        dr.compare_and_swap_record(workspace_layout, base(), expected=base())
    assert not dr.record_file(workspace_layout, "work/x", base().key).exists()


def test_compare_and_swap_reads_and_writes_with_one_owner_lock(workspace_layout, monkeypatch):
    lock = dr.locked_decision_owner
    load = dr.load_record
    write = dr.write_json_atomic
    held = False
    operations = []

    @contextmanager
    def tracked_lock(*args):
        nonlocal held
        assert not held, "nested owner lock"
        with lock(*args) as context:
            held = True
            try:
                yield context
            finally:
                held = False

    def checked_load(path):
        assert held
        operations.append("read")
        return load(path)

    def checked_write(path, payload):
        assert held
        operations.append("write")
        write(path, payload)

    monkeypatch.setattr(dr, "locked_decision_owner", tracked_lock)
    monkeypatch.setattr(dr, "load_record", checked_load)
    monkeypatch.setattr(dr, "write_json_atomic", checked_write)
    path = dr.compare_and_swap_record(workspace_layout, base(), expected=None)
    assert not held and operations == ["read", "write"]
    assert load(path) == base()
