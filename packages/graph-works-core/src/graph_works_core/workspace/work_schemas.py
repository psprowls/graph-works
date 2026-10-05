"""Installed work-lane schema copies, compared with the packaged seeds.

The workspace's `<declarations>/schema/*.schema.json` are copies of
`work_tracker_okf`'s package data taken at init. A release that adds a
frontmatter key leaves them stale, and the postcondition gate then rejects the
writer's own field. This module is the one place that inspects that drift
(lint, advance preflight) and repairs it (`gw config sync --schemas`).

Provenance -- the sha256 of the packaged bytes last installed -- is what lets
a refresh tell an old release's copy from a local edit. Without it, a
differing file is never assumed safe to replace.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from okf_ext.bundle import SCHEMA_DIRNAME
from okf_io.validate import Finding
from work_tracker_okf import __version__ as _WORK_TRACKER_VERSION
from work_tracker_okf.resources import SEED_RELATIVE_PATHS, seed_files

from graph_works_core.workspace.commits import CommitOutcome, WorkspaceCommit, commit_mode, commit_workspace
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

PROVENANCE_RELATIVE = f"{SCHEMA_DIRNAME}/work-tracker-okf.provenance.json"
_PROVENANCE_FORMAT = 1
_PACKAGE = "work-tracker-okf"
SCHEMA_DRIFT = "workspace.schema-drift"
SCHEMA_MISSING = "workspace.schema-missing"

SchemaState = Literal["current", "missing", "recognized", "edited", "unrecorded", "unsafe"]


def declarations_dir_for(layout: WorkspaceLayout) -> Path:
    """The declarations directory the work-mutation gate validates against.

    A configured workspace keeps its declaration identity even when its
    schemas are missing. Only a pre-manifest layout with an owned work-schema
    target in the bundle can use the legacy destination; custom files alone
    are not evidence of a legacy work installation.
    """
    if (
        not os.path.lexists(layout.manifest_path)
        and not os.path.lexists(layout.config_dir / SCHEMA_DIRNAME)
        and (layout.bundle_dir / SCHEMA_DIRNAME).is_dir()
        and any(
            os.path.lexists(layout.bundle_dir / relative)
            for relative in SEED_RELATIVE_PATHS
            if relative.startswith(f"{SCHEMA_DIRNAME}/")
        )
    ):
        return layout.bundle_dir
    return layout.config_dir


def packaged_schemas() -> Mapping[str, bytes]:
    """The eight packaged work schemas, keyed `schema/<Name>.schema.json`.

    Encoded from `seed_files()` so the bytes are exactly what the installer writes.
    """
    seeds = seed_files()
    return MappingProxyType(
        {rel: seeds[rel].encode("utf-8") for rel in SEED_RELATIVE_PATHS if rel.startswith(f"{SCHEMA_DIRNAME}/")}
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_provenance(digests: Mapping[str, str]) -> bytes:
    body = {
        "format": _PROVENANCE_FORMAT,
        "package": _PACKAGE,
        "package_version": _WORK_TRACKER_VERSION,
        "files": dict(sorted(digests.items())),
    }
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_provenance(path: Path) -> tuple[bytes | None, Mapping[str, str] | None, str | None]:
    """(raw bytes, trusted digests, error). Digests are `None` unless the file parses cleanly."""
    try:
        # Opening a FIFO can block before the refresh capture gets a chance to
        # reject it. Check ancestors and the target before any content read.
        for parent in reversed(path.parents):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                return None, None, f"{path}: unsafe parent {parent}: not a regular directory"
        if not stat.S_ISREG(path.lstat().st_mode):
            return None, None, f"{path}: not a regular file"
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None, None
    except OSError as exc:
        return None, None, f"{path}: unreadable: {exc}"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return raw, None, f"{path}: not JSON: {exc}"
    files = data.get("files") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or type(data.get("format")) is not int
        or data.get("format") != _PROVENANCE_FORMAT
        or data.get("package") != _PACKAGE
        or not isinstance(files, dict)
        or not all(
            isinstance(k, str) and isinstance(v, str) and len(v) == 64 and all(char in "0123456789abcdef" for char in v)
            for k, v in files.items()
        )
    ):
        return raw, None, f"{path}: not a format-{_PROVENANCE_FORMAT} {_PACKAGE} provenance record"
    return raw, MappingProxyType(dict(files)), None


@dataclass(frozen=True, slots=True)
class SchemaFileState:
    relative: str
    path: Path
    state: SchemaState
    installed: bytes | None
    packaged: bytes
    detail: str


@dataclass(frozen=True, slots=True)
class SchemaInspection:
    declarations_dir: Path
    files: tuple[SchemaFileState, ...]
    recorded: Mapping[str, str] | None
    provenance_bytes: bytes | None
    provenance_error: str | None

    @property
    def drifted(self) -> tuple[SchemaFileState, ...]:
        return tuple(state for state in self.files if state.state != "current")


def _state(path: Path, relative: str, packaged: bytes, recorded: Mapping[str, str] | None) -> SchemaFileState:
    def make(state: SchemaState, installed: bytes | None, detail: str) -> SchemaFileState:
        return SchemaFileState(relative, path, state, installed, packaged, detail)

    try:
        for parent in path.parents:
            try:
                mode = parent.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(mode):
                return make("unsafe", None, f"unsafe parent {parent}: not a regular directory")
        if not stat.S_ISREG(path.lstat().st_mode):
            return make("unsafe", None, "not a regular file")
        installed = path.read_bytes()
    except FileNotFoundError:
        return make("missing", None, "missing")
    except OSError as exc:
        return make("unsafe", None, f"unreadable: {exc}")
    if installed == packaged:
        return make("current", installed, "matches the packaged schema")
    if recorded is not None and recorded.get(relative) == _digest(installed):
        return make("recognized", installed, "an earlier packaged version")
    if recorded is not None:
        return make("edited", installed, "differs from the packaged and the recorded version (local edit?)")
    return make("unrecorded", installed, "differs from the packaged schema; no provenance records its origin")


def inspect_work_schemas(layout: WorkspaceLayout, *, declarations_dir: Path | None = None) -> SchemaInspection:
    """Compare each installed work schema with its packaged bytes. Never writes."""
    directory = declarations_dir_for(layout) if declarations_dir is None else declarations_dir
    raw, recorded, error = _read_provenance(directory / PROVENANCE_RELATIVE)
    files = tuple(
        _state(directory / relative, relative, packaged, recorded) for relative, packaged in packaged_schemas().items()
    )
    return SchemaInspection(directory, files, recorded, raw, error)


def refresh_command(layout: WorkspaceLayout) -> str:
    argv = ["gw", "config", "sync", "--schemas", "--workspace", str(layout.root)]
    return subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)


def _display(layout: WorkspaceLayout, path: Path) -> str:
    try:
        return path.relative_to(layout.root).as_posix()
    except ValueError:
        return path.as_posix()


def drift_findings(layout: WorkspaceLayout, inspection: SchemaInspection | None = None) -> tuple[Finding, ...]:
    found = inspection if inspection is not None else inspect_work_schemas(layout)
    command = refresh_command(layout)
    return tuple(
        Finding(
            code=SCHEMA_MISSING if state.state == "missing" else SCHEMA_DRIFT,
            severity="error",
            message=f"work schema {state.state}: {state.detail}; preview recovery with: {command}",
            spec="§5",
            path=_display(layout, state.path),
        )
        for state in found.drifted
    )


def drift_detail(layout: WorkspaceLayout, inspection: SchemaInspection | None = None) -> str | None:
    found = inspection if inspection is not None else inspect_work_schemas(layout)
    if not found.drifted:
        return None
    named = "; ".join(f"{_display(layout, state.path)} ({state.state}: {state.detail})" for state in found.drifted)
    return (
        f"installed work schemas differ from the packaged ones: {named} — preview recovery with: "
        f"{refresh_command(layout)}"
    )


RefusalKind = Literal["edited", "unrecorded", "unsafe"]
_REFRESH_SUBJECT = "workspace: refresh work-lane schemas"
# Content alone cannot detect a parent or same-bytes file replacement.
PathIdentity = tuple[int, int, int] | None


class SchemaRefreshStale(WorkspaceError):
    """A refresh input changed; preview a fresh plan before applying."""


@dataclass(frozen=True, slots=True)
class SchemaWrite:
    relative: str
    path: Path
    before: bytes | None
    after: bytes
    diff: str


@dataclass(frozen=True, slots=True)
class SchemaRefusal:
    relative: str
    path: Path
    reason: RefusalKind
    detail: str
    diff: str


@dataclass(frozen=True, slots=True)
class SchemaRefreshPlan:
    layout: WorkspaceLayout
    declarations_dir: Path
    writes: tuple[SchemaWrite, ...]
    refusals: tuple[SchemaRefusal, ...]
    skipped: tuple[str, ...]
    provenance: SchemaWrite | None
    fingerprints: Mapping[Path, bytes | None]
    force: bool
    identities: Mapping[Path, PathIdentity]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def changed(self) -> bool:
        return bool(self.writes) or self.provenance is not None


@dataclass(frozen=True, slots=True)
class SchemaRefreshResult:
    plan: SchemaRefreshPlan
    written: tuple[str, ...]
    commit: CommitOutcome | None


def _diff(relative: str, before: bytes | None, after: bytes) -> str:
    old = [] if before is None else before.decode("utf-8", errors="replace").splitlines(keepends=True)
    new = after.decode("utf-8", errors="replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, f"a/{relative}", f"b/{relative}"))


def _identity(path: Path) -> PathIdentity:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino, info.st_mode


def _input_paths(directory: Path) -> tuple[Path, ...]:
    return tuple(directory / rel for rel in (*packaged_schemas(), PROVENANCE_RELATIVE))


def _parent_paths(layout: WorkspaceLayout, directory: Path) -> set[Path]:
    # Include the directory-selection input, even when legacy declarations live
    # in the bundle, so introducing config/schema invalidates that preview.
    selectors = (layout.config_dir / SCHEMA_DIRNAME, directory / SCHEMA_DIRNAME, layout.root)
    return {ancestor for path in selectors for ancestor in (path, *path.parents)}


def _capture(
    layout: WorkspaceLayout, directory: Path
) -> tuple[dict[Path, bytes | None], dict[Path, PathIdentity], dict[Path, str]]:
    fingerprints: dict[Path, bytes | None] = {}
    identities: dict[Path, PathIdentity] = {}
    errors: dict[Path, str] = {}
    parents = _parent_paths(layout, directory)
    paths = _input_paths(directory)
    for path in sorted(parents | set(paths)):
        try:
            identity = _identity(path)
            identities[path] = identity
            if identity is not None:
                regular = stat.S_ISDIR(identity[2]) if path in parents else stat.S_ISREG(identity[2])
                if not regular:
                    errors[path] = "not a regular directory" if path in parents else "not a regular file"
        except OSError as exc:
            identities[path] = None
            errors[path] = f"unreadable: {exc}"
    for path in paths:
        fingerprints[path] = None
        unsafe_parent = next((parent for parent in path.parents if parent in errors), None)
        if unsafe_parent is not None:
            errors[path] = f"unsafe parent {unsafe_parent}: {errors[unsafe_parent]}"
        elif path not in errors:
            try:
                fingerprints[path] = path.read_bytes()
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors[path] = f"unreadable: {exc}"
    return fingerprints, identities, errors


def plan_schema_refresh(layout: WorkspaceLayout, *, force: bool = False) -> SchemaRefreshPlan:
    """Preview the owned schemas and provenance without writing anything."""
    inspection = inspect_work_schemas(layout)
    directory = inspection.declarations_dir
    fingerprints, identities, errors = _capture(layout, directory)
    writes: list[SchemaWrite] = []
    refusals: list[SchemaRefusal] = []
    skipped: list[str] = []
    for state in inspection.files:
        diff = _diff(state.relative, state.installed, state.packaged)
        if state.path in errors or state.state == "unsafe":
            refusals.append(
                SchemaRefusal(state.relative, state.path, "unsafe", errors.get(state.path, state.detail), diff)
            )
        elif state.state == "current":
            skipped.append(state.relative)
        elif state.state in ("edited", "unrecorded") and not force:
            refusals.append(SchemaRefusal(state.relative, state.path, state.state, state.detail, diff))
        else:
            writes.append(SchemaWrite(state.relative, state.path, state.installed, state.packaged, diff))
    provenance_path = directory / PROVENANCE_RELATIVE
    target = render_provenance({rel: _digest(data) for rel, data in packaged_schemas().items()})
    provenance = None
    if provenance_path in errors:
        refusals.append(SchemaRefusal(PROVENANCE_RELATIVE, provenance_path, "unsafe", errors[provenance_path], ""))
    elif inspection.provenance_bytes != target:
        provenance = SchemaWrite(
            PROVENANCE_RELATIVE,
            provenance_path,
            inspection.provenance_bytes,
            target,
            _diff(PROVENANCE_RELATIVE, inspection.provenance_bytes, target),
        )
    # An unsafe selector must block even if declarations fall back to the bundle.
    for path, detail in errors.items():
        if path not in fingerprints and not any(r.path == path or path in r.path.parents for r in refusals):
            refusals.append(SchemaRefusal(_display(layout, path), path, "unsafe", detail, ""))
    return SchemaRefreshPlan(
        layout,
        directory,
        tuple(writes),
        tuple(refusals),
        tuple(skipped),
        provenance,
        MappingProxyType(fingerprints),
        force,
        MappingProxyType(identities),
    )


def _validate_plan_fields(plan: SchemaRefreshPlan) -> None:
    if (
        not isinstance(plan.layout, WorkspaceLayout)
        or not isinstance(plan.declarations_dir, Path)
        or type(plan.force) is not bool
        or not isinstance(plan.fingerprints, Mapping)
        or not isinstance(plan.identities, Mapping)
        or not isinstance(plan.writes, tuple)
        or not all(isinstance(write, SchemaWrite) for write in plan.writes)
        or not isinstance(plan.refusals, tuple)
        or not all(isinstance(refusal, SchemaRefusal) for refusal in plan.refusals)
        or not isinstance(plan.skipped, tuple)
        or not all(isinstance(relative, str) for relative in plan.skipped)
        or (plan.provenance is not None and not isinstance(plan.provenance, SchemaWrite))
    ):
        raise WorkspaceError("schema refresh refused: malformed plan fields; re-run the preview")


def _check_refresh(plan: SchemaRefreshPlan) -> None:
    """Validate plan ownership and fresh inputs before the first write."""
    directory = plan.declarations_dir
    if directory not in (plan.layout.config_dir, plan.layout.bundle_dir) or type(plan.force) is not bool:
        raise WorkspaceError("schema refresh refused: malformed plan destination or force flag")
    paths = set(_input_paths(directory))
    if set(plan.fingerprints) != paths or set(plan.identities) != paths | _parent_paths(plan.layout, directory):
        raise WorkspaceError("schema refresh refused: incomplete or foreign plan inputs")
    if declarations_dir_for(plan.layout) != directory:
        raise SchemaRefreshStale("schema declaration destination changed; re-run the preview")
    fingerprints, identities, errors = _capture(plan.layout, directory)
    if errors or fingerprints != plan.fingerprints or identities != plan.identities:
        raise SchemaRefreshStale("schema input content, identity or accessibility changed; re-run the preview")
    fresh = plan_schema_refresh(plan.layout, force=plan.force)
    if fresh.fingerprints != plan.fingerprints or fresh.identities != plan.identities:
        raise SchemaRefreshStale("schema input changed during refresh validation; re-run the preview")
    if fresh != plan:
        raise WorkspaceError("schema refresh refused: malformed plan writes or packaged metadata; re-run the preview")


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    # Exclusive creation avoids following an existing temp symlink or clobbering
    # an unrelated temporary file. Clean even a partial write before replace.
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(data)
        os.replace(temporary, path)  # noqa: PTH105 -- explicit atomic-replace seam for rollback tests
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def _restore(done: list[SchemaWrite], failure: OSError) -> None:
    for write in reversed(done):
        try:
            if write.before is None:
                write.path.unlink(missing_ok=True)
            else:
                _replace(write.path, write.before)
        except OSError as exc:
            failure.add_note(f"schema refresh rollback failed for {write.path}: {exc}")


def apply_schema_refresh(plan: SchemaRefreshPlan) -> SchemaRefreshResult:
    """Apply a validated preview under the mutation lock; provenance goes last."""
    from graph_works_core.workspace.transactions import held_bundle_lock

    _validate_plan_fields(plan)
    if plan.refusals:
        named = ", ".join(f"{r.relative} ({r.reason})" for r in plan.refusals)
        raise WorkspaceError(f"schema refresh refused: {named}")
    effects = [*plan.writes, *(() if plan.provenance is None else (plan.provenance,))]
    with held_bundle_lock(plan.layout):
        _check_refresh(plan)
        root = plan.layout.root
        owned = all(write.path.is_relative_to(root) for write in effects)
        # Invalid commit configuration must fail before any schema is written.
        mode = commit_mode(plan.layout) if effects and owned else None
        done: list[SchemaWrite] = []
        try:
            for write in effects:
                _replace(write.path, write.after)
                done.append(write)
        except OSError as exc:
            _restore(done, exc)
            raise
        labels = tuple(_display(plan.layout, write.path) for write in effects)
        commit = (
            commit_workspace(plan.layout, WorkspaceCommit(_REFRESH_SUBJECT, root_paths=labels), (), mode=mode)
            if effects and owned
            else None
        )
    return SchemaRefreshResult(plan, labels, commit)


def seed_provenance(layout: WorkspaceLayout) -> str | None:
    """Seed absent/invalid provenance only for a safe, entirely current install."""
    from graph_works_core.workspace.transactions import held_bundle_lock

    with held_bundle_lock(layout):
        inspection = inspect_work_schemas(layout)
        if inspection.drifted or inspection.recorded is not None:
            return None
        plan = plan_schema_refresh(layout)
        if plan.refusals or plan.writes or plan.provenance is None:
            return None
        _check_refresh(plan)
        _replace(plan.provenance.path, plan.provenance.after)
        return _display(layout, plan.provenance.path)
