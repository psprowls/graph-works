"""Selective extraction into inactive staging; no accepted-state application."""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .adaptations import name_adaptation, permission_mode, replay, resource_replacement
from .git import GitError
from .machine import Services
from .previews import PreviewError, ancestors, preview_bytes
from .records import (
    Adaptation,
    Artifact,
    Dependency,
    DiscoveryContext,
    FileOperation,
    Finding,
    Inventory,
    InventoryEntry,
    Preview,
    Result,
    Roots,
    Selection,
    Snapshot,
    SnapshotEntry,
    SourceMapping,
    SourceSpec,
)
from .references import scan_host_metadata, scan_markdown
from .snapshots import SnapshotError, capture, materialize_link, validate_path, write_snapshot
from .validation import parse_skill_metadata


def _mapped(path: str, mappings: tuple[SourceMapping, ...]) -> str | None:
    for mapping in sorted(mappings, key=lambda m: len(m.source), reverse=True):
        if path == mapping.source or path.startswith(mapping.source + "/"):
            return mapping.destination + path[len(mapping.source) :]
    return None


def _overlap(left: str, right: str) -> bool:
    left, right = left.casefold(), right.casefold()
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def plan_fork(
    source: SourceSpec,
    selection: Selection,
    roots: Roots,
    *,
    intent: tuple[str, ...],
    services: Services,
    discovery: DiscoveryContext | None = None,
) -> Result:
    findings: list[Finding] = []
    try:
        return _prepare(source, selection, roots, intent, services, discovery, findings)
    except (SnapshotError, PreviewError, GitError) as exc:
        code = exc.code if isinstance(exc, GitError) else "fork.refused"
        return Result(
            "fork", None, None, False, False, (*findings, Finding(code, "error", None, None, str(exc))), (), {}
        )


