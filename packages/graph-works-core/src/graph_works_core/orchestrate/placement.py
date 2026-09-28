"""`gw work record-placement`: write an observed worktree/branch and its baseline, nothing else.

The third orchestrate module, beside the planner (`commands.py`) and the
stage-completion shell (`stage_advance.py`). It shares no module-level symbol
with either; what it shares with `stage_advance` is the decision owner's lock.

The coordinator reads where Orca actually placed a worker and records that
pair here, immediately after launch (D-006). A live record re-reads the bundle
and re-plans inside `locked_decision_owner`, so it serializes with
`gw work advance`: when a worker's own advance wins the lock first, the record
sees the new phase and refuses rather than stamping a stage that already ended.
The lock prevents lost updates; it does not remove that race, and the refusal
is how the race is made visible.

Nothing here reads Orca; only `run_record_baseline` runs git, through `provenance`'s gate probes.
The pair is the caller's verified observation; this module proves only that the item is entitled to it now.
`run_record_reader` is the reader counterpart: it writes a receipt in
workspace coordination storage, never the page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType

from okf_io import Bundle, load_bundle, parse
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.mutation import PlannedWrite, WorkMutationPlan
from work_tracker_okf.paths import item_page
from work_tracker_okf.placement import (
    BaselinePlan,
    BaselineRefusal,
    PlacementPlan,
    ReaderReceiptPlan,
    apply_baseline,
    apply_placement,
    plan_baseline,
    plan_placement,
    plan_reader_receipt,
)
from work_tracker_okf.placement import (
    ReaderObservation as ReaderObservation,
)

from graph_works_core.workspace import provenance
from graph_works_core.workspace.commits import (
    COMMIT_FAILED_PREFIX,
    CommitOutcome,
    WorkspaceCommit,
    commit_mode,
    item_stem,
)
from graph_works_core.workspace.decision_owner import locked_decision_owner
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import ItemRepo, declared_repositories, resolve_item_repo, resolve_repos
from graph_works_core.workspace.transactions import MutationApplication, apply_mutation, commit_pending
from graph_works_core.workspace.workspace_branch import WORKSPACE_REPO, workspace_repo

READER_RECEIPT_SCHEMA = "gw-reader-receipt"


@dataclass(frozen=True, slots=True)
class ReaderRecord:
    """An attempt's receipt outcome; only an unknown item has no receipt path."""

    plan: ReaderReceiptPlan
    receipt_path: Path | None
    written: bool
    replayed: bool
    conflict: str | None = None


def _reject_capabilities(value: str) -> None:
    if "dcap_" in value or "--dispatch-capability" in value:
        raise WorkspaceError("dispatch capabilities are never recorded")


def reader_receipt_path(layout: WorkspaceLayout, path: str, dispatch_id: str) -> Path:
    """Locate an attempt, rejecting unsafe identifiers before constructing paths."""
    _reject_capabilities(dispatch_id)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", dispatch_id) is None or ".." in dispatch_id:
        raise WorkspaceError("dispatch_id is not a plain attempt identifier")
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return layout.cache_dir / "reader-receipts" / digest / f"{dispatch_id}.json"


def read_reader_receipt(layout: WorkspaceLayout, path: str, dispatch_id: str) -> dict[str, object] | None:
    """Read attempt evidence; broken coordination storage requires repair."""
    target = reader_receipt_path(layout, path, dispatch_id)
    try:
        value: object = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"cannot read reader receipt {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkspaceError(f"invalid reader receipt {target}: expected a JSON object")
    _reject_capabilities(json.dumps(value))
    return value


def _receipt_body(plan: ReaderReceiptPlan) -> dict[str, object]:
    observation = plan.observation
    return {
        "schema": READER_RECEIPT_SCHEMA,
        "version": 1,
        "path": plan.path,
        "root": plan.root,
        "phase": plan.expected_phase,
        "task_id": observation.task_id,
        "dispatch_id": observation.dispatch_id,
        "dispatch_key": observation.dispatch_key,
        "repo": observation.repo,
        "worktree": observation.worktree,
        "start_sha": observation.start_sha,
    }


