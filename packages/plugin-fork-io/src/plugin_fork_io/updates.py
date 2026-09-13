"""Prepare editable whole-variant candidates without advancing accepted provenance."""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .adaptations import name_adaptation, permission_mode, relocate, replay
from .fork import _mapped, _overlap, validate_candidate
from .git import GitError
from .machine import Services
from .merge import merge_entry
from .previews import PreviewError, _encode, ancestors, preview_bytes
from .records import (
    Adaptation,
    Artifact,
    Conflict,
    Dependency,
    Finding,
    JsonValue,
    Ledger,
    Preview,
    Result,
    Roots,
    Selection,
    Snapshot,
    SnapshotEntry,
    SourceIdentity,
    SourceLink,
    SourceMapping,
    SourceSpec,
)
from .references import scan_host_metadata, scan_markdown
from .snapshots import capture, materialize_link, read_snapshot, validate_path, write_snapshot
from .status import read_status
from .store import identifier, inventory, load_ledger, observe, owned_paths, record_bytes
from .validation import parse_skill_metadata


def _renamed(path: str, mappings: Mapping[str, str]) -> str:
    return _mapped(path, tuple(SourceMapping(a, b) for a, b in mappings.items())) or path


def _selection(
    ledger: Ledger, selected: Selection | None, renames: Mapping[str, str]
) -> tuple[Selection, tuple[SourceMapping, ...]]:
    for source, destination in renames.items():
        validate_path(source)
        validate_path(destination)
        if not any(source == m.source or source.startswith(m.source + "/") for m in ledger.mappings):
            raise PreviewError("Explicit mapping must name a recorded selected source path")
    components = tuple(replace(c, source=_renamed(c.source, renames)) for c in ledger.components)
    resources = tuple(
        replace(m, source=_renamed(m.source, renames))
        for m in ledger.mappings
        if m.source not in {c.source for c in ledger.components}
    )
    recorded_edits = tuple(replace(e, path=_renamed(e.path, renames)) for e in ledger.adaptations)
    if selected is None:
        selected = Selection(components, resources, ledger.dependencies, (), ())
    encoded = _encode(selected)
    assert isinstance(encoded, dict)
    Selection.from_json(encoded)
    if set(selected.skills) != set(components) or len(selected.skills) != len(components):
        raise PreviewError(
            "Update must retain every existing component and its local name; use explicit source mappings"
        )
    selected = replace(selected, skills=components)
    for resource in resources:
        if resource not in selected.resources:
            raise PreviewError("Update must retain every existing resource mapping")
    # Explicit reviewed edits replace matching recorded edit identities, never unrelated edits.
    edits = (
        tuple(
            e
            for e in recorded_edits
            if not any((e.path, e.kind, e.start) == (n.path, n.kind, n.start) for n in selected.adaptations)
        )
        + selected.adaptations
    )
    links = tuple(
        dict.fromkeys(
            (
                *selected.source_links,
                *(SourceLink(e.path, "materialize") for e in recorded_edits if e.kind == "materialize-link"),
            )
        )
    )
    selected = replace(selected, adaptations=edits, source_links=links)
    skill_mappings = tuple(
        SourceMapping(c.source, next(m.destination for m in ledger.mappings if m.source == old.source))
        for c, old in zip(components, ledger.components, strict=True)
    )
    result = skill_mappings + selected.resources
    # File-level moves align with their previous local coordinate; no inferred rename joins.
    for source, incoming in renames.items():
        if source not in {m.source for m in ledger.mappings}:
            local_destination = _mapped(source, ledger.mappings)
            assert local_destination is not None
            result += (SourceMapping(incoming, local_destination),)
    transformed_recorded = tuple(replace(m, source=_renamed(m.source, renames)) for m in ledger.mappings)
    for index, mapping in enumerate(result):
        for other in result[:index]:
            nested_explicit = (
                mapping.source in renames.values() or mapping in transformed_recorded
            ) and mapping.destination.startswith(other.destination + "/")
            if (
                _overlap(mapping.destination, other.destination) or _overlap(mapping.source, other.source)
            ) and not nested_explicit:
                raise PreviewError("Update mappings collide")
    return selected, result


