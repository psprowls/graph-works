"""Plain-data projection for the read-only agent-configuration report."""

from __future__ import annotations

from graph_works_core.agent_config import AgentConfig, AgentConfigReport, Layer
from graph_works_core.agent_config.records import JsonValue


def _layer(layer: Layer) -> dict[str, JsonValue]:
    return {
        "scope": layer.scope,
        "path": str(layer.path),
        "format": layer.format,
        "exists": layer.exists,
        "parse": layer.parse,
        "parse_error": layer.parse_error,
        "git": layer.git,
        "applied": layer.applied,
        "excluded_reason": layer.excluded_reason,
        "data": layer.data,
    }


def _agent(agent: AgentConfig) -> dict[str, JsonValue]:
    trust = agent.trust
    return {
        "agent": agent.agent,
        "home": str(agent.home),
        "policy_source": agent.policy_source,
        "trust": {
            "state": trust.state,
            "record_path": str(trust.record_path) if trust.record_path is not None else None,
            "matched_key": trust.matched_key,
            "match": trust.match,
            "assumed": trust.assumed,
        },
        "layers": [_layer(layer) for layer in agent.layers],
        "effective": agent.effective,
        "keys": [
            {
                "path": list(key.path),
                "defined_in": list(key.defined_in),
                "effective_from": list(key.effective_from),
            }
            for key in agent.keys
        ],
        "findings": [
            {"code": finding.code, "message": finding.message, "path": finding.path} for finding in agent.findings
        ],
    }


def agent_config_payload(report: AgentConfigReport) -> dict[str, JsonValue]:
    """Project every agent's layered configuration to plain JSON-compatible data."""
    return {
        "projects": [
            {"path": str(project.path), "exists": project.exists, "agents": [_agent(agent) for agent in project.agents]}
            for project in report.projects
        ]
    }
