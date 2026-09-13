"""Ordered exclusive locks and recoverable, per-path promotion.

The durable journal is the intent and the backup. A sequence of replacements is
never a globally atomic swap. Failed journals stay available for explicit recovery.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

from .config import portable_content_root
from .machine import Services
from .previews import PreviewError, _decode, ancestors, load_preview, preview_bytes
from .records import (
    AncestorIdentity,
    Change,
    Finding,
    History,
    InventoryEntry,
    Journal,
    JsonValue,
    Ledger,
    Preview,
    Result,
    SnapshotEntry,
    SourceMapping,
)
from .store import identifier, inventory, ledgers, observe, owned_paths, record_bytes, sealed_bytes, unseal


class TransactionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def failure(operation: str, preview_id: str | None, code: str, message: str, variant_id: str | None = None) -> Result:
    return Result(
        operation, variant_id, preview_id, False, False, (Finding(code, "error", None, None, message),), (), {}
    )


def journal_path(state: Path, journal_id: str) -> Path:
    return state / "transactions" / identifier(journal_id) / "journal.json"


def load_journal(state: Path, journal_id: str) -> Journal:
    journal = cast(Journal, _decode(unseal(journal_path(state, journal_id).read_bytes()), Journal))
    if journal.schema_version != 1 or journal.id != journal_id:
        raise PreviewError("Invalid journal schema or identity")
    return journal


def incomplete(state: Path) -> tuple[Journal, ...]:
    root = state / "transactions"
    if not root.exists():
        return ()
    return tuple(
        journal
        for path in sorted(root.iterdir())
        if (journal := load_journal(state, path.name)).phase not in {"complete", "rolled_back"}
    )


def persist_journal(state: Path, journal: Journal, *, services: Services) -> None:
    path = journal_path(state, journal.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            services.filesystem.write_exclusive(path, sealed_bytes(journal))
        except OSError:
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
            raise
        services.filesystem.sync_directory(path.parent)
        services.filesystem.sync_directory(path.parent.parent)
        services.filesystem.sync_directory(state)
        return
    temporary = path.with_name("journal-" + services.new_id() + ".tmp")
    services.filesystem.write_exclusive(temporary, sealed_bytes(journal))
    services.filesystem.replace(temporary, path)
    services.filesystem.sync_directory(path.parent.parent)


@contextmanager
def locks(state: Path, variants: tuple[str, ...], *, services: Services) -> Iterator[None]:
    root = state / "locks"
    root.mkdir(parents=True, exist_ok=True)
    acquired: list[Path] = []
    try:
        for name in sorted({"ownership", *(identifier(v) for v in variants)}):
            path = root / (name + ".lock")
            try:
                services.filesystem.write_exclusive(path, services.new_id().encode("ascii"))
            except FileExistsError as exc:
                raise TransactionError("lock.present", f"Lock present (possibly stale); never stolen: {path}") from exc
            acquired.append(path)
            services.filesystem.sync_directory(root)
        yield
    finally:
        for path in reversed(acquired):
            path.unlink()
            services.filesystem.sync_directory(root)


def _overlap(left: Path, right: Path) -> bool:
    a, b = left.as_posix().casefold(), right.as_posix().casefold()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def revalidate(preview: Preview, *, services: Services) -> None:
    state, content = Path(preview.state_root), Path(preview.content_root)
    for identity in preview.ancestors:
        if services.filesystem.identity(Path(identity.path)) != identity:
            raise TransactionError("preview.stale", "Destination ancestor was replaced")
    ancestors(
        tuple(Path(o.destination) for o in preview.operations),
        services=services,
        replacing=tuple(Path(o.destination) for o in preview.operations if o.kind == "mkdir"),
    )
    for path in preview.expected_absences:
        if observe(Path(path), services=services) is not None:
            raise TransactionError("preview.stale", f"Expected absent destination now exists: {path}")
    for expected in preview.observed:
        root = Path(expected.root)
        paths = dict(preview.inventory_scopes).get(str(root), preview.owned_paths if root == content else (".",))
        if inventory(root, paths, services=services) != expected:
            raise TransactionError("preview.stale", f"Observed inventory changed: {root}")
    for variant, generation in preview.expected_generations:
        matching = [ledger for ledger in ledgers(state) if ledger.variant_id == variant]
        actual = matching[0].generation if matching else None
        if actual != generation:
            raise TransactionError("preview.stale", "Variant generation changed")
    if preview.operation == "install":
        from .installation import revalidate_install

        if not preview.allowed or any(f.severity == "error" for f in preview.findings):
            raise TransactionError("preview.blocked", "Installation validation blocks application")
        revalidate_install(preview, services=services)
        return
    destinations = tuple(content / path for path in preview.owned_paths)
    for i, destination in enumerate(destinations):
        if any(_overlap(destination, other) for other in destinations[:i]):
            raise TransactionError("ownership.overlap", "Preview ownership overlaps")
    for ledger in ledgers(state):
        if preview.operation in {"adopt", "accept", "rollback"} and ledger.variant_id in preview.variant_ids:
            continue
        from .store import resolve_content_root

        owner_root = resolve_content_root(state, ledger)
        if any(
            _overlap(destination, owner_root / owned) for destination in destinations for owned in owned_paths(ledger)
        ):
            raise TransactionError("ownership.overlap", f"Known owner overlaps: {ledger.variant_id}")
    if preview.operation not in {"fork", "adopt", "accept", "rollback"} or len(preview.variant_ids) != 1:
        raise TransactionError("preview.unsupported", "Unsupported application operation")
    if not preview.allowed or any(f.severity == "error" for f in preview.findings):
        raise TransactionError("preview.blocked", "Preview validation blocks application")
    if preview.operation in {"accept", "rollback"}:
        from .acceptance import content_changes
        from .snapshots import read_snapshot
        from .store import load_ledger

        stage = Path(preview.candidate_path).parent
        planned = cast(Ledger, _decode(json.loads((stage / "ledger.json").read_bytes()), Ledger))
        old = load_ledger(state, preview.variant_ids[0])
        if (
            planned.variant_id != old.variant_id
            or planned.generation != old.generation + 1
            or planned.history != (*old.history, preview.id)
            or planned.base_digest != read_snapshot(stage / "base.tar.gz").digest
            or planned.source != read_snapshot(stage / "base.tar.gz").source
            or (
                preview.operation == "accept"
                and planned.files
                != inventory(Path(preview.candidate_path), preview.owned_paths, services=services).entries
            )
        ):
            raise PreviewError("Acceptance or rollback ledger differs from authorized evidence")
        if preview.operation == "rollback":
            from .status import _attestation

            history = _attestation(state, old.variant_id)
            if history.operation != "accept":
                raise PreviewError("Rollback requires latest unreversed acceptance")
            original = next(e for e in history.before_tracking if e.path == "ledger.json")
            before_ledger = cast(Ledger, _decode(json.loads(original.content), Ledger))
            if planned != replace(
                before_ledger,
                generation=old.generation + 1,
                content_root=old.content_root,
                history=(*old.history, preview.id),
            ):
                raise PreviewError("Rollback must restore the original tracking state")
        changes = content_changes(preview, services=services)
        expected_operations = [
            (
                c.destination,
                "delete" if c.after is None else "mkdir" if c.after.kind == "directory" else "write",
                None
                if c.after is None or c.after.kind == "directory"
                else "candidate/" + Path(c.destination).relative_to(content).as_posix(),
                c.after.mode if c.after else 0,
            )
            for c in changes
        ]
        if expected_operations != [(o.destination, o.kind, o.artifact, o.mode) for o in preview.operations]:
            raise PreviewError("Operations differ from final candidate")
        return
    if preview.operation == "adopt":
        if preview.operations:
            raise PreviewError("Adoption must not contain live content operations")
        planned = _adoption_ledger(preview)
        expected_generation = preview.expected_generations[0][1]
        if (
            planned.variant_id != preview.variant_ids[0]
            or planned.generation != (expected_generation or 0) + 1
            or planned.history[-1:] != (preview.id,)
        ):
            raise PreviewError("Adoption ledger identity or generation mismatch")
        if planned.files != inventory(content, preview.owned_paths, services=services).entries:
            raise PreviewError("Adoption observation differs from content")
        if tuple(m.destination for m in planned.mappings) != preview.owned_paths:
            raise PreviewError("Adoption ownership differs from ledger")
        return
    candidate = inventory(Path(preview.candidate_path), (".",), services=services)
    expected_ops = []
    for entry in candidate.entries:
        destination = content / entry.path
        if not any(destination == root or root in destination.parents for root in destinations):
            if entry.kind == "directory" and any(destination in root.parents for root in destinations):
                continue
            raise PreviewError("Candidate has unowned content")
        expected_ops.append(
            (
                str(destination),
                "mkdir" if entry.kind == "directory" else "write",
                None if entry.kind == "directory" else "candidate/" + entry.path,
                entry.mode,
            )
        )
    actual_ops = [(o.destination, o.kind, o.artifact, o.mode) for o in preview.operations]
    if sorted(expected_ops) != sorted(actual_ops):
        raise PreviewError("Operations do not match the immutable candidate")


def _changes(preview: Preview, *, services: Services) -> tuple[Change, ...]:
    if preview.operation == "install":
        from .installation import installation_changes

        return installation_changes(preview, services=services)
    if preview.operation in {"accept", "rollback"}:
        from .acceptance import accepted_changes

        return accepted_changes(preview, services=services)
    stage = Path(preview.candidate_path).parent
    changes: list[Change] = []
    for operation in sorted(preview.operations, key=lambda o: (len(Path(o.destination).parts), o.destination)):
        after = SnapshotEntry(
            operation.destination,
            b"" if operation.artifact is None else services.filesystem.read_bytes(stage / operation.artifact),
            "directory" if operation.kind == "mkdir" else "file",
            operation.mode,
        )
        changes.append(Change(operation.destination, observe(Path(operation.destination), services=services), after))
    variant = preview.variant_ids[0]
    state = Path(preview.state_root)
    files = inventory(Path(preview.candidate_path), preview.owned_paths, services=services).entries
    mappings = tuple(SourceMapping(c.source, c.name) for c in preview.selection.skills) + preview.selection.resources
    ledger = Ledger(
        1,
        variant,
        1,
        portable_content_root(Path(preview.content_root), state, platform=services.platform),
        preview.source,
        "known",
        preview.selection.skills,
        mappings,
        preview.base_digest,
        files,
        preview.adaptations,
        preview.intent,
        tuple(d for d in preview.candidate_dependencies if d.required and not d.satisfied),
        (preview.id,),
        preview.selection.dependencies,
    )
    tracking = state / "forks" / variant
    if preview.operation == "adopt":
        ledger = _adoption_ledger(preview)
        tracking_data = [(a.path, services.filesystem.read_bytes(stage / a.path)) for a in preview.artifacts]
    else:
        tracking_data = [
            ("base.tar.gz", services.filesystem.read_bytes(stage / "base.tar.gz")),
            ("ledger.json", record_bytes(ledger)),
        ]
    for name, data in tracking_data:
        destination = tracking / name
        changes.append(
            Change(
                str(destination),
                observe(destination, services=services),
                SnapshotEntry(str(destination), data, "file", 0o600),
            )
        )
    history = History(
        1,
        preview.id,
        variant,
        ledger,
        tuple(
            replace(c.after, path=Path(c.destination).relative_to(tracking).as_posix())
            for c in changes
            if tracking in Path(c.destination).parents and c.after is not None
        ),
    )
    history_path = state / "forks" / variant / "history" / (preview.id + ".json")
    changes.append(
        Change(str(history_path), None, SnapshotEntry(str(history_path), sealed_bytes(history), "file", 0o600))
    )
    return tuple(changes)


def _adoption_ledger(preview: Preview) -> Ledger:
    stage = Path(preview.candidate_path).parent
    names = {a.path for a in preview.artifacts}
    if names not in (
        {"ledger.json", "observation.tar.gz", "evidence.json"},
        {"ledger.json", "observation.tar.gz", "evidence.json", "base.tar.gz"},
    ):
        raise PreviewError("Invalid adoption tracking artifacts")
    ledger = cast(Ledger, _decode(json.loads((stage / "ledger.json").read_bytes()), Ledger))
    if ledger.schema_version != 1 or (ledger.origin == "known") != ("base.tar.gz" in names):
        raise PreviewError("Adoption origin does not match evidence")
    from .snapshots import read_snapshot

    if ledger.base_digest != (read_snapshot(stage / "base.tar.gz").digest if "base.tar.gz" in names else None):
        raise PreviewError("Adoption base digest does not match original archive")
    observation = read_snapshot(stage / "observation.tar.gz")
    entries = tuple(InventoryEntry(e.path, e.kind, e.mode, e.hash) for e in observation.entries)
    if entries != ledger.files:
        raise PreviewError("Adoption observation archive differs from ledger files")
    return ledger


def promote(
    change: Change,
    expected: SnapshotEntry | None,
    value: SnapshotEntry | None,
    *,
    services: Services,
    directory_identity: AncestorIdentity | None = None,
) -> None:
    path = Path(change.destination)
    if observe(path, services=services) != expected:
        raise TransactionError("recovery.ambiguous", f"Unexpected edit preserved: {path}")
    if value is None:
        if expected is not None:
            if expected.kind == "directory":
                if directory_identity is not None and services.filesystem.identity(path) != directory_identity:
                    raise TransactionError("recovery.ambiguous", f"Directory replaced before deletion: {path}")
                path.rmdir()
            else:
                path.unlink()
            services.filesystem.sync_directory(path.parent)
        return
    if expected is not None and expected.kind != value.kind and "directory" in {expected.kind, value.kind}:
        promote(change, expected, None, services=services, directory_identity=directory_identity)
        expected = None
    if value.kind == "directory":
        if expected is not None and directory_identity is not None:
            actual_identity = services.filesystem.identity(path)
            if replace(actual_identity, mode=directory_identity.mode) != directory_identity:
                raise TransactionError("recovery.ambiguous", f"Directory replaced before restoration: {path}")
        path.mkdir(exist_ok=expected is not None)
        services.filesystem.chmod(path, value.mode)
        services.filesystem.sync_directory(path.parent)
        return
    temporary = path.with_name(".plugin-fork-" + services.new_id() + ".tmp")
    try:
        if value.kind == "symlink":
            services.filesystem.link(value.content.decode("utf-8"), temporary)
        else:
            services.filesystem.write_exclusive(temporary, value.content)
            services.filesystem.chmod(temporary, value.mode)
            services.filesystem.sync_file(temporary)
        if observe(path, services=services) != expected:
            raise TransactionError("recovery.ambiguous", f"Unexpected edit preserved: {path}")
        services.filesystem.replace(temporary, path, absent=expected is None)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def verify_original_ancestors(journal: Journal, *, services: Services) -> None:
    for identity in journal.ancestors:
        change = next((c for c in journal.changes if c.destination == identity.path), None)
        try:
            actual = services.filesystem.identity(Path(identity.path))
        except (FileNotFoundError, NotADirectoryError):
            actual = None
        restored = dict((entry.path, entry) for _, entry in journal.recovery_directories)
        if actual == identity or (actual is not None and actual == restored.get(identity.path)):
            continue
        # Planned directory mode changes retain device/inode identity. A
        # deleted/replaced directory can be absent or the exact journal after
        # image; an unrelated directory with identical bytes is never accepted.
        if change is not None and change.before is not None and change.before.kind == "directory":
            if change.after is None or change.after.kind != "directory":
                current = observe(Path(identity.path), services=services)
                if current is None or current == change.after:
                    continue
            elif actual is not None and replace(actual, mode=identity.mode) == identity:
                if actual.mode & 0o7777 in {change.before.mode, change.after.mode}:
                    continue
        raise TransactionError("preview.stale", "An original destination ancestor changed")


def _verify_ancestors(journal: Journal, *, services: Services) -> None:
    verify_original_ancestors(journal, services=services)
    for identity in journal.created_identities:
        if services.filesystem.identity(Path(identity.path)) != identity:
            raise TransactionError("preview.stale", "A created ancestor changed during promotion")


def _make_directory(state: Path, journal: Journal, path: Path, mode: int | None, *, services: Services) -> Journal:
    _verify_ancestors(journal, services=services)
    path.mkdir()
    identity = services.filesystem.identity(path)
    try:
        if mode is not None:
            services.filesystem.chmod(path, mode)
        journal = replace(journal, created_identities=(*journal.created_identities, services.filesystem.identity(path)))
        persist_journal(state, journal, services=services)
        services.filesystem.sync_directory(path.parent)
        return journal
    except OSError:
        # A failed durability write does not authorize deleting a racing creator's
        # directory. Only clean the exact empty directory we successfully created.
        current = services.filesystem.identity(path)
        if (current.device, current.inode) == (identity.device, identity.inode):
            path.rmdir()
        raise


def _verify_content(preview: Preview, journal: Journal, *, services: Services) -> None:
    if preview.operation == "install":
        from .installation import verify_install_content

        verify_install_content(preview, services=services)
        return
    content = Path(preview.content_root)
    if preview.operation == "adopt":
        expected_inventory = next(i for i in preview.observed if i.root == str(content))
        if inventory(content, preview.owned_paths, services=services) != expected_inventory:
            raise TransactionError("transaction.verify", "Adopted content changed before tracking promotion")
        return
    expected = tuple(
        sorted(
            (
                InventoryEntry(
                    Path(change.destination).relative_to(content).as_posix(),
                    change.after.kind,
                    change.after.mode,
                    change.after.hash,
                )
                for change in journal.changes
                if content in Path(change.destination).parents and change.after is not None
            ),
            key=lambda entry: entry.path,
        )
    )
    # Called only once every owned content operation is promoted. No owned
    # staging name may remain; tracking staging is outside the content roots.
    actual = inventory(content, preview.owned_paths, services=services)
    if actual.entries != expected:
        raise TransactionError("transaction.verify", "Complete owned inventory differs from the journal")


def _run(preview: Preview, *, services: Services) -> Result:
    state = Path(preview.state_root)
    changes = _changes(preview, services=services)
    parents: set[Path] = set()
    operation_dirs = {Path(c.destination) for c in changes if c.after and c.after.kind == "directory"}
    for change in changes:
        for parent in Path(change.destination).parents:
            if observe(parent, services=services) is not None:
                break
            if parent not in operation_dirs:
                parents.add(parent)
    staged: list[Change] = []
    for index, change in enumerate(changes):
        if change.after is not None and change.after.kind != "directory":
            path = Path(change.destination).with_name(f".plugin-fork-{preview.id}-{index}.tmp")
            staged.append(Change(str(path), None, replace(change.after, path=str(path))))
    journal = Journal(
        1,
        preview.id,
        hashlib.sha256(preview_bytes(preview)).hexdigest(),
        preview.variant_ids,
        "prepared",
        changes,
        (),
        tuple(str(p) for p in sorted(parents, key=lambda p: (len(p.parts), str(p)))),
        ancestors(
            tuple(Path(c.destination) for c in changes),
            services=services,
            replacing=tuple(Path(c.destination) for c in changes if c.after and c.after.kind == "directory"),
        ),
        tuple(staged),
        (),
        preview.operation,
        preview.lock_stores,
        preview.owner_observations,
    )
    persist_journal(state, journal, services=services)
    for directory in journal.created_directories:
        journal = _make_directory(state, journal, Path(directory), None, services=services)
    # Empty destination directories are required to stage beside their files.
    # Their creation is already described by the durable prepared intent.
    for index, change in enumerate(changes):
        if (
            change.after is not None
            and change.after.kind == "directory"
            and (change.before is None or change.before.kind != "directory")
        ):
            if change.before is not None:
                promote(change, change.before, None, services=services)
            journal = _make_directory(state, journal, Path(change.destination), change.after.mode, services=services)
            journal = replace(journal, completed=(*journal.completed, index))
            persist_journal(state, journal, services=services)
    for stage in journal.staged_changes:
        _verify_ancestors(journal, services=services)
        assert stage.after is not None
        path = Path(stage.destination)
        if stage.after.kind == "symlink":
            try:
                services.filesystem.link(stage.after.content.decode("utf-8"), path)
            except OSError as exc:
                if preview.operation == "install":
                    raise TransactionError(
                        "binding.link-unavailable",
                        f"{exc}; choose explicit copy installation. Recover retained journal {journal.id}",
                    ) from exc
                raise
        else:
            services.filesystem.write_exclusive(path, stage.after.content)
            services.filesystem.chmod(path, stage.after.mode)
            services.filesystem.sync_file(path)
        services.filesystem.sync_directory(path.parent)
    journal = replace(journal, phase="applying")
    persist_journal(state, journal, services=services)
    for index, change in enumerate(changes):
        _verify_ancestors(journal, services=services)
        if Path(preview.state_root) in Path(change.destination).parents:
            _verify_content(preview, journal, services=services)
            for prior in changes[:index]:
                if observe(Path(prior.destination), services=services) != prior.after:
                    raise TransactionError("transaction.verify", "Content changed before tracking promotion")
        if change.after is not None and change.after.kind != "directory":
            stage_path = Path(change.destination).with_name(f".plugin-fork-{preview.id}-{index}.tmp")
            expected_stage = replace(change.after, path=str(stage_path))
            if observe(stage_path, services=services) != expected_stage:
                raise TransactionError("transaction.verify", "Staging changed before promotion")
            current = observe(Path(change.destination), services=services)
            if current != change.before:
                raise TransactionError("preview.stale", "Destination changed before promotion")
            if change.before is not None and change.before.kind == "directory":
                promote(change, change.before, None, services=services)

            _verify_ancestors(journal, services=services)
            services.filesystem.replace(
                stage_path, Path(change.destination), absent=change.before is None or change.before.kind == "directory"
            )
        elif change.after is None:
            promote(change, change.before, None, services=services)
        elif change.before is not None and change.before.kind == "directory" and change.after.kind == "directory":
            promote(change, change.before, change.after, services=services)
        elif observe(Path(change.destination), services=services) != change.after:
            raise TransactionError("transaction.verify", "Directory changed before promotion")
        journal = replace(journal, completed=tuple(sorted({*journal.completed, index})))
        persist_journal(state, journal, services=services)
    for change in changes:
        if observe(Path(change.destination), services=services) != change.after:
            raise TransactionError("transaction.verify", "Live content or tracking differs from promoted evidence")
    _verify_content(preview, journal, services=services)
    _verify_ancestors(journal, services=services)
    persist_journal(state, replace(journal, phase="complete"), services=services)
    return Result(
        preview.operation,
        preview.variant_ids[0],
        preview.id,
        True,
        True,
        preview.findings,
        tuple(c.destination for c in changes),
        {"journal_path": str(journal_path(state, journal.id)), **_application_data(preview)},
    )


def _application_data(preview: Preview) -> dict[str, JsonValue]:
    if preview.operation == "install":
        from .installation import installation_data

        return installation_data(preview)
    return {}


@contextmanager
def application_locks(preview: Preview, *, services: Services) -> Iterator[None]:
    with ExitStack() as stack:
        for root in sorted(preview.lock_stores or (preview.state_root,)):
            variants = tuple(v for state, v, _ in preview.owner_observations if state == root)
            if root == preview.state_root:
                variants += preview.variant_ids
            stack.enter_context(locks(Path(root), variants, services=services))
        yield


def apply_preview(state: Path, preview_id: str, *, services: Services) -> Result:
    try:
        identifier(preview_id)
        if (state / "previews" / preview_id / "recovery.json").is_file():
            from .recovery import apply_recovery

            return apply_recovery(state, preview_id, services=services)
        preview = load_preview(state, preview_id)
        digest = hashlib.sha256(preview_bytes(preview)).hexdigest()
        with application_locks(preview, services=services):
            if journal_path(state, preview_id).exists():
                previous = load_journal(state, preview_id)
                code = "preview.consumed" if previous.phase in {"complete", "rolled_back"} else "transaction.incomplete"
                raise TransactionError(
                    code, f"Preview already has transaction evidence: {journal_path(state, preview_id)}"
                )
            if any(incomplete(Path(root)) for root in (preview.lock_stores or (str(state),))):
                raise TransactionError("transaction.incomplete", "Recover retained incomplete journals before mutating")
            current = load_preview(state, preview_id)
            if hashlib.sha256(preview_bytes(current)).hexdigest() != digest:
                raise PreviewError("Preview metadata changed while locking")
            revalidate(current, services=services)
            return _run(current, services=services)
    except TransactionError as exc:
        return failure("apply", preview_id, exc.code, str(exc))
    except (PreviewError, ValueError, TypeError) as exc:
        return failure("apply", preview_id, "preview.invalid", str(exc))
    except OSError as exc:
        return failure(
            "apply", preview_id, "transaction.io", f"{exc}; retained evidence: {state / 'transactions' / preview_id}"
        )
