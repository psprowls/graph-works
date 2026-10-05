"""Prepare returned coverage durably, then guard canonical phase publication.

An unknown commit outcome or changed evidence stays fenced for inspection.
A crash after canonical commit is recoverable only by proving the exact recorded
page, plan and committed coverage; a phase alone never proves publication.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType

from okf_io import Bundle
from work_tracker_okf.advance import RefusalReason
from work_tracker_okf.compose import stage_artifact_ref
from work_tracker_okf.items import IGNORE, WorkItem, is_commit_oid, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import item_page
from work_tracker_okf.pipeline import PipelineDefinition
from work_tracker_okf.returns import ExecuteReturn, new_record, plan_scope, render_reopened, verify_report

from graph_works_core.workspace import provenance
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import (
    COMMIT_FAILED_PREFIX,
    WorkspaceCommit,
    commit_candidates,
    commit_target,
    item_stem,
    plan_paths,
)
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.execute_return import (
    LocalPublication,
    write_operation,
)
from graph_works_core.workspace.execute_return import (
    ReturnOperation as ReturnOperation,
)
from graph_works_core.workspace.execute_return import (
    digest as digest,
)
from graph_works_core.workspace.execute_return import (
    finish_return as finish_return,
)
from graph_works_core.workspace.execute_return import (
    mark_publication as mark_publication,
)
from graph_works_core.workspace.execute_return import (
    pending_return as pending_return,
)
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation, held_bundle_lock
from graph_works_core.workspace.workspace_placement import (
    ReturnDestination as ReturnDestination,
)
from graph_works_core.workspace.workspace_placement import (
    confined_member,
)
from graph_works_core.workspace.workspace_placement import (
    resolve_destination as resolve_destination,
)


@dataclass(frozen=True, slots=True)
class ReturnPlan:
    record: ExecuteReturn
    destination: ReturnDestination
    coverage_member: str
    coverage_before: bytes | None
    coverage_after: bytes
    plan_sha256: str | None
    resume: ReturnOperation | None
    path: str
    page_sha256: str
    plan_member: str
    fingerprint: str


def read_member(root: Path, member: str) -> bytes | None:
    try:
        return confined_member(root, member).read_bytes()
    except FileNotFoundError:
        return None


def plan_return(
    layout: WorkspaceLayout,
    bundle: Bundle,
    items: Mapping[str, WorkItem],
    item: WorkItem,
    *,
    scope: Sequence[str],
    definition: PipelineDefinition,
    today: date,
    new_id: Callable[[], str],
) -> ReturnPlan | tuple[RefusalReason, str]:
    pending = pending_return(layout, item.path)
    if pending is not None and (pending.malformed or pending.local is not None):
        return "return-pending", "malformed return journal; inspect before retrying"
    selected = plan_scope(
        scope, item.finish_obligations, obligations_malformed="finish_obligations" in item.invalid_optional_fields
    )
    if selected.refusal:
        return selected.refusal, selected.detail
    destination = resolve_destination(layout, items, item)
    if isinstance(destination, tuple):
        return (
            ("return-pending", f"pending return destination is unverified; inspect: {destination[1]}")
            if pending
            else destination
        )
    coverage = stage_artifact_ref(item.path, definition.artifacts["execute"])
    plan = stage_artifact_ref(item.path, definition.artifacts["plan"])
    try:
        plan_sha = digest(read_member(layout.bundle_dir, plan.rel))
        before = read_member(destination.bundle_root, coverage.rel)
        text = before.decode("utf-8") if before is not None else None
    except (OSError, ValueError) as exc:
        return "return-destination-unverified", str(exc)
    page_sha = hashlib.sha256(bundle.concepts[item.path].serialize().encode("utf-8")).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "scope": selected.scope,
                "plan": plan.rel,
                "plan_sha256": plan_sha,
                "coverage": coverage.rel,
                "worktree": destination.worktree,
                "branch": destination.branch,
                "identity": destination.identity,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if pending is not None and (
        pending.fingerprint != fingerprint
        or pending.page_sha256 != page_sha
        or digest(before) not in (pending.coverage_before_sha256, pending.coverage_after_sha256)
    ):
        return "return-pending", "return intent or evidence changed; inspect the pending operation"
    record = (
        pending.record
        if pending
        else new_record(
            new_id(),
            selected.scope,
            on=today,
            coverage=coverage.resource,
            plan=plan.resource if plan_sha else None,
            plan_sha256=plan_sha,
        )
    )
    if record is None:
        return "return-pending", "return journal has no record; inspect"
    after = render_reopened(text, record).encode("utf-8")
    if pending is not None and digest(after) != pending.coverage_after_sha256:
        return "return-pending", "return coverage changed; inspect"
    return ReturnPlan(
        record, destination, coverage.rel, before, after, plan_sha, pending, item.path, page_sha, plan.rel, fingerprint
    )


def coverage_mutation(plan: ReturnPlan) -> WorkMutationPlan:
    parent = Path(plan.coverage_member).parent.as_posix()
    return WorkMutationPlan(
        root=plan.destination.bundle_root,
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
        directory_preconditions=()
        if (plan.destination.bundle_root / parent).exists()
        else (DirectoryPrecondition(parent, None),),
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


def _fresh_destination(layout: WorkspaceLayout, path: str) -> ReturnDestination:
    items = {i.path: i for i in load_items(load_workspace_bundle(layout, ignore=IGNORE))}
    if path not in items:
        raise WorkspaceError("return item disappeared; inspect")
    destination = resolve_destination(layout, items, items[path])
    if isinstance(destination, tuple):
        raise WorkspaceError(destination[1])
    return destination


def _same_destination(destination: ReturnDestination, op: ReturnOperation) -> bool:
    return (destination.worktree, destination.branch, destination.identity) == (op.worktree, op.branch, op.identity)


def _check_source(layout: WorkspaceLayout, op: ReturnOperation, *, published: bool = False) -> None:
    expected = op.canonical_after_sha256 if published else op.page_sha256
    if (
        expected is None
        or digest(read_member(layout.bundle_dir, item_page(op.path).rel)) != expected
        or digest(read_member(layout.bundle_dir, op.plan_member)) != op.plan_sha256
    ):
        raise WorkspaceError("return evidence changed; inspect and retry")


def _check_committed(destination: ReturnDestination, op: ReturnOperation) -> None:
    if op.content_commit is None or not _same_destination(destination, op):
        raise WorkspaceError("return destination changed; inspect and retry")
    root = destination.layout.root
    _git(root, "merge-base", "--is-ancestor", op.content_commit, "HEAD")
    if (
        digest(read_member(destination.bundle_root, op.coverage_member)) != op.coverage_after_sha256
        or not _committed_member(destination.layout, op.coverage_member, op.coverage_after_sha256)
        or not _committed_member(destination.layout, op.coverage_member, op.coverage_after_sha256, op.content_commit)
    ):
        raise WorkspaceError("return evidence changed; inspect and retry")


def apply_content(layout: WorkspaceLayout, plan: ReturnPlan) -> ReturnOperation:
    destination = plan.destination
    with held_bundle_lock(destination.layout):
        fresh = _fresh_destination(layout, plan.path)
        if fresh != destination:
            raise WorkspaceError("return destination changed; inspect and retry")
        head = _head(destination.layout.root)
        op = plan.resume or ReturnOperation(
            path=plan.path,
            return_id=plan.record.id,
            fingerprint=plan.fingerprint,
            page_sha256=plan.page_sha256,
            plan_sha256=plan.plan_sha256,
            worktree=destination.worktree or "",
            branch=destination.branch or "",
            identity=destination.identity or "",
            coverage_member=plan.coverage_member,
            coverage_before_sha256=digest(plan.coverage_before),
            coverage_after_sha256=hashlib.sha256(plan.coverage_after).hexdigest(),
            record=plan.record,
            plan_member=plan.plan_member,
            content_before_head=head,
        )
        _check_source(layout, op)
        if plan.resume is None:
            if pending_return(layout, plan.path) is not None:
                raise WorkspaceError("return operation appeared; inspect")
            write_operation(layout, op)
        elif pending_return(layout, plan.path) != op:
            raise WorkspaceError("return journal changed; inspect")
        if op.state == "content-committed":
            _check_committed(fresh, op)
            return op
        if digest(read_member(destination.bundle_root, op.coverage_member)) != op.coverage_before_sha256:
            # Crash between the successful Git commit and journal update. Exact
            # parent, subject and blob prove this operation, not just equal bytes.
            subject = _git(destination.layout.root, "log", "-1", "--format=%s").strip()
            parent = _git(destination.layout.root, "rev-parse", "HEAD^").strip()
            if parent != op.content_before_head or subject != _subject(op):
                raise WorkspaceError("return commit outcome unknown; inspect")
            proven = replace(op, content_commit=head, state="content-committed")
            _check_committed(fresh, proven)
            write_operation(layout, proven)
            return proven
        if head != op.content_before_head:
            raise WorkspaceError("return content HEAD changed; inspect")

        def guard() -> None:
            if _fresh_destination(layout, plan.path) != destination:
                raise WorkspaceError("return destination changed; inspect")
            _check_source(layout, op)

        application = apply_mutation(
            destination.layout,
            coverage_mutation(plan),
            commit=WorkspaceCommit(_subject(op), extra_paths=(plan.coverage_member,)),
            validate_read_set=guard,
        )
        commit = application.commit
        if (
            not application.ok
            or commit is None
            or commit.status != "committed"
            or commit.sha is None
            or any(w.startswith(COMMIT_FAILED_PREFIX) for w in application.warnings)
            or commit.sha == head
            or _head(destination.layout.root) != commit.sha
        ):
            raise WorkspaceError(f"execute return {op.return_id} content commit unproven; inspect pending journal")
        committed = replace(op, content_commit=commit.sha, state="content-committed")
        _check_committed(_fresh_destination(layout, plan.path), committed)
        write_operation(layout, committed)
        return committed


def _subject(op: ReturnOperation) -> str:
    return f"workspace: reopen {item_stem(op.path)} execute coverage for {op.return_id}"


def publish_guard(layout: WorkspaceLayout, op: ReturnOperation) -> Callable[[], None]:
    def guard() -> None:
        if pending_return(layout, op.path) != op:
            raise WorkspaceError("return journal changed; inspect")
        _check_source(layout, op)
        _check_committed(_fresh_destination(layout, op.path), op)

    return guard


def published(layout: WorkspaceLayout, op: ReturnOperation, item: WorkItem, *, scope: Sequence[str]) -> bool:
    """Prove exact canonical publication after a crash; never infer from phase."""
    if op.malformed or op.record is None or item.execute_return != op.record or item.phase != "execute":
        return False
    selected = plan_scope(
        scope, item.finish_obligations, obligations_malformed="finish_obligations" in item.invalid_optional_fields
    )
    if selected.refusal or selected.scope != tuple(row.text for row in op.record.scope):
        return False
    if op.local is not None:
        return op.local.kind == "same-root-return" and local_published(layout, op)
    try:
        _check_source(layout, op, published=True)
        if not _committed_member(layout, item_page(op.path).rel, op.canonical_after_sha256):
            return False
        _check_committed(_fresh_destination(layout, op.path), op)
    except (OSError, ValueError):
        return False
    return True


@contextmanager
def publication_locks(layout: WorkspaceLayout, op: ReturnOperation) -> Iterator[None]:
    """Inside admission/decision ownership, always lock content then canonical.

    Reacquisition after content preparation is intentional: the publication read
    guard proves fresh identity/content after both locks are held. Retain both
    through proof and journal removal so cooperating writers cannot split them.
    """
    content = layout_for(
        op.worktree,
        bundle_dir=layout.bundle_dir.relative_to(layout.root).as_posix(),
        config_dir=layout.config_dir.relative_to(layout.root).as_posix(),
    )
    with held_bundle_lock(content), held_bundle_lock(layout):
        yield


def recover_publication(layout: WorkspaceLayout, op: ReturnOperation, *, scope: Sequence[str]) -> bool:
    """Prove and clear an exact replay with the same locks as normal publish."""
    if op.malformed:
        return False
    try:
        with publication_locks(layout, op):
            items = {i.path: i for i in load_items(load_workspace_bundle(layout, ignore=IGNORE))}
            item = items.get(op.path)
            if item is None or pending_return(layout, op.path) != op or not published(layout, op, item, scope=scope):
                return False
            finish_return(layout, op)
            return True
    except (OSError, ValueError):
        return False


def apply_publication(
    layout: WorkspaceLayout,
    op: ReturnOperation,
    before: bytes,
    apply: Callable[[], MutationApplication],
) -> MutationApplication:
    """Keep known failed publication at finish; fence unknown Git outcomes.

    Only this operation's exact page postimage may be restored, under the same
    bundle lock, with an unchanged HEAD. A landed commit is never rolled back.
    Other paths and index entries are never reset.
    """
    member = item_page(op.path).rel
    relative = (layout.bundle_dir / member).relative_to(layout.root).as_posix()
    with publication_locks(layout, op):
        head_before = _head(layout.root)
        index_before = _git(layout.root, "ls-files", "--stage", "--", relative)
        expected_mode = _stage_mode(
            layout.root,
            relative,
            index_before,
            filemode=_git(layout.root, "config", "--bool", "--default", "true", "core.filemode").strip() == "true",
        )
        application = apply()
        if not application.ok:
            return application
        items = {i.path: i for i in load_items(load_workspace_bundle(layout, ignore=IGNORE))}
        item = items.get(op.path)
        if (
            item is not None
            and op.record is not None
            and published(layout, op, item, scope=tuple(row.text for row in op.record.scope))
        ):
            finish_return(layout, op)
            return application
        # A failed/unknown commit is never reported as a successful return.
        failures = (*application.failures, "canonical return commit unproven; inspect pending return")
        restored = False
        try:
            if (
                _head(layout.root) == head_before
                and digest(read_member(layout.bundle_dir, member)) == op.canonical_after_sha256
            ):
                restore = WorkMutationPlan(
                    root=layout.bundle_dir,
                    operation="file",
                    path_mapping=MappingProxyType({}),
                    move_plan=None,
                    moves=(),
                    writes=(PlannedWrite(member, op.canonical_after_sha256, before),),
                    deletes=(),
                    mkdirs=(),
                    warnings=(),
                    refusals=(),
                    validate_paths=(op.path,),
                    directory_preconditions=(),
                )

                def unchanged() -> None:
                    if _head(layout.root) != head_before:
                        raise WorkspaceError("canonical HEAD changed; inspect")

                index_after = _git(layout.root, "ls-files", "--stage", "--", relative)
                expected_entry = _staged_postimage(
                    layout.root, relative, expected_mode, read_member(layout.bundle_dir, member)
                )
                staged_matches = index_after == index_before or (
                    expected_entry is not None
                    and _index_entries(layout.root, (relative,)).get(relative, "") == expected_entry
                )
                if not staged_matches:
                    raise WorkspaceError("canonical index changed; inspect")
                rollback = apply_mutation(layout, restore, commit=None, validate_read_set=unchanged)
                restored = rollback.ok
                if restored and index_after != index_before:
                    # Restore only the Git representation of our exact postimage.
                    if (
                        not staged_matches
                        or "\n" in index_before.rstrip("\n")
                        or _git(layout.root, "ls-files", "--stage", "--", relative) != index_after
                    ):
                        raise WorkspaceError("canonical index changed; inspect")
                    metadata, _, name = index_before.rstrip("\n").partition("\t")
                    mode, oid, stage = metadata.split(" ")
                    if stage != "0" or name != relative:
                        raise WorkspaceError("canonical index was unmerged; inspect")
                    _git(layout.root, "update-index", "--cacheinfo", mode, oid, relative)
        except (OSError, ValueError):
            failures = (*failures, "canonical restoration incomplete; inspect page and index")
        return replace(application, failures=failures, rolled_back=restored)


def _repository_identity(layout: WorkspaceLayout) -> tuple[str, str]:
    return (
        _git(layout.root, "symbolic-ref", "--short", "HEAD").strip(),
        _git(layout.root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip(),
    )


def local_published(layout: WorkspaceLayout, op: ReturnOperation) -> bool:
    """Positive exact commit proof; failed/unknown transport is not authority."""
    local = op.local
    if local is None:
        return False
    try:
        return (
            pending_return(layout, op.path) == op
            and str(layout.root.resolve()) == op.worktree
            and _repository_identity(layout) == (op.branch, op.identity)
            and _git(layout.root, "rev-parse", "HEAD^").strip() == local.head
            and _git(layout.root, "log", "-1", "--format=%s").strip() == local.subject
            and digest(read_member(layout.bundle_dir, op.plan_member)) == op.plan_sha256
            and all(_committed_member(layout, member, sha) for member, sha in local.members)
        )
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
        branch, identity = _repository_identity(layout)
        op = ReturnOperation(
            path,
            return_id=record.id,
            record=record,
            worktree=str(layout.root.resolve()),
            branch=branch,
            identity=identity,
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
            or _repository_identity(layout) != (op.branch, op.identity)
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
def completion_locks(
    layout: WorkspaceLayout, items: Mapping[str, WorkItem], item: WorkItem
) -> Iterator[ReturnDestination | None]:
    """Hold content then canonical locks through returned completion's commit.

    Invoked inside decision ownership, only on live execute advances. Invalid
    destinations are left for verify_completion to report as refusal data.
    """
    record = item.execute_return
    if item.phase != "execute" or record is None or not record.active:
        yield None
        return
    destination = resolve_destination(layout, items, item)
    if isinstance(destination, tuple):
        yield None
        return
    with held_bundle_lock(destination.layout), held_bundle_lock(layout):
        yield destination


def verify_completion(
    layout: WorkspaceLayout,
    items: Mapping[str, WorkItem],
    item: WorkItem,
    definition: PipelineDefinition,
) -> tuple[str | None, RefusalReason | None, str]:
    """Read the current return's report from its verified content destination.

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
    destination = resolve_destination(layout, items, item)
    if isinstance(destination, tuple):
        return None, *destination
    try:
        raw = read_member(destination.bundle_root, coverage.rel)
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