def _adapt(
    original: Snapshot,
    mappings: tuple[SourceMapping, ...],
    edits: tuple[Adaptation, ...],
    links: tuple[SourceLink, ...],
    *,
    relocating: bool,
) -> tuple[Snapshot, tuple[Adaptation, ...], tuple[Finding, ...], Snapshot]:
    active: list[SnapshotEntry] = []
    evidence = {
        e.path: e
        for e in original.entries
        if _mapped(e.path, mappings) is not None
        or PurePosixPath(e.path).name.lower().startswith(("license", "copying", "notice"))
        or e.path in {".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json"}
    }
    findings: list[Finding] = []
    retained: list[Adaptation] = []
    for entry in original.entries:
        if _mapped(entry.path, mappings) is None:
            continue
        if entry.kind == "symlink":
            if entry.path not in {link.path for link in links}:
                findings.append(
                    Finding(
                        "source.link-unapproved",
                        "error",
                        entry.path,
                        None,
                        "Source link needs explicit materialization approval",
                    )
                )
                continue
            link_edits = tuple(e for e in edits if e.path == entry.path and e.kind == "materialize-link")
            if link_edits and any(e.expected != entry.content for e in link_edits):
                findings.append(
                    Finding(
                        "adaptation.ambiguous",
                        "error",
                        entry.path,
                        None,
                        "Source-link target differs from approved materialization",
                    )
                )
                continue
            paths: set[str] = set()
            active.extend(materialize_link(original, entry.path, evidence_paths=paths))
            evidence.update({e.path: e for e in original.entries if e.path in paths})
            retained.append(
                Adaptation(
                    "materialize-link",
                    entry.path,
                    0,
                    len(entry.content),
                    entry.content,
                    b"",
                    "Explicit contained source-link materialization",
                )
            )
        else:
            active.append(entry)
    mapped: list[SnapshotEntry] = []
    for entry in active:
        path = _mapped(entry.path, mappings)
        assert path is not None
        path_edits = tuple(e for e in edits if e.path == entry.path and e.kind != "materialize-link")
        if relocating:
            path_edits, problems = relocate(entry.content, path_edits)
            findings.extend(problems)
        content, problems = replay(entry.content, path_edits)
        findings.extend(problems)
        mode, problems = permission_mode(entry.mode, path_edits)
        findings.extend(problems)
        if entry.mode & 0o7000 and not any(e.kind == "permission" for e in path_edits):
            path_edits += (
                Adaptation(
                    "permission",
                    entry.path,
                    0,
                    len(oct(entry.mode).encode()),
                    oct(entry.mode).encode(),
                    oct(mode).encode(),
                    "Remove special permission bits",
                ),
            )
        retained.extend(path_edits)
        mapped.append(replace(entry, path=path, content=content, mode=mode))
    mapped_paths = [e.path for e in mapped]
    if len({p.casefold() for p in mapped_paths}) != len(mapped_paths):
        raise PreviewError("Mapped incoming paths collide")
    return (
        Snapshot(original.source, tuple(sorted(mapped, key=lambda e: e.path))),
        tuple(dict.fromkeys(retained)),
        tuple(dict.fromkeys(findings)),
        Snapshot(original.source, tuple(sorted(evidence.values(), key=lambda e: e.path))),
    )


def _diff(before: Snapshot, after: Snapshot) -> list[JsonValue]:
    left, right = ({e.path: e for e in snapshot.entries} for snapshot in (before, after))
    changes: list[JsonValue] = []
    for path in sorted(left.keys() | right.keys()):
        a, b = left.get(path), right.get(path)
        if a == b:
            continue
        text: str | None = None
        if all(e is None or (e.kind == "file" and b"\0" not in e.content) for e in (a, b)):
            with suppress(UnicodeError):
                text = "".join(
                    difflib.unified_diff(
                        (a.content if a else b"").decode("utf-8").splitlines(keepends=True),
                        (b.content if b else b"").decode("utf-8").splitlines(keepends=True),
                        fromfile="before/" + path,
                        tofile="after/" + path,
                    )
                )
        changes.append(
            {
                "path": path,
                "before_hash": a.hash if a else None,
                "after_hash": b.hash if b else None,
                "before_mode": a.mode if a else None,
                "after_mode": b.mode if b else None,
                "before_kind": a.kind if a else None,
                "after_kind": b.kind if b else None,
                "text": text,
            }
        )
    return changes


