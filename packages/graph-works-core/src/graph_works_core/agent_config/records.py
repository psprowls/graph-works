"""The agent-config read's public records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type AgentName = Literal["claude", "codex", "pi"]
type Scope = Literal["managed", "user", "project", "local"]
type GitState = Literal["committed", "ignored", "untracked", "outside-repo", "unknown"]
type ExcludedReason = Literal["untrusted", "trust-unrecorded"]
type TrustState = Literal["trusted", "untrusted", "unrecorded", "unknown"]
type ParseState = Literal["ok", "absent", "error"]

AGENTS: tuple[AgentName, ...] = ("claude", "codex", "pi")
#: Project-related scopes, including a main-checkout local file. These carry git
#: provenance and are subject to project restrictions and per-file trust gates.
PROJECT_SCOPES: frozenset[Scope] = frozenset({"project", "local"})


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class Layer:
    scope: Scope
    path: Path
    format: Literal["json", "toml"]
    exists: bool
    parse: ParseState
    parse_error: str | None
    git: GitState
    applied: bool
    excluded_reason: ExcludedReason | None
    data: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class TrustStatus:
    state: TrustState
    record_path: Path | None
    matched_key: str | None
    match: Literal["exact", "ancestor"] | None
    assumed: bool


@dataclass(frozen=True, slots=True)
class KeyEntry:
    path: tuple[str, ...]
    defined_in: tuple[Scope, ...]
    effective_from: tuple[Scope, ...]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    agent: AgentName
    home: Path
    trust: TrustStatus
    layers: tuple[Layer, ...]
    effective: dict[str, JsonValue]
    keys: tuple[KeyEntry, ...]
    findings: tuple[Finding, ...]
    policy_source: str


@dataclass(frozen=True, slots=True)
class ProjectAgentConfig:
    path: Path
    exists: bool
    agents: tuple[AgentConfig, ...]


@dataclass(frozen=True, slots=True)
class AgentConfigReport:
    projects: tuple[ProjectAgentConfig, ...]
