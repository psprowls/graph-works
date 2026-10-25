"""Prepare returned coverage durably, then guard canonical phase publication.

An unknown commit outcome or changed evidence stays fenced for inspection.
A crash after canonical commit is recoverable only by proving the exact recorded
page, plan and committed coverage; a phase alone never proves publication.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType

from gw_kernel.workspace import provenance
from gw_kernel.workspace.bundle import load_workspace_bundle
from gw_kernel.workspace.commits import (
    WorkspaceCommit,
    commit_candidates,
    commit_target,
    plan_paths,
)
from gw_kernel.workspace.errors import WorkspaceError
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_pipeline.execute_return import (
    LocalPublication,
    write_operation,
)
from gw_pipeline.execute_return import (
    ReturnOperation as ReturnOperation,
)
from gw_pipeline.execute_return import (
    digest as digest,
)
from gw_pipeline.execute_return import (
    finish_return as finish_return,
)
from gw_pipeline.execute_return import (
    pending_return as pending_return,
)
from gw_pipeline.members import confined_member
from gw_pipeline.transactions import MutationApplication, apply_mutation, held_bundle_lock
from work_tracker_okf.advance import RefusalReason
from work_tracker_okf.compose import stage_artifact_ref
from work_tracker_okf.items import IGNORE, WorkItem, is_commit_oid, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.pipeline import PipelineDefinition
from work_tracker_okf.returns import ExecuteReturn, new_record, plan_scope, render_reopened, verify_report


@dataclass(frozen=True, slots=True)
class ReturnPlan:
    record: ExecuteReturn
    coverage_member: str
    coverage_before: bytes | None
    coverage_after: bytes
    plan_sha256: str | None
    path: str
    plan_member: str


def read_member(root: Path, member: str) -> bytes | None:
    try:
        return confined_member(root, member).read_bytes()
    except FileNotFoundError:
        return None


def plan_return(
    layout: WorkspaceLayout,
    item: WorkItem,
    *,
    scope: Sequence[str],
    definition: PipelineDefinition,
    today: date,
    new_id: Callable[[], str],
) -> ReturnPlan | tuple[RefusalReason, str]:
    pending = pending_return(layout, item.path)
    if pending is not None and pending.retired_worktree is not None:
        return (
            "return-pending",
            f"return journal names a retired workspace worktree {pending.retired_worktree!r}; inspect",
        )
    if pending is not None:
        return "return-pending", "malformed return journal; inspect before retrying"
    selected = plan_scope(
        scope, item.finish_obligations, obligations_malformed="finish_obligations" in item.invalid_optional_fields
    )
    if selected.refusal:
        return selected.refusal, selected.detail
    coverage = stage_artifact_ref(item.path, definition.artifacts["execute"])
    plan = stage_artifact_ref(item.path, definition.artifacts["plan"])
    try:
        plan_sha = digest(read_member(layout.bundle_dir, plan.rel))
        before = read_member(layout.bundle_dir, coverage.rel)
        text = before.decode("utf-8") if before is not None else None
    except (OSError, ValueError) as exc:
        return "return-evidence-missing", str(exc)
    record = new_record(
        new_id(),
        selected.scope,
        on=today,
        coverage=coverage.resource,
        plan=plan.resource if plan_sha else None,
        plan_sha256=plan_sha,
    )
    after = render_reopened(text, record).encode("utf-8")
    return ReturnPlan(record, coverage.rel, before, after, plan_sha, item.path, plan.rel)


def coverage_mutation(layout: WorkspaceLayout, plan: ReturnPlan) -> WorkMutationPlan:
    parent = Path(plan.coverage_member).parent.as_posix()
    return WorkMutationPlan(
        root=layout.bundle_dir,
        operation="file",
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=(PlannedWrite(plan.coverage_member, digest(plan.coverage_before), plan.coverage_after),),
        deletes=(),
        mkdirs=(parent,),
        warnings=(),
        refusals=(),
        validate_paths=(),
        directory_preconditions=() if (layout.bundle_dir / parent).exists() else (DirectoryPrecondition(parent, None),),
    )


def _git(root: Path, *args: str) -> str:
    result = provenance.probe_git(root, *args, preserve_output=True)
    if result.returncode != 0:
        raise WorkspaceError("return Git evidence unprovable; inspect and retry")
    return result.stdout


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _committed_member(layout: WorkspaceLayout, member: str, expected: str | None, revision: str = "HEAD") -> bool:
    """Match exact working bytes to Git's path-aware committed representation.

    Git may store LF for CRLF working bytes. Let Git apply its own configured
    conversion rather than normalizing text ourselves; the raw SHA remains the
    freshness authority before and after the read. No object or file is written.
    """
    target = confined_member(layout.bundle_dir, member)
    repository = Path(_git(layout.root, "rev-parse", "--show-toplevel").strip())
    relative = target.relative_to(repository).as_posix()
    if digest(read_member(layout.bundle_dir, member)) != expected:
        return False
    expected_oid = _git(layout.root, "hash-object", f"--path={relative}", "--", str(target)).strip()
    stored_oid = _git(layout.root, "rev-parse", f"{revision}:{relative}").strip()
    return (
        is_commit_oid(expected_oid)
        and stored_oid == expected_oid
        and digest(read_member(layout.bundle_dir, member)) == expected
    )


def local_published(layout: WorkspaceLayout, op: ReturnOperation) -> bool:
    """Positive exact commit proof; failed/unknown transport is not authority."""
    local = op.local
    if local is None:
        return False
    try:
        return (
            pending_return(layout, op.path) == op
            and _git(layout.root, "rev-parse", "HEAD^").strip() == local.head
            and _git(layout.root, "log", "-1", "--format=%s").strip() == local.subject
            and digest(read_member(layout.bundle_dir, op.plan_member)) == op.plan_sha256
            and all(_committed_member(layout, member, sha) for member, sha in local.members)
        )
    except (OSError, ValueError):
        return False


def published(layout: WorkspaceLayout, op: ReturnOperation, item: WorkItem, *, scope: Sequence[str]) -> bool:
    """Prove exact canonical publication after a crash; never infer from phase."""
    if op.malformed or op.record is None or item.execute_return != op.record or item.phase != "execute":
        return False
    selected = plan_scope(
        scope, item.finish_obligations, obligations_malformed="finish_obligations" in item.invalid_optional_fields
    )
    if selected.refusal or selected.scope != tuple(row.text for row in op.record.scope):
        return False
    return op.local is not None and op.local.kind == "same-root-return" and local_published(layout, op)


def recover_publication(layout: WorkspaceLayout, op: ReturnOperation, *, scope: Sequence[str]) -> bool:
    """Prove and clear an exact replay under the bundle lock."""
    if op.malformed:
        return False
    try:
        with held_bundle_lock(layout):
            items = {i.path: i for i in load_items(load_workspace_bundle(layout, ignore=IGNORE))}
            item = items.get(op.path)
            if item is None or pending_return(layout, op.path) != op or not published(layout, op, item, scope=scope):
                return False
            finish_return(layout, op)
            return True
    except (OSError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class PublicationIndex:
    candidates: tuple[str, ...]
    entries: Mapping[str, str]
    working: Mapping[str, bytes | None]
    modes: Mapping[str, str | None]


def _index_entries(repository: Path, candidates: Sequence[str]) -> dict[str, str]:
    entries: dict[str, str] = {}
    raw = _git(repository, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", *candidates)
    for entry in raw.split("\0"):
        if entry:
            _, separator, path = entry.partition("\t")
            if not separator:
                raise WorkspaceError("return index representation unproven; inspect")
            entries[path] = entries.get(path, "") + entry + "\0"
    return entries


def _working_bytes(repository: Path, relative: str) -> bytes | None:
    try:
        return (repository / relative).read_bytes()
    except FileNotFoundError:
        return None


def _stage_mode(repository: Path, relative: str, entry: str, *, filemode: bool) -> str | None:
    target = repository / relative
    if target.is_symlink():
        return None  # Unsupported representations stay fenced if staging changed them.
    try:
        mode = target.stat().st_mode
    except FileNotFoundError:
        return "100644"  # A newly created mutation-owned Markdown file.
    if not stat.S_ISREG(mode):
        return None
    if filemode:
        return "100755" if mode & stat.S_IXUSR else "100644"
    before_mode = entry.split(" ", 1)[0]
    return before_mode if before_mode in {"100644", "100755"} else "100644"


def _capture_index(repository: Path, candidates: tuple[str, ...], files: Sequence[str]) -> PublicationIndex:
    entries = _index_entries(repository, candidates)
    names = set(entries)
    for args in (
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        ("ls-tree", "-r", "--name-only", "-z", "HEAD"),
    ):
        names.update(filter(None, _git(repository, "--literal-pathspecs", *args, "--", *candidates).split("\0")))
    # Explicit new-file pathspecs are absent from both Git listings before apply.
    names.update(files)
    filemode = _git(repository, "config", "--bool", "--default", "true", "core.filemode").strip() == "true"
    return PublicationIndex(
        candidates,
        entries,
        {path: _working_bytes(repository, path) for path in names},
        {path: _stage_mode(repository, path, entries.get(path, ""), filemode=filemode) for path in names},
    )


def _staged_postimage(repository: Path, relative: str, mode: str | None, data: bytes | None) -> str | None:
    if data is None:
        return ""
    if mode is None:
        return None
    oid = _git(repository, "hash-object", f"--path={relative}", "--", str(repository / relative)).strip()
    return f"{mode} {oid} 0\t{relative}\0" if is_commit_oid(oid) else None


def _restore_index_entry(repository: Path, relative: str, entry: str) -> None:
    # Exact path only; -z retains unusual authored filenames and all stages.
    removal = f"0 {'0' * 40}\t{relative}\0"
    result = provenance.probe_git(
        repository, "update-index", "-z", "--index-info", input_text=removal + entry, preserve_output=True
    )
    if result.returncode != 0:
        raise WorkspaceError("return index restoration unproven; inspect")


def apply_local_publication(
    layout: WorkspaceLayout,
    path: str,
    record: ExecuteReturn,
    plan_member: str,
    mutation: WorkMutationPlan,
    commit: WorkspaceCommit,
    apply: Callable[[], MutationApplication],
    *,
    completion: bool,
) -> MutationApplication:
    """Fence canonical-only lifecycle publication without changing transactions.

    Caller holds admission and decision ownership, plus completion's content
    lock when applicable. Capture mutation-owned files and the complete commit
    staging footprint under the canonical lock; retain it through fence clear.
    Unknown outcomes stay inspection-only unless exact publication is proven.
    """
    with held_bundle_lock(layout):
        repository, skip = commit_target(layout)
        if repository is None:
            if skip in {"disabled", "not-a-repo", "not-own-repo"}:
                return apply()
            raise WorkspaceError(f"return commit target unproven: {skip}; inspect")
        before = {write.member: read_member(layout.bundle_dir, write.member) for write in mutation.writes}
        if any(digest(before[write.member]) != write.before_digest for write in mutation.writes):
            raise WorkspaceError("return publication preimages changed; retry")
        relatives = {member: (layout.bundle_dir / member).relative_to(repository).as_posix() for member in before}
        candidates, candidate_error = commit_candidates(layout, commit, plan_paths(mutation), repository)
        if candidate_error is not None:
            raise WorkspaceError(candidate_error)
        index = _capture_index(repository, candidates, tuple(relatives.values()))
        op = ReturnOperation(
            path,
            return_id=record.id,
            record=record,
            plan_member=plan_member,
            plan_sha256=digest(read_member(layout.bundle_dir, plan_member)),
            local=LocalPublication(
                "completion" if completion else "same-root-return",
                _head(layout.root),
                commit.subject,
                tuple((write.member, hashlib.sha256(write.after).hexdigest()) for write in mutation.writes),
            ),
        )
        if pending_return(layout, path) is not None:
            raise WorkspaceError("return journal appeared; inspect")
        write_operation(layout, op)
        application = apply()
        if application.ok and local_published(layout, op):
            finish_return(layout, op)
            return application
        failures = (*application.failures, "canonical return commit unproven; inspect pending return")
        restored = False
        # A transaction/read-guard failure can already have restored all files.
        # Refuse rollback unless every owned file and index entry is still one
        # of the exact captured representations, and HEAD/identity are unchanged.
        try:
            restored = _restore_local(layout, op, mutation, before, index, relatives, repository)
            if restored:
                finish_return(layout, op)
        except (OSError, ValueError):
            failures = (*failures, "canonical restoration incomplete; inspect page and index")
        return replace(application, failures=failures, rolled_back=restored)


def _restore_local(
    layout: WorkspaceLayout,
    op: ReturnOperation,
    mutation: WorkMutationPlan,
    before: Mapping[str, bytes | None],
    index: PublicationIndex,
    relatives: Mapping[str, str],
    repository: Path,
) -> bool:
    local = op.local
    if local is None:
        return False
    post = dict(local.members)
    current_index = _index_entries(repository, index.candidates)
    current_working = {relative: _working_bytes(repository, relative) for relative in index.working}
    current = {member: current_working[relative] for member, relative in relatives.items()}

    def guard() -> None:
        if (
            pending_return(layout, op.path) != op
            or _head(layout.root) != local.head
            or any(_working_bytes(repository, path) != value for path, value in current_working.items())
            or _index_entries(repository, index.candidates) != current_index
        ):
            raise WorkspaceError("return restoration ownership changed; inspect")

    guard()
    if set(current_index) - set(index.working):
        return False
    for member, value in current.items():
        if value != before[member] and digest(value) != post[member]:
            return False
    for relative, value in current_working.items():
        if relative not in relatives.values() and value != index.working[relative]:
            return False
        if current_index.get(relative, "") != index.entries.get(relative, ""):
            expected = _staged_postimage(repository, relative, index.modes[relative], value)
            if current_index.get(relative, "") != expected:
                return False
    # The transaction engine guards each current byte hash again before effects.
    restore = replace(
        mutation,
        writes=tuple(
            PlannedWrite(member, digest(current[member]), value)
            for member, value in before.items()
            if value is not None and value != current[member]
        ),
        deletes=tuple(member for member, value in before.items() if value is None and current[member] is not None),
        mkdirs=(),
        directory_preconditions=(),
    )
    if not apply_mutation(layout, restore, commit=None, validate_read_set=guard).ok:
        return False
    for member, relative in relatives.items():
        current_working[relative] = before[member]
    for relative in index.working:
        guard()
        original = index.entries.get(relative, "")
        if current_index.get(relative, "") != original:
            _restore_index_entry(repository, relative, original)
            if original:
                current_index[relative] = original
            else:
                current_index.pop(relative, None)
    guard()
    return current_index == index.entries


@contextmanager
def completion_locks(layout: WorkspaceLayout, item: WorkItem) -> Iterator[bool]:
    """Hold the bundle lock through returned completion's commit.

    Invoked inside decision ownership, only on live execute advances. Yields
    whether a return is active for the item.
    """
    record = item.execute_return
    if item.phase != "execute" or record is None or not record.active:
        yield False
        return
    with held_bundle_lock(layout):
        yield True


def verify_completion(
    layout: WorkspaceLayout,
    item: WorkItem,
    definition: PipelineDefinition,
) -> tuple[str | None, RefusalReason | None, str]:
    """Read the current return's report from the primary bundle.

    Raw UTF-8 decoding preserves authored line endings; canonical plan hashes
    use exact bytes. Absence/unreadability never falls back to old coverage.
    """
    if "execute_return" in item.invalid_optional_fields:
        return None, "return-metadata-invalid", "repair malformed execute_return before completing execute"
    record = item.execute_return
    if record is None or not record.active:
        return None, None, ""
    coverage = stage_artifact_ref(item.path, definition.artifacts["execute"])
    plan = stage_artifact_ref(item.path, definition.artifacts["plan"])
    if record.coverage != coverage.resource or (record.plan is not None and record.plan != plan.resource):
        return None, "return-metadata-invalid", "execute_return artifacts differ from the configured pipeline"
    try:
        raw = read_member(layout.bundle_dir, coverage.rel)
        text = raw.decode("utf-8") if raw is not None else None
    except (OSError, ValueError) as exc:
        return None, "return-evidence-missing", f"return {record.id} coverage unreadable: {exc}"
    refusal, detail = verify_report(record, text)
    if refusal is not None:
        return None, refusal, detail
    if record.plan is not None:
        try:
            plan_sha = digest(read_member(layout.bundle_dir, plan.rel))
        except (OSError, ValueError):
            plan_sha = None
        if plan_sha != record.plan_sha256:
            return (
                None,
                "return-plan-changed",
                (f"plan changed since return {record.id}; return again with refreshed scope"),
            )
    return text, None, ""
