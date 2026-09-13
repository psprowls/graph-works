"""Read-only comparison of maintained content and accepted provenance."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from .machine import Services
from .previews import PreviewError, _decode, _encode
from .records import Binding, Finding, History, JsonValue, Ledger, Result, Roots
from .snapshots import read_snapshot
from .store import (
    inventory,
    load_binding,
    load_ledger,
    observe,
    owned_paths,
    record_bytes,
    resolve_content_root,
    unseal,
)
from .transactions import TransactionError, incomplete, load_journal


def _attestation(state: Path, variant_id: str) -> History:
    evidence: list[History] = []
    directory = state / "forks" / variant_id / "history"
    for path in sorted(directory.iterdir()) if directory.is_dir() else ():
        if path.is_symlink() or path.parent.is_symlink():
            raise PreviewError("History must not traverse links")
        history = cast(History, _decode(unseal(path.read_bytes()), History))
        ledger = history.ledger
        if (
            history.schema_version != 1
            or history.id + ".json" != path.name
            or history.variant_id != variant_id
            or ledger.variant_id != variant_id
            or ledger.generation < 1
            or ledger.history[-1:] != (history.id,)
        ):
            raise PreviewError("Inconsistent portable history identity")
        from .snapshots import validate_path

        for entry in (*history.tracking, *history.before_tracking, *history.before_content):
            validate_path(entry.path)
        after_images = {entry.path: entry for entry in history.tracking}
        ledger_image = after_images.get("ledger.json")
        if (
            len(after_images) != len(history.tracking)
            or ledger_image is None
            or ledger_image.content != record_bytes(ledger)
        ):
            raise PreviewError("Portable history must attest its exact ledger after-image")
        evidence.append(history)
    # Retained machine journals remain consistency evidence, but are not required
    # by a portable transfer. Compare identities, never the old absolute root.
    transactions = state / "transactions"
    for path in sorted(transactions.iterdir()) if transactions.is_dir() else ():
        journal = load_journal(state, path.name)
        if journal.phase != "complete" or variant_id not in journal.variant_ids:
            continue
        matching = [
            c for c in journal.changes if Path(c.destination).parts[-3:] == ("forks", variant_id, "ledger.json")
        ]
        if not matching and journal.operation == "install":
            continue
        if len(matching) != 1 or matching[0].after is None:
            raise PreviewError("Completed variant journal does not attest its tracking ledger")
        attested = cast(Ledger, _decode(json.loads(matching[0].after.content), Ledger))
        if not any(h.id == journal.id and h.ledger == attested for h in evidence):
            raise PreviewError("Completed journal differs from portable history")
    if not evidence:
        raise PreviewError("No portable history attests this variant")
    generation = max(h.ledger.generation for h in evidence)
    latest = [h for h in evidence if h.ledger.generation == generation]
    if len(latest) != 1:
        raise PreviewError("Multiple histories attest the latest generation")
    latest_history = latest[0]
    if set(latest_history.ledger.history) != {h.id for h in evidence}:
        raise PreviewError("Portable history chain has missing or unrelated events")
    return latest_history


def _missing_binding_entries(
    destination: Path, owned: str, ledger: Ledger, *, services: Services
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    skill_roots = {m.destination for m in ledger.mappings if m.source in {c.source for c in ledger.components}}
    for entry in ledger.files:
        if owned not in skill_roots or entry.path != owned + "/SKILL.md":
            continue
        path = destination / entry.path[len(owned) + 1 :]
        current = observe(path, services=services)
        if current is None:
            findings.append(Finding("binding.missing", "error", str(path), None, "Installed owned entry is missing"))
        elif current.kind != entry.kind:
            findings.append(Finding("binding.foreign", "error", str(path), None, "Installed owned entry changed kind"))
    return tuple(findings)


def _binding_findings(binding: Binding, ledger: Ledger, *, services: Services) -> tuple[Finding, ...]:
    # Binding roots describe discovery destinations; mappings describe the owner.
    # Only these selected paths belong to the variant, never adjacent siblings.
    from .installation import _component_paths

    components = _component_paths(ledger)
    paths = {m.destination: m.source for m in components}
    paths.update({p: p for p in owned_paths(ledger) if p not in {m.source for m in components}})
    findings: list[Finding] = []
    for target in binding.targets:
        for local, owned in paths.items():
            destination = Path(target.root) / local
            source = Path(binding.content_root) / owned
            try:
                current = observe(destination, services=services)
                owner = observe(source, services=services)
                expected_kind = next((entry.kind for entry in ledger.files if entry.path == owned), None)
                if current is None:
                    code, message = "missing", "Installed owned path is missing"
                elif owner is None:
                    code, message = "missing", "Installed binding has a missing owner path"
                elif owner.kind == "symlink" or owner.kind != expected_kind:
                    code, message = "foreign", "Recorded owner root was replaced by a link or changed kind"
                elif destination == source:
                    findings.extend(_missing_binding_entries(destination, owned, ledger, services=services))
                    continue
                elif current.kind != "symlink":
                    code, message = "foreign", "Shared discovery link was replaced by unrelated content"
                elif destination.resolve() != source.resolve():
                    code, message = "retargeted", "Shared discovery link no longer targets its recorded owner"
                else:
                    findings.extend(_missing_binding_entries(destination, owned, ledger, services=services))
                    continue
            except (OSError, ValueError) as exc:
                code, message = "inaccessible", str(exc)
            findings.append(Finding("binding." + code, "error", str(destination), None, message))
    return tuple(findings)


def read_status(roots: Roots, variant_id: str, *, services: Services) -> Result:
    findings: list[Finding] = []
    data: dict[str, JsonValue] = {}
    try:
        pending = incomplete(roots.state)
        for journal in pending:
            if variant_id in journal.variant_ids:
                findings.append(
                    Finding(
                        "transaction.incomplete",
                        "error",
                        str(roots.state / "transactions" / journal.id),
                        None,
                        "Incomplete transaction requires explicit recovery",
                    )
                )
        ledger = load_ledger(roots.state, variant_id)
        if (
            ledger.content_root is not None or load_binding(roots.state, variant_id) is not None
        ) and resolve_content_root(roots.state, ledger).resolve() != roots.content.resolve():
            findings.append(
                Finding("tracking.binding", "error", None, None, "Explicit content root differs from portable binding")
            )
        actual = inventory(roots.content, owned_paths(ledger), services=services)
        if actual.entries != ledger.files:
            findings.append(
                Finding(
                    "content.modified",
                    "warn",
                    str(roots.content),
                    None,
                    "Live owned inventory differs from accepted files",
                )
            )
        if ledger.base_digest is not None:
            base = read_snapshot(roots.state / "forks" / variant_id / "base.tar.gz")
            if base.digest != ledger.base_digest:
                findings.append(
                    Finding("tracking.base-mismatch", "error", None, None, "Accepted base archive differs from ledger")
                )
        history = _attestation(roots.state, variant_id)
        attested = history.ledger
        if ledger.history != attested.history:
            findings.append(
                Finding(
                    "tracking.history",
                    "error",
                    None,
                    None,
                    "Ledger history differs from independently located transaction evidence",
                )
            )
        tracking = roots.state / "forks" / variant_id
        for entry in history.tracking:
            path = tracking / entry.path
            if observe(path, services=services) != replace(entry, path=str(path)):
                findings.append(
                    Finding(
                        "tracking.modified",
                        "error",
                        str(path),
                        None,
                        "Tracking differs from independently located portable history",
                    )
                )
        binding = load_binding(roots.state, variant_id)
        if binding:
            findings.extend(_binding_findings(binding, ledger, services=services))
        data.update(
            unresolved=_encode(ledger.unresolved),
            dependencies=_encode(ledger.dependencies),
            generation=ledger.generation,
            origin=ledger.origin,
            base_digest=ledger.base_digest,
            bindings=[{"agent": target.agent, "root": target.root, "scope": target.scope} for target in binding.targets]
            if binding
            else [],
        )
    except TransactionError as exc:
        findings.append(Finding(exc.code, "error", None, None, str(exc)))
    except (ValueError, OSError) as exc:
        findings.append(Finding("tracking.invalid", "error", None, None, str(exc)))
    return Result(
        "status", variant_id, None, False, not any(f.severity == "error" for f in findings), tuple(findings), (), data
    )
