"""The Orca-mutating orchestrate module (spec §4.2/§4.3).

Every Orca call goes through OrcaPort and Git runs through probe_git.
Journal attempted before effects and done after verified results; only an
uncertain create may be resolved by title, while other pending effects halt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from subagents_io.backend import BackendError
from work_tracker_okf.pipeline import code_phases
from work_tracker_okf.placement import ReaderObservation

from graph_works_core.orchestrate import dispatch_record as dr
from graph_works_core.orchestrate.orca_port import OrcaPort
from graph_works_core.orchestrate.placement import PlacementRecord, run_record_placement, run_record_reader
from graph_works_core.workspace.execute_return import pending_return, return_dispatch_admission
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.provenance import GitFailure, gate_git, probe_git, strict_commit, strict_git
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO

_CODE_PHASES: frozenset[str] = code_phases()

FAILURE_REASONS: frozenset[str] = frozenset(
    {
        "plan-invalid",
        "key-not-in-plan",
        "override-invalid",
        "placement-refused",
        "task-create-failed",
        "launch-failed",
        "receipt-mismatch",
        "outcome-unknown",
        "placement-mismatch",
        "placement-unrecorded",
        "unsent",
        "task-update-failed",
        "recovery-inspection",
        "record-invalid",
        "no-task",
        "reroute-live",
        "reason-missing",
    }
)
PROBE_OUTCOMES = ("submitted-heartbeat", "submitted-transcript", "nudged", "inconclusive", "no-terminal", "skipped")
#: `pin-detached` is the planner's reader placement (`commands.READER_ACTION`): a
#: dedicated checkout detached at the planned `start_sha`, never a shared one.
READER_ACTION = "pin-detached"
CREATION_ACTIONS = ("fork-child", "create-top-level")
ACTIONS = ("reuse", "main", *CREATION_ACTIONS, READER_ACTION)
RECORDED = ("recorded", "unchanged", "skipped:read-only-descendant", "reader-receipt", "reader-replayed")
_HEX_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


@dataclass(frozen=True, slots=True)
class DispatchFailure:
    step: str
    reason: str
    detail: str
    task_id: str | None = None
    dispatch_id: str | None = None
    refusal: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedPlacement:
    action: str
    path: str | None
    branch: str | None
    display_name: str | None
    repo_id: str | None
    parent_worktree_id: str | None
    lineage_set: bool
    notes: tuple[str, ...]
    #: The detached commit a `pin-detached` reader was verified at; `None` otherwise.
    start_sha: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: Literal["dispatched", "resumed", "existing"] | None
    key: str
    run_id: str
    task_id: str | None = None
    task_title: str | None = None
    display_name: str | None = None
    dispatch_id: str | None = None
    terminal: str | None = None
    placement: ObservedPlacement | None = None
    recorded: str | None = None
    probe: str | None = None
    record_path: str | None = None
    failure: DispatchFailure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def encode_launch_envelope(
    *,
    key: str,
    agent: str,
    model: str | None,
    reasoning_effort: str | None,
    placement_argv: list[str],
    mode: str,
    worktree_path: str | None,
    prompt: str,
) -> str:
    envelope = {
        "version": 2,
        "dispatch_key": key,
        "agent": agent,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "placement_argv": placement_argv,
        "mode": mode,
        "worktree_path": worktree_path,
    }
    return "GW_LAUNCH_V1 " + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n" + prompt


class _Stop(Exception):
    def __init__(self, reason: str, detail: str, *, refusal: str | None = None) -> None:
        self.reason, self.detail, self.refusal = reason, detail, refusal


@dataclass
class _Dispatch:
    layout: WorkspaceLayout
    port: OrcaPort
    today: date
    clock: Callable[[], datetime]
    result: DispatchResult
    root: str = ""
    entry: dict[str, Any] | None = None
    record: dr.DispatchRecord | None = None
    persisted: dr.DispatchRecord | None = None
    step: str = "validate"

    @property
    def item(self) -> dict[str, Any]:
        assert self.entry is not None
        return self.entry

    @property
    def journal(self) -> dr.DispatchRecord:
        assert self.record is not None
        return self.record

    @property
    def attempt(self) -> dr.Attempt:
        return self.journal.attempts[-1]

    def save(self, record: dr.DispatchRecord) -> None:
        dr.compare_and_swap_record(self.layout, record, expected=self.persisted)
        self.record = self.persisted = record

    def mark(
        self,
        state: Literal["attempted", "done", "skipped"],
        *,
        result: Mapping[str, Any] | None = None,
        reason: str | None = None,
        **fields: Any,  # noqa: ANN401 - forwarded dataclass field updates
    ) -> None:
        self.save(
            self.journal.with_step(
                len(self.journal.attempts) - 1,
                self.step,
                dr.StepState(state, at=self.clock().isoformat(), result=result, reason=reason),
                **fields,
            )
        )


def _validate(c: _Dispatch, plan: object) -> None:
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("path"), str)
        or not isinstance(plan.get("dispatches"), list)
        or not c.result.run_id.strip()
    ):
        raise _Stop("plan-invalid", "expected a plan path, dispatches list and nonblank run id")
    if any(not isinstance(e, dict) for e in plan["dispatches"]):
        raise _Stop("plan-invalid", "dispatch entries must be objects")
    entry = next((e for e in plan["dispatches"] if e.get("key") == c.result.key), None)
    if entry is None:
        raise _Stop("key-not-in-plan", "dispatch key is absent from plan")
    if (
        any(not isinstance(entry.get(n), str) for n in ("path", "phase", "mode", "agent", "prompt"))
        or any(entry.get(n) is not None and not isinstance(entry[n], str) for n in ("model", "reasoning_effort"))
        or not isinstance(entry.get("worktree"), dict)
        or entry["worktree"].get("action") not in ACTIONS
        or (entry.get("repo") is not None and not isinstance(entry["repo"], dict))
    ):
        raise _Stop("plan-invalid", "dispatch entry has invalid launch fields")
    if entry["worktree"]["action"] == READER_ACTION and (
        not isinstance(entry["worktree"].get("start_sha"), str)
        or not _HEX_OID.fullmatch(entry["worktree"]["start_sha"])
        or entry["worktree"].get("branch") is not None
    ):
        raise _Stop("plan-invalid", "reader dispatch needs a full commit start_sha and no branch")
    if pending_return(c.layout, entry["path"]) is not None:
        raise _Stop("recovery-inspection", "execute return is pending; inspect or resume before dispatch")
    c.root, c.entry = plan["path"], entry
    path = dr.record_file(c.layout, entry["path"], c.result.key)
    loaded = dr.load_record(path)
    if loaded is not None and (loaded.key, loaded.work_path, loaded.phase, loaded.run_id) != (
        c.result.key,
        entry["path"],
        entry["phase"],
        c.result.run_id,
    ):
        raise _Stop("record-invalid", "record identity differs from dispatch entry/run")
    c.persisted = loaded
    c.record = loaded or dr.DispatchRecord(c.result.key, entry["path"], entry["phase"], c.result.run_id)
    c.result = replace(
        c.result,
        task_title=c.result.key,
        display_name=f"{entry['path']} · {entry['phase']}",
        record_path="/" + dr.record_ref(entry["path"], c.result.key),
    )
    if c.journal.attempts and c.attempt.task_id not in c.journal.superseded:
        try:
            _validate_persisted(c.attempt, c.result.key)
        except dr.DispatchRecordError:
            # IDs are independently readable even when another persisted field
            # is corrupt. Do not attempt placement/result restoration on error.
            _restore_ids(c)
            raise


def _string(value: object) -> bool:
    return isinstance(value, str)


def _nullable_string(value: object) -> bool:
    return value is None or _string(value)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _shape(
    value: object,
    label: str,
    required: Mapping[str, Callable[[object], bool]],
    optional: Mapping[str, Callable[[object], bool]] | None = None,
) -> None:
    optional = optional or {}
    if (
        not isinstance(value, Mapping)
        or not required.keys() <= value.keys()
        or value.keys() - (required.keys() | optional.keys())
    ):
        raise dr.DispatchRecordError(f"{label}: missing or unexpected fields")
    for name, check in {**required, **optional}.items():
        if name in value and not check(value[name]):
            raise dr.DispatchRecordError(f"{label}.{name}: invalid value")


def _validate_persisted(a: dr.Attempt, key: str) -> None:
    """Check dispatch-owned shapes before any restore, lookup or resumed effect.

    The shared journal reader guarantees JSON mappings, not these producer
    contracts. Attempted and skipped steps may lack results; done data steps may not.
    """
    _shape(
        a.envelope,
        "envelope",
        {
            "version": lambda v: type(v) is int and v == 2,
            "dispatch_key": lambda v: v == key,
            "agent": _string,
            "model": _nullable_string,
            "reasoning_effort": _nullable_string,
            "placement_argv": _strings,
            "mode": _string,
            "worktree_path": _nullable_string,
        },
    )

    def action(value: object) -> bool:
        return value in ACTIONS

    def flag(value: object) -> bool:
        return type(value) is bool

    _shape(
        a.placement,
        "placement",
        {
            "action": action,
            "placement_argv": _strings,
            "repo_id": _nullable_string,
            "parent_worktree_id": _nullable_string,
        },
        {
            "worktree_id": _string,
            "start_sha": _string,
            "path": _string,
            "attempt_id": _string,
            "marker": _string,
            "reused": flag,
        },
    )
    schemas: dict[str, dict[str, Callable[[object], bool]]] = {
        "place": {"notes": _strings},
        "create": {"task_id": _string},
        "launch": dict.fromkeys(("dispatch_id", "terminal", "worktree_id"), _nullable_string),
        "settle": {
            "action": action,
            "path": lambda v: isinstance(v, str) and bool(v),
            "branch": lambda v: v is None or (isinstance(v, str) and bool(v)),
            "display_name": _nullable_string,
            "repo_id": _nullable_string,
            "parent_worktree_id": _nullable_string,
            "lineage_set": lambda v: type(v) is bool,
            "notes": _strings,
        },
        "record": {"recorded": lambda v: v in RECORDED},
        "probe": {"probe": lambda v: v in PROBE_OUTCOMES},
    }
    for name, state in a.steps.items():
        if name == "reroute":  # This verb does not consume reroute-owned results.
            continue
        schema = schemas.get(name, {})
        if state.result is None and (state.state in ("attempted", "skipped") or not schema):
            continue
        optional: dict[str, Callable[[object], bool]] = {}
        if name == "create":
            optional = {"adopted": flag}
        elif name == "attend":
            optional = {"warning": _string}
        elif name == "settle":
            optional = {"start_sha": _nullable_string}
        _shape(state.result, f"{name}.result", schema, optional)
        settled = state.result
        if name == "settle" and settled is not None:
            # Only a reader settles without a branch, and then it settles with a commit.
            is_reader = settled["action"] == READER_ACTION
            if is_reader != (settled["branch"] is None) or (is_reader and not _string(settled.get("start_sha"))):
                raise dr.DispatchRecordError("settle.result: branch and start_sha disagree with the action")
        if state.state == "done":
            if name == "create" and a.task_id is None:
                raise dr.DispatchRecordError("create: completed step requires task_id")
            if name == "launch" and a.dispatch_id is None:
                raise dr.DispatchRecordError("launch: completed step requires dispatch_id")
            if name == "settle" and (a.placement is None or not _string(a.placement.get("worktree_id"))):
                raise dr.DispatchRecordError("settle: completed step requires placement.worktree_id")


def _restore_ids(c: _Dispatch) -> None:
    a = c.attempt
    launched = a.steps.get("launch", dr.StepState("attempted")).result or {}
    created = a.steps.get("create", dr.StepState("attempted")).result or {}

    def known(value: object) -> str | None:
        return value if isinstance(value, str) else None

    c.result = replace(
        c.result,
        task_id=c.result.task_id or a.task_id or known(created.get("task_id")),
        dispatch_id=c.result.dispatch_id or a.dispatch_id or known(launched.get("dispatch_id")),
        terminal=c.result.terminal or known(launched.get("terminal")),
        display_name=a.display_name,
    )


def _restore(c: _Dispatch) -> None:
    a = c.attempt
    launch = a.steps.get("launch", dr.StepState("attempted")).result or {}
    settled = a.steps.get("settle", dr.StepState("attempted")).result
    placement = None
    if settled is not None:
        placement = ObservedPlacement(**{**settled, "notes": tuple(settled["notes"])})
    attended = a.steps.get("attend", dr.StepState("attempted")).result or {}
    if placement is not None and "warning" in attended:
        placement = replace(placement, notes=(*placement.notes, f"in-review not set: {attended['warning']}"))
    record_step = a.steps.get("record", dr.StepState("attempted"))
    probe_step = a.steps.get("probe", dr.StepState("attempted"))
    recorded = record_step.result or {}
    probed = probe_step.result or {}
    c.result = replace(
        c.result,
        task_id=a.task_id,
        dispatch_id=a.dispatch_id,
        display_name=a.display_name,
        terminal=launch.get("terminal"),
        placement=placement,
        recorded=recorded.get("recorded", "skipped:read-only-descendant" if record_step.state == "skipped" else None),
        probe=probed.get("probe", "skipped" if probe_step.state == "skipped" else None),
    )


def _resolve(c: _Dispatch) -> bool:
    """Return whether an attempt exists to resume; an existing result ends the call."""
    c.step = "create"
    try:
        tasks = c.port.task_list(c.result.run_id)
    except BackendError as exc:
        if c.journal.attempts and c.attempt.task_id not in c.journal.superseded:
            _restore(c)
        raise _Stop("recovery-inspection", f"cannot resolve current Tasks: {exc}") from exc
    titled = [t for t in tasks if t["title"] == c.result.key and t["id"] not in c.journal.superseded]
    a = c.journal.attempts[-1] if c.journal.attempts else None
    if a is not None and a.task_id not in c.journal.superseded and not a.complete:
        _restore(c)
        pending = a.pending()
        if pending == "create":
            if len(titled) > 1:
                raise _Stop("recovery-inspection", "multiple Tasks match an uncertain create")
            if titled:
                task_id = titled[0]["id"]
                c.result = replace(c.result, task_id=task_id)
                c.mark("done", result={"task_id": task_id, "adopted": True}, task_id=task_id)
        elif pending is not None:
            c.step = pending
            raise _Stop("recovery-inspection", "previous effect is attempted but not confirmed")
        elif a.task_id not in {t["id"] for t in titled}:
            raise _Stop("recovery-inspection", "incomplete journal Task is absent from title lookup")
        c.result = replace(c.result, status="resumed")
        return True
    if len(titled) > 1:
        raise _Stop("recovery-inspection", "multiple current Tasks share the dispatch title")
    if titled:
        if a is not None and a.task_id == titled[0]["id"]:
            _restore(c)
        c.result = replace(c.result, status="existing", task_id=titled[0]["id"], display_name=titled[0]["display_name"])
    return False


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Stop("placement-refused", f"{label} is missing")
    return value


def _placement_file(c: _Dispatch) -> Path:
    path = (
        dr.record_file(c.layout, c.item["path"], c.result.key).parent.parent / "orca-placement" / f"{c.result.key}.json"
    )
    owner = c.layout.bundle_dir / c.item["path"]
    if (
        owner.resolve() != c.layout.bundle_dir.resolve() / c.item["path"]
        or not path.parent.resolve().is_relative_to(owner.resolve())
        or path.is_symlink()
    ):
        raise _Stop("placement-refused", "placement path escapes its owner directory")
    return path


def _owning_checkout(repo_path: str) -> str | None:
    """The primary checkout that owns *repo_path*'s git directory, or None when git cannot say.

    Git lists the main working tree first. For a managed repository the plan
    names a linked working checkout, and the repository Orca knows is the
    clone that owns it.
    """
    inventory = probe_git(Path(repo_path), "worktree", "list", "--porcelain", "-z")
    if inventory.returncode != 0:
        return None
    first = next((field for field in inventory.stdout.split("\0") if field.startswith("worktree ")), None)
    return os.path.realpath(first[len("worktree ") :]) if first else None


def _orca_repo_id(c: _Dispatch, repo_path: str) -> str:
    """The one Orca repository for *repo_path*: registered at its owning checkout, else at the path itself."""
    wanted = [path for path in dict.fromkeys((_owning_checkout(repo_path), os.path.realpath(repo_path))) if path]
    registered = c.port.repo_list()
    for path in wanted:
        repos = [r for r in registered if os.path.realpath(r["path"]) == path]
        if len(repos) == 1:
            return repos[0]["id"]
        if repos:
            raise _Stop("placement-refused", f"{len(repos)} Orca repositories match {path}; exactly one required")
    raise _Stop("placement-refused", f"0 Orca repositories match {' or '.join(wanted)}; exactly one required")


def _reader_problem(repo: str, path: str, sha: str, *, detached: bool = True) -> str | None:
    """Why *path* is not a clean checkout of *repo* at *sha* (detached unless told otherwise)."""
    if not Path(path).is_absolute():
        return "path is not absolute"
    common = probe_git(Path(repo), "rev-parse", "--path-format=absolute", "--git-common-dir")
    actual = probe_git(Path(path), "rev-parse", "--path-format=absolute", "--git-common-dir")
    if (
        common.returncode != 0
        or actual.returncode != 0
        or os.path.realpath(common.stdout.strip()) != os.path.realpath(actual.stdout.strip())
    ):
        return "wrong observed repository"
    top = probe_git(Path(path), "rev-parse", "--show-toplevel")
    if top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(path):
        return "path is not the checkout root"
    if detached:
        symbolic = probe_git(Path(path), "symbolic-ref", "-q", "HEAD")
        if symbolic.returncode == 0:
            return "checkout is on a branch, not detached"
        if symbolic.returncode != 1:
            return "cannot prove detached HEAD"
    head = probe_git(Path(path), "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != sha:
        return f"HEAD is {head.stdout.strip() or 'unknown'}, not {sha}"
    status = probe_git(Path(path), "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0 or status.stdout.strip():
        return "checkout is dirty"
    return None


def _prepare_reader(c: _Dispatch, placement_file: Path) -> dict[str, Any]:
    """Allocate one detached reader checkout per attempt; recovery verifies, never repairs.

    The marker (an Orca worktree comment) is derived from the key, the SHA,
    the repository and this attempt's ordinal, so a crash between creation
    and the journal write is recovered by re-deriving it and finding the
    checkout again. A superseded attempt gets a new ordinal, so a launched
    checkout is never shared with a later dispatch, even at the same SHA.
    """
    wt = c.item["worktree"]
    sha = wt["start_sha"]
    base = _text(wt.get("base_branch"), "source branch")
    repo = _text((c.item.get("repo") or {}).get("path"), "code repository path")
    if not Path(repo).is_absolute():
        raise _Stop("placement-refused", "code repository path is not absolute")
    commit = probe_git(Path(repo), "rev-parse", "--verify", f"{sha}^{{commit}}")
    if commit.returncode != 0 or commit.stdout.strip() != sha:
        raise _Stop("placement-refused", f"start_sha {sha} is not a commit in {repo}")
    inventory = probe_git(Path(repo), "worktree", "list", "--porcelain", "-z")
    if inventory.returncode != 0:
        raise _Stop("placement-refused", "cannot read the repository's worktree inventory")
    before = {
        os.path.realpath(field[len("worktree ") :])
        for field in inventory.stdout.split("\0")
        if field.startswith("worktree ")
    }
    before.add(os.path.realpath(repo))
    protected = {os.path.realpath(repo)}
    parent_path = wt.get("parent_path")
    if parent_path is not None:
        protected.add(os.path.realpath(_text(parent_path, "parent_path")))
    repo_id = _orca_repo_id(c, repo)
    attempt = f"{c.result.run_id}-{len(c.journal.attempts) + 1}"
    digest = hashlib.sha256(
        json.dumps([c.result.key, sha, os.path.realpath(repo), attempt], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    marker = f"gw-reader:{digest}"
    # Intent before effect: a crash after `worktree create` leaves this file
    # naming the marker the next attempt re-derives and looks for.
    dr.write_json_atomic(
        placement_file,
        {"action": READER_ACTION, "attempt_id": attempt, "marker": marker, "start_sha": sha, "prepared": False},
    )
    marked = [row for row in c.port.worktree_list(repo_id) if row["comment"] == marker]
    if len(marked) > 1:
        raise _Stop("placement-refused", "ambiguous reader markers; repair before creating")
    reused = bool(marked)
    if marked:
        row = marked[0]
        tip = sha
    else:
        tip_probe = probe_git(Path(repo), "rev-parse", "--verify", f"{base}^{{commit}}")
        if tip_probe.returncode != 0 or not tip_probe.stdout.strip():
            raise _Stop("placement-refused", f"source branch {base} is not a commit in {repo}")
        tip = tip_probe.stdout.strip()
        row = c.port.worktree_create(name=f"gw-reader-{digest}", repo_id=repo_id, base_branch=base, comment=marker)
    if row["repo_id"] != repo_id:
        raise _Stop("placement-refused", "wrong observed repository")
    raw_path = row["path"]
    if not raw_path or not Path(raw_path).is_absolute():
        raise _Stop("placement-refused", "observed path is not absolute")
    path = os.path.realpath(raw_path)
    if path in protected or row["is_main"] is not False:
        raise _Stop("placement-refused", f"reader cannot enter an integration checkout {path}")
    if not reused:
        if path in before:
            raise _Stop("placement-refused", f"Orca returned an existing checkout {path}")
        problem = _reader_problem(repo, path, tip, detached=False)
        if problem is not None:
            raise _Stop("placement-refused", f"newly created checkout failed verification ({problem})")
        # Only a new, clean, correct-repository checkout at the source tip
        # returned by this creation is ever mutated, and only by detaching it.
        if probe_git(Path(path), "checkout", "--quiet", "--detach", sha).returncode != 0:
            raise _Stop("placement-refused", f"cannot detach the reader checkout at {sha}")
    problem = _reader_problem(repo, path, sha)
    if problem is not None:
        raise _Stop("placement-refused", f"reader checkout is not verified ({problem}); it is never reset -- repair")
    return {
        "action": READER_ACTION,
        "placement_argv": ["--worktree", f"path:{path}"],
        "repo_id": repo_id,
        "parent_worktree_id": None,
        "start_sha": sha,
        "path": path,
        "attempt_id": attempt,
        "marker": marker,
        "reused": reused,
    }


def _place(c: _Dispatch) -> tuple[dict[str, Any], tuple[str, ...]]:
    c.step = "place"
    wt = c.item["worktree"]
    action = wt["action"]
    repo_id = parent_id = None
    notes: list[str] = []
    placement_file = _placement_file(c)
    # The entry itself is durable too: a park-resume or retry of this Task
    # (auto-drive §2.6/§4.1) re-prepares a reader from it, never from a later plan.
    dr.write_json_atomic(placement_file.with_name(f"{c.result.key}.dispatch.json"), c.item)
    if action == READER_ACTION:
        placed = _prepare_reader(c, placement_file)
        dr.write_json_atomic(placement_file, placed)
        return placed, ()
    git = gate_git(c.layout)
    if isinstance(git, GitFailure):
        raise _Stop("placement-refused", f"git-unavailable ({git.cause}): {git.detail}")
    if action in ("reuse", "main"):
        argv = ["--worktree", f"path:{_text(wt.get('path'), 'worktree path')}"]
        baseline = strict_commit(Path(_text(wt.get("path"), "worktree path")), "HEAD", git=git)
    else:
        branch, base = _text(wt.get("branch"), "branch"), _text(wt.get("base_branch"), "base_branch")
        repo_path = _text((c.item.get("repo") or {}).get("path"), "code repository path")
        baseline = strict_commit(Path(repo_path), base, git=git)
        repo_id = _orca_repo_id(c, repo_path)
        parent_path = wt.get("parent_path")
        if parent_path is not None:
            parent_path = _text(parent_path, "parent_path")
            try:
                parent = c.port.worktree_show(f"path:{parent_path}")
            except BackendError:
                parent = None
            if parent is None:
                notes.append(
                    f"Orca does not know the planned parent {parent_path}; "
                    "creating a parentless top-level worktree instead."
                )
            elif parent["repo_id"] != repo_id:
                raise _Stop("placement-refused", "planned parent belongs to another repository")
            elif parent["is_main"] is not False:
                notes.append(
                    f"Parent {parent_path} is the repository's own checkout; "
                    "creating a parentless top-level worktree instead."
                )
            else:
                parent_id = parent["id"]
        argv = ["--worktree", "new-top-level", "--name", branch, "--base-branch", base, "--repo", f"id:{repo_id}"]
    if isinstance(baseline, GitFailure):
        raise _Stop("placement-refused", f"cannot read the pre-launch baseline: {baseline.detail}")
    placed = {
        "action": action,
        "placement_argv": argv,
        "repo_id": repo_id,
        "parent_worktree_id": parent_id,
        "start_sha": baseline,
    }
    dr.write_json_atomic(placement_file, placed)
    return placed, tuple(notes)


def _encode(c: _Dispatch, placed: dict[str, Any], profile: tuple[str, str | None, str | None]) -> dict[str, Any]:
    c.step = "encode"
    agent, model, effort = profile
    spec = encode_launch_envelope(
        key=c.result.key,
        agent=agent,
        model=model,
        reasoning_effort=effort,
        placement_argv=placed["placement_argv"],
        mode=c.item["mode"],
        worktree_path=c.item["worktree"].get("path"),
        prompt="",
    )
    envelope: dict[str, Any] = json.loads(spec.split("\n", 1)[0].removeprefix("GW_LAUNCH_V1 "))
    return envelope


def _create(c: _Dispatch) -> None:
    a = c.attempt
    spec = (
        "GW_LAUNCH_V1 "
        + json.dumps(dict(a.envelope), ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + c.item["prompt"]
    )
    task_id = c.port.task_create(c.result.run_id, spec=spec, title=c.result.key, display_name=a.display_name)
    c.result = replace(c.result, task_id=task_id)
    c.mark("done", result={"task_id": task_id}, task_id=task_id)


def _launch(c: _Dispatch) -> None:
    assert c.result.task_id is not None
    try:
        start = c.port.worker_start(
            c.result.run_id,
            c.result.task_id,
            request=dict(c.attempt.envelope),
            placement_argv=c.attempt.envelope["placement_argv"],
        )
    except BackendError as exc:
        # BackendError is the cross-band seam. Whitelist IDs; never persist the
        # backend's request, receipt, prompt or preamble carriers.
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            dispatch = details.get("dispatchId")
            terminal = details.get("agentTerminalHandle")
            worktree_id = None
            effects = details.get("effects")
            for effect in effects if isinstance(effects, list) else []:
                if not isinstance(effect, dict) or not isinstance(effect.get("id"), str):
                    continue
                if effect.get("kind") == "worktree":
                    worktree_id = effect["id"]
                elif effect.get("kind") == "terminal" and effect.get("role") == "agent" and terminal is None:
                    terminal = effect["id"]
            c.result = replace(
                c.result,
                dispatch_id=dispatch if isinstance(dispatch, str) else None,
                terminal=terminal if isinstance(terminal, str) else None,
            )
            c.mark(
                "attempted",
                dispatch_id=c.result.dispatch_id,
                result={"dispatch_id": c.result.dispatch_id, "terminal": c.result.terminal, "worktree_id": worktree_id},
            )
        raise
    c.result = replace(c.result, dispatch_id=start["dispatch_id"], terminal=start["terminal"])
    known = {k: start[k] for k in ("dispatch_id", "terminal", "worktree_id")}
    if start["state"] == "outcome_unknown" or start["dispatch_id"] is None or start["receipt_problem"]:
        c.mark("attempted", result=known, dispatch_id=start["dispatch_id"])
        if start["state"] == "outcome_unknown":
            raise _Stop("outcome-unknown", "worker start outcome is unknown")
        if start["dispatch_id"] is None:
            raise _Stop("launch-failed", "worker start returned no dispatch id")
        raise _Stop("receipt-mismatch", start["receipt_problem"] or "launch receipt mismatch")
    c.mark("done", result=known, dispatch_id=start["dispatch_id"])


def _writer_baseline(c: _Dispatch, path: Path, branch: str, *, creation: bool, pre_launch: str) -> str:
    """The commit this writer's work starts from, proved rather than assumed.

    Existing checkouts: the HEAD read before launch (`_place`). New branches:
    the branch's creation commit from its reflog; without a reflog, the
    pre-launch base tip only when it is exactly where the branch forked.
    """
    if not pre_launch:
        raise _Stop("placement-mismatch", "no pre-launch baseline was captured for this attempt")
    if not creation:
        return pre_launch
    git = gate_git(c.layout)
    if isinstance(git, GitFailure):
        raise _Stop("placement-mismatch", f"git-unavailable ({git.cause}): {git.detail}")
    log = strict_git(path, "reflog", "show", "--format=%H", f"refs/heads/{branch}", "--", git=git)
    entries = log.split() if isinstance(log, str) else []
    if entries:
        created = entries[-1]
        if created != pre_launch:
            raise _Stop(
                "placement-mismatch",
                f"branch {branch} was created at {created}, not the pre-launch base tip {pre_launch}",
            )
        return created
    fork = strict_git(path, "merge-base", "HEAD", c.item["worktree"]["base_branch"], git=git)
    # merge-base(HEAD, base) equal to the pre-launch tip already proves it is an ancestor of HEAD.
    if isinstance(fork, str) and fork.strip() == pre_launch:
        return pre_launch
    raise _Stop("placement-mismatch", f"cannot prove where branch {branch} started")


def _settle(c: _Dispatch) -> None:
    assert c.result.dispatch_id is not None
    launched = c.attempt.steps["launch"].result or {}
    wid = launched.get("worktree_id") or c.port.worker_show(c.result.dispatch_id)["worktree_id"]
    if not wid:
        raise _Stop("placement-mismatch", "worker has no observed worktree id")
    row = c.port.worktree_show(f"id:{wid}")
    if row is None:
        raise _Stop("placement-mismatch", "worker worktree is absent")
    wt = c.item["worktree"]
    placed = c.attempt.placement or {}
    reader = wt["action"] == READER_ACTION
    creation = wt["action"] in CREATION_ACTIONS
    lineage_set = False
    if reader:
        prepared = placed.get("path")
        if not row["path"] or not prepared or os.path.realpath(row["path"]) != os.path.realpath(prepared):
            raise _Stop("placement-mismatch", "observed reader path differs from the prepared path")
        if row["parent_id"] is not None:
            raise _Stop("placement-mismatch", "reader has unexpected parent")
    elif not creation:
        if not row["path"] or os.path.realpath(row["path"]) != os.path.realpath(wt["path"]):
            raise _Stop("placement-mismatch", "observed worktree path differs from planned path")
    else:
        if row["repo_id"] != placed.get("repo_id") or row["is_main"] is not False:
            raise _Stop("placement-mismatch", "worktree repository or main-checkout flag differs")
        parent = placed.get("parent_worktree_id")
        if parent is not None and row["parent_id"] is None:
            c.port.worktree_set_parent(wid, parent)
            lineage_set = True
            row = c.port.worktree_show(f"id:{wid}")
        if row is None or row["parent_id"] != parent:
            raise _Stop("placement-mismatch", "observed parent differs from planned parent")
    for worker in c.port.worker_list(c.result.run_id):
        if (
            worker["task_id"] != c.result.task_id
            and worker["worktree_id"] == wid
            and worker["state"] not in ("succeeded", "failed", "stopped")
        ):
            raise _Stop("placement-mismatch", "path already claimed by another dispatch this Run")
    path = row["path"]
    if not path or not Path(path).is_dir():
        raise _Stop("placement-mismatch", "observed path not reachable from this host; cannot verify")
    if creation and probe_git(Path(path), "merge-base", "--is-ancestor", wt["base_branch"], "HEAD").returncode != 0:
        raise _Stop("placement-mismatch", "planned base is not an ancestor of observed HEAD")
    branch: str | None
    start_sha: str | None = None
    if reader:
        # A reader is verified by content, not by branch: detached at the
        # planned commit, clean, and rooted in the planned repository.
        start_sha = str(placed.get("start_sha") or "")
        repo = str((c.item.get("repo") or {}).get("path") or "")
        problem = _reader_problem(repo, path, start_sha)
        if problem is not None:
            raise _Stop("placement-mismatch", f"reader checkout is not verified ({problem})")
        branch = None
    else:
        branch_probe = probe_git(Path(path), "branch", "--show-current")
        branch = branch_probe.stdout.strip()
        if branch_probe.returncode != 0 or not branch or branch != row["branch"]:
            raise _Stop("placement-mismatch", "Git branch disagrees with observed worktree branch")
        start_sha = _writer_baseline(
            c, Path(path), branch, creation=creation, pre_launch=str(placed.get("start_sha") or "")
        )
    place_step = c.attempt.steps["place"].result or {}
    notes = tuple(place_step.get("notes", ()))
    if creation and row["display_name"] != wt["branch"]:
        notes += (f'worktree name uniquified by Orca (requested "{wt["branch"]}", landed at {path})',)
    observed = ObservedPlacement(
        wt["action"],
        path,
        branch,
        row["display_name"],
        row["repo_id"],
        row["parent_id"],
        lineage_set,
        notes,
        start_sha,
    )
    c.result = replace(c.result, placement=observed)
    # Keep the resolved id even when worker-start did not return it.
    c.mark("done", result={**asdict(observed), "notes": list(observed.notes)}, placement={**placed, "worktree_id": wid})


def _record_reader(c: _Dispatch) -> None:
    """A reader — root or descendant — records a receipt, never a placement stamp."""
    observed = c.result.placement
    assert observed is not None and observed.path is not None and observed.start_sha is not None
    assert c.result.task_id is not None and c.result.dispatch_id is not None
    repo_name = (c.item.get("repo") or {}).get("name")
    if not isinstance(repo_name, str) or not repo_name:
        raise _Stop("placement-unrecorded", "reader dispatch names no declared repository")
    observation = ReaderObservation(
        c.result.task_id, c.result.dispatch_id, c.result.key, repo_name, observed.path, observed.start_sha
    )
    written = run_record_reader(
        c.layout, c.item["path"], root=c.root, phase=c.item["phase"], observation=observation, dry_run=False
    )
    if written.plan.refusal is not None:
        raise _Stop("placement-unrecorded", str(written.plan.detail), refusal=written.plan.refusal)
    if written.conflict is not None:
        raise _Stop(
            "placement-unrecorded", "reader receipt differs from an earlier attempt's", refusal=written.conflict
        )
    if not (written.written or written.replayed):
        raise _Stop("placement-unrecorded", "reader receipt was not written")
    c.result = replace(c.result, recorded="reader-receipt" if written.written else "reader-replayed")
    c.mark("done", result={"recorded": c.result.recorded})


def _record(c: _Dispatch) -> None:
    if c.item["worktree"]["action"] == READER_ACTION:
        _record_reader(c)
        return
    if c.item["path"] != c.root and c.item["phase"] not in _CODE_PHASES:
        c.result = replace(c.result, recorded="skipped:read-only-descendant")
        c.mark("skipped", reason="read-only-descendant", result={"recorded": c.result.recorded})
        return
    observed = c.result.placement
    assert observed is not None and observed.path is not None and observed.branch is not None

    path, branch = observed.path, observed.branch
    selected_repo = (c.item.get("repo") or {}).get("name")

    code_phase = c.item["phase"] in _CODE_PHASES and selected_repo != WORKSPACE_REPO

    def apply(dry_run: bool, start_sha: str | None) -> PlacementRecord:
        return run_record_placement(
            c.layout,
            c.item["path"],
            root=c.root,
            phase=c.item["phase"],
            worktree=path,
            branch=branch,
            today=c.today,
            repo_name=None if selected_repo == WORKSPACE_REPO else selected_repo,
            repo=WORKSPACE_REPO if selected_repo == WORKSPACE_REPO else None,
            start_sha=start_sha,
            require_start_sha=code_phase and dry_run is False,
            dry_run=dry_run,
        )

    preview = apply(True, None)
    keep = preview.plan.refusal is None and not preview.plan.changed and preview.plan.start_after is not None
    candidate = None if keep or not code_phase else observed.start_sha
    written = apply(False, candidate)
    if written.plan.refusal is not None:
        raise _Stop("placement-unrecorded", str(written.plan.detail), refusal=written.plan.refusal)
    if written.application is not None and not written.application.ok:
        raise _Stop("placement-unrecorded", "placement application failed")
    verified = apply(True, candidate)
    if verified.plan.refusal is not None or verified.plan.changed:
        raise _Stop("placement-unrecorded", "verification failed", refusal=verified.plan.refusal)
    c.result = replace(c.result, recorded="recorded" if written.written else "unchanged")
    c.mark("done", result={"recorded": c.result.recorded})


def _attend(c: _Dispatch) -> None:
    if c.item["mode"] != "attend":
        c.mark("skipped", reason="not-attend")
        return
    assert c.attempt.placement is not None
    warning: dict[str, str] = {}
    try:
        c.port.worktree_set_status(c.attempt.placement["worktree_id"], "in-review")
    except BackendError as exc:
        warning = {"warning": str(exc)}
        assert c.result.placement is not None
        c.result = replace(
            c.result,
            placement=replace(c.result.placement, notes=(*c.result.placement.notes, f"in-review not set: {exc}")),
        )
    c.mark("done", result=warning)


def _probe(c: _Dispatch, enabled: bool, seconds: float, sleep: Callable[[float], None]) -> None:
    def finish(outcome: str) -> None:
        c.result = replace(c.result, probe=outcome)
        c.mark(
            "skipped" if outcome == "skipped" else "done",
            result={"probe": outcome},
            reason="probe-disabled" if outcome == "skipped" else None,
        )

    if not enabled:
        finish("skipped")
        return
    if not c.result.terminal:
        finish("no-terminal")
        return
    assert c.result.dispatch_id is not None
    try:
        sleep(seconds)
        if c.port.worker_show(c.result.dispatch_id)["last_heartbeat_at"]:
            finish("submitted-heartbeat")
            return
        for sent in range(3):
            read = c.port.worker_read(c.result.dispatch_id, limit=5)
            if read["source"] != "transcript":
                finish("inconclusive")
                return
            if read["message_count"] > 0:
                finish("nudged" if sent else "submitted-transcript")
                return
            # Re-check immediately before EVERY Enter, including the first:
            # a heartbeat may have arrived during either transcript read.
            if c.port.worker_show(c.result.dispatch_id)["last_heartbeat_at"]:
                finish("submitted-heartbeat")
                return
            if sent == 2:
                break
            c.port.terminal_send_enter(c.result.terminal)
            sleep(seconds)
    except BackendError:
        finish("inconclusive")
        return
    raise _Stop("unsent", "prompt remains unsubmitted after two Enter nudges")


def _task_update(c: _Dispatch) -> None:
    assert c.result.task_id is not None
    c.port.task_update(c.result.run_id, c.result.task_id, "dispatched")
    c.mark("done")


def run_dispatch(
    layout: WorkspaceLayout,
    key: str,
    *,
    plan: object,
    run_id: str,
    port: OrcaPort,
    today: date,
    clock: Callable[[], datetime],
    probe: bool = True,
    settle_seconds: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> DispatchResult:
    c = _Dispatch(layout, port, today, clock, DispatchResult(None, key, run_id))
    with ExitStack() as ownership:
        try:
            _validate(c, plan)
            ownership.enter_context(dr.execution_owner(layout, key))
            ownership.enter_context(return_dispatch_admission(layout, c.item["path"]))
            # Validation read is only a snapshot until execution ownership is held.
            if dr.load_record(dr.record_file(layout, c.item["path"], key)) != c.persisted:
                raise dr.DispatchRecordConflict("dispatch record changed before execution ownership; retry")
            if pending_return(layout, c.item["path"]) is not None:
                raise _Stop("recovery-inspection", "execute return is pending; inspect or resume before dispatch")
            resuming = _resolve(c)
            if c.result.status == "existing":
                return c.result
            if not resuming:
                # Reject overrides before *any* placement write or provisioning.
                c.step = "encode"
                profile = dr.apply_overrides(
                    c.item["agent"], c.item.get("model"), c.item.get("reasoning_effort"), c.journal.latest_overrides()
                )
                placed, notes = _place(c)
                envelope = _encode(c, placed, profile)
                at = clock().isoformat()
                steps = {name: dr.StepState("done", at=at) for name in ("validate", "place", "encode")}
                steps["place"] = dr.StepState("done", at=at, result={"notes": list(notes)})
                steps["create"] = dr.StepState("attempted", at=at)
                attempt = dr.Attempt(None, None, c.result.display_name or "", envelope, placed, steps)
                c.save(c.journal.with_attempt(attempt))
                c.result = replace(c.result, status="dispatched")
            operations: tuple[tuple[str, Callable[[], None]], ...] = (
                ("create", lambda: _create(c)),
                ("launch", lambda: _launch(c)),
                ("settle", lambda: _settle(c)),
                ("record", lambda: _record(c)),
                ("attend", lambda: _attend(c)),
                ("probe", lambda: _probe(c, probe, settle_seconds, sleep)),
                ("task-update", lambda: _task_update(c)),
            )
            for name, operation in operations:
                c.step = name
                state = c.attempt.steps.get(name)
                if state is not None and state.state in ("done", "skipped"):
                    continue
                if state is None:
                    c.mark("attempted")
                operation()
            return c.result
        except (dr.DispatchRecordError, dr.OverrideInvalid, _Stop, BackendError, OSError, ValueError) as exc:
            reason = {
                "validate": "plan-invalid",
                "create": "task-create-failed",
                "place": "placement-refused",
                "encode": "plan-invalid",
                "launch": "launch-failed",
                "settle": "placement-mismatch",
                "record": "placement-unrecorded",
                "attend": "record-invalid",
                "probe": "recovery-inspection",
                "task-update": "task-update-failed",
            }[c.step]
            refusal = None
            detail = str(exc)
            if isinstance(exc, dr.DispatchRecordConflict):
                reason = "recovery-inspection"
                if c.record is not None and c.record.attempts:
                    _restore_ids(c)
            elif isinstance(exc, dr.DispatchRecordError):
                reason = "record-invalid"
            elif isinstance(exc, dr.OverrideInvalid):
                reason = "override-invalid"
            elif isinstance(exc, _Stop):
                reason, detail, refusal = exc.reason, exc.detail, exc.refusal
            failure = DispatchFailure(c.step, reason, detail, c.result.task_id, c.result.dispatch_id, refusal)
            return replace(c.result, status=None, failure=failure)
