"""Per-agent merge rules as data, and the one merge that reads them.

The effective value models each agent's documented merge; it is not the agent's
own code. Every policy cites where its rules were taken from.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from graph_works_core.agent_config.records import (
    PROJECT_SCOPES,
    AgentName,
    ExcludedReason,
    Finding,
    JsonValue,
    KeyEntry,
    Scope,
    TrustState,
)

type PathPattern = tuple[str, ...]

_ALL_SCOPES: frozenset[Scope] = frozenset({"user", "project", "local", "managed"})
_PERSONAL_SCOPES: frozenset[Scope] = frozenset({"user", "local", "managed"})


@dataclass(frozen=True, slots=True)
class MergePolicy:
    precedence: tuple[Scope, ...]
    arrays: Literal["concat", "replace"]
    whole_value_paths: tuple[PathPattern, ...]
    project_forbidden: tuple[PathPattern, ...]
    trust_gated_scopes: frozenset[Scope]
    trust_gated_paths: tuple[PathPattern, ...]
    source: str
    managed_whole_paths: tuple[PathPattern, ...] = ()
    scope_forbidden: tuple[tuple[Scope, tuple[PathPattern, ...]], ...] = ()
    restrictive_booleans: tuple[tuple[PathPattern, bool, frozenset[Scope]], ...] = ()
    claude_exceptions: bool = False


POLICIES: Mapping[AgentName, MergePolicy] = {
    "claude": MergePolicy(
        precedence=("user", "project", "local", "managed"),
        arrays="concat",
        whole_value_paths=(("fallbackModel",), ("modelPicker",)),
        managed_whole_paths=(("availableModels",),),
        claude_exceptions=True,
        scope_forbidden=(("project", (("useAutoModeDuringPlan",), ("syncClaudeAiSkills",), ("syncClaudeAiPlugins",))),),
        project_forbidden=(("modelPicker",),),
        restrictive_booleans=tuple(
            ((key,), value, scopes)
            for key, value, scopes in (
                ("disableClaudeAiConnectors", True, _ALL_SCOPES),
                ("disableArtifact", True, _ALL_SCOPES),
                ("enableArtifact", False, _ALL_SCOPES),
                ("isolatePeerMachines", True, _ALL_SCOPES),
                ("remoteControlAtStartup", False, PROJECT_SCOPES),
                ("useAutoModeDuringPlan", False, _PERSONAL_SCOPES),
                ("syncClaudeAiSkills", False, _PERSONAL_SCOPES),
                ("syncClaudeAiPlugins", False, _PERSONAL_SCOPES),
            )
        ),
        trust_gated_scopes=frozenset(),
        trust_gated_paths=(("permissions", "allow"), ("permissions", "additionalDirectories")),
        source="https://code.claude.com/docs/en/settings ; https://code.claude.com/docs/en/permissions",
    ),
    "codex": MergePolicy(
        precedence=("user", "project"),
        arrays="replace",
        whole_value_paths=(),
        project_forbidden=tuple(
            (key,)
            for key in (
                "openai_base_url",
                "chatgpt_base_url",
                "apps_mcp_product_sku",
                "responses_api_metadata",
                "model_provider",
                "model_providers",
                "notify",
                "profile",
                "profiles",
                "experimental_realtime_webrtc_call_base_url",
                "experimental_realtime_ws_base_url",
                "otel",
            )
        ),
        trust_gated_scopes=frozenset({"project"}),
        trust_gated_paths=(),
        source=(
            "https://github.com/openai/codex/blob/main/codex-rs/config/src/loader/mod.rs ; "
            "https://github.com/openai/codex/blob/main/codex-rs/config/src/merge.rs"
        ),
    ),
    "pi": MergePolicy(
        precedence=("user", "project"),
        arrays="replace",
        whole_value_paths=(),
        project_forbidden=(),
        trust_gated_scopes=frozenset({"project"}),
        trust_gated_paths=(),
        source="https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/settings.md",
    ),
}


@dataclass(frozen=True, slots=True)
class LayerInput:
    scope: Scope
    data: dict[str, JsonValue] | None
    applied: bool
    permission_gate: bool | None = None


@dataclass(frozen=True, slots=True)
class MergeResult:
    effective: dict[str, JsonValue]
    keys: tuple[KeyEntry, ...]
    findings: tuple[Finding, ...]


def _matches(path: tuple[str, ...], patterns: tuple[PathPattern, ...]) -> bool:
    return any(
        len(path) >= len(pattern)
        and all(expected in ("*", actual) for expected, actual in zip(pattern, path, strict=False))
        for pattern in patterns
    )


def layer_gate(policy: MergePolicy, scope: Scope, trust: TrustState) -> ExcludedReason | None:
    if scope not in policy.trust_gated_scopes:
        return None
    if trust == "untrusted":
        return "untrusted"
    if trust == "unrecorded":
        return "trust-unrecorded"
    return None


def _leaves(
    value: dict[str, JsonValue], whole: tuple[PathPattern, ...], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], JsonValue]]:
    for key, child in value.items():
        path = (*prefix, key)
        if isinstance(child, dict) and child and not _matches(path, whole):
            yield from _leaves(child, whole, path)
        else:
            yield path, child


def _models(data: dict[str, JsonValue]) -> dict[str, JsonValue]:
    models = data.get("modelSettings")
    return models if isinstance(models, dict) else {}


def merge(policy: MergePolicy, layers: Sequence[LayerInput], trust: TrustState) -> MergeResult:
    """Merge low-to-high input layers without modifying their parsed data."""
    rank = {scope: index for index, scope in enumerate(policy.precedence)}
    effective: dict[str, JsonValue] = {}
    origin: dict[tuple[str, ...], list[Scope]] = {}
    defined: dict[tuple[str, ...], set[Scope]] = {}
    findings: list[Finding] = []
    gated = trust in ("untrusted", "unrecorded")
    restrictions = {path: (value, scopes) for path, value, scopes in policy.restrictive_booleans}
    models = (
        {
            name
            for layer in layers
            if layer.data and layer.applied
            for name, entry in _models(layer.data).items()
            if isinstance(entry, dict)
        }
        if policy.claude_exceptions
        else set()
    )

    def drop_related(path: tuple[str, ...]) -> None:
        related = [
            candidate for candidate in origin if candidate[: len(path)] == path or path[: len(candidate)] == candidate
        ]
        for other in related:
            del origin[other]

    for layer in layers:
        if layer.data is None:
            continue
        leaves = [(path, value, True) for path, value in _leaves(layer.data, policy.whole_value_paths)]
        if policy.claude_exceptions and "effortLevel" in layer.data:
            for model in sorted(models):
                entry = _models(layer.data).get(model)
                if not isinstance(entry, dict) or "effortLevel" not in entry:
                    leaves.append((("modelSettings", model, "effortLevel"), layer.data["effortLevel"], False))
        for path, value, authored in leaves:
            if authored:
                defined.setdefault(path, set()).add(layer.scope)
            if not layer.applied:
                continue
            dotted = ".".join(path)
            forbidden = layer.scope in PROJECT_SCOPES and _matches(path, policy.project_forbidden)
            forbidden |= any(layer.scope == scope and _matches(path, paths) for scope, paths in policy.scope_forbidden)
            forbidden |= policy.claude_exceptions and (
                (path == ("remoteControlAtStartup",) and layer.scope in PROJECT_SCOPES and value is not False)
                or (path == ("disableArtifact",) and value is False)
            )
            if forbidden:
                findings.append(
                    Finding("agent-config.not-overridable", f"{dotted} cannot be set from {layer.scope} scope")
                )
                continue
            gate = gated if layer.permission_gate is None else layer.permission_gate
            if layer.scope in PROJECT_SCOPES and gate and _matches(path, policy.trust_gated_paths):
                findings.append(Finding("agent-config.trust-gated-key", f"{dotted} is ignored until trusted"))
                continue
            node = effective
            for depth, segment in enumerate(path[:-1]):
                if not isinstance(node.get(segment), dict):
                    node[segment] = {}
                    drop_related(path[: depth + 1])
                child = node[segment]
                assert isinstance(child, dict)
                node = child
            last = path[-1]
            existing = node.get(last)
            whole = _matches(path, policy.whole_value_paths) or (
                layer.scope == "managed" and _matches(path, policy.managed_whole_paths)
            )
            restriction = restrictions.get(path)
            if restriction is not None:
                restrictive, scopes = restriction
                locked = existing is restrictive and bool(set(origin.get(path, ())) & scopes)
                incoming_lock = value is restrictive and layer.scope in scopes
                if locked:
                    if incoming_lock:
                        origin[path].append(layer.scope)
                    continue
            if isinstance(value, dict) and not value and isinstance(existing, dict) and not whole:
                continue
            if policy.arrays == "concat" and not whole and isinstance(value, list) and isinstance(existing, list):
                merged = list(existing)
                for item in value:
                    if item not in merged:
                        merged.append(deepcopy(item))
                node[last] = merged
                origin.setdefault(path, []).append(layer.scope)
                continue
            if isinstance(value, list) and policy.arrays == "concat" and not whole:
                value = [item for index, item in enumerate(value) if item not in value[:index]]
            node[last] = deepcopy(value)
            drop_related(path)
            origin[path] = [layer.scope]

    if policy.claude_exceptions and effective.get("disableArtifact") is True and "enableArtifact" in effective:
        prior = origin.get(("enableArtifact",), []) if effective["enableArtifact"] is False else []
        effective["enableArtifact"] = False
        origin[("enableArtifact",)] = [*prior, *origin[("disableArtifact",)]]

    def high_to_low(scopes: Iterable[Scope]) -> tuple[Scope, ...]:
        return tuple(sorted(set(scopes), key=lambda scope: -rank[scope]))

    keys = tuple(
        KeyEntry(path, high_to_low(defined.get(path, ())), high_to_low(origin.get(path, ())))
        for path in sorted(defined.keys() | origin.keys())
    )
    return MergeResult(effective, keys, tuple(dict.fromkeys(findings)))
