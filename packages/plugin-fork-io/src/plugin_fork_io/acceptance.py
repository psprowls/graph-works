"""Final candidate authorization and portable latest-acceptance content rollback."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from .fork import validate_candidate
from .machine import Services
from .previews import PreviewError, _decode, ancestors, load_preview, preview_bytes
from .records import (
    Artifact,
    Change,
    Dependency,
    FileOperation,
    Finding,
    History,
    InventoryEntry,
    Ledger,
    Preview,
    Resolution,
    Result,
    Review,
    Roots,
    Selection,
    Snapshot,
    SnapshotEntry,
    SourceIdentity,
    SourceSpec,
)
from .snapshots import capture, read_snapshot, validate_path
from .status import _attestation, read_status
from .store import identifier, inventory, load_ledger, observe, owned_paths, record_bytes, sealed_bytes
from .transactions import TransactionError, failure
from .updates import _write_candidate
from .validation import parse_skill_metadata


def _snapshot(root: Path, paths: tuple[str, ...], *, services: Services) -> Snapshot:
    observed = inventory(root, paths, services=services)
    entries: list[SnapshotEntry] = []
    for item in observed.entries:
        entry = observe(root / item.path, services=services)
        if entry is None or (entry.hash, entry.kind, entry.mode) != (item.hash, item.kind, item.mode):
            raise PreviewError("Content changed during snapshot")
        entries.append(replace(entry, path=item.path))
    return Snapshot(SourceIdentity("local", str(root)), tuple(entries))


def _resolutions(preview: Preview, candidate: Snapshot, resolutions: tuple[Resolution, ...]) -> tuple[Finding, ...]:
    conflicts = {c.id: c for c in preview.conflicts}
    entries = {e.path: e for e in candidate.entries}
    found: set[str] = set()
    findings: list[Finding] = []
    for resolution in resolutions:
        validate_path(resolution.path)
        conflict = conflicts.get(resolution.conflict_id)
        entry = entries.get(resolution.path)
        if conflict is None or resolution.conflict_id in found or conflict.path != resolution.path:
            raise PreviewError("Resolution must identify exactly one recorded conflict and its path")
        found.add(resolution.conflict_id)
        if (resolution.deleted and (entry is not None or resolution.hash is not None)) or (
            not resolution.deleted and (entry is None or entry.hash != resolution.hash)
        ):
            findings.append(
                Finding("resolution.stale", "error", resolution.path, None, "Resolution differs from final bytes")
            )
    for conflict in preview.conflicts:
        if conflict.id not in found:
            findings.append(Finding("resolution.missing", "error", conflict.path, None, conflict.id))
    return tuple(findings)


def plan_accept(
    roots: Roots,
    variant_id: str,
    candidate_id: str,
    *,
    review: Review | None,
    intent: tuple[str, ...],
    resolutions: tuple[Resolution, ...],
    services: Services,
) -> Result:
    try:
        update = load_preview(roots.state, candidate_id)
        if (
            update.operation != "update"
            or update.variant_ids != (variant_id,)
            or update.content_root != str(roots.content)
        ):
            raise PreviewError("Acceptance requires this variant's whole update candidate")
        ledger = load_ledger(roots.state, variant_id)
        status = read_status(roots, variant_id, services=services)
        if not status.allowed:
            return replace(status, operation="accept")
        if update.expected_generations != ((variant_id, ledger.generation),):
            raise TransactionError("preview.stale", "Accepted generation changed since update")
        for expected in update.observed:
            root = Path(expected.root)
            paths = update.owned_paths if root == roots.content else ("ledger.json", "base.tar.gz")
            if inventory(root, paths, services=services) != expected:
                raise TransactionError("preview.stale", "Maintained content or tracking changed since update")
        if {c.name for c in update.selection.skills} != {c.name for c in ledger.components}:
            raise PreviewError("Acceptance must retain the whole component selection")
        candidate = capture(SourceSpec(update.candidate_path, "local"), (), services=services)
        original = read_snapshot(Path(update.candidate_path).parent / "incoming.tar.gz")
        names: dict[str, str] = {}
        for component in update.selection.skills:
            entry = next((e for e in original.entries if e.path == component.source + "/SKILL.md"), None)
            if entry is not None:
                metadata, _ = parse_skill_metadata(entry.content, path=entry.path)
                if metadata is not None:
                    names[metadata.name] = component.name
        dependencies, mechanical = validate_candidate(candidate, update.selection, update.source_mappings, names)
        findings = list(mechanical)
        # Non-merge acquisition/adaptation errors have no file-conflict resolution
        # contract; require a new update with explicit corrected approvals.
        findings.extend(
            f for f in update.findings if f.severity == "error" and f.code.startswith(("adaptation.", "source.link"))
        )
        findings.extend(_resolutions(update, candidate, resolutions + (review.resolutions if review else ())))
        if review is not None and review.candidate_digest != candidate.digest:
            findings.append(Finding("review.stale", "error", None, None, "Review does not bind the final candidate"))
        if review is not None:
            findings.extend(replace(f, code="review." + f.code, severity="warn") for f in review.findings)
        return _prepare(
            roots,
            ledger,
            update,
            candidate,
            original_data=(Path(update.candidate_path).parent / "incoming.tar.gz").read_bytes(),
            findings=tuple(findings),
            review=review,
            resolutions=resolutions,
            intent=intent or ledger.intent,
            dependencies=dependencies,
            services=services,
        )
    except TransactionError as exc:
        return failure("accept", None, exc.code, str(exc), variant_id)
    except (ValueError, TypeError) as exc:
        return failure("accept", None, "accept.invalid", str(exc), variant_id)
    except OSError as exc:
        return failure("accept", None, "accept.io", str(exc), variant_id)


def _prepare(
    roots: Roots,
    ledger: Ledger,
    update: Preview,
    candidate: Snapshot,
    *,
    original_data: bytes,
    findings: tuple[Finding, ...],
    review: Review | None,
    resolutions: tuple[Resolution, ...],
    intent: tuple[str, ...],
    dependencies: tuple[Dependency, ...],
    services: Services,
) -> Result:
    deps = dependencies
    next_id = identifier(services.new_id())
    tracking = roots.state / "forks" / ledger.variant_id
    before = _snapshot(roots.content, update.owned_paths, services=services)
    tracking_inventory = inventory(tracking, (".",), services=services)
    next_ledger = replace(
        ledger,
        generation=ledger.generation + 1,
        source=update.source,
        components=update.selection.skills,
        mappings=update.source_mappings,
        base_digest=update.base_digest,
        files=(),
        adaptations=update.adaptations,
        intent=intent,
        unresolved=tuple(d for d in deps if d.required and not d.satisfied),
        dependencies=update.selection.dependencies,
        history=(*ledger.history, next_id),
    )
    stage = roots.state / "previews" / next_id
    stage.mkdir(parents=True)
    _write_candidate(stage / "candidate", candidate, services=services)
    next_ledger = replace(
        next_ledger, files=inventory(stage / "candidate", update.owned_paths, services=services).entries
    )
    evidence = record_bytes(replace(review, evidence_path=None) if review else None)
    data = {
        "base.tar.gz": original_data,
        "ledger.json": record_bytes(next_ledger),
        "review.json": evidence,
        "resolutions.json": record_bytes(resolutions),
    }
    if inventory(tracking, ("ledger.json", "base.tar.gz"), services=services) != update.observed[1]:
        raise PreviewError("Tracking changed during acceptance preparation")
    if inventory(roots.content, update.owned_paths, services=services) != update.observed[0]:
        raise PreviewError("Maintained content changed during acceptance preparation")
    if capture(SourceSpec(update.candidate_path, "local"), (), services=services).digest != candidate.digest:
        raise PreviewError("Candidate changed during acceptance preparation")
    observed = (
        inventory(roots.content, update.owned_paths, services=services),
        tracking_inventory,
        inventory(Path(update.candidate_path), (".",), services=services),
    )
    if before.entries != _snapshot(roots.content, update.owned_paths, services=services).entries:
        raise PreviewError("Maintained content changed during acceptance preparation")
    # Bind review file bytes at the CLI boundary; historical evidence is the
    # captured record above and never needs this machine-local file later.
    evidence_files: tuple[tuple[str, str], ...] = ()
    if review and review.evidence_path:
        path = Path(review.evidence_path)
        evidence_bytes = path.read_bytes()
        payload = json.loads(evidence_bytes)
        supplied = cast(Review, _decode(payload, Review))
        if replace(supplied, evidence_path=str(path)) != review:
            raise PreviewError("Review file changed during acceptance preparation")
        evidence_files = ((str(path), hashlib.sha256(evidence_bytes).hexdigest()),)
    preview = replace(
        update,
        id=next_id,
        operation="accept",
        expected_generations=((ledger.variant_id, ledger.generation),),
        observed=observed,
        expected_absences=(),
        artifacts=(),
        candidate_path=str(stage / "candidate"),
        candidate_digest=candidate.digest,
        operations=(),
        findings=findings,
        review_state="not_requested"
        if review is None
        else "completed_with_findings"
        if review.findings
        else "completed_without_findings",
        allowed=not any(f.severity == "error" for f in findings),
        intent=intent,
        candidate_dependencies=deps,
        evidence_files=evidence_files,
    )
    return _seal(preview, data, services=services)


def _seal(preview: Preview, data: dict[str, bytes], *, services: Services) -> Result:
    candidate = Path(preview.candidate_path)
    stage = candidate.parent
    roots = Roots(Path(preview.content_root), Path(preview.state_root))
    final = inventory(candidate, (".",), services=services)
    destinations = tuple(roots.content / p for p in preview.owned_paths)
    for entry in final.entries:
        path = roots.content / entry.path
        if not any(path == owned or owned in path.parents for owned in destinations):
            if entry.kind == "directory" and any(path in owned.parents for owned in destinations):
                continue
            raise PreviewError("Final candidate contains unowned content")
        if entry.kind == "symlink" and preview.operation != "rollback":
            raise PreviewError("Final candidate links require explicit materialization")
    artifacts = []
    for name, content in data.items():
        services.filesystem.write_exclusive(stage / name, content)
        artifacts.append(Artifact(name, hashlib.sha256(content).hexdigest()))
    changes = content_changes(preview, services=services)
    operations = tuple(
        FileOperation(
            "delete" if c.after is None else "mkdir" if c.after.kind == "directory" else "write",
            c.destination,
            None
            if c.after is None or c.after.kind == "directory"
            else "candidate/" + Path(c.destination).relative_to(roots.content).as_posix(),
            c.after.mode if c.after else 0,
        )
        for c in changes
    )
    preview = replace(
        preview,
        artifacts=tuple(artifacts),
        operations=operations,
        ancestors=ancestors(
            tuple(Path(c.destination) for c in changes),
            services=services,
            replacing=tuple(Path(c.destination) for c in changes if c.after and c.after.kind == "directory"),
        ),
    )
    services.filesystem.write_exclusive(stage / "preview.json", preview_bytes(preview))
    for artifact in (*artifacts, Artifact("preview.json", "")):
        services.filesystem.sync_file(stage / artifact.path)
    services.filesystem.sync_directory(stage)
    return Result(
        preview.operation,
        preview.variant_ids[0],
        preview.id,
        False,
        preview.allowed,
        preview.findings,
        tuple(c.destination for c in changes),
        {
            "candidate_path": preview.candidate_path,
            "candidate_digest": preview.candidate_digest,
            "review_state": preview.review_state,
            "mechanical_allowed": preview.allowed,
            "review": json.loads(data.get("review.json", b"null")),
        },
    )


def content_changes(preview: Preview, *, services: Services) -> tuple[Change, ...]:
    root = Path(preview.content_root)
    before = _snapshot(root, preview.owned_paths, services=services)
    expected = next(i for i in preview.observed if i.root == str(root))
    if tuple(InventoryEntry(e.path, e.kind, e.mode, e.hash) for e in before.entries) != expected.entries:
        raise PreviewError("Maintained inventory differs from authorized before-state")
    complete = _snapshot(Path(preview.candidate_path), (".",), services=services)
    if complete.digest != preview.candidate_digest:
        raise PreviewError("Candidate differs from authorized final bytes")
    after = replace(
        complete,
        entries=tuple(
            e for e in complete.entries if any(e.path == p or e.path.startswith(p + "/") for p in preview.owned_paths)
        ),
    )
    left, right = ({e.path: replace(e, path=str(root / e.path)) for e in s.entries} for s in (before, after))
    changes = [Change(str(root / path), left.get(path), right.get(path)) for path in left.keys() | right.keys()]
    # Keep unchanged entries in the journal so final inventory verification and
    # recovery see every owned file, including local additions.
    return tuple(
        sorted(
            changes,
            key=lambda c: (
                0 if c.after is None else 1,
                -len(Path(c.destination).parts) if c.after is None else len(Path(c.destination).parts),
                c.destination,
            ),
        )
    )


def accepted_changes(preview: Preview, *, services: Services) -> tuple[Change, ...]:
    stage = Path(preview.candidate_path).parent
    state = Path(preview.state_root)
    variant = preview.variant_ids[0]
    tracking = state / "forks" / variant
    old_history = _attestation(state, variant)
    artifacts = {}
    for artifact in preview.artifacts:
        content = (stage / artifact.path).read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.digest:
            raise PreviewError("Immutable acceptance artifact changed before promotion")
        artifacts[artifact.path] = content
    ledger = cast(Ledger, _decode(json.loads(artifacts["ledger.json"]), Ledger))
    changes = list(content_changes(preview, services=services))
    before_content = tuple(
        replace(c.before, path=Path(c.destination).relative_to(Path(preview.content_root)).as_posix())
        for c in changes
        if c.before is not None
    )
    before_tracking = []
    for entry in old_history.tracking:
        current = observe(tracking / entry.path, services=services)
        if current is None or replace(current, path=entry.path) != entry:
            raise PreviewError("Tracking changed before snapshot")
        before_tracking.append(entry)
    for name in ("base.tar.gz", "ledger.json"):
        path = tracking / name
        changes.append(
            Change(
                str(path),
                observe(path, services=services),
                SnapshotEntry(str(path), artifacts[name], "file", 0o600),
            )
        )
    images = {e.path: e for e in old_history.tracking}
    for c in changes:
        if tracking in Path(c.destination).parents and c.after is not None:
            name = Path(c.destination).relative_to(tracking).as_posix()
            images[name] = replace(c.after, path=name)
    reversed_event = old_history.id if preview.operation == "rollback" else None
    history = History(
        1,
        preview.id,
        variant,
        ledger,
        tuple(images.values()),
        preview.operation,
        before_content,
        tuple(before_tracking),
        reversed_event,
        cast(Review | None, _decode(json.loads(artifacts["review.json"]), Review | None))
        if preview.operation == "accept"
        else None,
        cast(
            tuple[Resolution, ...],
            _decode(json.loads(artifacts["resolutions.json"]), tuple[Resolution, ...]),
        )
        if preview.operation == "accept"
        else (),
    )
    path = tracking / "history" / (preview.id + ".json")
    changes.append(Change(str(path), None, SnapshotEntry(str(path), sealed_bytes(history), "file", 0o600)))
    return tuple(changes)


def plan_content_rollback(roots: Roots, variant_id: str, *, services: Services) -> Result:
    try:
        status = read_status(roots, variant_id, services=services)
        if not status.allowed:
            return replace(status, operation="rollback")
        ledger = load_ledger(roots.state, variant_id)
        history = _attestation(roots.state, variant_id)
        if history.operation != "accept":
            raise TransactionError("rollback.unavailable", "Latest event is not an unreversed acceptance")
        if inventory(roots.content, owned_paths(ledger), services=services).entries != history.ledger.files:
            raise TransactionError("rollback.modified", "Current owned content differs from accepted after-state")
        images = {e.path: e for e in history.before_tracking}
        before_ledger = cast(Ledger, _decode(json.loads(images["ledger.json"].content), Ledger))
        next_id = identifier(services.new_id())
        restored = replace(
            before_ledger,
            generation=ledger.generation + 1,
            content_root=ledger.content_root,
            history=(*ledger.history, next_id),
        )
        stage = roots.state / "previews" / next_id
        stage.mkdir(parents=True)
        snapshot = Snapshot(SourceIdentity("local", str(roots.content)), history.before_content)
        _write_candidate(stage / "candidate", snapshot, services=services)
        owned = tuple(dict.fromkeys((*owned_paths(ledger), *owned_paths(restored))))
        tracking = roots.state / "forks" / variant_id
        preview = Preview(
            1,
            next_id,
            "rollback",
            (variant_id,),
            ((variant_id, ledger.generation),),
            (inventory(roots.content, owned, services=services), inventory(tracking, (".",), services=services)),
            (),
            (),
            (),
            str(stage / "candidate"),
            snapshot.digest,
            (),
            (),
            "not_requested",
            True,
            str(roots.content),
            str(roots.state),
            owned,
            Selection(restored.components, (), (), restored.adaptations, ()),
            restored.source,
            restored.base_digest or "",
            restored.adaptations,
            (),
            restored.intent,
            source_mappings=restored.mappings,
        )
        return _seal(
            preview,
            {"ledger.json": record_bytes(restored), "base.tar.gz": images["base.tar.gz"].content},
            services=services,
        )
    except TransactionError as exc:
        return failure("rollback", None, exc.code, str(exc), variant_id)
    except (OSError, ValueError, KeyError) as exc:
        return failure("rollback", None, "rollback.invalid", str(exc), variant_id)
