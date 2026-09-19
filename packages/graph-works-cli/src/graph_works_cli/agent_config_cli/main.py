"""`gw agent-config` — report each coding agent's configuration. Read-only.

ADR-0013: this module resolves, injects (`Path.home()`, environment, platform)
and formats; core owns all configuration logic and wire owns the JSON projection.
"""

from __future__ import annotations

import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import cast

import typer
from graph_works_core.agent_config import AGENTS
from graph_works_core.agent_config import show as show_agent_config
from graph_works_core.agent_config.records import AgentName
from graph_works_core.workspace.errors import WorkspaceError

from graph_works_cli import exit_codes
from graph_works_cli.agent_config_cli.rendering import render
from graph_works_cli.workspace_resolution import resolve_workspace

agent_config_app = typer.Typer(
    name="agent-config",
    help="Read how Claude Code, Codex and Pi are configured for this workspace's projects.",
    no_args_is_help=True,
)


class AgentChoice(StrEnum):
    """The agent names the CLI accepts as repeatable filters."""

    claude = "claude"
    codex = "codex"
    pi = "pi"


_WORKSPACE_OPTION = typer.Option(
    "", "--workspace", help="Workspace path (default: GRAPH_WORKS_DIR, then cwd discovery)."
)
_PROJECT_OPTION = typer.Option("", "--project", help="Report this one directory; no workspace needed.")
_AGENT_OPTION = typer.Option(None, "--agent", help="Limit to an agent (repeatable).")
_JSON_OPTION = typer.Option(False, "--json", help="Emit machine-readable JSON.")


@agent_config_app.command()
def show(
    workspace: str = _WORKSPACE_OPTION,
    project: str = _PROJECT_OPTION,
    agent: list[AgentChoice] | None = _AGENT_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Show every layer, git state, trust decision, and effective value."""
    agents = cast(tuple[AgentName, ...], tuple(choice.value for choice in agent)) if agent else AGENTS
    user_id: int | None
    if sys.platform == "win32":
        user_id = None
    else:
        user_id = os.getuid()
    try:
        if project:
            report = show_agent_config(
                None,
                project=Path(project).expanduser(),
                home=Path.home(),
                env=os.environ,
                agents=agents,
                platform=sys.platform,
                user_id=user_id,
            )
        else:
            report = show_agent_config(
                resolve_workspace(workspace),
                home=Path.home(),
                env=os.environ,
                agents=agents,
                platform=sys.platform,
                user_id=user_id,
            )
    except WorkspaceError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=exit_codes.SCHEMA_MISMATCH) from exc
    typer.echo(render(report, json_output=json_output))