def _write_candidate(candidate: Path, snapshot: Snapshot, *, services: Services) -> None:
    candidate.mkdir()
    for entry in snapshot.entries:
        path = candidate / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if entry.kind == "directory":
            path.mkdir(exist_ok=True)
        elif entry.kind == "symlink":
            services.filesystem.link(entry.content.decode("utf-8"), path)
        else:
            services.filesystem.write_exclusive(path, entry.content)
            services.filesystem.chmod(path, entry.mode)
    for entry in reversed(snapshot.entries):
        if entry.kind == "directory":
            services.filesystem.chmod(candidate / entry.path, entry.mode)


def plan_update(
    roots: Roots,
    variant_id: str,
    incoming: SourceSpec,
    *,
    selection: Selection | None,
    mappings: Mapping[str, str],
    services: Services,
) -> Result:
    try:
        return _prepare(roots, variant_id, incoming, selection, mappings, services)
    except (ValueError, OSError) as exc:
        code = exc.code if isinstance(exc, GitError) else "update.io" if isinstance(exc, OSError) else "update.refused"
        return Result("update", variant_id, None, False, False, (Finding(code, "error", None, None, str(exc)),), (), {})


def _prepare(
    roots: Roots,
    variant_id: str,
    incoming: SourceSpec,
    selected: Selection | None,
    renames: Mapping[str, str],
    services: Services,
) -> Result:
    identifier(variant_id)
    if not roots.content.is_absolute() or not roots.state.is_absolute():
        raise PreviewError("Update roots must be resolved absolute paths")
    ledger = load_ledger(roots.state, variant_id)
    if ledger.origin != "known":
        return Result(
            "update",
            variant_id,
            None,
            False,
            False,
            (Finding("origin.unknown", "error", None, None, "Reconcile original-base provenance before updating"),),
            (),
            {},
        )
    status = read_status(roots, variant_id, services=services)
    if not status.allowed:
        return replace(status, operation="update")
    chosen, incoming_mappings = _selection(ledger, selected, renames)
    owned = tuple(dict.fromkeys((*owned_paths(ledger), *(m.destination for m in chosen.resources))))
    observed = inventory(roots.content, owned, services=services)
    local_entries: list[SnapshotEntry] = []
    for item in observed.entries:
        entry = observe(roots.content / item.path, services=services)
        if entry is None or (entry.kind, entry.mode, entry.hash) != (item.kind, item.mode, item.hash):
            raise PreviewError("Maintained content changed while inventorying")
        local_entries.append(replace(entry, path=item.path))
    local = Snapshot(SourceIdentity("local", str(roots.content)), tuple(local_entries))
    tracking = roots.state / "forks" / variant_id
    base_path = tracking / "base.tar.gz"
    tracking_before = inventory(tracking, ("ledger.json", "base.tar.gz"), services=services)
    if (tracking / "ledger.json").read_bytes() != record_bytes(ledger):
        raise PreviewError("Tracking changed while preparing update")
    original_base = read_snapshot(base_path)
    if original_base.digest != ledger.base_digest:
        raise PreviewError("Original base differs from accepted digest")
    original_incoming = capture(incoming, (), services=services)
    incoming_paths = {e.path for e in original_incoming.entries}
    original_paths = {e.path for e in original_base.entries}
    for old_path, new_path in renames.items():
        if old_path not in original_paths or new_path not in incoming_paths:
            raise PreviewError("Explicit source mapping must join existing original and incoming paths")
    prior_resources = {_renamed(m.source, renames) for m in ledger.mappings}
    if any(m.source not in prior_resources and m.source not in incoming_paths for m in chosen.resources):
        raise PreviewError("New selected resource is missing from incoming source")
    if selected is not None and any(
        _mapped(edit.path, incoming_mappings) is None or edit.path not in incoming_paths
        for edit in selected.adaptations
    ):
        raise PreviewError("Explicit adaptation targets missing or unselected incoming content")
    if selected is not None and any(
        _mapped(link.path, incoming_mappings) is None
        or not any(e.path == link.path and e.kind == "symlink" for e in original_incoming.entries)
        for link in selected.source_links
    ):
        raise PreviewError("Source-link approval targets missing or unselected incoming links")
    base, _, base_findings, _ = _adapt(
        original_base,
        ledger.mappings,
        ledger.adaptations,
        tuple(SourceLink(e.path, "materialize") for e in ledger.adaptations if e.kind == "materialize-link"),
        relocating=False,
    )
    incoming_edits = list(chosen.adaptations)
    names: dict[str, str] = {}
    for component in chosen.skills:
        skill_path = component.source + "/SKILL.md"
        entry = next((e for e in original_incoming.entries if e.path == skill_path and e.kind == "file"), None)
        if entry is not None:
            metadata, _ = parse_skill_metadata(entry.content, path=skill_path)
            if metadata is not None:
                if metadata.name in names:
                    raise PreviewError("Selected skills have ambiguous incoming names")
                names[metadata.name] = component.name
        if entry is not None and not any(e.path == skill_path and e.kind == "skill-name" for e in incoming_edits):
            edit, problems = name_adaptation(entry.content, skill_path, component.name)
            base_findings += problems
            if edit is not None and edit.expected != edit.replacement:
                incoming_edits.append(edit)
    adapted, adaptations, problems, incoming_base = _adapt(
        original_incoming, incoming_mappings, tuple(incoming_edits), chosen.source_links, relocating=True
    )
    findings = [*status.findings, *base_findings, *problems]
    # Observe original requirements independently of candidate validity.
    dependencies: list[Dependency] = list(chosen.dependencies)
    for entry in incoming_base.entries:
        if entry.kind == "file" and _mapped(entry.path, incoming_mappings) is not None:
            if entry.path.endswith(".md"):
                found, errors = scan_markdown(entry.content, entry.path)
            elif entry.path.endswith("agents/openai.yaml"):
                found, errors = scan_host_metadata(entry.content, entry.path)
            else:
                continue
            dependencies.extend(found)
            findings.extend(replace(e, code="source." + e.code, severity="warn") for e in errors)
    b, local_by_path, n = ({e.path: e for e in snapshot.entries} for snapshot in (base, local, adapted))
    merged: list[SnapshotEntry] = []
    conflicts: list[Conflict] = []
    for path in sorted(b.keys() | local_by_path.keys() | n.keys()):
        entry, merge_conflicts = merge_entry(path, b.get(path), local_by_path.get(path), n.get(path), services=services)
        conflicts.extend(merge_conflicts)
        if entry is not None:
            merged.append(entry)
    # Parent type changes cannot leave a child beneath a file/link. Keep the local
    # subtree for inspection and require an explicit resolution of the parent.
    for entry in tuple(merged):
        if entry.kind != "directory" and any(other.path.startswith(entry.path + "/") for other in merged):
            _, merge_conflicts = merge_entry(
                entry.path, b.get(entry.path), local_by_path.get(entry.path), n.get(entry.path), services=services
            )
            if not merge_conflicts:
                digest = hashlib.sha256(("tree:" + entry.path).encode()).hexdigest()
                merge_conflicts = (
                    Conflict(
                        digest,
                        entry.path,
                        "merge.kind",
                        b[entry.path].hash if entry.path in b else None,
                        local_by_path[entry.path].hash if entry.path in local_by_path else None,
                        n[entry.path].hash if entry.path in n else None,
                        b[entry.path].mode if entry.path in b else None,
                        local_by_path[entry.path].mode if entry.path in local_by_path else None,
                        n[entry.path].mode if entry.path in n else None,
                        b[entry.path].kind if entry.path in b else None,
                        local_by_path[entry.path].kind if entry.path in local_by_path else None,
                        n[entry.path].kind if entry.path in n else None,
                        "Explicit final path/hash or deletion required",
                    ),
                )
            conflicts.extend(e for e in merge_conflicts if e not in conflicts)
            merged = [e for e in merged if e.path != entry.path and not e.path.startswith(entry.path + "/")]
            merged.extend(e for e in local.entries if e.path == entry.path or e.path.startswith(entry.path + "/"))
    # Retained local-only children keep their original parent permission metadata,
    # even when upstream removed the directory's last originally owned child.
    merged_paths = {e.path for e in merged}
    for child in tuple(merged):
        for parent in PurePosixPath(child.path).parents:
            parent_path = str(parent)
            if parent_path == "." or parent_path in merged_paths:
                continue
            parent_entry = local_by_path.get(parent_path) or n.get(parent_path) or b.get(parent_path)
            if parent_entry is not None and parent_entry.kind == "directory":
                merged.append(parent_entry)
                merged_paths.add(parent_path)
    candidate_snapshot = Snapshot(local.source, tuple(sorted(merged, key=lambda e: e.path)))
    candidate_dependencies, validation = validate_candidate(candidate_snapshot, chosen, incoming_mappings, names)
    findings.extend(validation)
    findings.extend(Finding(c.code, "error", c.path, None, c.resolution) for c in conflicts)
    # Similar content is a suggestion only; it never changes alignment.
    deleted = [
        e
        for e in original_base.entries
        if e.kind == "file" and _renamed(e.path, renames) not in {n.path for n in original_incoming.entries}
    ]
    for old in deleted:
        for new in original_incoming.entries:
            if new.kind == "file" and new.hash == old.hash and new.path != old.path:
                findings.append(
                    Finding(
                        "mapping.rename-suggested",
                        "warn",
                        old.path,
                        None,
                        f"Possible source rename to {new.path}; provide an explicit mapping",
                    )
                )
    allowed = not any(f.severity == "error" for f in findings)
    preview_id = identifier(services.new_id())
    stage = roots.state / "previews" / preview_id
    ancestors((stage / "preview.json", *(roots.content / p for p in owned)), services=services)
    stage.mkdir(parents=True, exist_ok=False)
    candidate = stage / "candidate"
    _write_candidate(candidate, candidate_snapshot, services=services)
    # The old archive is copied byte-for-byte; incoming remains original/unadapted.
    services.filesystem.write_exclusive(stage / "base.tar.gz", base_path.read_bytes())
    write_snapshot(incoming_base, stage / "incoming.tar.gz")
    write_snapshot(local, stage / "local.tar.gz")
    upstream_diff = _diff(original_base, incoming_base)
    candidate_diff = _diff(local, candidate_snapshot)
    comparisons: dict[str, JsonValue] = {
        "upstream_diff": upstream_diff,
        "candidate_diff": candidate_diff,
        "mappings": [{"source": m.source, "destination": m.destination} for m in incoming_mappings],
    }
    # Durable comparisons are immutable evidence alongside the typed preview.
    import json

    services.filesystem.write_exclusive(
        stage / "comparisons.json", json.dumps(comparisons, sort_keys=True).encode("utf-8")
    )
    artifacts = tuple(
        Artifact(name, hashlib.sha256((stage / name).read_bytes()).hexdigest())
        for name in ("base.tar.gz", "incoming.tar.gz", "local.tar.gz", "comparisons.json")
    )
    for artifact in artifacts:
        services.filesystem.sync_file(stage / artifact.path)
    staged = capture(SourceSpec(str(candidate), "local"), (), services=services)
    if inventory(tracking, ("ledger.json", "base.tar.gz"), services=services) != tracking_before:
        raise PreviewError("Tracking changed while preparing update")
    preview = Preview(
        1,
        preview_id,
        "update",
        (variant_id,),
        ((variant_id, ledger.generation),),
        (observed, tracking_before),
        tuple(str(roots.content / p) for p in owned if not any(e.path == p for e in observed.entries)),
        ancestors((stage / "preview.json", *(roots.content / p for p in owned)), services=services),
        artifacts,
        str(candidate),
        staged.digest,
        (),
        tuple(findings),
        "not_requested",
        allowed,
        str(roots.content),
        str(roots.state),
        owned,
        replace(chosen, adaptations=adaptations),
        incoming_base.source,
        incoming_base.digest,
        adaptations,
        tuple(dependencies),
        ledger.intent,
        candidate_dependencies,
        tuple(conflicts),
        incoming_mappings,
    )
    services.filesystem.write_exclusive(stage / "preview.json", preview_bytes(preview))
    return Result(
        "update",
        variant_id,
        preview_id,
        False,
        allowed,
        tuple(findings),
        tuple(str(roots.content / p) for p in owned),
        {
            "candidate_path": str(candidate),
            "preview_path": str(stage / "preview.json"),
            "upstream_diff": upstream_diff,
            "candidate_diff": candidate_diff,
            "conflicts": _encode(tuple(conflicts)),
            "text_conflicts": _encode(tuple(c for c in conflicts if c.code == "merge.text")),
            "behavioral_review": {"state": preview.review_state},
            "mappings": comparisons["mappings"],
        },
    )
