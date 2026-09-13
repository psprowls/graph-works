"""Previewed shared activation and independently owned content copies."""

from __future__ import annotations

import hashlib
from configparser import Error as ConfigParserError
from dataclasses import replace
from pathlib import Path
from typing import Literal

from .acceptance import _snapshot
from .adapters import adapter, discovered_names, discovery_context, discovery_inventory, normalized
from .config import portable_content_root
from .fork import _mapped, validate_candidate
from .machine import Services
from .previews import PreviewError, ancestors, preview_bytes
from .records import (
    AgentBinding,
    Artifact,
    Binding,
    Change,
    DiscoveryContext,
    FileOperation,
    Finding,
    History,
    InstallationTarget,
    Inventory,
    InventoryEntry,
    JsonValue,
    Ledger,
    Preview,
    Result,
    Roots,
    Selection,
    Snapshot,
    SnapshotEntry,
    SourceMapping,
)
from .status import _attestation, read_status
from .store import (
    identifier,
    inventory,
    ledgers,
    load_binding,
    load_ledger,
    observe,
    owned_paths,
    record_bytes,
    resolve_content_root,
    sealed_bytes,
)
from .transactions import TransactionError, failure
from .updates import _write_candidate


def _overlap(a: Path, b: Path) -> bool:
    left, right = normalized(a.as_posix()), normalized(b.as_posix())
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _binding_path(state: Path, variant: str) -> Path:
    return state / "bindings" / (identifier(variant) + ".json")


def _component_paths(ledger: Ledger) -> tuple[SourceMapping, ...]:
    return tuple(SourceMapping(_mapped(c.source, ledger.mappings) or c.name, c.name) for c in ledger.components)


def _copy_snapshot(snapshot: Snapshot, ledger: Ledger) -> tuple[Snapshot, Ledger]:
    moves = _component_paths(ledger)
    mappings = tuple(replace(m, destination=_mapped(m.destination, moves) or m.destination) for m in ledger.mappings)
    entries = tuple(
        sorted((replace(e, path=_mapped(e.path, moves) or e.path) for e in snapshot.entries), key=lambda e: e.path)
    )
    return replace(snapshot, entries=entries), replace(ledger, mappings=mappings)


def _observe_owner(roots: Roots, ledger: Ledger, *, services: Services) -> None:
    status = read_status(roots, ledger.variant_id, services=services)
    errors = [
        f
        for f in status.findings
        if f.severity == "error" and f.code != "tracking.binding" and not f.code.startswith("binding.")
    ]
    if errors:
        raise TransactionError(errors[0].code, errors[0].message)
    try:
        old_root = resolve_content_root(roots.state, ledger)
    except PreviewError:
        old_root = None
    if old_root is None or old_root.resolve() != roots.content.resolve():
        if (
            ledger.origin != "known"
            or inventory(roots.content, owned_paths(ledger), services=services).entries != ledger.files
        ):
            raise TransactionError(
                "binding.identity", "Relocated content must match known portable content/ownership evidence"
            )
        if old_root is not None and inventory(old_root, owned_paths(ledger), services=services).entries:
            raise TransactionError(
                "binding.duplicate-root", "Original bound content still exists; use an independent copy"
            )