def _write_receipt(target: Path, body: dict[str, object]) -> None:
    """Publish a complete sibling temp file atomically while holding the owner lock."""
    temp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        temp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(body, indent=2, sort_keys=True) + "\n")
        temp.replace(target)
    except OSError as exc:
        raise WorkspaceError(f"cannot write reader receipt {target}: {exc}") from exc
    finally:
        if temp is not None:
            primary = sys.exception()
            try:
                temp.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                if primary is not None:
                    primary.add_note(f"cannot clean up temporary reader receipt {temp}: {cleanup_exc}")
                else:
                    raise WorkspaceError(
                        f"cannot clean up temporary reader receipt {temp}: {cleanup_exc}"
                    ) from cleanup_exc


def run_record_reader(
    layout: WorkspaceLayout,
    path: str,
    *,
    root: str,
    phase: str,
    observation: ReaderObservation,
    dry_run: bool = True,
) -> ReaderRecord:
    """Record a reader observation without mutating item bytes or placement stamps.

    Refusal takes precedence over replay: old evidence cannot authorize a stale
    phase. Live decisions, including replay/conflict checks and publication, run
    under the same decision-owner lock as stage advance. Dry runs never lock.
    """
    for value in (
        path,
        root,
        phase,
        observation.task_id,
        observation.dispatch_id,
        observation.dispatch_key,
        observation.repo,
        observation.worktree,
        observation.start_sha,
    ):
        _reject_capabilities(value)
    target = reader_receipt_path(layout, path, observation.dispatch_id)

    def decide(items: Sequence[WorkItem]) -> ReaderRecord:
        if observation.repo == WORKSPACE_REPO:
            workspace, note = workspace_repo(layout)
            if workspace is None:
                raise WorkspaceError(f"{path}: repo {WORKSPACE_REPO} but {note}")
        elif observation.repo not in declared_repositories(layout):
            raise WorkspaceError(f"{path}: repo {observation.repo!r} names no declared repository")
        plan = plan_reader_receipt(items, path, root=root, phase=phase, observation=observation)
        receipt_path = None if plan.refusal == "unknown-path" else target
        if plan.refusal is not None:
            return ReaderRecord(plan, receipt_path, written=False, replayed=False)
        existing = read_reader_receipt(layout, path, observation.dispatch_id)
        body = _receipt_body(plan)
        return ReaderRecord(
            plan,
            target,
            written=False,
            replayed=existing == body,
            conflict="attempt-mismatch" if existing is not None and existing != body else None,
        )

    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    if dry_run or not any(item.path == path for item in items):
        return decide(items)
    with locked_decision_owner(layout, path) as context:
        record = decide(context.items)
        if record.plan.refusal is not None or record.replayed or record.conflict is not None:
            return record
        assert record.receipt_path is not None
        _write_receipt(record.receipt_path, _receipt_body(record.plan))
        return replace(record, written=True)


@dataclass(frozen=True, slots=True)
class PlacementRecord:
    """What one record did. An unchanged pair can commit pending owned files."""

    plan: PlacementPlan
    application: MutationApplication | None = None
    repo_note: str | None = None
    pending_commit: CommitOutcome | None = None

    @property
    def warnings(self) -> tuple[str, ...]:
        if self.application is not None:
            return self.application.warnings
        if self.pending_commit is not None and self.pending_commit.status == "failed":
            return (f"{COMMIT_FAILED_PREFIX}{self.pending_commit.reason}",)
        return ()

    @property
    def written(self) -> bool:
        return self.application is not None and self.application.ok


