"""Contract-test inputs for `graph_works_wire.agent_config`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from graph_works_core.agent_config import (
    AgentConfig,
    AgentConfigReport,
    Finding,
    KeyEntry,
    Layer,
    TrustStatus,
)
from graph_works_core.agent_config.records import ProjectAgentConfig
from graph_works_wire import agent_config

_PRESENT = AgentConfigReport(
    (
        ProjectAgentConfig(
            Path("/repo"),
            True,
            (
                AgentConfig(
                    "claude",
                    Path("/home/.claude"),
                    TrustStatus("trusted", Path("/home/.claude.json"), "/repo", "exact", False),
                    (
                        Layer(
                            "project",
                            Path("/repo/.claude/settings.json"),
                            "json",
                            True,
                            "ok",
                            None,
                            "committed",
                            True,
                            None,
                            {"plugins": ["gw@market"]},
                        ),
                    ),
                    {"plugins": ["gw@market"]},
                    (KeyEntry(("plugins",), ("project",), ("project",)),),
                    (),
                    "https://example.invalid/claude",
                ),
            ),
        ),
    )
)

_ABSENT = AgentConfigReport(
    (
        ProjectAgentConfig(
            Path("/gone"),
            False,
            (
                AgentConfig(
                    "pi",
                    Path("/home/.pi/agent"),
                    TrustStatus("unrecorded", None, None, None, True),
                    (
                        Layer(
                            "user",
                            Path("/home/.pi/agent/settings.json"),
                            "json",
                            False,
                            "absent",
                            None,
                            "outside-repo",
                            False,
                            None,
                            None,
                        ),
                    ),
                    {},
                    (),
                    (Finding("agent-config.absent", "not configured", None),),
                    "https://example.invalid/pi",
                ),
            ),
        ),
    )
)

_ERROR_AND_EXCLUDED = AgentConfigReport(
    (
        ProjectAgentConfig(
            Path("/untrusted"),
            True,
            (
                AgentConfig(
                    "pi",
                    Path("/home/.pi/agent"),
                    TrustStatus("untrusted", Path("/home/.pi/agent/trust.json"), "/untrusted", "exact", False),
                    (
                        Layer(
                            "project",
                            Path("/untrusted/.pi/settings.json"),
                            "json",
                            True,
                            "error",
                            "invalid JSON",
                            "untracked",
                            False,
                            "untrusted",
                            None,
                        ),
                    ),
                    {},
                    (),
                    (Finding("agent-config.parse", "invalid JSON", "/untrusted/.pi/settings.json"),),
                    "https://example.invalid/pi",
                ),
            ),
        ),
    )
)

AGENT_CONFIG: dict[str, tuple[Callable[[], object], ...]] = {
    "agent_config.agent_config_payload": (
        lambda: agent_config.agent_config_payload(_PRESENT),
        lambda: agent_config.agent_config_payload(_ABSENT),
        lambda: agent_config.agent_config_payload(_ERROR_AND_EXCLUDED),
    ),
}
