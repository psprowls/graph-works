"""The agent-config vertical: how each coding agent is configured for a project, and why.

Read-only. Layer 1 — independent of every other vertical; imports `workspace` and
`plugin_fork_io.adapters` (the where-agents-keep-things table) only.
"""

from __future__ import annotations

from graph_works_core.agent_config.git_state import GitProbe, SubprocessGit
from graph_works_core.agent_config.read import read_project, show
from graph_works_core.agent_config.records import (
    AGENTS,
    AgentConfig,
    AgentConfigReport,
    Finding,
    KeyEntry,
    Layer,
    ProjectAgentConfig,
    TrustStatus,
)

__all__ = [
    "AGENTS",
    "AgentConfig",
    "AgentConfigReport",
    "Finding",
    "GitProbe",
    "KeyEntry",
    "Layer",
    "ProjectAgentConfig",
    "SubprocessGit",
    "TrustStatus",
    "read_project",
    "show",
]
