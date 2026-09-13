"""Resolved standalone discovery conventions; adapters never invoke an agent."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .machine import Services
from .records import DiscoveryContext, Finding, Inventory, InventoryEntry
from .store import observe
from .validation import SKILL_NAME, parse_skill_metadata


@dataclass(frozen=True)
class AgentAdapter:
    name: str
    project_path: str
    personal_path: str
    invocation_prefix: str

    def root(self, scope: Literal["project", "personal"], *, project: Path, home: Path) -> Path:
        return project / self.project_path if scope == "project" else home / self.personal_path

    def invocation(self, name: str) -> str:
        return self.invocation_prefix + name

    def validate_name(self, name: str) -> tuple[Finding, ...]:
        if not SKILL_NAME.fullmatch(name) or len(name) > 64 or (self.name == "claude" and name.casefold() == "synced"):
            return (
                Finding("binding.name", "error", name, None, f"Unsupported standalone name for {self.name}: {name}"),
            )
        return ()


def adapter(name: str) -> AgentAdapter:
    choices = {
        "codex": AgentAdapter("codex", ".agents/skills", ".agents/skills", "$"),
        "claude": AgentAdapter("claude", ".claude/skills", ".claude/skills", "/"),
        "pi": AgentAdapter("pi", ".pi/skills", ".pi/agent/skills", "/skill:"),
    }
    if name not in choices:
        raise ValueError(f"Unsupported agent: {name}")
    return choices[name]


def normalized(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def discovery_context(
    agents: tuple[str, ...],
    *,
    project: Path,
    home: Path,
    configured: DiscoveryContext | None,
) -> DiscoveryContext:
    roots: list[Path] = list(configured.roots if configured else ())
    # A .git marker is sufficient to delimit ancestor discovery; do not run Git.
    for ancestor in (project, *project.parents):
        for name in agents:
            roots.append(ancestor / adapter(name).project_path)
            if name == "pi":
                roots.append(ancestor / ".agents/skills")
        if (ancestor / ".git").exists():
            break
    for name in agents:
        roots.append(home / adapter(name).personal_path)
        if name == "pi":
            roots.append(home / ".agents/skills")
    return DiscoveryContext(
        tuple(dict.fromkeys(roots)),
        (
            *((configured.limits) if configured else ()),
            "Plugin/admin discovery sources and unconfigured stores were not enumerated",
        ),
        configured.state_roots if configured else (),
    )


def discovery_inventory(root: Path, *, services: Services) -> Inventory:
    try:
        children = services.filesystem.children(root)
    except FileNotFoundError:
        return Inventory(str(root), ())
    entries = []
    for path in children:
        observed = observe(path, services=services)
        if observed is not None:
            entries.append(InventoryEntry(path.name, observed.kind, observed.mode, observed.hash))
    return Inventory(str(root), tuple(sorted(entries, key=lambda e: e.path)))


def discovered_names(root: Path, *, services: Services) -> tuple[tuple[tuple[str, Path], ...], tuple[Finding, ...]]:
    names: list[tuple[str, Path]] = []
    try:
        children = services.filesystem.children(root)
    except FileNotFoundError:
        return (), (Finding("discovery.missing", "warn", str(root), None, "Discovery root does not exist"),)
    except OSError as exc:
        return (), (Finding("discovery.inaccessible", "warn", str(root), None, f"Discovery scan limited: {exc}"),)
    for path in children:
        names.append((path.name, path))
        try:
            definition = path / "SKILL.md"
            if definition.is_file():
                metadata, _ = parse_skill_metadata(services.filesystem.read_bytes(definition), path=str(definition))
                if metadata:
                    names.append((metadata.name, path))
        except OSError:
            return tuple(names), (
                Finding(
                    "discovery.inaccessible", "warn", str(path), None, "Could not inspect discovered skill metadata"
                ),
            )
    return tuple(names), ()