def plan_install(
    roots: Roots,
    variant_id: str,
    *,
    agents: tuple[str, ...],
    scope: Literal["project", "personal"],
    mode: Literal["shared", "copy"],
    project: Path,
    home: Path,
    destination: Path | None,
    services: Services,
    discovery: DiscoveryContext | None = None,
) -> Result:
    try:
        if not agents or scope not in {"project", "personal"} or mode not in {"shared", "copy"}:
            raise PreviewError("Install requires supported agents, scope, and mode")
        if any(
            not path.is_absolute()
            for path in (roots.content, roots.state, project, home, *((destination,) if destination else ()))
        ):
            raise PreviewError("Installation paths must be resolved absolute paths")
        agents = tuple(dict.fromkeys(agents))
        for name in agents:
            adapter(name)
        ledger = load_ledger(roots.state, variant_id)
        _observe_owner(roots, ledger, services=services)
        original_history = _attestation(roots.state, variant_id)
        source = _snapshot(roots.content, owned_paths(ledger), services=services)
        candidate, template = _copy_snapshot(source, ledger) if mode == "copy" else (source, ledger)
        chosen = Selection(template.components, (), ledger.dependencies, template.adaptations, ())
        dependencies, validation = validate_candidate(
            candidate, chosen, template.mappings, {c.name: c.name for c in ledger.components}
        )
        findings = list(validation)
        if any(e.kind == "symlink" for e in candidate.entries):
            findings.append(
                Finding(
                    "binding.link-content",
                    "error",
                    None,
                    None,
                    "Materialize current content links through explicit selection review before activation",
                )
            )
        git_config = project / ".git/config"
        if git_config.is_file():
            from configparser import ConfigParser

            settings = ConfigParser()
            settings.read_string(git_config.read_bytes().decode("utf-8"))
            if settings.get("core", "symlinks", fallback="true").strip().casefold() == "false":
                findings.append(
                    Finding(
                        "binding.git-symlinks-disabled",
                        "warn",
                        str(git_config),
                        None,
                        "Git core.symlinks=false may check links out as regular files; "
                        "live bindings still require filesystem links",
                    )
                )
        for agent in agents:
            for component in ledger.components:
                findings.extend(adapter(agent).validate_name(component.name))
        # Out-of-scope executable host capabilities require a reviewed selection
        # change, never activation or silent rewriting here.
        for entry in candidate.entries:
            if entry.path.endswith("/SKILL.md"):
                header = entry.content.split(b"---", 2)[1] if entry.content.startswith(b"---") else b""
                if any(
                    line.split(b":", 1)[0].strip() in {b"hooks", b"mcp", b"mcp_servers", b"agent"}
                    for line in header.splitlines()
                ):
                    findings.append(
                        Finding(
                            "binding.capability",
                            "error",
                            entry.path,
                            None,
                            "Host capability needs explicit selection adaptation; installation cannot activate it",
                        )
                    )
        context = discovery_context(agents, project=project, home=home, configured=discovery)
        selected: dict[Path, list[str]] = {}
        for agent in agents:
            target_path = destination or adapter(agent).root(scope, project=project, home=home)
            if (
                mode == "shared"
                and agent == "pi"
                and roots.content in {project / ".agents/skills", home / ".agents/skills"}
                and destination is None
            ):
                target_path = roots.content
            selected.setdefault(target_path, []).append(agent)
        old_binding = load_binding(roots.state, variant_id)
        targets: list[InstallationTarget] = []
        new_ids: list[str] = []
        for target_path, target_agent_names in selected.items():
            new_id = identifier(services.new_id()) if mode == "copy" else variant_id
            if mode == "copy":
                new_ids.append(new_id)
            paths = (
                owned_paths(template)
                if mode == "copy"
                else tuple(
                    dict.fromkeys(
                        (
                            *[m.destination for m in _component_paths(ledger)],
                            *[p for p in owned_paths(ledger) if p not in {m.source for m in _component_paths(ledger)}],
                        )
                    )
                )
            )
            targets.append(InstallationTarget(str(target_path), new_id, mode, tuple(target_agent_names), paths))
        for planned_target in targets:
            target_root = Path(planned_target.root)
            for owned in planned_target.owned_paths:
                destination_path = target_root / owned
                for checked in (destination_path, *destination_path.parents):
                    if checked == target_root.parent:
                        break
                    try:
                        siblings = services.filesystem.children(checked.parent)
                    except FileNotFoundError:
                        continue
                    if any(normalized(p.name) == normalized(checked.name) and p.name != checked.name for p in siblings):
                        raise TransactionError(
                            "binding.collision", "Destination resource/name differs only by case or normalization"
                        )
        # Distinct roots must not create partially shared owners.
        for i, target in enumerate(targets):
            if any(
                _overlap(Path(target.root) / p, Path(other.root) / q)
                for other in targets[:i]
                for p in target.owned_paths
                for q in other.owned_paths
            ):
                raise TransactionError("ownership.overlap", "Installation destinations overlap")
            if mode == "copy" and any(
                _overlap(Path(target.root) / p, roots.content / q)
                for p in target.owned_paths
                for q in owned_paths(ledger)
            ):
                raise TransactionError("ownership.overlap", "Independent copy overlaps its source")
        if mode == "shared":
            for target in targets:
                for path in target.owned_paths:
                    final = Path(target.root) / path
                    origin = roots.content / next(
                        (m.source for m in _component_paths(ledger) if m.destination == path), path
                    )
                    if final != origin and any(_overlap(final, roots.content / p) for p in owned_paths(ledger)):
                        raise TransactionError("ownership.overlap", "Shared binding overlaps maintained source content")
        discovered: list[Inventory] = []
        allowed_paths = {roots.content / p for p in owned_paths(ledger)}
        if old_binding:
            allowed_paths.update(Path(b.root) / c.name for b in old_binding.targets for c in ledger.components)
        for root in dict.fromkeys((*context.roots, *selected)):
            found_names, limits = discovered_names(root, services=services)
            findings.extend(limits)
            try:
                discovered.append(discovery_inventory(root, services=services))
            except OSError:
                continue
            for name, discovered_path in found_names:
                if (
                    normalized(name) in {normalized(c.name) for c in ledger.components}
                    and discovered_path not in allowed_paths
                ):
                    findings.append(
                        Finding(
                            "binding.collision",
                            "error",
                            str(discovered_path),
                            None,
                            "Discovered name collides; adopt unrelated content instead of overwriting it",
                        )
                    )
        findings.extend(Finding("discovery.limit", "warn", None, None, limit) for limit in context.limits)
        findings.append(
            Finding(
                "binding.ignore-suggestion",
                "warn",
                str(project / ".gitignore"),
                None,
                "Consider explicit ignore rules for local bindings; no ignore file is changed",
            )
        )
        stores = tuple(dict.fromkeys((roots.state, project / ".plugin-fork", *context.state_roots)))
        owners: list[tuple[str, str, int]] = []
        observed: list[Inventory] = [inventory(roots.content, owned_paths(ledger), services=services)]
        scopes: list[tuple[str, tuple[str, ...]]] = [(str(roots.content), owned_paths(ledger))]
        for state in stores:
            discovered.append(discovery_inventory(state / "forks", services=services))
            discovered.append(discovery_inventory(state / "bindings", services=services))
            for owner in ledgers(state):
                owners.append((str(state), owner.variant_id, owner.generation))
                tracking = state / "forks" / owner.variant_id
                observed.append(inventory(tracking, (".",), services=services))
                scopes.append((str(tracking), (".",)))
                if state == roots.state and owner.variant_id == variant_id:
                    continue
                owner_root = resolve_content_root(state, owner)
                if any(
                    _overlap(Path(t.root) / p, owner_root / q)
                    for t in targets
                    for p in t.owned_paths
                    for q in owned_paths(owner)
                ):
                    raise TransactionError("ownership.overlap", "Known destination owner overlaps installation")
        preview_id = identifier(services.new_id())
        stage = roots.state / "previews" / preview_id
        stage.mkdir(parents=True)
        _write_candidate(stage / "candidate", candidate, services=services)
        link_mode = 0o777
        if mode == "shared" and (
            any(Path(t.root) != roots.content for t in targets)
            or any(m.source != m.destination for m in _component_paths(ledger))
        ):
            probe = stage / "link-probe"
            try:
                services.filesystem.link("candidate", probe)
                link_entry = observe(probe, services=services)
                assert link_entry is not None
                link_mode = link_entry.mode
            except OSError as exc:
                raise TransactionError("binding.link-unavailable", f"{exc}; choose explicit copy installation") from exc
            finally:
                if probe.is_symlink() and services.filesystem.readlink(probe) == "candidate":
                    probe.unlink()
                    services.filesystem.sync_directory(stage)
        data: dict[str, bytes] = {}
        bindings = {variant_id: Binding(1, variant_id, str(roots.content), old_binding.targets if old_binding else ())}
        for target in targets:
            target_root = Path(target.root)
            binding = bindings.get(target.variant_id, Binding(1, target.variant_id, target.root, ()))
            bindings[target.variant_id] = replace(
                binding,
                targets=tuple(
                    dict.fromkeys(
                        (*binding.targets, *(AgentBinding(name, target.root, scope) for name in target.agents))
                    )
                ),
            )
            observed.append(inventory(target_root, target.owned_paths, services=services))
            scopes.append((target.root, target.owned_paths))
            if mode == "copy":
                clone = replace(
                    template,
                    variant_id=target.variant_id,
                    generation=1,
                    content_root=portable_content_root(target_root, roots.state, platform=services.platform),
                    files=inventory(stage / "candidate", target.owned_paths, services=services).entries,
                    history=(preview_id,),
                )
                data[target.variant_id + "/ledger.json"] = record_bytes(clone)
                tracking_images = []
                for image in original_history.tracking:
                    if image.path == "ledger.json":
                        image = replace(image, content=record_bytes(clone))
                    tracking_images.append(image)
                    data[target.variant_id + "/" + image.path] = image.content
                history = History(1, preview_id, clone.variant_id, clone, tuple(tracking_images), "copy")
                data[target.variant_id + "/history/" + preview_id + ".json"] = sealed_bytes(history)
        for key, binding in bindings.items():
            data["bindings/" + key + ".json"] = sealed_bytes(binding)
            binding_file = _binding_path(roots.state, key)
            observed.append(inventory(binding_file.parent, (binding_file.name,), services=services))
            scopes.append(
                (
                    str(binding_file.parent),
                    tuple(p.name for p in (_binding_path(roots.state, name) for name in bindings)),
                )
            )
        # Consolidate scopes for roots shared by several requested agents/bindings.
        scoped: dict[str, tuple[str, ...]] = {}
        for scope_root, scope_paths in scopes:
            scoped[scope_root] = tuple(dict.fromkeys((*scoped.get(scope_root, ()), *scope_paths)))
        observed = [inventory(Path(root), paths, services=services) for root, paths in scoped.items()]
        if observed[0].entries != tuple(InventoryEntry(e.path, e.kind, e.mode, e.hash) for e in source.entries):
            raise PreviewError("Source changed during installation preparation")
        artifacts = []
        for artifact_name, content in data.items():
            destination_path = stage / artifact_name
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            services.filesystem.write_exclusive(destination_path, content)
            artifacts.append(Artifact(artifact_name, hashlib.sha256(content).hexdigest()))
        preview = Preview(
            1,
            preview_id,
            "install",
            (variant_id, *new_ids),
            ((variant_id, ledger.generation), *((key, None) for key in new_ids)),
            tuple(observed),
            (),
            (),
            tuple(artifacts),
            str(stage / "candidate"),
            candidate.digest,
            (),
            tuple(findings),
            "not_requested",
            not any(f.severity == "error" for f in findings),
            str(roots.content),
            str(roots.state),
            owned_paths(ledger),
            chosen,
            ledger.source,
            ledger.base_digest or "",
            ledger.adaptations,
            ledger.unresolved,
            ledger.intent,
            candidate_dependencies=dependencies,
            source_mappings=template.mappings,
            installation_targets=tuple(targets),
            inventory_scopes=tuple(scoped.items()),
            discovery_observations=tuple(discovered),
            owner_observations=tuple(owners),
            link_mode=link_mode,
            lock_stores=tuple(sorted(str(s) for s in stores if s == roots.state or (s / "forks").is_dir())),
        )
        changes = installation_changes(preview, services=services)
        operations = tuple(
            FileOperation(
                "link"
                if c.after and c.after.kind == "symlink"
                else "mkdir"
                if c.after and c.after.kind == "directory"
                else "write",
                c.destination,
                None,
                c.after.mode if c.after else 0,
            )
            for c in changes
        )
        preview = replace(
            preview,
            operations=operations,
            expected_absences=tuple(c.destination for c in changes if c.before is None),
            ancestors=ancestors(tuple(Path(c.destination) for c in changes), services=services),
        )
        services.filesystem.write_exclusive(stage / "preview.json", preview_bytes(preview))
        services.filesystem.sync_directory(stage)
        return Result(
            "install",
            variant_id,
            preview_id,
            False,
            preview.allowed,
            preview.findings,
            tuple(c.destination for c in changes),
            installation_data(preview),
        )
    except TransactionError as exc:
        return failure("install", None, exc.code, str(exc), variant_id)
    except (ValueError, OSError, ConfigParserError) as exc:
        return failure("install", None, "binding.invalid", str(exc), variant_id)


