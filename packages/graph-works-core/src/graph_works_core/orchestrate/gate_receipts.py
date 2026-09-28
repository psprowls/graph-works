"""Gate receipts: what `gw work gate` ran, keyed on the tree it ran against.

A managed Explanation page per owner (`references/03-gate-receipts.md`), shaped
like the finish receipt: `receipt_version: 1`, `owner`, and an append-only
`runs:` list. Parsing is strict per file and never raises out of the lookup: a
malformed file is a warning naming its path and contributes nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from okf_io import load_bundle, parse
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.sources import upsert

from graph_works_core.workspace.commits import WorkspaceCommit, commit_mode, item_stem
from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import apply_mutation

GateScope = Literal["full", "scoped"]
RECEIPT_GLOB = "work/**/references/03-gate-receipts.md"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class GateRun:
    run_id: str
    repo: str
    worktree: str
    head: str
    tree: str
    clean: bool
    tree_changed: bool
    scope: GateScope
    command: str
    names: tuple[str, ...]
    exit: int
    log_path: str
    log_tail: str
    started: str
    duration_s: float


@dataclass(frozen=True, slots=True)
class GateMatch:
    owner: str
    run: GateRun


@dataclass(frozen=True, slots=True)
class ReceiptLookup:
    match: GateMatch | None
    warnings: tuple[str, ...]


def sanitize_tail(text: str, *, lines: int = 40) -> str:
    """The last *lines* lines, with ANSI escapes, CRs and control bytes removed."""
    cleaned = _CONTROL.sub("", _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))
    return "\n".join(cleaned.splitlines()[-lines:])


_STR = ("run_id", "repo", "worktree", "command", "log_path", "log_tail", "started")


def _run(value: object) -> GateRun:
    if not isinstance(value, dict):
        raise ValueError("run is not a mapping")
    for key in _STR:
        if not isinstance(value.get(key), str):
            raise ValueError(f"run {key} is not a string")
    for key in ("head", "tree"):
        if not isinstance(value.get(key), str) or not _SHA.fullmatch(value[key]):
            raise ValueError(f"run {key} is not a commit or tree SHA")
    for key in ("clean", "tree_changed"):
        if type(value.get(key)) is not bool:
            raise ValueError(f"run {key} is not a boolean")
    if value.get("scope") not in ("full", "scoped"):
        raise ValueError("run scope is not full or scoped")
    if type(value.get("exit")) is not int:
        raise ValueError("run exit is not an integer")
    duration = value.get("duration_s")
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        raise ValueError("run duration_s is not a number")
    names = value.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("run names is not a list of strings")
    return GateRun(
        run_id=value["run_id"],
        repo=value["repo"],
        worktree=value["worktree"],
        head=value["head"],
        tree=value["tree"],
        clean=value["clean"],
        tree_changed=value["tree_changed"],
        scope=cast(GateScope, value["scope"]),
        command=value["command"],
        names=tuple(names),
        exit=value["exit"],
        log_path=value["log_path"],
        log_tail=value["log_tail"],
        started=value["started"],
        duration_s=float(duration),
    )


def parse_gate_receipt(text: str) -> tuple[str, tuple[GateRun, ...]]:
    doc = parse(text)
    data = doc.fm_data()
    runs = data.get("runs")
    if (
        doc.parse_error
        or data.get("type") != "Explanation"
        or type(data.get("receipt_version")) is not int
        or data.get("receipt_version") != 1
        or not isinstance(data.get("owner"), str)
        or not isinstance(runs, list)
    ):
        raise ValueError("malformed gate receipt")
    return data["owner"], tuple(_run(run) for run in runs)


def run_data(run: GateRun) -> dict[str, object]:
    data = asdict(run)
    data["names"] = list(run.names)
    return data


def render_receipt(owner: str, runs: Sequence[GateRun], *, created: str) -> str:
    """A fresh receipt page; appends go through `okf_io` `Document.set` instead."""
    page = parse(
        "---\ntype: Explanation\ntitle: Gate receipts\n"
        "description: Gate runs gw executed for this item.\nstatus: draft\n"
        f"created: {created}\nreceipt_version: 1\nowner: {json.dumps(owner)}\nruns: []\n---\n\n"
        "Gate runs recorded by `gw work gate`.\n"
    )
    page.set("runs", [run_data(run) for run in runs])
    return page.serialize()


def satisfies(run: GateRun, *, repo: str, tree: str, command: str) -> bool:
    return (
        run.repo == repo
        and run.tree == tree
        and run.clean
        and not run.tree_changed
        and run.exit == 0
        and run.scope == "full"
        and run.command == command
    )


def find_satisfying(bundle_root: Path, *, repo: str, tree: str, command: str) -> ReceiptLookup:
    """The newest satisfying run in any item's receipt, archived items included."""
    warnings: list[str] = []
    found: list[GateMatch] = []
    for path in sorted(bundle_root.glob(RECEIPT_GLOB)):
        rel = path.relative_to(bundle_root).as_posix()
        try:
            owner, runs = parse_gate_receipt(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            warnings.append(f"{rel}: malformed gate receipt ignored ({exc})")
            continue
        found.extend(GateMatch(owner, run) for run in runs if satisfies(run, repo=repo, tree=tree, command=command))
    found.sort(key=lambda match: (match.run.started, match.run.run_id), reverse=True)
    return ReceiptLookup(found[0] if found else None, tuple(warnings))


def any_for_tree(bundle_root: Path, *, repo: str, tree: str) -> bool:
    """Whether any item's receipt holds a run (of any outcome) for this repo and tree."""
    for path in sorted(bundle_root.glob(RECEIPT_GLOB)):
        try:
            _, runs = parse_gate_receipt(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if any(run.repo == repo and run.tree == tree for run in runs):
            return True
    return False


TERMINAL = frozenset({"resolved", "wontfix", "superseded"})


@dataclass(frozen=True, slots=True)
class GateRecord:
    refusal: str | None
    receipt_path: str | None
    changed: bool
    detail: str = ""


def record_gate_run(layout: WorkspaceLayout, owner: str, run: GateRun, *, today: date) -> GateRecord:
    """Append *run* to *owner*'s receipt as one committed gw write.

    Any non-terminal phase may record: a receipt is a fact about a tree, not a
    phase. Idempotent on `run_id`.
    """
    if not any(i.path == owner for i in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))):
        return GateRecord("unknown-item", None, False, f"{owner}: no such work item")
    commit_mode(layout)
    with locked_decision_owner(layout, owner) as context:
        item = next(i for i in context.items if i.path == owner)
        if item.work_status in TERMINAL:
            return GateRecord("owner-terminal", None, False, f"{owner} is {item.work_status}; receipts are closed")
        ref = artifact_ref(owner, MANAGED_ARTIFACTS["gate-receipts"])
        target = ref.path(context.bundle.root)
        try:
            before: bytes | None = target.read_bytes()
        except FileNotFoundError:
            before = None
        if before is None:
            receipt = parse(render_receipt(owner, [], created=today.isoformat()))
        else:
            try:
                recorded_owner, runs = parse_gate_receipt(before.decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                return GateRecord("malformed-receipt", ref.rel, False, f"{ref.rel}: {exc}; repair it by hand")
            if recorded_owner != owner:
                return GateRecord("malformed-receipt", ref.rel, False, f"{ref.rel}: owner is {recorded_owner}")
            if any(existing.run_id == run.run_id for existing in runs):
                return GateRecord(None, ref.rel, False)
            receipt = parse(before.decode("utf-8"))
        raw_runs = receipt.fm_data()["runs"]
        raw_runs.append(run_data(run))
        receipt.set("runs", raw_runs)
        page_before = context.bundle.concepts[owner].serialize().encode("utf-8")
        page = parse(page_before.decode("utf-8"))
        upsert(page, ref, title="Gate receipts")
        if "[^gate-receipts]" not in page.body:
            newline = "\r\n" if "\r\n" in page_before.decode("utf-8") else "\n"
            page.set_body(page.body + f"{newline}[^gate-receipts]: [Gate receipts]({ref.resource}){newline}")
        writes = tuple(
            PlannedWrite(member, hashlib.sha256(old).hexdigest() if old is not None else None, new)
            for member, old, new in (
                (item_page(owner).rel, page_before, page.serialize().encode("utf-8")),
                (ref.rel, before, receipt.serialize().encode("utf-8")),
            )
            if old != new
        )
        parent = target.parent.relative_to(context.bundle.root).as_posix()
        mutation = WorkMutationPlan(
            root=context.bundle.root,
            operation="file",
            path_mapping=MappingProxyType({}),
            move_plan=None,
            moves=(),
            writes=writes,
            deletes=(),
            mkdirs=(parent,),
            warnings=(),
            refusals=(),
            validate_paths=(owner,),
            directory_preconditions=()
            if (context.bundle.root / parent).exists()
            else (DirectoryPrecondition(parent, None),),
        )
        application = apply_mutation(
            layout,
            mutation,
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
            commit=WorkspaceCommit(f"workspace: record {item_stem(owner)} gate receipt {run.run_id}", items=(owner,)),
        )
        if not application.ok:
            return GateRecord("transaction-refused", ref.rel, False, str(application))
        return GateRecord(None, ref.rel, True)


__all__ = [
    "RECEIPT_GLOB",
    "TERMINAL",
    "GateMatch",
    "GateRecord",
    "GateRun",
    "GateScope",
    "ReceiptLookup",
    "any_for_tree",
    "find_satisfying",
    "parse_gate_receipt",
    "record_gate_run",
    "render_receipt",
    "run_data",
    "sanitize_tail",
    "satisfies",
]
