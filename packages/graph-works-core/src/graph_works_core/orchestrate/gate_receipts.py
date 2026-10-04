"""Gate receipts: what `gw work gate` ran, keyed on the tree it ran against.

A managed Explanation page per owner (`references/03-gate-receipts.md`), shaped
like the finish receipt: `receipt_version: 2`, `owner`, and an append-only
`runs:` list. Parsing is strict per file and never raises out of the lookup: a
malformed file is a warning naming its path and contributes nothing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Literal, cast

from okf_io import parse
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref, item_page
from work_tracker_okf.sources import upsert

from graph_works_core.orchestrate.gate_index import read_index, write_through
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import WorkspaceCommit, commit_mode, item_stem
from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repos
from graph_works_core.workspace.transactions import apply_mutation

GateScope = Literal["full", "scoped"]
RECEIPT_VERSION = 2
_HASH = re.compile(r"[0-9a-f]{64}")
RECEIPT_GLOB = "work/**/references/03-gate-receipts.md"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class Reuse:
    owner: str
    run_id: str


@dataclass(frozen=True, slots=True)
class UnitEntry:
    name: str
    hash: str
    ran: bool
    exit: int | None = None
    log_path: str | None = None
    duration_s: float | None = None
    reused_from: Reuse | None = None


@dataclass(frozen=True, slots=True)
class RepoWideEntry:
    tree: str
    command: str
    ran: bool
    exit: int | None = None
    duration_s: float | None = None
    reused_from: Reuse | None = None


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
    manifest_hash: str | None = None
    repo_wide: RepoWideEntry | None = None
    units: tuple[UnitEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class UnitEvidence:
    name: str
    hash: str
    owner: str
    run_id: str


@dataclass(frozen=True, slots=True)
class GateEvidence:
    owner: str
    run_id: str
    units: tuple[UnitEvidence, ...]


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    satisfied: bool
    evidence: GateEvidence | None
    stale: tuple[str, ...]
    repo_wide_green: bool
    green: Mapping[str, UnitEvidence]
    repo_wide: Reuse | None
    warnings: tuple[str, ...]


def sanitize_tail(text: str, *, lines: int = 40) -> str:
    """The last *lines* lines, with ANSI escapes, CRs and control bytes removed."""
    cleaned = _CONTROL.sub("", _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))
    return "\n".join(cleaned.splitlines()[-lines:])


_STR = ("run_id", "repo", "worktree", "command", "log_path", "log_tail", "started")


def _required_string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} is not a nonblank string")
    return value


def _duration(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where} duration_s is not a number")
    try:
        duration = float(value)
    except OverflowError as exc:
        raise ValueError(f"{where} duration_s is not finite") from exc
    if not math.isfinite(duration) or duration < 0:
        raise ValueError(f"{where} duration_s is not finite and nonnegative")
    return duration


def _reuse(value: object, where: str) -> Reuse | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{where} reused_from is not {{owner, run_id}}")
    return Reuse(
        _required_string(value.get("owner"), f"{where} reused_from owner"),
        _required_string(value.get("run_id"), f"{where} reused_from run_id"),
    )


def _evidence_fields(value: dict[str, object], where: str) -> tuple[bool, int | None, float | None, Reuse | None]:
    ran = value.get("ran")
    if type(ran) is not bool:
        raise ValueError(f"{where} ran is not a boolean")
    code = value.get("exit")
    if (code is not None or ran) and type(code) is not int:
        raise ValueError(f"{where} exit is not an integer")
    duration = value.get("duration_s")
    seconds = None if duration is None else _duration(duration, where)
    reuse = _reuse(value.get("reused_from"), where)
    if not ran and reuse is None:
        raise ValueError(f"{where} did not run and names no reused_from")
    return ran, code, seconds, reuse


def _unit_entry(value: object) -> UnitEntry:
    if not isinstance(value, dict):
        raise ValueError("unit entry is not a mapping")
    name = _required_string(value.get("name"), "unit name")
    digest = value.get("hash")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ValueError(f"unit {name} hash is not a sha256")
    log_path = value.get("log_path")
    if log_path is not None and not isinstance(log_path, str):
        raise ValueError(f"unit {name} log_path is not a string")
    ran, code, duration, reuse = _evidence_fields(value, f"unit {name}")
    return UnitEntry(name, digest, ran, code, log_path, duration, reuse)


def _repo_wide(value: object) -> RepoWideEntry | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("repo_wide is not a mapping")
    command = _required_string(value.get("command"), "repo_wide command")
    tree = value.get("tree")
    if not isinstance(tree, str) or not _SHA.fullmatch(tree):
        raise ValueError("repo_wide tree is not a tree SHA")
    ran, code, duration, reuse = _evidence_fields(value, "repo_wide")
    return RepoWideEntry(tree, command, ran, code, duration, reuse)


def run_from_data(value: object) -> GateRun:
    if not isinstance(value, dict):
        raise ValueError("run is not a mapping")
    for key in _STR:
        if not isinstance(value.get(key), str):
            raise ValueError(f"run {key} is not a string")
        if key != "log_tail":
            _required_string(value[key], f"run {key}")
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
    seconds = _duration(duration, "run")
    names = value.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("run names is not a list of strings")
    manifest_hash = value.get("manifest_hash")
    if manifest_hash is not None and (not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash)):
        raise ValueError("run manifest_hash is not a sha256")
    units = value.get("units", [])
    if not isinstance(units, list):
        raise ValueError("run units is not a list")
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
        duration_s=seconds,
        manifest_hash=manifest_hash,
        repo_wide=_repo_wide(value.get("repo_wide")),
        units=tuple(_unit_entry(unit) for unit in units),
    )


def parse_gate_receipt(text: str) -> tuple[str, tuple[GateRun, ...]]:
    doc = parse(text)
    data = doc.fm_data()
    runs = data.get("runs")
    if (
        doc.parse_error
        or data.get("type") != "Explanation"
        or type(data.get("receipt_version")) is not int
        or data.get("receipt_version") not in (1, RECEIPT_VERSION)
        or not isinstance(data.get("owner"), str)
        or not isinstance(runs, list)
    ):
        raise ValueError("malformed gate receipt")
    return _required_string(data["owner"], "owner"), tuple(run_from_data(run) for run in runs)


def _entry_data(entry: UnitEntry | RepoWideEntry) -> dict[str, object]:
    return {key: value for key, value in asdict(entry).items() if value is not None}


def run_data(run: GateRun) -> dict[str, object]:
    data = asdict(run)
    data["names"] = list(run.names)
    for key in ("manifest_hash", "repo_wide", "units"):
        del data[key]
    if run.manifest_hash is not None:
        data["manifest_hash"] = run.manifest_hash
    if run.repo_wide is not None:
        data["repo_wide"] = _entry_data(run.repo_wide)
    if run.units:
        data["units"] = [_entry_data(unit) for unit in run.units]
    return data


def render_receipt(owner: str, runs: Sequence[GateRun], *, created: str) -> str:
    """A fresh receipt page; recording appends to the raw runs sequence."""
    page = parse(
        "---\ntype: Explanation\ntitle: Gate receipts\n"
        "description: Gate runs gw executed for this item.\nstatus: draft\n"
        f"created: {created}\nreceipt_version: {RECEIPT_VERSION}\nowner: {json.dumps(owner)}\nruns: []\n---\n\n"
        "Gate runs recorded by `gw work gate`.\n"
    )
    page.set("runs", [run_data(run) for run in runs])
    return page.serialize()


def _v1_satisfies(run: GateRun, *, repo: str, tree: str, command: str) -> bool:
    return (
        run.manifest_hash is None
        and not run.units
        and run.repo_wide is None
        and run.repo == repo
        and run.tree == tree
        and run.clean
        and not run.tree_changed
        and run.exit == 0
        and run.scope == "full"
        and run.command == command
    )


def _key(run: GateRun) -> tuple[str, str]:
    return (run.started, run.run_id)


def _plain(text: str) -> tuple[str, list[dict[str, Any]]]:
    owner, runs = parse_gate_receipt(text)
    return owner, [run_data(run) for run in runs]


def receipts(layout: WorkspaceLayout) -> tuple[list[tuple[str, tuple[GateRun, ...]]], tuple[str, ...]]:
    """Every well-formed receipt as `(owner, runs)`, plus warnings; reads through the receipt cache."""
    read = read_index(layout.bundle_dir, layout.cache_dir, glob=RECEIPT_GLOB, parse=_plain)
    warnings = list(read.warnings)
    found: list[tuple[str, tuple[GateRun, ...]]] = []
    for receipt in read.receipts:
        try:
            if receipt.error is not None or receipt.owner is None:
                raise ValueError(receipt.error or "malformed gate receipt")
            found.append((receipt.owner, tuple(run_from_data(run) for run in receipt.runs)))
        except ValueError as exc:
            warnings.append(f"{receipt.rel}: malformed gate receipt ignored ({exc})")
    return found, tuple(warnings)


def evaluate(
    layout: WorkspaceLayout,
    *,
    repo: str,
    tree: str,
    full_command: str,
    repo_wide_command: str | None,
    hashes: Mapping[str, str],
) -> GateEvaluation:
    """Per-unit and repo-wide evidence for *tree*, across every item's receipt (archived included).

    Evidence is only a `ran: true`, exit-0 entry of a clean, tree-unchanged run (D-004). A v1
    run that satisfies the pre-v2 rule satisfies every unit on its tree.
    """
    if not hashes:
        raise ValueError("gate evaluation requires at least one unit hash")
    found_receipts, receipt_warnings = receipts(layout)
    warnings = list(receipt_warnings)
    wanted = {(name, h) for name, h in hashes.items()}
    best_unit: dict[str, tuple[tuple[str, str], UnitEvidence]] = {}
    best_wide: tuple[tuple[str, str], Reuse] | None = None
    best_v1: tuple[tuple[str, str], Reuse] | None = None
    for owner, runs in found_receipts:
        for run in runs:
            if run.repo != repo or not run.clean or run.tree_changed:
                continue
            rank = _key(run)
            if _v1_satisfies(run, repo=repo, tree=tree, command=full_command):
                if best_v1 is None or rank > best_v1[0]:
                    best_v1 = (rank, Reuse(owner, run.run_id))
                continue
            for entry in run.units:
                if entry.ran and entry.exit == 0 and (entry.name, entry.hash) in wanted:
                    current = best_unit.get(entry.name)
                    if current is None or rank > current[0]:
                        best_unit[entry.name] = (rank, UnitEvidence(entry.name, entry.hash, owner, run.run_id))
            wide = run.repo_wide
            if (
                wide is not None
                and wide.ran
                and wide.exit == 0
                and run.tree == tree
                and wide.tree == tree
                and wide.command == repo_wide_command
                and (best_wide is None or rank > best_wide[0])
            ):
                best_wide = (rank, Reuse(owner, run.run_id))
    names = sorted(hashes)
    if best_v1 is not None:
        reuse = best_v1[1]
        units = tuple(UnitEvidence(n, hashes[n], reuse.owner, reuse.run_id) for n in names)
        green = {u.name: u for u in units}
        v1_evidence = GateEvidence(reuse.owner, reuse.run_id, units)
        return GateEvaluation(True, v1_evidence, (), True, green, reuse, tuple(warnings))
    green = {name: found[1] for name, found in best_unit.items()}
    stale = tuple(n for n in names if n not in green)
    wide_green = repo_wide_command is None or best_wide is not None
    satisfied = wide_green and not stale
    evidence: GateEvidence | None = None
    if satisfied:
        units = tuple(green[n] for n in names)
        if best_wide is not None:
            anchor = best_wide[1]
        else:
            newest = max(best_unit.values(), key=lambda item: item[0])[1]
            anchor = Reuse(newest.owner, newest.run_id)
        evidence = GateEvidence(anchor.owner, anchor.run_id, units)
    return GateEvaluation(
        satisfied,
        evidence,
        stale,
        best_wide is not None or repo_wide_command is None,
        green,
        None if best_wide is None else best_wide[1],
        tuple(warnings),
    )


def any_for_tree(layout: WorkspaceLayout, *, repo: str, tree: str) -> bool:
    """Whether any item's receipt holds a run (of any outcome) for this repo and tree."""
    return any(run.repo == repo and run.tree == tree for _, runs in receipts(layout)[0] for run in runs)


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
    if not any(i.path == owner for i in load_items(load_workspace_bundle(layout, ignore=IGNORE))):
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
            if receipt.fm_data().get("receipt_version") != RECEIPT_VERSION:
                receipt.set("receipt_version", RECEIPT_VERSION)
        receipt.fm_raw["runs"].append(run_data(run))
        receipt.mark_dirty()
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
        write_through(context.bundle.root, layout.cache_dir, ref.rel, parse=_plain)  # best effort
        return GateRecord(None, ref.rel, True)


__all__ = [
    "RECEIPT_GLOB",
    "RECEIPT_VERSION",
    "TERMINAL",
    "GateEvaluation",
    "GateEvidence",
    "GateRecord",
    "GateRun",
    "GateScope",
    "RepoWideEntry",
    "Reuse",
    "UnitEntry",
    "UnitEvidence",
    "any_for_tree",
    "evaluate",
    "parse_gate_receipt",
    "receipts",
    "record_gate_run",
    "render_receipt",
    "run_data",
    "run_from_data",
    "sanitize_tail",
]