def installation_data(preview: Preview) -> dict[str, JsonValue]:
    return {
        "created_variants": [t.variant_id for t in preview.installation_targets if t.mode == "copy"],
        "bindings": [
            {"root": t.root, "variant_id": t.variant_id, "agents": list(t.agents), "mode": t.mode}
            for t in preview.installation_targets
        ],
        "sharing": "Requests resolving to one identical root share one owner; distinct copies have independent owners",
    }


def installation_changes(preview: Preview, *, services: Services) -> tuple[Change, ...]:
    state, source_root, stage = (
        Path(preview.state_root),
        Path(preview.content_root),
        Path(preview.candidate_path).parent,
    )
    ledger = load_ledger(state, preview.variant_ids[0])
    source_binding = load_binding(state, ledger.variant_id)
    candidate = _snapshot(Path(preview.candidate_path), (".",), services=services)
    if candidate.digest != preview.candidate_digest:
        raise PreviewError("Immutable installation content changed")
    changes: list[Change] = []
    for target in preview.installation_targets:
        root = Path(target.root)
        if target.mode == "copy":
            for entry in candidate.entries:
                path = root / entry.path
                if observe(path, services=services) is not None:
                    raise TransactionError("binding.collision", "Copy destination exists; use adoption instead")
                changes.append(Change(str(path), None, replace(entry, path=str(path))))
        else:
            mapping = _component_paths(ledger)
            for local in target.owned_paths:
                source = source_root / next((m.source for m in mapping if m.destination == local), local)
                destination = root / local
                if destination == source:
                    continue
                existing = observe(destination, services=services)
                if existing is not None:
                    known = source_binding is not None and any(b.root == target.root for b in source_binding.targets)
                    if not known or existing.kind != "symlink" or destination.resolve() != source.resolve():
                        raise TransactionError(
                            "binding.collision", "Existing unrelated content/link is never overwritten; use adoption"
                        )
                    continue
                relative = portable_content_root(source, destination.parent, platform=services.platform)
                if relative is None:
                    raise TransactionError(
                        "binding.link-unavailable",
                        "Relative links across volumes are unavailable; choose explicit copy installation",
                    )
                changes.append(
                    Change(
                        str(destination),
                        None,
                        SnapshotEntry(str(destination), relative.encode("utf-8"), "symlink", preview.link_mode),
                    )
                )
    changes.sort(key=lambda c: (len(Path(c.destination).parts), c.destination))
    for artifact in preview.artifacts:
        content = (stage / artifact.path).read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.digest:
            raise PreviewError("Immutable installation tracking changed")
        path = state / artifact.path if artifact.path.startswith("bindings/") else state / "forks" / artifact.path
        current = observe(path, services=services)
        if not artifact.path.startswith("bindings/") and current is not None:
            raise TransactionError("ownership.overlap", "Copy identity already exists")
        changes.append(Change(str(path), current, SnapshotEntry(str(path), content, "file", 0o600)))
    return tuple(changes)


