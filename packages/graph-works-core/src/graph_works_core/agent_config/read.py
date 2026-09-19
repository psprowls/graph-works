"""The public read-only entry points for project agent configuration.

Nothing on the content path raises; only malformed workspace configuration
propagates its configuration error.  The caller injects all machine state.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import tomllib
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Literal

from graph_works_core.agent_config.conventions import ResolvedLayer, resolve_conventions
from graph_works_core.agent_config.git_state import GitProbe, RepositoryContext, SubprocessGit, repository_context
from graph_works_core.agent_config.local import permission_gate, resolve_local
from graph_works_core.agent_config.merge import POLICIES, LayerInput, MergePolicy, layer_gate, merge
from graph_works_core.agent_config.records import (
    AGENTS,
    PROJECT_SCOPES,
    AgentConfig,
    AgentConfigReport,
    AgentName,
    Finding,
    GitState,
    JsonValue,
    Layer,
    ParseState,
    ProjectAgentConfig,
    TrustState,
)
from graph_works_core.agent_config.trust import read_trust
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout

# Bound both objects and arrays before recursive merge, deepcopy and wire encoding.
_MAX_CONTENT_DEPTH = 64


def _jsonable(value: object, depth: int = 0) -> JsonValue:
    if depth > _MAX_CONTENT_DEPTH:
        raise ValueError(f"configuration nesting exceeds {_MAX_CONTENT_DEPTH} levels")
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item, depth + 1) for item in value]
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    assert value is None or isinstance(value, (bool, int, float, str))
    return value


def _parse(
    path: Path, fmt: Literal["json", "toml"]
) -> tuple[bool, ParseState, str | None, dict[str, JsonValue] | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return False, "absent", None, None
    except OSError as exc:
        exists = False
        with suppress(OSError):
            exists = path.exists()
        return exists, "error", str(exc), None
    try:
        data: object = tomllib.loads(raw.decode("utf-8")) if fmt == "toml" else json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, dict):
            return True, "error", f"top-level value is {type(data).__name__}, not an object", None
        converted = _jsonable(data)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        return True, "error", str(exc), None
    assert isinstance(converted, dict)
    return True, "ok", None, converted


def _layer(
    spec: ResolvedLayer,
    project: Path,
    git: GitProbe,
    trust_state: TrustState,
    policy: MergePolicy,
    findings: list[Finding],
) -> Layer:
    exists, parsed, parse_error, data = _parse(spec.path, spec.format)
    if parsed == "error":
        findings.append(Finding("agent-config.parse", parse_error or "Could not parse configuration", str(spec.path)))
    state: GitState
    if spec.scope in PROJECT_SCOPES:
        try:
            relative = spec.path.relative_to(project).as_posix()
            git_base = project
        except ValueError:
            git_base = spec.path.parent.parent
            relative = spec.path.relative_to(git_base).as_posix()
        state, cause = git.state(git_base, relative)
        if state == "unknown":
            findings.append(Finding("agent-config.git", f"Git state is unknown: {cause}", str(spec.path)))
    else:
        state = "outside-repo"
    if spec.location_error:
        findings.append(Finding("agent-config.local-location", spec.location_error, str(spec.path)))
    excluded_reason = layer_gate(policy, spec.scope, trust_state)
    return Layer(
        spec.scope,
        spec.path,
        spec.format,
        exists,
        parsed,
        parse_error,
        state,
        parsed == "ok" and excluded_reason is None and spec.location_error is None,
        excluded_reason,
        data,
    )


def _read_agent(
    agent: AgentName,
    project: Path,
    *,
    home: Path,
    env: Mapping[str, str],
    platform: str,
    git: GitProbe,
    context: RepositoryContext,
    user_id: int | None,
) -> AgentConfig:
    conventions = resolve_conventions(agent, project=project, home=home, env=env, platform=platform)
    if agent == "claude":
        conventions = resolve_local(conventions, project, home.resolve(), context, platform=platform, user_id=user_id)
    policy = POLICIES[agent]
    findings: list[Finding] = []
    trust, trust_findings = read_trust(conventions.trust, conventions.trust_path, project, context=context)
    if trust.state == "unknown" and (policy.trust_gated_scopes or policy.trust_gated_paths):
        trust = replace(trust, assumed=True)
        trust_findings = (
            *trust_findings,
            Finding(
                "agent-config.trust-assumed",
                "Trust is unknown; trust gates are assumed open (other exclusions still apply)",
            ),
        )
    layers = [_layer(spec, project, git, trust.state, policy, findings) for spec in conventions.layers]
    inputs = [
        LayerInput(
            layer.scope,
            layer.data,
            layer.applied,
            permission_gate(layer, project, home.resolve(), conventions.home.resolve(), context, trust, findings)
            if agent == "claude"
            else None,
        )
        for layer in layers
    ]
    merged = merge(policy, inputs, trust.state)
    return AgentConfig(
        agent,
        conventions.home,
        trust,
        tuple(layers),
        merged.effective,
        merged.keys,
        (*findings, *trust_findings, *merged.findings),
        policy.source,
    )


def read_project(
    project: Path,
    *,
    home: Path,
    env: Mapping[str, str],
    agents: Sequence[AgentName] = AGENTS,
    platform: str = sys.platform,
    git: GitProbe | None = None,
    context: RepositoryContext | None = None,
    user_id: int | None = None,
) -> ProjectAgentConfig:
    """Read each requested agent's configuration layers for one project."""
    resolved_project = project.resolve()
    if not resolved_project.is_dir():
        return ProjectAgentConfig(resolved_project, False, ())
    probe = git or SubprocessGit()
    identity = (
        context
        if context is not None
        else (
            repository_context(probe, resolved_project)
            if any(agent != "pi" for agent in agents)
            else RepositoryContext()
        )
    )
    return ProjectAgentConfig(
        resolved_project,
        True,
        tuple(
            _read_agent(
                agent,
                resolved_project,
                home=home,
                env=env,
                platform=platform,
                git=probe,
                context=identity,
                user_id=user_id,
            )
            for agent in agents
        ),
    )


def _project_set(layout: WorkspaceLayout) -> list[Path]:
    paths = [layout.root]
    with suppress(FileNotFoundError):
        paths.extend(entry.path for entry in load_workspace_config(layout).repos)
    return paths


def show(
    layout: WorkspaceLayout | None,
    *,
    project: Path | None = None,
    home: Path,
    env: Mapping[str, str],
    agents: Sequence[AgentName] = AGENTS,
    platform: str = sys.platform,
    git: GitProbe | None = None,
    context: RepositoryContext | None = None,
    user_id: int | None = None,
) -> AgentConfigReport:
    """Read configuration for an explicit project or each workspace project."""
    if project is not None:
        candidates = [project]
    elif layout is not None:
        candidates = _project_set(layout)
    else:
        raise ValueError("show() needs a workspace layout or an explicit project")
    probe = git or SubprocessGit()
    resolved = list(dict.fromkeys(candidate.resolve() for candidate in candidates))
    return AgentConfigReport(
        tuple(
            read_project(
                path, home=home, env=env, agents=agents, platform=platform, git=probe, context=context, user_id=user_id
            )
            for path in resolved
        )
    )


__all__ = ["read_project", "show"]