def _prepare_placement(
    layout: WorkspaceLayout,
    items: Sequence[WorkItem],
    path: str,
    *,
    root: str,
    phase: str,
    worktree: str,
    branch: str,
    today: date,
    repo_name: str | None,
    repo: str | None,
    start_sha: str | None = None,
    require_start_sha: bool = False,
) -> tuple[PlacementPlan, ItemRepo | None]:
    """Plan first, then resolve the item's repository for eligible placements."""
    by_path = {item.path: item for item in items}
    own: ItemRepo | None = None
    target: str | None = None
    if repo and path in by_path:
        if repo == WORKSPACE_REPO:
            workspace, note = workspace_repo(layout)
            if workspace is None:
                raise WorkspaceError(f"{path}: --repo {WORKSPACE_REPO} but {note}")
        else:
            declared = declared_repositories(layout)
            if repo not in declared:
                raise WorkspaceError(
                    f"{path}: --repo {repo!r} names no declared repository in {layout.manifest_path}; "
                    f"declared: {sorted(declared)}"
                )
        own = resolve_item_repo(layout, by_path[path], by_path, repo_name=repo_name)
        target = None if own.name == repo else repo
    plan = plan_placement(
        items,
        path,
        root=root,
        phase=phase,
        worktree=worktree,
        branch=branch,
        today=today,
        repo=target,
        start_sha=start_sha,
        require_start_sha=require_start_sha,
    )
    if plan.refusal is None and own is None:
        own = resolve_item_repo(layout, by_path.get(path), by_path, repo_name=repo_name)
    return plan, own