def _prepare(
    source: SourceSpec,
    selection: Selection,
    roots: Roots,
    intent: tuple[str, ...],
    services: Services,
    discovery: DiscoveryContext | None,
    findings: list[Finding],
) -> Result:
    if not roots.content.is_absolute() or not roots.state.is_absolute():
        raise PreviewError("Fork roots must be resolved absolute paths")
    if roots.content == roots.state or roots.content in roots.state.parents or roots.state in roots.content.parents:
        raise PreviewError("Content and tracking roots must be separate")
    if not selection.skills:
        raise PreviewError("Select at least one skill")
    for dependency in selection.dependencies:
        if dependency.evidence not in {"user", "agent"}:
            raise PreviewError("Explicit dependencies must identify user or agent evidence")
    entire = capture(source, (), services=services)
    entries = {entry.path: entry for entry in entire.entries}
    mappings = tuple(SourceMapping(c.source, c.name) for c in selection.skills) + selection.resources
    # Root licenses accompany distributed content; manifests remain inert evidence.
    evidence = tuple(
        e
        for e in entire.entries
        if e.kind == "file"
        and (
            (
                PurePosixPath(e.path).name.lower().startswith(("license", "copying", "notice"))
                and (
                    str(PurePosixPath(e.path).parent) == "."
                    or _mapped(e.path, mappings) is not None
                    or any(m.source.startswith(str(PurePosixPath(e.path).parent) + "/") for m in mappings)
                )
            )
            or e.path in {".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json"}
        )
    )
    for entry in evidence:
        if (
            PurePosixPath(entry.path).name.lower().startswith(("license", "copying", "notice"))
            and _mapped(entry.path, mappings) is None
        ):
            mappings += (SourceMapping(entry.path, entry.path),)
    for index, mapping in enumerate(mappings):
        validate_path(mapping.source)
        validate_path(mapping.destination)
        if any(
            _overlap(mapping.source, other.source) or _overlap(mapping.destination, other.destination)
            for other in mappings[:index]
        ):
            raise PreviewError("Selected source roots or destination ownership collide")
        if mapping.source not in entries:
            raise PreviewError(f"Selected path missing: {mapping.source}")
    selected = tuple(e for e in entire.entries if _mapped(e.path, mappings) is not None)
    names: dict[str, str] = {}
    for component in selection.skills:
        skill = entries.get(component.source + "/SKILL.md")
        if skill is None or skill.kind != "file":
            raise PreviewError(f"Selected skill lacks a regular SKILL.md: {component.source}")
        metadata, problems = parse_skill_metadata(skill.content.removeprefix(b"\xef\xbb\xbf"), path=skill.path)
        findings.extend(replace(f, code="source." + f.code, severity="warn") for f in problems)
        if metadata:
            if metadata.name in names:
                raise PreviewError("Selected skills have ambiguous upstream names")
            names[metadata.name] = component.name
    if any(edit.kind == "materialize-link" for edit in selection.adaptations):
        raise PreviewError("Approve materialization through source_links; its evidence is generated")
    approvals = {link.path for link in selection.source_links}
    if any(path not in {e.path for e in selected if e.kind == "symlink"} for path in approvals):
        raise PreviewError("Source-link approval is outside the selected links")
    link_evidence: set[str] = set()
    active: list[SnapshotEntry] = []
    edits = list(selection.adaptations)
    for entry in selected:
        if entry.kind == "symlink":
            if entry.path not in approvals:
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
            active.extend(materialize_link(entire, entry.path, evidence_paths=link_evidence))
            edits.append(
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
    dependencies: list[Dependency] = list(selection.dependencies)
    for entry in active:
        if entry.kind != "file" or PurePosixPath(entry.path).name.lower().startswith(("license", "copying", "notice")):
            continue
        if entry.path.endswith(".md"):
            discovered, problems = scan_markdown(entry.content, entry.path, tuple(names))
        elif entry.path.endswith("agents/openai.yaml"):
            discovered, problems = scan_host_metadata(entry.content, entry.path)
        else:
            discovered, problems = (), ()
        dependencies.extend(discovered)
        findings.extend(replace(f, code="source." + f.code, severity="warn") for f in problems)
    for component in selection.skills:
        skill_path = component.source + "/SKILL.md"
        if not any(e.path == skill_path and e.kind == "skill-name" for e in edits):
            edit, problems = name_adaptation(entries[skill_path].content, skill_path, component.name)
            findings.extend(problems)
            if edit and edit.expected != edit.replacement:
                edits.append(edit)
    supplied = {(d.kind, d.target) for d in selection.dependencies if d.satisfied}
    resolved: list[Dependency] = []
    unlocated_renames: list[Dependency] = []
    for dependency in dependencies:
        satisfied = (dependency.kind, dependency.target) in supplied
        if dependency.kind in {"required-skill", "optional", "alternative"}:
            satisfied = satisfied or dependency.target in names or dependency.target in names.values()
        if dependency.kind == "required-resource" or (
            dependency.kind == "optional" and dependency.evidence == "markdown"
        ):
            satisfied = _mapped(dependency.target, mappings) is not None and any(
                e.path == dependency.target for e in active
            )
        resolved.append(replace(dependency, satisfied=satisfied))
        covered = any(
            edit.path == dependency.path
            and edit.kind not in {"permission", "materialize-link"}
            and edit.start < dependency.end
            and dependency.start < edit.end
            for edit in selection.adaptations
        )
        if covered:
            continue
        if dependency.evidence in {"invocation", "required-statement"} and dependency.target in names:
            if dependency.start == dependency.end:
                if dependency.required and names[dependency.target] != dependency.target:
                    unlocated_renames.append(dependency)
                continue
            entry = next(e for e in active if e.path == dependency.path)
            expected = entry.content[dependency.start : dependency.end]
            prefix = expected[: -len(dependency.target)]
            if prefix.endswith(b":") and prefix != b"/skill:":
                prefix = prefix[:1] if prefix.startswith((b"$", b"/")) else b""
            replacement = prefix + names[dependency.target].encode()
            if expected != replacement:
                edits.append(
                    Adaptation(
                        "invocation",
                        dependency.path,
                        dependency.start,
                        dependency.end,
                        expected,
                        replacement,
                        "Selected skill invocation",
                    )
                )
        if dependency.evidence == "markdown" and satisfied and dependency.start == dependency.end:
            origin = _mapped(dependency.path, mappings)
            target = _mapped(dependency.target, mappings)
            assert origin is not None and target is not None
            original_relative = posixpath.relpath(dependency.target, posixpath.dirname(dependency.path) or ".")
            preserved_target = posixpath.normpath(posixpath.join(posixpath.dirname(origin), original_relative))
            if dependency.required and preserved_target != target:
                unlocated_renames.append(replace(dependency, target=preserved_target))
        if dependency.evidence == "markdown" and satisfied and dependency.end > dependency.start:
            origin = _mapped(dependency.path, mappings)
            target = _mapped(dependency.target, mappings)
            assert origin is not None and target is not None
            entry = next(e for e in active if e.path == dependency.path)
            expected = entry.content[dependency.start : dependency.end]
            relative = posixpath.relpath(target, posixpath.dirname(origin) or ".")
            replacement = resource_replacement(expected, relative)
            if expected != replacement:
                edits.append(
                    Adaptation(
                        "resource-path",
                        dependency.path,
                        dependency.start,
                        dependency.end,
                        expected,
                        replacement,
                        "Selected resource mapping",
                    )
                )
    groups = {d.group for d in resolved if d.kind == "alternative" and d.satisfied and d.group}
    resolved = [replace(d, satisfied=True) if d.kind == "alternative" and d.group in groups else d for d in resolved]
    edits = list(dict.fromkeys(edits))
    active_paths = {e.path for e in active}
    if any(edit.path not in active_paths for edit in edits):
        raise PreviewError("Adaptation targets unselected content")
    # Outside references are observations only, with explicit incomplete-scan evidence.
    for entry in entire.entries:
        if entry.kind == "file" and entry.path.endswith(".md") and _mapped(entry.path, mappings) is None:
            _outside(entry.content, entry.path, names, findings)
    if discovery is None:
        findings.append(
            Finding(
                "discovery.limit",
                "warn",
                None,
                None,
                "No resolved discovery context supplied; "
                "configured, ancestor, personal and plugin sources were not scanned",
            )
        )
    else:
        for limit in discovery.limits:
            findings.append(Finding("discovery.limit", "warn", None, None, limit))
        for root in discovery.roots:
            try:
                outside = capture(SourceSpec(str(root), "local"), (), services=services)
                for entry in outside.entries:
                    if entry.kind == "file" and entry.path.endswith(".md"):
                        _outside(entry.content, str(root / entry.path), names, findings)
                    if entry.path.endswith("/SKILL.md") and PurePosixPath(entry.path).parent.name in names.values():
                        findings.append(
                            Finding(
                                "discovery.collision",
                                "error",
                                str(root / entry.path),
                                None,
                                "Local name already exists in a discovery root",
                            )
                        )
            except (OSError, SnapshotError) as exc:
                findings.append(Finding("discovery.limit", "warn", str(root), None, str(exc)))
    owned = tuple(m.destination for m in mappings)
    destinations = tuple(roots.content / path for path in owned)
    existing: list[str] = []
    for path in destinations:
        try:
            services.filesystem.mode(path)
        except FileNotFoundError:
            continue
        existing.append(path.relative_to(roots.content).as_posix())
        findings.append(Finding("destination.exists", "error", str(path), None, "Selected destination already exists"))
    observed = (
        capture(SourceSpec(str(roots.content), "local"), tuple(existing), services=services) if existing else None
    )
    identities = ancestors((*destinations, roots.state / "forks", roots.state / "previews"), services=services)
    preview_id, variant_id = services.new_id(), services.new_id()
    validate_path(preview_id)
    validate_path(variant_id)
    if "/" in preview_id or "/" in variant_id:
        raise PreviewError("Generated IDs must be path components")
    stage = roots.state / "previews" / preview_id
    stage.mkdir(parents=True, exist_ok=False)
    candidate = stage / "candidate"
    candidate.mkdir()
    operations: list[FileOperation] = []
    for entry in sorted(active, key=lambda e: e.path):
        destination = _mapped(entry.path, mappings)
        assert destination is not None
        path = candidate / destination
        adjusted_mode, mode_findings = permission_mode(entry.mode, tuple(e for e in edits if e.path == entry.path))
        findings.extend(mode_findings)
        if entry.mode & 0o7000 and not any(e.kind == "permission" and e.path == entry.path for e in edits):
            edits.append(
                Adaptation(
                    "permission",
                    entry.path,
                    0,
                    len(oct(entry.mode).encode()),
                    oct(entry.mode).encode(),
                    oct(adjusted_mode).encode(),
                    "Remove special permission bits from staged supporting content",
                )
            )
        if entry.kind == "directory":
            path.mkdir(parents=True, exist_ok=True)
            operations.append(FileOperation("mkdir", str(roots.content / destination), None, adjusted_mode))
            continue
        content, problems = replay(entry.content, tuple(e for e in edits if e.path == entry.path))
        findings.extend(problems)
        path.parent.mkdir(parents=True, exist_ok=True)
        services.filesystem.write_exclusive(path, content)
        services.filesystem.chmod(path, adjusted_mode)
        operations.append(
            FileOperation("write", str(roots.content / destination), "candidate/" + destination, adjusted_mode)
        )
    for operation in reversed(operations):
        if operation.kind == "mkdir":
            directory_relative = Path(operation.destination).relative_to(roots.content)
            services.filesystem.chmod(candidate / directory_relative, operation.mode)
    # Original, unadapted selected evidence plus inert license/manifest evidence.
    # Link targets are retained as original evidence to make materialization replayable.
    base_entries = {e.path: e for e in selected + evidence}
    for entry in entire.entries:
        if entry.path in link_evidence:
            base_entries.setdefault(entry.path, entry)
    base = Snapshot(entire.source, tuple(sorted(base_entries.values(), key=lambda e: e.path)))
    write_snapshot(base, stage / "base.tar.gz")
    services.filesystem.sync_file(stage / "base.tar.gz")
    staged = capture(SourceSpec(str(candidate), "local"), (), services=services)
    candidate_dependencies, candidate_findings = validate_candidate(staged, selection, mappings, names)
    findings.extend(candidate_findings)
    for dependency in unlocated_renames:
        if any(
            d.path == _mapped(dependency.path, mappings) and d.target == dependency.target and d.required
            for d in candidate_dependencies
        ):
            findings.append(
                Finding(
                    "adaptation.ambiguous",
                    "error",
                    dependency.path,
                    dependency.line,
                    "Required renamed reference has no safe original edit span; provide a reviewed adaptation",
                )
            )
    allowed = not any(f.severity == "error" for f in findings)
    preview = Preview(
        1,
        preview_id,
        "fork",
        (variant_id,),
        ((variant_id, None),),
        (
            Inventory(
                str(roots.content),
                tuple(InventoryEntry(e.path, e.kind, e.mode, e.hash) for e in observed.entries) if observed else (),
            ),
            Inventory(str(roots.state / "forks" / variant_id), ()),
        ),
        (*(str(path) for path in destinations), str(roots.state / "forks" / variant_id)),
        identities,
        (Artifact("base.tar.gz", hashlib.sha256((stage / "base.tar.gz").read_bytes()).hexdigest()),),
        str(candidate),
        staged.digest,
        tuple(operations),
        tuple(findings),
        "not_requested",
        allowed,
        str(roots.content),
        str(roots.state),
        owned,
        replace(
            selection,
            resources=tuple(
                m for m in mappings if m not in tuple(SourceMapping(c.source, c.name) for c in selection.skills)
            ),
        ),
        entire.source,
        base.digest,
        tuple(edits),
        tuple(resolved),
        intent,
        candidate_dependencies,
    )
    preview = replace(
        preview,
        ancestors=ancestors((*destinations, roots.state / "forks", stage / "preview.json"), services=services),
    )
    services.filesystem.write_exclusive(stage / "preview.json", preview_bytes(preview))
    return Result(
        "fork",
        variant_id,
        preview_id,
        False,
        allowed,
        tuple(findings),
        tuple(str(p) for p in destinations),
        {
            "candidate_path": str(candidate),
            "preview_path": str(stage / "preview.json"),
            "base_digest": base.digest,
            "unresolved": [d.target for d in candidate_dependencies if d.required and not d.satisfied],
        },
    )


def _outside(content: bytes, path: str, names: dict[str, str], findings: list[Finding]) -> None:
    dependencies, limitations = scan_markdown(content, path)
    findings.extend(
        replace(f, code="discovery.limit", severity="warn", message="Outside scan: " + f.message) for f in limitations
    )
    for dependency in dependencies:
        if dependency.target in names and dependency.target != names[dependency.target]:
            findings.append(
                Finding(
                    "reference.outside-selection",
                    "warn",
                    path,
                    dependency.line,
                    f"Outside invocation of renamed skill {dependency.target}; left unchanged",
                )
            )


def validate_candidate(
    snapshot: Snapshot,
    selection: Selection,
    mappings: tuple[SourceMapping, ...],
    names: dict[str, str],
) -> tuple[tuple[Dependency, ...], tuple[Finding, ...]]:
    """Recompute mechanical eligibility from final bytes in candidate coordinates.

    Original source dependency evidence remains in Preview.dependencies. It cannot
    authorize new requirements or keep a repaired source defect blocking forever.
    """
    entries = {entry.path: entry for entry in snapshot.entries}
    findings: list[Finding] = []
    local_names = {component.name for component in selection.skills}
    for component in selection.skills:
        path = (_mapped(component.source, mappings) or component.name) + "/SKILL.md"
        entry = entries.get(path)
        if entry is None or entry.kind != "file":
            findings.append(Finding("skill.missing", "error", path, None, "Candidate skill entry is missing"))
            continue
        metadata, problems = parse_skill_metadata(entry.content, path=path)
        findings.extend(problems)
        if metadata is not None and metadata.name != component.name:
            findings.append(
                Finding(
                    "skill.name-mismatch",
                    "error",
                    path,
                    2,
                    "Candidate metadata name must equal the selected local folder name",
                )
            )
    dependencies: list[Dependency] = []
    for dependency in selection.dependencies:
        target = dependency.target
        if dependency.kind == "required-resource":
            target = _mapped(target, mappings) or target
        elif dependency.kind in {"required-skill", "optional", "alternative"}:
            target = names.get(target, target)
        dependencies.append(
            replace(dependency, path=_mapped(dependency.path, mappings) or dependency.path, target=target)
        )
    supplied = {(d.kind, d.target) for d in dependencies if d.satisfied}
    for entry in snapshot.entries:
        if entry.kind != "file" or PurePosixPath(entry.path).name.lower().startswith(("license", "copying", "notice")):
            continue
        if entry.path.endswith(".md"):
            discovered, problems = scan_markdown(entry.content, entry.path)
        elif entry.path.endswith("agents/openai.yaml"):
            discovered, problems = scan_host_metadata(entry.content, entry.path)
        else:
            continue
        dependencies.extend(discovered)
        findings.extend(problems)
    resolved: list[Dependency] = []
    for dependency in dependencies:
        satisfied = (dependency.kind, dependency.target) in supplied
        if dependency.kind == "required-resource" or (
            dependency.kind == "optional" and dependency.evidence == "markdown"
        ):
            satisfied = dependency.target in entries
        elif dependency.kind in {"required-skill", "optional", "alternative"}:
            satisfied = satisfied or dependency.target in local_names
        resolved.append(replace(dependency, satisfied=satisfied))
    groups = {d.group for d in resolved if d.kind == "alternative" and d.satisfied and d.group}
    resolved = [replace(d, satisfied=True) if d.kind == "alternative" and d.group in groups else d for d in resolved]
    for dependency in resolved:
        if dependency.required and not dependency.satisfied:
            findings.append(
                Finding(
                    "dependency.missing",
                    "error",
                    dependency.path,
                    dependency.line,
                    f"Missing {dependency.kind}: {dependency.target}",
                )
            )
    return tuple(resolved), tuple(findings)
