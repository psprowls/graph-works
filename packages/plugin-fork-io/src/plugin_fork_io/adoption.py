"""Explicit provenance establishment without rewriting existing content."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .config import portable_content_root
from .fork import _mapped, _overlap
from .git import GitError
from .machine import Services
from .previews import PreviewError, _decode, _encode, ancestors, preview_bytes
from .records import (
    Artifact,
    Component,
    JsonValue,
    Ledger,
    Preview,
    Result,
    Roots,
    Selection,
    Snapshot,
    SourceIdentity,
    SourceMapping,
    SourceSpec,
)
from .snapshots import capture, validate_path, write_snapshot
from .store import identifier, inventory, load_ledger, record_bytes
from .transactions import failure
from .validation import parse_skill_metadata


def plan_adopt(
    roots: Roots,
    selection: Selection,
    *,
    base: SourceSpec | None,
    evidence: Mapping[str, JsonValue],
    services: Services,
    variant_id: str | None = None,
    intent: tuple[str, ...] | None = None,
) -> Result:
    try:
        return _prepare(roots, selection, base, evidence, services, variant_id, intent)
    except GitError as exc:
        return failure("adopt", None, exc.code, str(exc), variant_id)
    except ValueError as exc:
        return failure("adopt", None, "adopt.refused", str(exc), variant_id)
    except OSError as exc:
        return failure("adopt", None, "adopt.io", str(exc), variant_id)


def _prepare(
    roots: Roots,
    selection: Selection,
    base: SourceSpec | None,
    evidence: Mapping[str, JsonValue],
    services: Services,
    variant_id: str | None,
    intent: tuple[str, ...] | None,
) -> Result:
    if not roots.content.is_absolute() or not roots.state.is_absolute():
        raise PreviewError("Adoption roots must be absolute")
    if roots.content == roots.state or roots.content in roots.state.parents or roots.state in roots.content.parents:
        raise PreviewError("Content and tracking roots must be separate")
    encoded = _encode(selection)
    assert isinstance(encoded, dict)
    Selection.from_json(encoded)
    if not selection.skills or selection.adaptations or selection.source_links:
        raise PreviewError("Adoption requires existing skills and does not apply adaptations or source links")
    owned = tuple(c.source for c in selection.skills) + tuple(m.destination for m in selection.resources)
    for index, path in enumerate(owned):
        validate_path(path)
        if any(_overlap(path, other) for other in owned[:index]):
            raise PreviewError("Selected ownership overlaps")
    ancestors(tuple(roots.content / p for p in owned), services=services)
    observed = inventory(roots.content, owned, services=services)
    current = capture(SourceSpec(str(roots.content), "local"), owned, services=services)
    entries = {e.path: e for e in current.entries}
    for component in selection.skills:
        skill = entries.get(component.source + "/SKILL.md")
        if skill is None or skill.kind != "file":
            raise PreviewError("Selected existing skill must have a regular SKILL.md")
        metadata, findings = parse_skill_metadata(skill.content, path=skill.path)
        if (
            metadata is None
            or any(f.severity == "error" for f in findings)
            or metadata.name != component.name
            or Path(component.source).name != component.name
        ):
            raise PreviewError("Existing skill metadata and selected folder name must agree")
    if any(path not in entries for path in owned):
        raise PreviewError("Selected existing resource is missing")
    previous = load_ledger(roots.state, variant_id) if variant_id else None
    if previous:
        if base is None or previous.origin != "uncertain":
            raise PreviewError("Reconciliation requires an uncertain variant and explicit original base")
        if (
            previous.content_root is not None
            and (roots.state / previous.content_root).resolve() != roots.content.resolve()
        ):
            raise PreviewError("Explicit roots disagree with existing ownership")
        if tuple(m.destination for m in previous.mappings) != owned:
            raise PreviewError("Reconciliation must preserve the whole existing selection")
        from .status import read_status

        status = read_status(roots, previous.variant_id, services=services)
        if not status.allowed:
            raise PreviewError("Existing tracking must validate before reconciliation")
    mappings = tuple(SourceMapping(p, p) for p in owned)
    original: Snapshot | None = None
    if base is not None:
        if set(evidence) - {"digest", "revision", "declaration", "mappings"}:
            raise PreviewError("Unknown original evidence fields")
        declaration = evidence.get("declaration")
        if (
            not isinstance(declaration, str)
            or not declaration.strip()
            or "mappings" not in evidence
            or not (evidence.get("digest") or evidence.get("revision"))
        ):
            raise PreviewError("Known adoption requires original hash/revision, mappings and a user declaration")
        mappings = cast(tuple[SourceMapping, ...], _decode(evidence["mappings"], tuple[SourceMapping, ...]))
        if tuple(m.destination for m in mappings) != owned:
            raise PreviewError("Original mappings must cover the complete existing selection in selection order")
        for index, mapping in enumerate(mappings):
            validate_path(mapping.source)
            validate_path(mapping.destination)
            if any(_overlap(mapping.source, other.source) for other in mappings[:index]):
                raise PreviewError("Original mappings overlap")
        entire = capture(base, (), services=services)
        if evidence.get("digest") is not None and evidence["digest"] != entire.digest:
            raise PreviewError("Original snapshot hash does not match supplied evidence")
        if evidence.get("revision") is not None and evidence["revision"] != entire.source.resolved_commit:
            raise PreviewError("Original resolved revision does not match supplied evidence")
        if any(m.source not in {e.path for e in entire.entries} for m in mappings):
            raise PreviewError("Original mapped path is missing")
        original = Snapshot(
            entire.source,
            tuple(
                e
                for e in entire.entries
                if _mapped(e.path, mappings) is not None
                or Path(e.path).name.lower().startswith(("license", "copying", "notice"))
                or e.path in {".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json"}
            ),
        )
    elif evidence:
        raise PreviewError("Original evidence requires --base; current content is only an observation")
    preview_id = identifier(services.new_id())
    variant = identifier(variant_id or services.new_id())
    stage = roots.state / "previews" / preview_id
    stage.mkdir(parents=True, exist_ok=False)
    candidate = stage / "candidate"
    candidate.mkdir()
    write_snapshot(current, stage / "observation.tar.gz")
    files = ["observation.tar.gz"]
    if original:
        write_snapshot(original, stage / "base.tar.gz")
        files.append("base.tar.gz")
    evidence_bytes = json.dumps(dict(evidence), sort_keys=True).encode("utf-8")
    services.filesystem.write_exclusive(stage / "evidence.json", evidence_bytes)
    files.append("evidence.json")
    components = tuple(
        Component(next(m.source for m in mappings if m.destination == c.source), c.name) for c in selection.skills
    )
    ledger = Ledger(
        1,
        variant,
        previous.generation + 1 if previous else 1,
        previous.content_root
        if previous
        else portable_content_root(roots.content, roots.state, platform=services.platform),
        original.source
        if original
        else SourceIdentity("local", "", label="Unknown original; current content is an observation"),
        "known" if original else "uncertain",
        components,
        mappings,
        original.digest if original else None,
        observed.entries,
        (),
        intent if intent is not None else previous.intent if previous else (),
        # Establishing an original base is not dependency-resolution evidence.
        # Preserve previous blockers even if an explicit selection omits them.
        tuple(
            dict.fromkeys(
                (
                    *(previous.unresolved if previous else ()),
                    *(d for d in selection.dependencies if d.required and not d.satisfied),
                )
            )
        ),
        (*previous.history, preview_id) if previous else (preview_id,),
        tuple(dict.fromkeys((*(previous.dependencies if previous else ()), *selection.dependencies))),
    )
    services.filesystem.write_exclusive(stage / "ledger.json", record_bytes(ledger))
    files.append("ledger.json")
    artifacts = tuple(Artifact(name, hashlib.sha256((stage / name).read_bytes()).hexdigest()) for name in files)
    tracking = roots.state / "forks" / variant
    preview = Preview(
        1,
        preview_id,
        "adopt",
        (variant,),
        ((variant, previous.generation if previous else None),),
        (observed, inventory(tracking, (".",), services=services)),
        () if previous else (str(tracking),),
        ancestors(
            (tracking / "ledger.json", stage / "preview.json", *(roots.content / p for p in owned)), services=services
        ),
        artifacts,
        str(candidate),
        capture(SourceSpec(str(candidate), "local"), (), services=services).digest,
        (),
        (),
        "not_requested",
        True,
        str(roots.content),
        str(roots.state),
        owned,
        selection,
        ledger.source,
        ledger.base_digest or "",
        (),
        (),
        ledger.intent,
    )
    from .transactions import revalidate

    revalidate(preview, services=services)
    services.filesystem.write_exclusive(stage / "preview.json", preview_bytes(preview))
    deltas: list[JsonValue] = []
    if original:
        mapped = {_mapped(e.path, mappings): e for e in original.entries if _mapped(e.path, mappings) is not None}
        for delta_path in sorted(set(mapped) | set(entries), key=str):
            before, after = mapped.get(delta_path), entries.get(delta_path or "")
            if (
                before is None
                or after is None
                or (before.kind, before.mode, before.hash) != (after.kind, after.mode, after.hash)
            ):
                deltas.append(delta_path)
    return Result(
        "adopt",
        variant,
        preview_id,
        False,
        True,
        (),
        tuple(str(tracking / name) for name in files),
        {
            "origin": ledger.origin,
            "base_digest": ledger.base_digest,
            "local_deltas": deltas,
            "declaration": evidence.get("declaration"),
            "preview_path": str(stage / "preview.json"),
        },
    )
