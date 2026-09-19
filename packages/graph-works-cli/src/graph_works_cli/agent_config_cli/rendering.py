"""Human and JSON renderings of an `AgentConfigReport`."""

from __future__ import annotations

from graph_works_core.agent_config import AgentConfigReport
from graph_works_wire.agent_config import agent_config_payload

from graph_works_cli.json_output import encode


def render(report: AgentConfigReport, *, json_output: bool) -> str:
    """Render the wire payload as JSON or a compact, human-readable report."""
    if json_output:
        return encode(agent_config_payload(report))

    lines: list[str] = []
    for project in report.projects:
        lines.append(f"{project.path}" + ("" if project.exists else "  (missing)"))
        for agent in project.agents:
            trust = agent.trust
            source = f" via {trust.matched_key} ({trust.match})" if trust.matched_key else ""
            assumed = " [assumed]" if trust.assumed else ""
            lines.append(f"  {agent.agent}  trust: {trust.state}{source}{assumed}")
            for layer in agent.layers:
                status = "applied" if layer.applied else (layer.excluded_reason or layer.parse)
                lines.append(f"    {layer.scope:<8} {layer.parse:<6} {layer.git:<12} {status:<16} {layer.path}")
            for key in agent.keys:
                source_scopes = ",".join(key.effective_from) or "-"
                lines.append(f"    {'.'.join(key.path)} <- {source_scopes}")
            for finding in agent.findings:
                lines.append(f"    ! {finding.code}: {finding.message}")
    return "\n".join(lines)
