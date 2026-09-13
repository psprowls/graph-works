"""Explicit rollback previews for incomplete journals; unknown edits are preserved."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import cast

from .machine import Services
from .previews import PreviewError, _decode, ancestors
from .records import Change, Journal, RecoveryPreview, Result, Roots
from .store import observe, sealed_bytes, unseal
from .transactions import (
    TransactionError,
    failure,
    incomplete,
    journal_path,
    load_journal,
    locks,
    persist_journal,
    promote,
    verify_original_ancestors,
)


def _observations(journal: Journal, *, services: Services) -> tuple[Change, ...]:
    observations: list[Change] = []
    known = {c.destination for c in (*journal.changes, *journal.staged_changes)} | set(journal.created_directories)
    try:
        verify_original_ancestors(journal, services=services)
    except TransactionError as exc:
        raise TransactionError("recovery.ambiguous", str(exc)) from exc
    created = {identity.path: identity for identity in journal.created_identities}
    directory_paths = set(journal.created_directories) | {
        c.destination
        for c in journal.changes
        if (c.before is None or c.before.kind != "directory") and c.after and c.after.kind == "directory"
    }
    for directory in directory_paths:
        current = observe(Path(directory), services=services)
        if current is not None and current.kind == "directory":
            expected = created.get(directory)
            if expected is None or services.filesystem.identity(Path(directory)) != expected:
                raise TransactionError("recovery.ambiguous", f"Directory ownership is unproven: {directory}")
    for staging_path, identity in journal.recovery_directories:
        path = Path(staging_path)
        current = observe(path, services=services)
        if current is not None:
            if services.filesystem.identity(path) != replace(
                identity, path=staging_path
            ) or services.filesystem.children(path):
                raise TransactionError("recovery.ambiguous", f"Restoration staging was replaced or edited: {path}")
        elif services.filesystem.identity(Path(identity.path)) != identity:
            raise TransactionError("recovery.ambiguous", f"Restoration directory identity was lost: {identity.path}")
    for change in journal.changes:
        current = observe(Path(change.destination), services=services)
        transition = change.before is not None and change.after is not None and change.before.kind != change.after.kind
        if current not in (change.before, change.after) and not (transition and current is None):
            raise TransactionError("recovery.ambiguous", f"Unknown edit preserved: {change.destination}")
        observations.append(Change(change.destination, current, change.before))
    for staged in journal.staged_changes:
        current = observe(Path(staged.destination), services=services)
        # A crash may precede chmod on a scratch artifact. Scratch permission
        # changes alone do not authorize different bytes or link targets.
        if current is not None:
            assert staged.after is not None
            if replace(current, mode=staged.after.mode) != staged.after:
                raise TransactionError("recovery.ambiguous", f"Unknown staged bytes preserved: {staged.destination}")
        observations.append(Change(staged.destination, current, None))
    for destination in known:
        path = Path(destination)
        current = observe(path, services=services)
        if current and current.kind == "directory":
            if any(str(child) not in known for child in services.filesystem.children(path)):
                raise TransactionError("recovery.ambiguous", f"Unknown directory contents preserved: {path}")
        elif destination in journal.created_directories and current is not None:
            raise TransactionError("recovery.ambiguous", f"Created directory was replaced: {path}")
    return tuple(observations)


def plan_rollback(roots: Roots, variant_id: str, *, services: Services) -> Result:
    try:
        journals = [j for j in incomplete(roots.state) if variant_id in j.variant_ids]
        if not journals:
            from .acceptance import plan_content_rollback

            return plan_content_rollback(roots, variant_id, services=services)
        if len(journals) != 1:
            raise TransactionError("recovery.unavailable", "Exactly one incomplete journal must identify this variant")
        journal = journals[0]
        observed = _observations(journal, services=services)
        preview_id = services.new_id()
        from .store import identifier

        identifier(preview_id)
        preview = RecoveryPreview(
            1,
            preview_id,
            journal.id,
            hashlib.sha256(journal_path(roots.state, journal.id).read_bytes()).hexdigest(),
            observed,
            ancestors(
                tuple(Path(c.destination) for c in observed),
                services=services,
                replacing=tuple(
                    Path(c.destination)
                    for c in journal.changes
                    if (c.before and c.before.kind == "directory") or (c.after and c.after.kind == "directory")
                ),
            ),
        )
        stage = roots.state / "previews" / preview_id
        stage.mkdir(parents=True)
        services.filesystem.write_exclusive(stage / "recovery.json", sealed_bytes(preview))
        services.filesystem.sync_directory(stage)
        return Result(
            "rollback",
            variant_id,
            preview_id,
            False,
            True,
            (),
            tuple(c.destination for c in observed),
            {"journal_path": str(journal_path(roots.state, journal.id))},
        )
    except TransactionError as exc:
        return failure("rollback", None, exc.code, str(exc), variant_id)
    except (OSError, ValueError) as exc:
        return failure("rollback", None, "recovery.invalid", str(exc), variant_id)


def _prepare_directories(state: Path, journal: Journal, observed: tuple[Change, ...], *, services: Services) -> Journal:
    """Seal directory identities before any live restoration can destroy an inode."""
    recorded = {identity.path for _, identity in journal.recovery_directories}
    pending = list(journal.recovery_directories)
    changed = {c.destination for c in journal.changes} | set(journal.created_directories)
    try:
        for change in observed:
            if (
                change.after is None
                or change.after.kind != "directory"
                or (change.before is not None and change.before.kind == "directory")
                or change.destination in recorded
            ):
                continue
            parent = Path(change.destination).parent
            while str(parent) in changed or not parent.is_dir():
                parent = parent.parent
            stage = parent / (".plugin-fork-recovery-" + services.new_id() + ".tmp")
            stage.mkdir()
            identity = services.filesystem.identity(stage)
            pending.append((str(stage), replace(identity, path=change.destination)))
            services.filesystem.chmod(stage, change.after.mode)
            pending[-1] = (str(stage), replace(services.filesystem.identity(stage), path=change.destination))
            services.filesystem.sync_directory(stage)
            services.filesystem.sync_directory(parent)
        if tuple(pending) != journal.recovery_directories:
            journal = replace(journal, recovery_directories=tuple(pending))
            persist_journal(state, journal, services=services)
        return journal
    except OSError:
        # A persistence error may occur after its atomic write. Keep every
        # durably named directory; remove only our unrecorded, unchanged staging.
        durable = load_journal(state, journal.id).recovery_directories
        for staged, identity in pending:
            path = Path(staged)
            if (staged, identity) not in durable and services.filesystem.identity(path) == replace(
                identity, path=staged
            ):
                path.rmdir()
                services.filesystem.sync_directory(path.parent)
        raise


def apply_recovery(state: Path, preview_id: str, *, services: Services) -> Result:
    preview_path = state / "previews" / preview_id / "recovery.json"
    original = preview_path.read_bytes()
    preview = cast(RecoveryPreview, _decode(unseal(original), RecoveryPreview))
    if preview.schema_version != 1 or preview.id != preview_id:
        raise PreviewError("Invalid recovery preview")
    journal = load_journal(state, preview.journal_id)
    with ExitStack() as stack:
        for root in sorted(journal.lock_stores or (str(state),)):
            variants = tuple(v for owner_state, v, _ in journal.lock_owners if owner_state == root)
            if root == str(state):
                variants += journal.variant_ids
            stack.enter_context(locks(Path(root), variants, services=services))
        if preview_path.read_bytes() != original:
            raise PreviewError("Recovery preview changed while locking")
        journal = load_journal(state, preview.journal_id)
        if journal.phase in {"complete", "rolled_back"}:
            raise TransactionError("preview.consumed", "Journal no longer needs recovery")
        if hashlib.sha256(journal_path(state, journal.id).read_bytes()).hexdigest() != preview.journal_digest:
            raise TransactionError("preview.stale", "Journal changed since recovery preparation")
        for identity in preview.ancestors:
            if services.filesystem.identity(Path(identity.path)) != identity:
                raise TransactionError("preview.stale", "Recovery ancestor changed")
        observed = _observations(journal, services=services)
        if observed != preview.observed:
            raise TransactionError("preview.stale", "Recovery destinations changed")
        journal = _prepare_directories(state, journal, observed, services=services)
        created = {identity.path: identity for identity in (*journal.ancestors, *journal.created_identities)}
        restored = {identity.path: (stage, identity) for stage, identity in journal.recovery_directories}
        for change in reversed(observed):
            if change.destination in restored and change.after is not None and change.after.kind == "directory":
                stage, identity = restored[change.destination]
                destination = Path(change.destination)
                if observe(Path(stage), services=services) is not None:
                    if services.filesystem.identity(Path(stage)) != replace(identity, path=stage):
                        raise TransactionError("recovery.ambiguous", "Restoration staging identity changed")
                    promote(
                        change,
                        change.before,
                        None,
                        services=services,
                        directory_identity=created.get(change.destination),
                    )
                    services.filesystem.rename_directory(Path(stage), destination)
                elif services.filesystem.identity(destination) != identity:
                    raise TransactionError("recovery.ambiguous", "Restored directory was replaced")
                continue
            promote(
                change,
                change.before,
                change.after,
                services=services,
                directory_identity=created.get(change.destination),
            )
        for directory in reversed(journal.created_directories):
            path = Path(directory)
            if observe(path, services=services) is not None:
                if services.filesystem.identity(path) != created.get(directory):
                    raise TransactionError("recovery.ambiguous", f"Directory replaced before cleanup: {path}")
                path.rmdir()
                services.filesystem.sync_directory(path.parent)
        _observations(journal, services=services)
        persist_journal(state, replace(journal, phase="rolled_back"), services=services)
        return Result(
            "rollback",
            journal.variant_ids[0],
            preview_id,
            True,
            True,
            (),
            tuple(c.destination for c in observed),
            {"journal_path": str(journal_path(state, journal.id))},
        )