def run_record_placement(
    layout: WorkspaceLayout,
    path: str,
    *,
    root: str,
    phase: str,
    worktree: str,
    branch: str,
    today: date,
    repo_name: str | None = None,
    repo: str | None = None,
    dry_run: bool = True,
    expected_preparation: str | None = None,
    start_sha: str | None = None,
    require_start_sha: bool = False,
) -> PlacementRecord:
    """Record (*worktree*, *branch*) on *path* for its *phase* dispatch under *root*.

    Every eligible placement, including a preview or unchanged replay,
    validates *path*'s repository and returns its selection note. The code
    repo for postcondition validation is *path*'s own --
    `resolve_item_repo`'s strict chain: the nearest `repo:` over *path* and
    its ancestors, then *repo_name*, then the sole declared repository;
    several declared with no `repo:` and no *repo_name* still raise
    `WorkspaceError` rather than guess. The validation itself checks
    `affects` against every declared repo (`resolve_repos(layout)`), not
    only the resolved one -- matching `work file`/`work advance`
    (`stage_advance._advance`'s `repo_root=resolved_repo, repo_roots=declared`).
    The differential postcondition gate excuses a pre-existing
    `affects-missing` finding either way, so this only bites when baseline
    capture itself fails and the gate falls back to its absolute form.

    *repo* names the repository the observed pair actually lives in. When it
    names a declared repository other than *path*'s own, the pair is written
    under `repo_stamps[repo]` instead of the scalar `worktree`/`branch`
    pair; when it names *path*'s own, the scalar pair is written as usual.
    An undeclared *repo* raises `WorkspaceError`. *path*'s own repository is
    resolved (honouring *repo_name*) whenever *repo* is given, in both dry
    and live runs, so a dry run can plan the foreign-vs-own distinction too.

    Dry runs and unknown paths plan without locking, like `run_stage_advance`.
    A live record takes the decision owner's lock, re-plans and re-resolves
    against the projection read inside it, and applies one journaled page
    write before releasing it. A stale preimage returns a failed
    `MutationApplication` and writes nothing.

    *start_sha* is the observed commit this placement's work starts from (its
    execute baseline); see `plan_placement` for keep/drop/conflict.
    *require_start_sha* refuses a code placement that would end up with no
    baseline.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    known = any(item.path == path for item in items)
    if dry_run or not known:
        plan, own = _prepare_placement(
            layout,
            items,
            path,
            root=root,
            phase=phase,
            worktree=worktree,
            branch=branch,
            today=today,
            repo_name=repo_name,
            repo=repo,
            start_sha=start_sha,
            require_start_sha=require_start_sha,
        )
        return PlacementRecord(plan=plan, repo_note=own.note if own else None)
    commit_mode(layout)
    with locked_decision_owner(layout, path) as context:
        if (
            expected_preparation is not None
            and _preparation_guard(layout, context.bundle, path) != expected_preparation
        ):
            raise WorkspaceError(f"{path}: preparation changed; replan before recording")
        plan, own = _prepare_placement(
            layout,
            context.items,
            path,
            root=root,
            phase=phase,
            worktree=worktree,
            branch=branch,
            today=today,
            repo_name=repo_name,
            repo=repo,
            start_sha=start_sha,
            require_start_sha=require_start_sha,
        )
        workspace_commit = WorkspaceCommit(f"workspace: record {item_stem(path)} {phase} placement", items=(path,))
        if plan.refusal is not None:
            return PlacementRecord(plan=plan, repo_note=own.note if own else None)
        if not plan.changed:
            return PlacementRecord(
                plan=plan, repo_note=own.note if own else None, pending_commit=commit_pending(layout, workspace_commit)
            )
        assert own is not None

        def validate_preparation() -> None:
            if expected_preparation is not None and preparation_guard(layout, path) != expected_preparation:
                raise WorkspaceError(f"{path}: preparation changed; replan before recording")

        application = apply_mutation(
            layout,
            _mutation(context.bundle, plan),
            repo_root=own.path,
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
            validate_read_set=validate_preparation if expected_preparation is not None else None,
            commit=workspace_commit,
        )
        return PlacementRecord(plan=plan, application=application, repo_note=own.note)


def preparation_guard(layout: WorkspaceLayout, path: str, *, bundle: Bundle | None = None) -> str:
    """Capture owner/ancestor bytes and repository configuration before provisioning.

    The opaque token is checked under the placement lock and from fresh reads
    under the bundle mutation lock immediately before effects. Provisioning
    itself never holds either lock. Ancestors bind inherited repo assignments;
    complete owner bytes bind phase, terminal state and every existing stamp.
    Supply the planning bundle to bind the token to the same read as the decision.
    """
    return _preparation_guard(
        layout, bundle if bundle is not None else load_bundle(layout.bundle_dir, ignore=IGNORE), path
    )


def _preparation_guard(layout: WorkspaceLayout, bundle: Bundle, path: str) -> str:
    items = {item.path: item for item in load_items(bundle)}
    item = items.get(path)
    if item is None:
        raise WorkspaceError(f"{path}: unknown preparation owner")
    digest = hashlib.sha256()
    for member in (path, *item.ancestor_paths):
        document = bundle.concepts.get(member)
        digest.update(repr((member, document.serialize() if document is not None else None)).encode("utf-8"))
    for config in (layout.manifest_path, layout.manifest_path.with_name("workspace.local.yaml")):
        digest.update(repr((str(config), config.read_bytes() if config.exists() else None)).encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    plan: BaselinePlan
    application: MutationApplication | None = None
    repo_note: str | None = None

    @property
    def written(self) -> bool:
        return self.application is not None and self.application.ok


def run_record_baseline(
    layout: WorkspaceLayout,
    path: str,
    *,
    cwd: Path,
    today: date,
    repo_name: str | None = None,
    dry_run: bool = True,
    environ: Mapping[str, str] | None = None,
) -> BaselineRecord:
    """Record *cwd*'s HEAD as *path*'s scalar execute baseline, before execute work starts.

    The attended pipeline's equivalent of preparation's stamp (D-001): the
    workflow skill runs it from the checkout where the execute stage's work
    begins. *cwd* must be in *path*'s own code repository, and HEAD is read with
    the gate's git (`provenance.gate_git`), so the baseline and the gate that
    later reads it agree on which git answered. A live record takes the decision
    owner's lock and applies one journaled page write.
    """
    git = provenance.gate_git(layout, environ=environ)

    def decide(items: Sequence[WorkItem]) -> tuple[BaselinePlan, ItemRepo | None]:
        by_path = {item.path: item for item in items}
        item = by_path.get(path)
        if item is None:
            return plan_baseline(items, path, observed_head="", head_descends_from_recorded=False, today=today), None
        own = resolve_item_repo(layout, item, by_path, repo_name=repo_name)

        def refused(reason: BaselineRefusal, detail: str) -> tuple[BaselinePlan, ItemRepo]:
            return BaselinePlan(path, item.start_sha, item.start_sha, (), reason, detail), own

        if own.path is None:
            return refused(
                "no-repo", f"no code repository resolved for {path}" + (f" ({own.note})" if own.note else "")
            )
        if provenance.repository_of(cwd, (own.path,)) is None:
            return refused(
                "outside-repository",
                f"{cwd} is not in {path}'s repository {own.name!r} ({own.path}); "
                "run from the checkout where execute work starts",
            )
        if isinstance(git, provenance.GitFailure):
            return refused("git-unavailable", f"no usable git ({git.cause}): {git.detail}")
        head = provenance.strict_commit(cwd, "HEAD", git=git)
        if isinstance(head, provenance.GitFailure):
            return refused("git-unavailable", f"cannot read HEAD in {cwd}: {head.detail}")
        descends = False
        if item.start_sha is not None and item.start_sha != head:
            answer = provenance.strict_is_ancestor(cwd, item.start_sha, head, git=git)
            if isinstance(answer, provenance.GitFailure):
                return refused("git-unavailable", answer.detail)
            descends = answer
        return plan_baseline(items, path, observed_head=head, head_descends_from_recorded=descends, today=today), own

    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    if dry_run or not any(item.path == path for item in items):
        plan, own = decide(items)
        return BaselineRecord(plan, repo_note=own.note if own else None)
    commit_mode(layout)
    with locked_decision_owner(layout, path) as context:
        plan, own = decide(context.items)
        if plan.refusal is not None or not plan.changed:
            return BaselineRecord(plan, repo_note=own.note if own else None)
        assert own is not None
        application = apply_mutation(
            layout,
            _baseline_mutation(context.bundle, plan),
            repo_root=own.path,
            repo_roots=resolve_repos(layout),
            baseline_bundle=context.bundle,
            commit=WorkspaceCommit(f"workspace: record {item_stem(path)} execute baseline", items=(path,)),
        )
        return BaselineRecord(plan, application=application, repo_note=own.note)


def _baseline_mutation(bundle: Bundle, plan: BaselinePlan) -> WorkMutationPlan:
    member = item_page(plan.path).rel
    before = bundle.concepts[plan.path].serialize().encode("utf-8")
    document = parse(before.decode("utf-8"), path=bundle.root / member)
    apply_baseline(document, plan)
    return WorkMutationPlan(
        root=bundle.root,
        operation="file",
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=(PlannedWrite(member, hashlib.sha256(before).hexdigest(), document.serialize().encode("utf-8")),),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(plan.path,),
        directory_preconditions=(),
    )


def _mutation(bundle: Bundle, plan: PlacementPlan) -> WorkMutationPlan:
    member = item_page(plan.path).rel
    page = bundle.root / member
    before = bundle.concepts[plan.path].serialize().encode("utf-8")
    # Copy the authoritative projection, not a later disk read: an external
    # edit must fail the preimage check, never authorize this older plan.
    # Parsing a fresh document keeps the lock-held validation baseline intact.
    document = parse(before.decode("utf-8"), path=page)
    apply_placement(document, plan)
    return WorkMutationPlan(
        root=bundle.root,
        operation="file",
        path_mapping=MappingProxyType({}),
        move_plan=None,
        moves=(),
        writes=(PlannedWrite(member, hashlib.sha256(before).hexdigest(), document.serialize().encode("utf-8")),),
        deletes=(),
        mkdirs=(),
        warnings=(),
        refusals=(),
        validate_paths=(plan.path,),
        directory_preconditions=(),
    )


__all__ = [
    "READER_RECEIPT_SCHEMA",
    "BaselineRecord",
    "PlacementRecord",
    "ReaderRecord",
    "preparation_guard",
    "read_reader_receipt",
    "reader_receipt_path",
    "run_record_baseline",
    "run_record_placement",
    "run_record_reader",
]