def revalidate_install(preview: Preview, *, services: Services) -> None:
    state = Path(preview.state_root)
    ledger = load_ledger(state, preview.variant_ids[0])
    _observe_owner(Roots(Path(preview.content_root), state), ledger, services=services)
    for root, variant, generation in preview.owner_observations:
        if load_ledger(Path(root), variant).generation != generation:
            raise TransactionError("preview.stale", "Observed owner generation changed")
    for expected in preview.discovery_observations:
        if discovery_inventory(Path(expected.root), services=services) != expected:
            raise TransactionError("preview.stale", "Discovery names or owner stores changed")
    changes = installation_changes(preview, services=services)
    expected_operations = tuple(
        FileOperation(
            "link"
            if c.after and c.after.kind == "symlink"
            else "mkdir"
            if c.after and c.after.kind == "directory"
            else "write",
            c.destination,
            None,
            c.after.mode if c.after else 0,
        )
        for c in changes
    )
    if expected_operations != preview.operations:
        raise PreviewError("Installation operations differ from authorized ownership")


def verify_install_content(preview: Preview, *, services: Services) -> None:
    source = next(i for i in preview.observed if i.root == preview.content_root)
    if inventory(Path(source.root), preview.owned_paths, services=services) != source:
        raise TransactionError("transaction.verify", "Source snapshot changed before installation completed")
    candidate = _snapshot(Path(preview.candidate_path), (".",), services=services)
    for target in preview.installation_targets:
        if target.mode == "copy":
            expected = tuple(
                InventoryEntry(e.path, e.kind, e.mode, e.hash)
                for e in candidate.entries
                if any(e.path == p or e.path.startswith(p + "/") for p in target.owned_paths)
            )
            if inventory(Path(target.root), target.owned_paths, services=services).entries != expected:
                raise TransactionError("transaction.verify", "Complete copied inventory differs from source snapshot")
